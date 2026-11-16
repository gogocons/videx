"""Offline artwork regressions: synthetic images and tools, no WebKit required."""
import importlib.util
from pathlib import Path
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'docs/social-preview/generate-social-preview.py'
spec = importlib.util.spec_from_file_location('social_preview', SCRIPT)
art = importlib.util.module_from_spec(spec)
spec.loader.exec_module(art)


def chunk(kind, value):
    return (struct.pack('>I', len(value)) + kind + value
            + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff))


def image(shade=16, metadata=False, filtering=0):
    row = bytes([shade, 18, 23, 255]) * art.WIDTH
    if filtering == 1:
        row = row[:4] + bytes((row[i] - row[i - 4]) & 255 for i in range(4, len(row)))
    data = art.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', art.WIDTH, art.HEIGHT, 8, 6, 0, 0, 0))
    if metadata:
        data += chunk(b'iTXt', b'XML:com.adobe.xmp\0\0\0\0\0stale dimensions')
        data += chunk(b'eXIf', b'stale EXIF')
    return data + chunk(b'IDAT', zlib.compress((bytes([filtering]) + row) * art.HEIGHT)) + chunk(b'IEND', b'')


def inject(data, kind, payload):
    offset = 8
    while data[offset + 4:offset + 8] != b'IDAT':
        offset += struct.unpack('>I', data[offset:offset + 4])[0] + 12
    return data[:offset] + chunk(kind, payload) + data[offset:]


def assemble(chunks):
    return art.PNG_SIGNATURE + b''.join(chunk(kind, payload) for kind, payload in chunks)


class ArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.png = image(metadata=True)
        cls.approved_svg = (SCRIPT.parent / 'videx-social-preview.svg').read_bytes()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='videx-artwork-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / 'result.svg'
        self.png_output = self.output.with_suffix('.png')
        self.output.write_bytes(b'existing svg')
        self.png_output.write_bytes(self.png)
        self.baseline = self.root / 'videx-social-preview.png'
        self.baseline.write_bytes(self.png)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(art, 'ARTWORK_DIR', self.root).start()

    def unchanged(self):
        self.assertEqual(self.output.read_bytes(), b'existing svg')
        self.assertEqual(self.png_output.read_bytes(), self.png)
        self.assertEqual(self.baseline.read_bytes(), self.png)
        self.assertFalse(list(self.root.glob('.social-preview-*')))

    def native(self, candidate=None):
        mock.patch.object(art.sys, 'platform', 'darwin').start()
        mock.patch.object(art.shutil, 'which', side_effect=lambda name: '/synthetic/' + name).start()

        def run(command):
            if command[0].endswith('/swift'):
                Path(command[-1]).write_bytes(candidate if candidate is not None else self.png)
        return mock.patch.object(art, 'run_tool', side_effect=run)

    def test_svg_from_root_and_unrelated_directory_repeats_exactly(self):
        values = []
        for cwd in (ROOT, self.root, self.root):
            result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--svg-only', '--output', str(self.output)],
                                    cwd=cwd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values.append(self.output.read_bytes())
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])
        self.assertEqual(values[0], (SCRIPT.parent / 'videx-social-preview.svg').read_bytes())
        self.assertEqual(self.png_output.read_bytes(), self.png)

    def test_default_output_is_beside_script_not_working_directory(self):
        copied = self.root / SCRIPT.name
        copied.write_bytes(SCRIPT.read_bytes())
        for cwd in (ROOT, self.root):
            result = subprocess.run([sys.executable, '-B', str(copied), '--svg-only'], cwd=cwd,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.root / 'videx-social-preview.svg').read_bytes(),
                             (SCRIPT.parent / 'videx-social-preview.svg').read_bytes())

    def test_wrong_dimensions_and_bad_stream_rejected(self):
        chunks = art.png_chunks(self.png)
        wrong = art.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', 640, 640, 8, 6, 0, 0, 0))
        wrong += b''.join(chunk(k, v) for k, v in chunks[1:])
        with self.assertRaisesRegex(ValueError, 'Expected 1280 x 640'):
            art.png_pixels(wrong)
        invalid = art.PNG_SIGNATURE + chunk(*chunks[0]) + chunk(b'IDAT', zlib.compress(b'too short')) + chunk(b'IEND', b'')
        with self.assertRaisesRegex(ValueError, 'compressed image'):
            art.png_pixels(invalid)

    def test_svg_only_needs_no_native_tools_on_linux(self):
        with mock.patch.object(art.sys, 'platform', 'linux'), mock.patch.object(art.shutil, 'which', side_effect=AssertionError):
            art.build(self.output, svg_only=True)
        self.assertTrue(self.output.read_bytes().startswith(b'<svg'))

    def test_linux_png_failure_preserves_outputs(self):
        with mock.patch.object(art.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, '--svg-only'):
                art.build(self.output)
        self.unchanged()

    def test_cli_failure_returns_nonzero_and_preserves_outputs(self):
        environment = dict(os.environ, PATH=str(self.root / 'empty-path'))
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--output', str(self.output)],
                                env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--svg-only', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.unchanged()

    def test_missing_output_directory_preserves_outputs(self):
        with self.assertRaisesRegex(ValueError, 'directory does not exist'):
            art.build(self.root / 'missing/result.svg', svg_only=True)
        self.unchanged()

    def test_each_missing_tool_preserves_outputs(self):
        for missing in ('swift', 'sips'):
            with mock.patch.object(art.sys, 'platform', 'darwin'), mock.patch.object(
                    art.shutil, 'which', side_effect=lambda name: None if name == missing else '/tool/' + name):
                with self.assertRaisesRegex(RuntimeError, 'Missing ' + missing):
                    art.build(self.output)
            self.unchanged()

    def test_renderer_failure_preserves_outputs(self):
        with self.native(), mock.patch.object(art, 'run_tool', side_effect=RuntimeError('renderer failed')):
            with self.assertRaisesRegex(RuntimeError, 'renderer failed'):
                art.build(self.output)
        self.unchanged()

    def test_normalizer_failure_preserves_outputs(self):
        with self.native() as run:
            original = run.side_effect
            def failing(command):
                if command[0].endswith('/sips'):
                    raise RuntimeError('normalization failed')
                original(command)
            run.side_effect = failing
            with self.assertRaisesRegex(RuntimeError, 'normalization failed'):
                art.build(self.output)
        self.unchanged()

    def test_real_tool_failure_diagnostic(self):
        with self.assertRaisesRegex(RuntimeError, 'failed \\(7\\): synthetic renderer error'):
            art.run_tool([sys.executable, '-c', 'import sys; sys.stderr.write("synthetic renderer error"); sys.exit(7)'])

    def test_real_tool_timeout_preserves_outputs(self):
        with self.native(), mock.patch.object(art, 'TIMEOUT', 0.1):
            # Exercise the real bounded subprocess runner during build.
            runner = self._real_runner
            with mock.patch.object(art, 'run_tool', side_effect=lambda command: runner([sys.executable, '-c', 'import time; time.sleep(10)'])):
                with self.assertRaisesRegex(RuntimeError, 'timed out'):
                    art.build(self.output)
        self.unchanged()

    _real_runner = staticmethod(art.run_tool)

    def test_success_and_repeatability_preserve_pixels_and_strip_stale_metadata(self):
        with self.native():
            art.build(self.output)
            first = self.png_output.read_bytes()
            art.build(self.output)
        self.assertEqual(first, self.png_output.read_bytes())
        self.assertEqual(art.png_pixels(first), art.png_pixels(self.png))
        self.assertEqual([k for k, v in art.png_chunks(first)], [b'IHDR', b'IDAT', b'IEND'])
        self.assertEqual([v for k, v in art.png_chunks(first) if k == b'IDAT'],
                         [v for k, v in art.png_chunks(self.png) if k == b'IDAT'])

    def test_visual_difference_preserves_outputs_and_saves_review_outside_artwork(self):
        review = self.root / 'review'
        review.mkdir()
        original = art.tempfile.mkdtemp
        def temporary(*args, **kwargs):
            return str(review) if kwargs.get('prefix') == 'videx-social-preview-review-' else original(*args, **kwargs)
        with self.native(image(shade=17)), mock.patch.object(art.tempfile, 'mkdtemp', side_effect=temporary):
            with self.assertRaisesRegex(RuntimeError, '819200/819200 pixels differ'):
                art.build(self.output)
        self.unchanged()
        self.assertEqual((review / 'candidate.png').read_bytes(), image(shade=17))
        self.assertEqual((review / 'approved.png').read_bytes(), art.normalize_metadata(self.png))

    def test_color_profile_difference_requires_review(self):
        candidate = inject(self.png, b'sRGB', b'\0')
        review = self.root / 'review'
        review.mkdir()
        original = art.tempfile.mkdtemp
        def temporary(*args, **kwargs):
            return str(review) if kwargs.get('prefix') == 'videx-social-preview-review-' else original(*args, **kwargs)
        with self.native(candidate), mock.patch.object(art.tempfile, 'mkdtemp', side_effect=temporary):
            with self.assertRaisesRegex(RuntimeError, 'color metadata differs: True'):
                art.build(self.output)
        self.unchanged()

    def test_invalid_png_and_dimensions_fail_without_publication(self):
        wrong_size = self.png.replace(struct.pack('>I', art.WIDTH), struct.pack('>I', 640), 1)
        for candidate in (b'not a PNG', self.png[:-1], wrong_size):
            with self.native(candidate):
                with self.assertRaises(ValueError):
                    art.build(self.output)
            self.unchanged()

    def test_pixel_decoder_sub_filter(self):
        self.assertEqual(art.png_pixels(image(filtering=1)), art.png_pixels(image()))

    def test_publication_failure_rolls_back_svg_and_png(self):
        replace = os.replace
        calls = 0
        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic publication failure')
            replace(source, target)
        with self.native(), mock.patch.object(art.os, 'replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                art.build(self.output)
        self.unchanged()

    def test_output_suffix_and_symlink_rejected(self):
        with self.assertRaisesRegex(ValueError, '.svg'):
            art.build(self.root / 'image.png', svg_only=True)
        link = self.root / 'link.svg'
        link.symlink_to(self.output)
        with self.assertRaisesRegex(ValueError, 'symbolic links'):
            art.build(link, svg_only=True)
        self.unchanged()

    def test_approved_asset_exact_inventory_and_color_values(self):
        data = (SCRIPT.parent / 'videx-social-preview.png').read_bytes()
        chunks = art.png_chunks(data)
        self.assertEqual([k for k, v in chunks],
                         [b'IHDR', b'gAMA', b'cHRM', b'IDAT', b'IDAT', b'IDAT', b'IDAT', b'IEND'])
        self.assertEqual(dict(chunks)[b'gAMA'], struct.pack('>I', 45455))
        self.assertEqual(dict(chunks)[b'cHRM'], struct.pack('>8I', 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000))
        self.assertEqual(art.normalize_metadata(data), data)
        art.validate_svg(self.approved_svg)

    def test_all_unapproved_ancillary_metadata_removed_losslessly(self):
        additions = [
            (b'tEXt', b'Author\0Synthetic Person'),
            (b'zTXt', b'Comment\0\0' + zlib.compress(b'/home/synthetic')),
            (b'iTXt', b'Creator\0\0\0\0\0Synthetic Creator'),
            (b'iTXt', b'Alternate XMP keyword\0\0\0\0\0<xmp>synthetic</xmp>'),
            (b'eXIf', b'synthetic EXIF'),
            (b'tIME', struct.pack('>H5B', 2020, 1, 2, 3, 4, 5)),
            (b'pHYs', struct.pack('>IIB', 5669, 5669, 1)),
            (b'vpAg', b'private payload'),
            (b'aaAa', b'unknown ancillary'),
        ]
        base = image()
        pixels = art.png_pixels(base)
        ids = [v for k, v in art.png_chunks(base) if k == b'IDAT']
        for kind, payload in additions:
            with self.subTest(kind=kind, keyword=payload.split(b'\0')[0]):
                contaminated = inject(base, kind, payload)
                cleaned = art.normalize_metadata(contaminated)
                self.assertEqual(cleaned, base)
                self.assertEqual(art.png_pixels(cleaned), pixels)
                self.assertEqual([v for k, v in art.png_chunks(cleaned) if k == b'IDAT'], ids)

    def test_invalid_gamma_chromaticities_and_srgb_rejected(self):
        for kind, payload in [(b'gAMA', b''), (b'gAMA', struct.pack('>I', 100000)),
                              (b'cHRM', b'bad'), (b'cHRM', bytes(32)),
                              (b'sRGB', b''), (b'sRGB', b'\4'), (b'sRGB', b'\0\0')]:
            with self.subTest(kind=kind, payload=payload):
                with self.assertRaisesRegex(ValueError, 'color policy'):
                    art.normalize_metadata(inject(image(), kind, payload))

    def test_standard_srgb_with_reviewed_colors_supported(self):
        base = inject(inject(image(), b'gAMA', art.GAMMA), b'cHRM', art.CHROMATICITIES)
        for intent in range(4):
            data = inject(base, b'sRGB', bytes([intent]))
            self.assertEqual(art.normalize_metadata(data), data)

    def test_icc_malformed_and_unapproved_always_fail_closed(self):
        for payload in (b'', b'bad profile', b'Synthetic ICC\0\0' + zlib.compress(bytes(132))):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, 'no ICC profiles are approved'):
                    art.normalize_metadata(inject(image(), b'iCCP', payload))

    def test_chunk_order_duplicates_crc_types_and_trailing_data(self):
        base = art.png_chunks(image())
        ihdr, idat, iend = base
        malformed = [
            assemble([idat, ihdr, iend]), assemble([ihdr, ihdr, idat, iend]),
            assemble([ihdr, idat, (b'gAMA', art.GAMMA), iend]),
            assemble([ihdr, (b'gAMA', art.GAMMA), (b'gAMA', art.GAMMA), idat, iend]),
            assemble([ihdr, (b'cHRM', art.CHROMATICITIES), (b'cHRM', art.CHROMATICITIES), idat, iend]),
            assemble([ihdr, (b'sRGB', b'\0'), (b'sRGB', b'\0'), idat, iend]),
            assemble([ihdr, (b'pHYs', bytes(9)), (b'pHYs', bytes(9)), idat, iend]),
            assemble([ihdr, idat, (b'tEXt', b'note\0text'), idat, iend]),
            assemble([ihdr, idat, iend, iend]), image() + b'secret trailer',
            image()[:-1], image()[:-1] + bytes([image()[-1] ^ 1]),
            assemble([ihdr, (b'aaag', b'invalid reserved bit'), idat, iend]),
            assemble([ihdr, (b'1aAg', b'invalid type'), idat, iend]),
            assemble([ihdr, (b'ABCD', b'unknown critical'), idat, iend]),
        ]
        for number, data in enumerate(malformed):
            with self.subTest(case=number):
                with self.assertRaises(ValueError):
                    art.normalize_metadata(data)

    def test_contaminated_baseline_and_renderer_metadata_are_cleaned(self):
        dirty = inject(inject(self.png, b'tEXt', b'Author\0Synthetic'), b'vpAg', b'private')
        self.baseline.write_bytes(dirty)
        with self.native(dirty):
            art.build(self.output)
        self.assertEqual(self.png_output.read_bytes(), image())
        self.assertEqual(self.baseline.read_bytes(), dirty)  # custom output leaves original alone

    def test_invalid_baseline_fails_before_rendering_and_preserves_outputs(self):
        for dirty in (inject(self.png, b'iCCP', b'bad'), self.png + b'trailer'):
            self.baseline.write_bytes(dirty)
            with self.native() as renderer:
                with self.assertRaises(ValueError):
                    art.build(self.output)
                renderer.assert_not_called()
            self.assertEqual(self.output.read_bytes(), b'existing svg')
            self.assertEqual(self.png_output.read_bytes(), self.png)
            self.assertEqual(self.baseline.read_bytes(), dirty)

    def test_renderer_icc_rejected_before_sips_without_review_artifacts(self):
        with self.native(inject(self.png, b'iCCP', b'bad')) as renderer, mock.patch.object(
                art.tempfile, 'mkdtemp', wraps=art.tempfile.mkdtemp) as temporary:
            with self.assertRaisesRegex(ValueError, 'ICC'):
                art.build(self.output)
            self.assertEqual(renderer.call_count, 1)  # no resize after ICC failure
            self.assertFalse(any(c.kwargs.get('prefix') == 'videx-social-preview-review-' for c in temporary.call_args_list))
        self.unchanged()

    def test_review_artifacts_are_sanitized_on_both_sides(self):
        review = self.root / 'review'
        review.mkdir()
        original = art.tempfile.mkdtemp
        def temporary(*args, **kwargs):
            return str(review) if kwargs.get('prefix') == 'videx-social-preview-review-' else original(*args, **kwargs)
        dirty = inject(image(shade=17), b'tEXt', b'Author\0Synthetic')
        self.baseline.write_bytes(inject(self.png, b'vpAg', b'synthetic private'))
        with self.native(dirty), mock.patch.object(art.tempfile, 'mkdtemp', side_effect=temporary):
            with self.assertRaisesRegex(RuntimeError, 'Visual review required'):
                art.build(self.output)
        for name in ('candidate.png', 'approved.png'):
            data = (review / name).read_bytes()
            self.assertTrue(all(k in art.PNG_ALLOWED for k, v in art.png_chunks(data)))
            self.assertEqual(art.normalize_metadata(data), data)
        art.validate_svg((review / 'candidate.svg').read_bytes())
        self.assertEqual(self.output.read_bytes(), b'existing svg')
        self.assertEqual(self.png_output.read_bytes(), self.png)

    def test_svg_privacy_rejects_unapproved_structure_and_content(self):
        original = self.approved_svg.decode()
        forbidden = [
            '<metadata>Creator: Synthetic</metadata>', '<script>alert(1)</script>',
            '<image href="data:image/png;base64,AAAA"/>',
            '<image href="https://example.invalid/image.png"/>',
            '<style>rect { fill: url(https://example.invalid/a); }</style>',
            '<g xmlns="urn:unexpected"/>', '<!-- Creator: Synthetic -->',
            '<text>/home/synthetic/artwork</text>',
        ]
        candidates = [original.replace('</svg>', value + '</svg>') for value in forbidden]
        candidates += [
            '<?instruction synthetic?>' + original,
            '<!DOCTYPE svg>' + original,
            '<!DOCTYPE svg [<!ENTITY name "Synthetic">]>' + original,
            original.replace('width="1280"', 'width="1280" data-author="Synthetic"', 1),
            original.replace('fill="#101217"', 'fill="url(https://example.invalid/a)"', 1),
            original.replace('fill="#101217"', 'fill="/home/synthetic/image"', 1),
            original.replace('<svg ', '<svg xml:base="/home/synthetic/" ', 1),
            original.replace('<svg ', '<svg style="background:url(https://example.invalid/a)" ', 1),
            original.replace('<svg ', '<svg xmlns:xlink="http://www.w3.org/1999/xlink" ', 1),
            original.replace('Your videos. Your library.', 'Synthetic Person'),
            original.replace('Segoe UI', 'Synthetic Custom Font'),
            original.replace('Videx play icon', 'Creator: Synthetic'),
            original.replace('<svg ', '<svg xmlns:private="urn:private" ', 1),
            '<!-- private comment -->' + original,
            original + '<!-- private comment -->',
        ]
        for number, candidate in enumerate(candidates):
            with self.subTest(case=number):
                with self.assertRaises(ValueError):
                    art.validate_svg(candidate.encode())

    def test_invalid_generated_svg_preserves_outputs_before_rendering(self):
        dirty = self.approved_svg.replace(b'</svg>', b'<metadata>Synthetic</metadata></svg>')
        with self.native() as renderer, mock.patch.object(art, 'generate', side_effect=lambda path: path.write_bytes(dirty)):
            with self.assertRaisesRegex(ValueError, 'SVG privacy'):
                art.build(self.output)
            renderer.assert_not_called()
        self.unchanged()


if __name__ == '__main__':
    unittest.main()
