"""Offline screenshot preparation tests using synthetic PNGs only."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / 'docs/screenshots/prepare-screenshots.py'
spec = importlib.util.spec_from_file_location('screenshot_prepare', SCRIPT)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
try:
    from PIL import Image
except ImportError:
    Image = None


def assemble(chunks):
    return prep.SIGNATURE + b''.join(prep.chunk(k, v) for k, v in chunks)


def fixture(size=(1440, 900), extra=(), alpha=False):
    width, height = size
    channels = 4 if alpha else 3
    row = bytes((30, 40, 50, 255) if alpha else (30, 40, 50)) * width
    raw = bytearray((b'\0' + row) * height)
    if alpha:
        # A synthetic rounded transparent corner, with opaque interior.
        for y in range(24):
            for x in range(24):
                if (x - 24) ** 2 + (y - 24) ** 2 > 24 ** 2:
                    raw[y * (width * channels + 1) + 1 + x * channels + 3] = 0
    return assemble([(b'IHDR', struct.pack('>IIBBBBB', width, height, 8,
                                         6 if alpha else 2, 0, 0, 0)),
                     *extra, (b'IDAT', zlib.compress(raw)), (b'IEND', b'')])


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = fixture()

    def rejects(self, data):
        with self.assertRaises(ValueError):
            prep.inspect_png(data)

    def test_bad_dimensions(self):
        for size in [(1440, 901), (1600, 900), (720, 450), (1432, 895)]:
            with self.subTest(size=size):
                self.rejects(fixture(size))

    def test_crc_truncation_trailing_and_invalid_signature(self):
        bad = bytearray(self.native)
        bad[29] ^= 1
        for data in [bytes(bad), self.native[:-1], self.native + b'private', b'no PNG']:
            self.rejects(data)

    def test_ordering_duplicates_and_nonconsecutive_idat(self):
        chunks, _ = prep.inspect_png(self.native)
        h, image, end = chunks
        for items in [[h, image, (b'sRGB', b'\0'), end],
                      [h, h, image, end],
                      [h, (b'sRGB', b'\0'), (b'sRGB', b'\0'), image, end],
                      [h, image, (b'tEXt', b'key\0text'), image, end],
                      [image, h, end], [h, image, (b'IEND', b'bad')]]:
            self.rejects(assemble(items))

    def test_unsupported_color_and_image_features(self):
        for k, v in [(b'iCCP', b'profile\0\0' + zlib.compress(b'ICC')),
                     (b'gAMA', struct.pack('>I', 100000)),
                     (b'cHRM', bytes(32)), (b'sRGB', b'\4'),
                     (b'cICP', bytes(4)), (b'mDCV', bytes(24)),
                     (b'cLLI', bytes(8)), (b'sBIT', bytes(3)),
                     (b'acTL', bytes(8)), (b'tRNS', bytes(6)),
                     (b'PLTE', bytes(3)), (b'ABCD', b'')]:
            with self.subTest(kind=k):
                self.rejects(fixture(extra=[(k, v)]))

    def test_complete_pixel_stream_and_filters(self):
        chunks, _ = prep.inspect_png(self.native)
        raw = zlib.decompress(chunks[1][1])
        for compressed in [b'broken', zlib.compress(raw[:-1]),
                           zlib.compress(raw + b'\0'),
                           zlib.compress(raw) + b'extra',
                           zlib.compress(b'\5' + raw[1:])]:
            self.rejects(assemble([chunks[0], (b'IDAT', compressed), chunks[-1]]))

    def test_resource_limits(self):
        with mock.patch.object(prep, 'MAX_INPUT', 8):
            self.rejects(self.native)
        chunks, _ = prep.inspect_png(self.native)
        h = (b'IHDR', struct.pack('>IIBBBBB', 14400, 9000, 8, 2, 0, 0, 0))
        self.rejects(assemble([h, *chunks[1:]]))


@unittest.skipIf(Image is None, 'Pillow is optional; install separately to test image processing')
class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = fixture(alpha=True)
        cls.hidpi = fixture((2880, 1800), alpha=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='videx-screenshot-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.review = self.root / 'review'
        self.review.mkdir()
        self.source = self.root / 'capture.png'
        self.source.write_bytes(self.native)

    def run_main(self, *inputs, apply=False, output=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        args = [str(p) for p in (inputs or (self.source,))]
        args += ['--output-dir', str(output or self.review)]
        if apply:
            args.append('--apply')
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = prep.main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_review_empty(self):
        self.assertEqual(list(self.review.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.native)

    def test_native_output_and_rounded_corner_pixels(self):
        result, size, _ = prep.prepare(self.native)
        self.assertEqual(size, prep.SIZE)
        prep.validate_output(result)
        with Image.open(io.BytesIO(result)) as image, Image.open(io.BytesIO(self.native)) as original:
            self.assertEqual(image.size, (1440, 900))
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.tobytes(), original.tobytes())
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((100, 100))[3], 255)

    def test_hidpi_lanczos_and_alpha(self):
        result, size, _ = prep.prepare(self.hidpi)
        self.assertEqual(size, (2880, 1800))
        with Image.open(io.BytesIO(result)) as image, Image.open(io.BytesIO(self.hidpi)) as source:
            expected = source.resize(prep.SIZE, Image.Resampling.LANCZOS)
            self.assertEqual(image.tobytes(), expected.tobytes())
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((100, 100))[3], 255)

    def test_metadata_removed_and_color_preserved(self):
        colors = [(b'sRGB', b'\1'), (b'gAMA', prep.GAMMA), (b'cHRM', prep.CHROMATICITIES)]
        private = [(b'eXIf', b'private'), (b'tEXt', b'Author\0someone'),
                   (b'zTXt', b'Author\0\0' + zlib.compress(b'someone')),
                   (b'iTXt', b'XML:com.adobe.xmp\0\0\0\0\0private'),
                   (b'tIME', bytes(7)), (b'pHYs', bytes(9)), (b'vpAg', b'private')]
        result, _, removed = prep.prepare(fixture(extra=colors + private))
        chunks, _ = prep.inspect_png(result)
        self.assertEqual([(k, v) for k, v in chunks if k in prep.COLORS], colors)
        self.assertTrue(all(k in prep.ALLOWED for k, _ in chunks))
        self.assertEqual(set(removed), {k.decode() for k, _ in private})

    def test_untagged_input_gets_srgb(self):
        result, _, _ = prep.prepare(self.native)
        self.assertIn((b'sRGB', b'\0'), prep.inspect_png(result)[0])

    def test_output_size_and_metadata_validation(self):
        result, _, _ = prep.prepare(self.native)
        with mock.patch.object(prep, 'MAX_OUTPUT', 1):
            with self.assertRaisesRegex(ValueError, '2 MiB'):
                prep.validate_output(result)
        for data in [self.native, fixture(extra=[(b'sRGB', b'\0'), (b'tEXt', b'k\0v')])]:
            with self.assertRaisesRegex(ValueError, 'privacy'):
                prep.validate_output(data)

    def test_dry_run_writes_nothing(self):
        status, output, _ = self.run_main()
        self.assertEqual(status, 0)
        self.assertIn('Dry-run: 1/1 validated; 0 published', output)
        self.assert_review_empty()

    def test_apply_and_refuse_overwrite(self):
        self.assertEqual(self.run_main(apply=True)[0], 0)
        dest = self.review / self.source.name
        before = dest.read_bytes()
        prep.validate_output(before)
        self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assertEqual(dest.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), self.native)
        self.assertFalse(list(self.review.glob('*.tmp')))

    def test_duplicate_destination_batch_preflight(self):
        other = self.root / 'other'
        other.mkdir()
        source = other / self.source.name
        source.write_bytes(self.native)
        self.assertEqual(self.run_main(self.source, source, apply=True)[0], 1)
        self.assert_review_empty()

    def test_symlink_output_file_and_directory_refused(self):
        dest = self.review / self.source.name
        dest.symlink_to(self.root / 'absent.png')
        self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assertTrue(dest.is_symlink())
        dest.unlink()
        alias = self.root / 'review-link'
        alias.symlink_to(self.review, target_is_directory=True)
        self.assertEqual(self.run_main(apply=True, output=alias)[0], 1)
        self.assert_review_empty()

    def test_symlink_input_and_hardlink_alias_refused(self):
        alias = self.root / 'alias.png'
        alias.symlink_to(self.source)
        self.assertEqual(self.run_main(alias, apply=True)[0], 1)
        alias.unlink()
        os.link(self.source, alias)
        self.assertEqual(self.run_main(self.source, alias, apply=True)[0], 1)
        os.link(self.source, self.review / self.source.name)
        self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assertEqual(self.source.read_bytes(), self.native)

    def test_same_directory_alias_and_baselines_refused(self):
        self.assertEqual(self.run_main(apply=True, output=self.root)[0], 1)
        self.assertEqual(self.run_main(apply=True, output=prep.BASELINES)[0], 1)
        self.assertEqual(self.source.read_bytes(), self.native)

    def test_missing_output_directory_not_created(self):
        missing = self.root / 'missing'
        self.assertEqual(self.run_main(apply=True, output=missing)[0], 1)
        self.assertFalse(missing.exists())

    def test_validation_failure_cleans_temporary_candidate(self):
        real = prep.validate_output
        calls = 0

        def fail_staged(data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError('staged validation failure')
            return real(data)

        with mock.patch.object(prep, 'validate_output', side_effect=fail_staged):
            self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assert_review_empty()

    def test_link_failure_and_atomic_collision_cleanup(self):
        with mock.patch.object(prep.os, 'link', side_effect=OSError('link failure')):
            self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assert_review_empty()
        real = os.link

        def competing_output(src, dst, **kwargs):
            (self.review / dst).write_bytes(b'competing approved output')
            return real(src, dst, **kwargs)

        with mock.patch.object(prep.os, 'link', side_effect=competing_output):
            self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assertEqual((self.review / self.source.name).read_bytes(), b'competing approved output')
        self.assertFalse(list(self.review.glob('*.tmp')))

    def test_rollback_current_output_if_temporary_unlink_fails(self):
        real = os.unlink
        failed = False

        def fail_once(name, **kwargs):
            nonlocal failed
            if str(name).startswith('.screenshot-') and not failed:
                failed = True
                raise OSError('temporary unlink failure')
            return real(name, **kwargs)

        with mock.patch.object(prep.os, 'unlink', side_effect=fail_once):
            self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assert_review_empty()

    def test_batch_partial_failure_reports_retained_success(self):
        bad = self.root / 'bad.png'
        bad.write_bytes(b'not PNG')
        status, output, error = self.run_main(self.source, bad, apply=True)
        self.assertEqual(status, 1)
        self.assertIn('Published:', output)
        self.assertIn('1/2 validated; 1 published', error)
        self.assertIn('retained', error)
        prep.validate_output((self.review / self.source.name).read_bytes())
        self.assertFalse((self.review / bad.name).exists())
        self.assertFalse(list(self.review.glob('*.tmp')))

    def test_failed_rollback_reports_retained_current_output(self):
        real = os.unlink
        first = True

        def fail_publication_cleanup(name, **kwargs):
            nonlocal first
            if str(name).startswith('.screenshot-') and first:
                first = False
                raise OSError('temporary unlink failure')
            if str(name) == self.source.name:
                raise OSError('rollback unlink failure')
            return real(name, **kwargs)

        with mock.patch.object(prep.os, 'unlink', side_effect=fail_publication_cleanup):
            status, _, error = self.run_main(apply=True)
        self.assertEqual(status, 1)
        self.assertIn('rollback failed', error)
        self.assertIn('1 published', error)
        prep.validate_output((self.review / self.source.name).read_bytes())
        self.assertFalse(list(self.review.glob('*.tmp')))

    def test_flush_failure_cleans_candidate(self):
        with mock.patch.object(prep.os, 'fsync', side_effect=OSError('flush failure')):
            status, _, error = self.run_main(apply=True)
        self.assertEqual(status, 1)
        self.assertIn('0 published', error)
        self.assert_review_empty()

    def test_staging_name_collision_preserves_existing_file(self):
        existing = self.review / ('.screenshot-' + '00' * 16 + '.tmp')
        existing.write_bytes(b'existing temporary artifact')
        with mock.patch.object(prep.os, 'urandom', return_value=bytes(16)):
            self.assertEqual(self.run_main(apply=True)[0], 1)
        self.assertEqual(existing.read_bytes(), b'existing temporary artifact')
        self.assertEqual(list(self.review.iterdir()), [existing])

    def test_missing_pillow_diagnostic(self):
        with mock.patch.dict(sys.modules, {'PIL': None}):
            status, _, error = self.run_main()
        self.assertEqual(status, 1)
        self.assertIn('Pillow is required', error)
        self.assert_review_empty()

    def test_cli_offline(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), str(self.source),
                                 '--output-dir', str(self.review)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0 published', result.stdout)
        self.assert_review_empty()


if __name__ == '__main__':
    unittest.main()
