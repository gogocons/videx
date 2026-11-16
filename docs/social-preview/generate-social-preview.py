#!/usr/bin/env python3
"""Generate a GitHub social preview for Videx using SVG."""

from pathlib import Path
import argparse
import html
import io
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib

ARTWORK_DIR = Path(__file__).resolve().parent
TIMEOUT = 90  # Includes Swift compilation; the renderer has its own 30-second limit.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

WIDTH = 1280
HEIGHT = 640

# Videx-inspired colors
BACKGROUND = "#101217"
ACCENT = "#d93636"
PRIMARY = "#e9edf4"
SECONDARY = "#aab3c2"
MUTED = "#7f8a9b"

# Reviewed sRGB-compatible color values; no ICC profiles are approved.
GAMMA = struct.pack(">I", 45455)
CHROMATICITIES = struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
PNG_ALLOWED = {b"IHDR", b"IDAT", b"IEND", b"gAMA", b"cHRM", b"sRGB"}
SVG_NS = "http://www.w3.org/2000/svg"
FONT_STACK = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"


def validate_svg(data):
    """Accept only the reviewed artwork structure, values, text, and comments."""
    text = data.decode("utf-8")
    if any(token in text for token in ("<?", "<!DOCTYPE", "<!ENTITY")):
        raise ValueError("SVG privacy: declarations and processing instructions are prohibited")
    try:
        namespaces = list(ET.iterparse(io.BytesIO(data), events=("start-ns",)))
        if namespaces != [("start-ns", ("", SVG_NS))]:
            raise ValueError("SVG privacy: only the default SVG namespace is approved")
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        root = ET.fromstring(data, parser=parser)
    except ET.ParseError as error:
        raise ValueError("SVG privacy: invalid XML") from error
    if root.tag != f"{{{SVG_NS}}}svg" or root.attrib != {
            "width": "1280", "height": "640", "viewBox": "0 0 1280 640"}:
        raise ValueError("SVG privacy: unapproved canvas or attributes")
    # Independent of the generation template: changes require explicit policy review.
    def label(value):
        return (ET.Comment, {}, value)
    def element(name, attributes, value=""):
        return (f"{{{SVG_NS}}}{name}", attributes, value)
    expected = [
        element("rect", {"width": "1280", "height": "640", "fill": "#101217"}),
        label("Videx play icon"),
        element("rect", {"x": "140", "y": "165", "width": "112", "height": "112", "rx": "24", "fill": "#d93636"}),
        label("Centered play symbol with rounded corners"),
        element("path", {"d": "M179 197 L179 245 L220 221 Z", "fill": "#e9edf4", "stroke": "#e9edf4", "stroke-width": "5", "stroke-linejoin": "round"}),
        label("Project name"),
        element("text", {"x": "280", "y": "251", "font-family": FONT_STACK, "font-size": "96", "font-weight": "700", "fill": "#e9edf4"}, "Videx"),
        label("Tagline"),
        element("text", {"x": "140", "y": "365", "font-family": FONT_STACK, "font-size": "38", "fill": "#aab3c2"}, "Your videos. Your library."),
        label("Description"),
        element("text", {"x": "140", "y": "430", "font-family": FONT_STACK, "font-size": "25", "fill": "#7f8a9b"}, "Offline-first video bookmarking"),
        label("Subtle decorative accent"),
        element("rect", {"x": "140", "y": "480", "width": "100", "height": "5", "rx": "2.5", "fill": "#d93636"}),
    ]
    if (root.text or "").strip() or (root.tail or "").strip() or len(root) != len(expected):
        raise ValueError("SVG privacy: unexpected content or element count")
    for node, (tag, attributes, value) in zip(root, expected):
        actual = (node.text or "").strip() if tag is ET.Comment else (node.text or "")
        if (node.tag != tag or node.attrib != attributes or actual != value
                or len(node) or (node.tail or "").strip()):
            raise ValueError("SVG privacy: unapproved element, attribute, text, or comment; review the artwork policy")
    # ElementTree omits comments outside the root; explicitly reject those too.
    if text.count("<!--") != 6:
        raise ValueError("SVG privacy: unexpected comments")


def generate(output: Path):
    title = html.escape("Videx")
    tagline = html.escape("Your videos. Your library.")
    description = html.escape("Offline-first video bookmarking")

    svg = f"""\
<svg xmlns="http://www.w3.org/2000/svg"
     width="{WIDTH}" height="{HEIGHT}"
     viewBox="0 0 {WIDTH} {HEIGHT}">

  <rect width="{WIDTH}" height="{HEIGHT}" fill="{BACKGROUND}"/>

  <!-- Videx play icon -->
  <rect x="140" y="165" width="112" height="112"
        rx="24" fill="{ACCENT}"/>

  <!-- Centered play symbol with rounded corners -->
  <path d="M179 197 L179 245 L220 221 Z"
        fill="{PRIMARY}"
        stroke="{PRIMARY}"
        stroke-width="5"
        stroke-linejoin="round"/>

  <!-- Project name -->
  <text x="280" y="251"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        font-size="96" font-weight="700"
        fill="{PRIMARY}">{title}</text>

  <!-- Tagline -->
  <text x="140" y="365"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        font-size="38"
        fill="{SECONDARY}">{tagline}</text>

  <!-- Description -->
  <text x="140" y="430"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        font-size="25"
        fill="{MUTED}">{description}</text>

  <!-- Subtle decorative accent -->
  <rect x="140" y="480" width="100" height="5"
        rx="2.5" fill="{ACCENT}"/>

</svg>
"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")


def png_chunks(data):
    """Check the PNG container and return its chunks (no image re-encoding)."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Not a PNG file")
    chunks = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated PNG chunk")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        if size > 0x7fffffff:
            raise ValueError("Invalid PNG chunk length")
        end = offset + 12 + size
        if end > len(data):
            raise ValueError("Truncated PNG payload")
        kind = data[offset + 4:offset + 8]
        if (not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind)
                or kind[2] & 32):
            raise ValueError("Invalid PNG chunk type")
        payload = data[offset + 8:end - 4]
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError("Invalid PNG checksum")
        if kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND") and not kind[0] & 32:
            raise ValueError("Unsupported critical PNG chunk")
        chunks.append((kind, payload))
        offset = end
        if kind == b"IEND":
            break
    kinds = [kind for kind, _ in chunks]
    if (not kinds or kinds[0] != b"IHDR" or kinds[-1] != b"IEND"
            or kinds.count(b"IHDR") != 1 or kinds.count(b"IEND") != 1
            or b"IDAT" not in kinds or chunks[-1][1] or offset != len(data)):
        raise ValueError("Invalid PNG chunk structure")
    ids = [i for i, kind in enumerate(kinds) if kind == b"IDAT"]
    if ids != list(range(ids[0], ids[-1] + 1)):
        raise ValueError("Nonconsecutive PNG image chunks")
    if len(chunks[0][1]) != 13:
        raise ValueError("Invalid PNG header")
    singletons = {b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"pHYs", b"tIME",
                  b"eXIf", b"sBIT", b"bKGD", b"hIST", b"tRNS"}
    for kind in singletons:
        if kinds.count(kind) > 1:
            raise ValueError("Duplicate PNG singleton chunk")
    before_image = {b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"pHYs", b"sBIT", b"bKGD", b"hIST", b"tRNS"}
    if any(k in before_image for k in kinds[ids[0] + 1:]):
        raise ValueError("Invalid PNG metadata ordering: color/resolution chunks must precede IDAT")
    if any(k in (b"PLTE", b"tRNS", b"acTL", b"fcTL", b"fdAT", b"hIST") for k in kinds):
        raise ValueError("Unsupported PNG palette, transparency, or animation; use static RGB/RGBA")
    if b"iCCP" in kinds:
        raise ValueError("PNG privacy: iCCP profile encountered; no ICC profiles are approved. Review its color effect and privacy before adding explicit support")
    for kind, payload in chunks:
        if kind == b"gAMA" and payload != GAMMA:
            raise ValueError("PNG color policy: expected reviewed gAMA value 45455")
        if kind == b"cHRM" and payload != CHROMATICITIES:
            raise ValueError("PNG color policy: expected reviewed sRGB chromaticities")
        if kind == b"sRGB" and payload not in (b"\0", b"\1", b"\2", b"\3"):
            raise ValueError("PNG color policy: invalid sRGB rendering intent")
    return chunks


def png_pixels(data):
    """Validate/decode our 8-bit RGB(A), noninterlaced PNGs using only stdlib."""
    chunks = png_chunks(data)
    if len(chunks[0][1]) != 13:
        raise ValueError("Invalid PNG header")
    width, height, bits, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1])
    if (width, height) != (WIDTH, HEIGHT):
        raise ValueError(f"Expected {WIDTH} x {HEIGHT} PNG, got {width} x {height}")
    if bits != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
        raise ValueError("Expected 8-bit RGB/RGBA, noninterlaced PNG")
    if any(k == b"tRNS" for k, _ in chunks):
        raise ValueError("Use RGBA for PNG transparency; tRNS is unsupported")
    channels = 4 if color == 6 else 3
    stride = width * channels
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(b"".join(v for k, v in chunks if k == b"IDAT"), expected + 1)
    except zlib.error as error:
        raise ValueError(f"Invalid PNG compressed image data: {error}") from error
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError("Invalid PNG compressed image data")
    pixels = bytearray()
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]
        if method > 4:
            raise ValueError("Invalid PNG row filter")
        row = bytearray(raw[start + 1:start + stride + 1])
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            above = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if method == 1:
                predictor = left
            elif method == 2:
                predictor = above
            elif method == 3:
                predictor = (left + above) // 2
            elif method == 4:
                p = left + above - corner
                distances = (abs(p - left), abs(p - above), abs(p - corner))
                predictor = (left, above, corner)[distances.index(min(distances))]
            else:
                predictor = 0
            row[x] = (row[x] + predictor) & 255
        if channels == 4:
            pixels.extend(row)
        else:
            for x in range(0, stride, 3):
                pixels.extend(row[x:x + 3])
                pixels.append(255)
        previous = row
    return bytes(pixels)


def normalize_metadata(data):
    """Keep only approved image/color chunks, without re-encoding image data."""
    result = bytearray(PNG_SIGNATURE)
    for kind, payload in png_chunks(data):
        if kind not in PNG_ALLOWED:
            continue
        result.extend(struct.pack(">I", len(payload)))
        result.extend(kind + payload)
        result.extend(struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    return bytes(result)


def run_tool(command):
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT)
        except BaseException as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError(f"{Path(command[0]).name} timed out after {TIMEOUT}s") from error
            raise
        if process.returncode:
            detail = (stderr or stdout).strip()
            if len(detail) > 3000:
                detail = detail[:1500] + "\n...\n" + detail[-1500:]
            raise RuntimeError(f"{Path(command[0]).name} failed ({process.returncode}): {detail}")


def publish(outputs):
    """Stage beside destinations, and roll back a partial multi-file publication."""
    originals = {path: path.read_bytes() if path.exists() else None for path in outputs}
    staged = {}
    replaced = []
    try:
        for path, data in outputs.items():
            if not path.parent.is_dir():
                raise ValueError(f"Output directory does not exist: {path.parent}")
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".social-preview-", delete=False) as f:
                staged[path] = Path(f.name)
                f.write(data)
            os.chmod(staged[path], path.stat().st_mode & 0o777 if path.exists() else 0o644)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def build(output, svg_only=False):
    if output.suffix.lower() != ".svg":
        raise ValueError("--output must name an .svg file; PNG uses the same stem")
    if output.is_symlink() or output.with_suffix(".png").is_symlink():
        raise ValueError("Output paths must not be symbolic links")
    if not svg_only:
        if sys.platform != "darwin":
            raise RuntimeError("PNG export requires macOS; use --svg-only on Linux")
        tools = {}
        for name in ("swift", "sips"):
            tools[name] = shutil.which(name)
            if not tools[name]:
                raise RuntimeError(f"Missing {name}: PNG export needs Apple development tools and sips; use --svg-only")
    with tempfile.TemporaryDirectory(prefix="videx-social-preview-") as directory:
        temporary = Path(directory)
        svg = temporary / "artwork.svg"
        generate(svg)
        validate_svg(svg.read_bytes())
        outputs = {output: svg.read_bytes()}
        if not svg_only:
            baseline = ARTWORK_DIR / "videx-social-preview.png"
            approved = normalize_metadata(baseline.read_bytes())
            before = png_pixels(approved)
            png = temporary / "artwork.png"
            run_tool([tools["swift"], str(ARTWORK_DIR / "svg-to-png.swift"), str(svg), str(png)])
            # Inspect native metadata before resizing can transform or discard it.
            png.write_bytes(normalize_metadata(png.read_bytes()))
            run_tool([tools["sips"], "-z", str(HEIGHT), str(WIDTH), str(png)])
            data = normalize_metadata(png.read_bytes())
            if len(data) >= 1_000_000:
                raise ValueError("PNG must be smaller than 1 MB for GitHub")
            pixels = png_pixels(data)
            # Always compare to the approved repository asset, even for custom outputs.
            color_kinds = (b"gAMA", b"cHRM", b"sRGB")
            colors = lambda image: [(k, v) for k, v in png_chunks(image) if k in color_kinds]
            if pixels != before or colors(data) != colors(approved):
                changed = sum(pixels[i:i + 4] != before[i:i + 4] for i in range(0, len(pixels), 4))
                maximum = max(abs(a - b) for a, b in zip(pixels, before))
                review = Path(tempfile.mkdtemp(prefix="videx-social-preview-review-"))
                (review / "candidate.png").write_bytes(data)
                (review / "approved.png").write_bytes(approved)
                (review / "candidate.svg").write_bytes(outputs[output])
                raise RuntimeError(
                    f"Visual review required: {changed}/{WIDTH * HEIGHT} pixels differ; "
                    f"maximum channel difference {maximum}/255; "
                    f"color metadata differs: {colors(data) != colors(approved)}. "
                    f"Approved outputs preserved. Comparison files: {review}")
            # Keep the approved encoding, retaining only reviewed metadata.
            final = normalize_metadata(approved)
            if len(final) >= 1_000_000:
                raise ValueError("Approved PNG must be smaller than 1 MB for GitHub")
            outputs[output.with_suffix(".png")] = final
        publish(outputs)
    print("Generated: " + ", ".join(str(path) for path in outputs))


def main():
    parser = argparse.ArgumentParser(description="Generate Videx social-preview artwork offline.")
    parser.add_argument("--output", type=Path, default=ARTWORK_DIR / "videx-social-preview.svg",
                        help="Destination SVG (PNG uses same stem); default: beside this script")
    parser.add_argument("--svg-only", action="store_true", help="Generate SVG without macOS rendering tools")
    args = parser.parse_args()
    try:
        build(args.output.resolve() if not args.output.is_symlink() else args.output, args.svg_only)
    except (OSError, ValueError, RuntimeError, ET.ParseError) as error:
        print(f"Social preview: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
