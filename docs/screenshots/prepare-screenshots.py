#!/usr/bin/env python3
"""Prepare privacy-reviewed documentation PNG candidates; dry-run by default."""

import argparse
import io
import os
from pathlib import Path
import stat
import struct
import sys
import zlib

SIGNATURE = b'\x89PNG\r\n\x1a\n'
SIZE = (1440, 900)
MAX_OUTPUT = 2 * 1024 * 1024
MAX_INPUT = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000
GAMMA = struct.pack('>I', 45455)
CHROMATICITIES = struct.pack('>8I', 31270, 32900, 64000, 33000,
                              30000, 60000, 15000, 6000)
COLORS = {b'sRGB', b'gAMA', b'cHRM'}
ALLOWED = {b'IHDR', b'IDAT', b'IEND'} | COLORS
BASELINES = Path(__file__).resolve().parent

# Exact top-left alpha samples from both approved screenshots at 8e2fc08.
# Each row ends at its last nonopaque pixel; all other pixels are opaque.
# Reflect this measured 12 x 12 corner rather than rasterizing a guessed radius.
CORNER_ALPHA = tuple(bytes.fromhex(row) for row in (
    '00 00 00 00 00 00 00 1f 77 b9 e4 f9',
    '00 00 00 00 00 1e a7 fd',
    '00 00 00 00 4e f2',
    '00 00 00 6e fd',
    '00 00 4e fd',
    '00 1e f2',
    '00 a7',
    '1f fd',
    '77',
    'b9',
    'e4',
    'f9',
))


class PublicationError(OSError):
    def __init__(self, message, output_retained):
        super().__init__(message)
        self.output_retained = output_retained


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))


def inspect_png(data):
    """Validate the container and bounded, complete decompressed scanline stream."""
    if len(data) > MAX_INPUT or not data.startswith(SIGNATURE):
        raise ValueError('Expected PNG input no larger than 64 MiB')
    chunks = []
    offset = 8
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('Truncated PNG chunk')
        length = struct.unpack_from('>I', data, offset)[0]
        end = offset + 12 + length
        if length > 0x7fffffff or end > len(data):
            raise ValueError('Invalid or truncated PNG payload')
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:end - 4]
        if not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind) or kind[2] & 32:
            raise ValueError('Invalid PNG chunk type')
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack_from('>I', data, end - 4)[0]:
            raise ValueError('Invalid PNG CRC')
        if not kind[0] & 32 and kind not in {b'IHDR', b'IDAT', b'IEND'}:
            raise ValueError('Unsupported critical PNG chunk')
        chunks.append((kind, payload))
        offset = end
        if kind == b'IEND':
            break
    kinds = [k for k, _ in chunks]
    if (not kinds or kinds[0] != b'IHDR' or kinds[-1] != b'IEND'
            or kinds.count(b'IHDR') != 1 or kinds.count(b'IEND') != 1
            or b'IDAT' not in kinds or chunks[-1][1] or offset != len(data)):
        raise ValueError('Invalid PNG structure or trailing data')
    ids = [i for i, k in enumerate(kinds) if k == b'IDAT']
    if ids != list(range(ids[0], ids[-1] + 1)):
        raise ValueError('Nonconsecutive IDAT chunks')
    singletons = COLORS | {b'iCCP', b'eXIf', b'pHYs', b'tIME', b'sBIT', b'bKGD', b'tRNS'}
    if any(kinds.count(k) > 1 for k in singletons):
        raise ValueError('Duplicate PNG singleton chunk')
    before = COLORS | {b'iCCP', b'pHYs', b'sBIT', b'bKGD', b'tRNS'}
    if any(k in before for k in kinds[ids[0] + 1:]):
        raise ValueError('Invalid PNG metadata ordering')
    if any(k in kinds for k in (b'iCCP', b'cICP', b'mDCV', b'cLLI', b'sBIT')):
        raise ValueError('Unapproved color information/profile; convert to reviewed sRGB separately')
    if any(k in kinds for k in (b'acTL', b'fcTL', b'fdAT', b'tRNS', b'hIST')):
        raise ValueError('Unsupported animation or transparency encoding')
    for kind, payload in chunks:
        if ((kind == b'gAMA' and payload != GAMMA)
                or (kind == b'cHRM' and payload != CHROMATICITIES)
                or (kind == b'sRGB' and payload not in (b'\0', b'\1', b'\2', b'\3'))):
            raise ValueError('Unapproved sRGB color values')
    if len(chunks[0][1]) != 13:
        raise ValueError('Invalid IHDR')
    width, height, bits, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunks[0][1])
    if bits != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
        raise ValueError('Use static 8-bit RGB/RGBA noninterlaced PNG')
    if width < SIZE[0] or height < SIZE[1] or width * 5 != height * 8:
        raise ValueError('Expected exact 8:5 ratio, at least 1440 x 900; no crop or upscale')
    if width * height > MAX_PIXELS:
        raise ValueError('Input exceeds 40 million pixels')
    stride = width * (4 if color == 6 else 3)
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(b''.join(v for k, v in chunks if k == b'IDAT'), expected + 1)
    except zlib.error as exc:
        raise ValueError('Invalid compressed PNG pixels') from exc
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid compressed PNG pixel length or trailing stream')
    if any(raw[y * (stride + 1)] > 4 for y in range(height)):
        raise ValueError('Invalid PNG row filter')
    return chunks, (width, height)


def round_corners(image):
    """Restore the approved corner pixels after resizing; retain lower alpha."""
    rounded = image.convert('RGBA')
    width, height = rounded.size
    for y, row in enumerate(CORNER_ALPHA):
        for x, mask_alpha in enumerate(row):
            for point in ((x, y), (width - 1 - x, y),
                          (x, height - 1 - y), (width - 1 - x, height - 1 - y)):
                red, green, blue, alpha = rounded.getpixel(point)
                alpha = min(alpha, mask_alpha)
                # Match the historical 8-bit premultiply/unpremultiply rounding.
                # This also clears hidden RGB at fully transparent corner pixels.
                rgb = tuple(((c * alpha + 127) // 255 * 255 + alpha // 2) // alpha
                            for c in (red, green, blue)) if alpha else (0, 0, 0)
                rounded.putpixel(point, (*rgb, alpha))
    return rounded


def prepare(data):
    try:
        from PIL import Image, PngImagePlugin
    except ImportError as exc:
        raise ValueError('Pillow is required for this optional documentation tool; no packages are installed automatically') from exc
    if not hasattr(Image, 'Resampling'):
        raise ValueError('This optional documentation tool requires Pillow 9.1+ (Image.Resampling.LANCZOS)')
    chunks, dimensions = inspect_png(data)
    # Strip metadata before decoding: EXIF must never rotate pixels, and text
    # metadata must never be decompressed or copied by the imaging backend.
    clean = SIGNATURE + b''.join(chunk(k, v) for k, v in chunks if k in ALLOWED)
    with Image.open(io.BytesIO(clean)) as original:
        original.load()
        if original.size != dimensions or original.mode not in ('RGB', 'RGBA'):
            raise ValueError('Unexpected decoded PNG image')
        resized = original.resize(SIZE, Image.Resampling.LANCZOS) if dimensions != SIZE else original
        fresh = round_corners(Image.frombytes(resized.mode, SIZE, resized.tobytes()))
        info = PngImagePlugin.PngInfo()
        colors = [(k, v) for k, v in chunks if k in COLORS]
        for k, v in colors:
            info.add(k, v)
        if not any(k == b'sRGB' for k, _ in colors):
            # Untagged captures are explicitly interpreted as sRGB by this tool.
            info.add(b'sRGB', b'\0')
        output = io.BytesIO()
        fresh.save(output, format='PNG', pnginfo=info, compress_level=9)
    result = output.getvalue()
    validate_output(result)
    return result, dimensions, sorted({k.decode() for k, _ in chunks if k not in ALLOWED})


def validate_output(data):
    chunks, dimensions = inspect_png(data)
    if dimensions != SIZE or len(data) > MAX_OUTPUT:
        raise ValueError('Output must be 1440 x 900 and at most 2 MiB')
    if any(k not in ALLOWED for k, _ in chunks) or not any(k == b'sRGB' for k, _ in chunks):
        raise ValueError('Output metadata violates privacy policy')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.size != SIZE or image.mode not in ('RGB', 'RGBA'):
            raise ValueError('Invalid decoded output')


def open_directory(path):
    """Pin an existing directory, refusing symlinks in every path component."""
    absolute = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_input(path):
    if path.suffix.lower() != '.png':
        raise ValueError('Input filename must end in .png')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_INPUT:
            raise ValueError('Input must be a regular PNG file no larger than 64 MiB')
        data = stream.read(MAX_INPUT + 1)
    return data, (status.st_dev, status.st_ino)


def publish(data, directory_fd, name):
    """Stage in the pinned directory, validate, then atomically link without replace."""
    # /dev/fd is not portable for mkstemp. Create via dir_fd instead.
    temporary = '.screenshot-' + os.urandom(16).hex() + '.tmp'
    linked = False
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        candidate_fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        with os.fdopen(candidate_fd, 'rb') as stream:
            validate_output(stream.read(MAX_OUTPUT + 1))
        os.link(temporary, name, src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd, follow_symlinks=False)
        linked = True
        os.unlink(temporary, dir_fd=directory_fd)
    except BaseException as error:
        if linked:
            try:
                os.unlink(name, dir_fd=directory_fd)
                linked = False
            except OSError as rollback_error:
                raise PublicationError(
                    f'Publication failed; current output {name} remains because rollback failed: '
                    f'{rollback_error}', True) from error
        raise
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                raise PublicationError(
                    f'Temporary cleanup failed for {temporary}: {cleanup_error}; '
                    f'current output retained: {linked}', linked) from cleanup_error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', type=Path)
    parser.add_argument('--output-dir', required=True, type=Path,
                        help='Existing separate review directory; no symlink components')
    parser.add_argument('--apply', action='store_true', help='Write new candidates (never overwrite)')
    args = parser.parse_args(argv)
    published = 0
    validated = 0
    directory_fd = None
    try:
        if args.output_dir.resolve() == BASELINES:
            raise ValueError('Use a separate review directory, not the approved screenshots directory')
        directory_fd = open_directory(args.output_dir)
        names = [p.with_suffix('.png').name for p in args.inputs]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate destination collision')
        seen = set()
        # Preflight the entire batch before any publication.
        for path, name in zip(args.inputs, names):
            _, identity = read_input(path)
            if identity in seen:
                raise ValueError('Duplicate input alias')
            seen.add(identity)
            try:
                existing = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (existing.st_dev, existing.st_ino) in seen:
                raise ValueError('Input/output alias refused')
            raise ValueError('Destination already exists (including symlinks): ' + name)
        for path, name in zip(args.inputs, names):
            data, _ = read_input(path)
            candidate, dimensions, removed = prepare(data)
            validated += 1
            print(f'{path.name}: {dimensions[0]} x {dimensions[1]} -> 1440 x 900; '
                  f'{len(candidate)} bytes; removed chunks: {", ".join(removed) or "none"}')
            if args.apply:
                publish(candidate, directory_fd, name)
                published += 1
                print(f'Published: {args.output_dir / name}')
        print(f'{"Apply" if args.apply else "Dry-run"}: {validated}/{len(names)} validated; '
              f'{published} published. Originals unchanged.')
        return 0
    except (OSError, ValueError) as exc:
        if isinstance(exc, PublicationError) and exc.output_retained:
            published += 1
        print(f'ERROR: {exc}', file=sys.stderr)
        print(f'Batch failed: {validated}/{len(args.inputs)} validated; {published} published. '
              'Earlier published candidates are retained; remaining inputs were not processed.', file=sys.stderr)
        return 1
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


if __name__ == '__main__':
    sys.exit(main())
