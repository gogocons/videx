"""Stage B identity regressions using only disposable synthetic collections."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / 'scripts'
A, B, C = 'AAAAAAAAAAA', 'BBBBBBBBBBB', 'CCCCCCCCCCC'
IMAGE = 'data:image/png;base64,aW1hZ2U='


def load(name):
    spec = spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = module_from_spec(spec)
    with patch.object(sys, 'path', [str(SCRIPTS)] + sys.path):
        spec.loader.exec_module(module)
    return module


tagger = load('yt-bookmark-tag')
generator = load('yt-bookmark-generate')
metadata_tool = load('yt-bookmark-metadata')
indexer = load('yt-bookmark-index')
upgrader = load('yt-bookmark-upgrade')
finder = load('yt-bookmark-find')


def document(identity=A):
    return '<img src="' + IMAGE + '"><script id="youtube-metadata">' + json.dumps({
        'id': identity, 'title': 'Synthetic title', 'uploader': 'Synthetic channel',
    }) + '</script>'


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='.consumer-test-', dir=PROJECT)
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.collection = self.root / 'bookmarks'
        self.collection.mkdir()

    def invoke(self, module, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['tool', *map(str, arguments)]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = module.main()
            except (ValueError, OSError) as exc:
                return exc, stdout.getvalue(), stderr.getvalue()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_metadata_curation_rejects_invalid_raw_ids(self):
        for identity in (None, 123, True, [], '', 'short', A + '.', A + '\n'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                metadata_tool.curate({'id': identity, 'title': 'Title'})
        self.assertEqual(metadata_tool.curate({'id': A.lower(), 'title': 'Title'})['id'], A.lower())

    def test_catalog_mutations_refuse_conflicts_malformed_unknown_and_duplicates(self):
        bad = self.collection / f'Bad [{A}].html'
        catalog = self.root / 'catalog.json'
        original = json.dumps({'version': 1, 'bookmarks': {A: {'tags': ['keep']}}, 'collections': {}})
        catalog.write_text(original, encoding='utf-8')
        operations = [('add', A, 'new'), ('remove', A, 'keep'), ('set', A, 'new'),
                      ('rename', 'keep', 'new'), ('prune',)]
        for content in (document(B), '<script id="youtube-metadata">{broken</script>',
                        '<script id="youtube-metadata"></script>',
                        '<script id="youtube-metadata">{}</script>'):
            bad.write_text(content, encoding='utf-8')
            for operation in operations:
                for apply in ([], ['--apply']):
                    with self.subTest(content=content, operation=operation, apply=apply):
                        status, _, stderr = self.invoke(tagger, self.root, *operation, *apply)
                        self.assertEqual(status, 2)
                        self.assertIn('Incomplete bookmark inventory', stderr)
                        self.assertEqual(catalog.read_text(), original)
                        self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])
        bad.write_text(document(A), encoding='utf-8')
        (self.collection / 'duplicate.html').write_text(document(A), encoding='utf-8')
        self.assertEqual(self.invoke(tagger, self.root, 'add', A, 'new', '--apply')[0], 2)
        self.assertEqual(catalog.read_text(), original)

    def test_catalog_mutations_refuse_unidentified_files_and_traversal_failures(self):
        unknown = self.collection / 'unidentified.html'
        unknown.write_text('<html>no identity</html>', encoding='utf-8')
        for operation in (('add', A, 'tag'), ('remove', A, 'tag'), ('set', A, 'tag'),
                          ('rename', 'old', 'new'), ('prune',)):
            with self.subTest(operation=operation):
                self.assertEqual(self.invoke(tagger, self.root, *operation, '--apply')[0], 2)
                with patch.object(tagger.os, 'scandir', side_effect=PermissionError('synthetic traversal denied')):
                    status, _, stderr = self.invoke(tagger, self.root, *operation, '--apply')
                    self.assertEqual(status, 2)
                    self.assertIn('synthetic traversal denied', stderr)
        self.assertFalse((self.root / 'catalog.json').exists())
        self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])

    def test_tagging_legacy_and_renamed_bookmarks_preserves_preview_and_backups(self):
        (self.collection / f'Legacy [{A}].html').write_text('legacy', encoding='utf-8')
        (self.collection / 'renamed.html').write_text(document(B), encoding='utf-8')
        catalog = self.root / 'catalog.json'
        for identity in (A, B):
            self.assertEqual(self.invoke(tagger, self.root, 'add', identity, 'tag')[0], 0)
            self.assertEqual(self.invoke(tagger, self.root, 'add', identity, 'tag', '--apply')[0], 0)
        before = catalog.read_bytes()
        self.assertEqual(self.invoke(tagger, self.root, 'add', B, 'another', '--apply')[0], 0)
        backups = list(self.root.glob('catalog.json.bak-*'))
        self.assertTrue(any(path.read_bytes() == before for path in backups))
        self.assertEqual(json.loads(catalog.read_text())['bookmarks'][A]['tags'], ['tag'])

    def test_generator_refuses_filename_conflict_and_unsafe_existing_identity(self):
        info, thumbnail = self.root / 'info.json', self.root / 'thumb.png'
        info.write_text(json.dumps({'id': B, 'title': 'Title'}), encoding='utf-8')
        thumbnail.write_bytes(b'image')
        output = self.collection / f'Video [{A}].html'
        status, _, _ = self.invoke(generator, info, thumbnail, output)
        self.assertIsInstance(status, ValueError)
        self.assertFalse(output.exists())
        output = self.collection / 'renamed.html'
        for old in (document(A), '<script id="youtube-metadata">{broken</script>', 'unidentified'):
            output.write_text(old, encoding='utf-8')
            status, _, _ = self.invoke(generator, info, thumbnail, output)
            self.assertNotIn(status, (None, 0))
            self.assertEqual(output.read_text(), old)
        output.write_text(document(B), encoding='utf-8')
        self.assertIsNone(self.invoke(generator, info, thumbnail, output)[0])

    def test_generator_refuses_symlink_without_following_target(self):
        info, thumbnail = self.root / 'info.json', self.root / 'thumb.png'
        info.write_text(json.dumps({'id': A, 'title': 'Title'}), encoding='utf-8')
        thumbnail.write_bytes(b'image')
        target = self.root / 'sentinel'
        target.write_text('keep', encoding='utf-8')
        output = self.collection / 'linked.html'
        output.symlink_to(target)
        self.assertEqual(self.invoke(generator, info, thumbnail, output)[0], 2)
        self.assertTrue(output.is_symlink())
        self.assertEqual(target.read_text(), 'keep')

    def test_generator_accepts_legacy_refresh_with_matching_id(self):
        info, thumbnail = self.root / 'info.json', self.root / 'thumb.png'
        info.write_text(json.dumps({'id': A, 'title': 'Title'}), encoding='utf-8')
        thumbnail.write_bytes(b'image')
        output = self.collection / f'Legacy [{A}].html'
        output.write_text('legacy', encoding='utf-8')
        self.assertIsNone(self.invoke(generator, info, thumbnail, output)[0])
        self.assertIn('youtube-metadata', output.read_text())

    def test_index_skips_invalid_pages_but_indexes_renamed_valid_page(self):
        (self.collection / 'renamed.html').write_text(document(A), encoding='utf-8')
        catalog = self.root / 'catalog.json'
        catalog.write_text(json.dumps({'version': 1, 'bookmarks': {
            A: {'tags': [' Renamed ', 'renamed', 'HIDDEN']},
            B: {'tags': ['must not infer identity']},
        }, 'collections': {}}))
        fixtures = {f'Conflict [{A}].html': document(B), f'Legacy [{C}].html': 'legacy',
                    'malformed.html': '<script id="youtube-metadata">{broken</script>',
                    'missing-id.html': '<script id="youtube-metadata">{}</script>'}
        for name, content in fixtures.items():
            (self.collection / name).write_text(content, encoding='utf-8')
        before = {path: path.read_bytes() for path in [catalog, *self.collection.iterdir()]}
        with patch.object(indexer, 'Image', None):
            status, stdout, stderr = self.invoke(indexer, self.root)
        self.assertEqual(status, 0)
        self.assertIn('Indexed 1 bookmarks (4 skipped)', stdout)
        for name in fixtures:
            self.assertIn(name, stderr)
        output = (self.root / 'index.html').read_text()
        self.assertIn('renamed.html', output)
        self.assertNotIn('Conflict%20', output)
        payload = output.split('<script id="bookmark-data" type="application/json">', 1)[1].split('</script>', 1)[0]
        items = json.loads(payload)['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], A)
        self.assertEqual(items[0]['personalTags'], ['Renamed', 'hidden'])
        self.assertNotIn('must not infer identity', output)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_upgrade_skips_bad_pages_and_preserves_dry_run_and_backups(self):
        valid = self.collection / 'renamed.html'
        valid.write_text(document(A), encoding='utf-8')
        invalid = {f'Conflict [{A}].html': document(B), f'Legacy [{C}].html': 'legacy',
                   'malformed.html': '<script id="youtube-metadata">{broken</script>'}
        for name, content in invalid.items():
            (self.collection / name).write_text(content, encoding='utf-8')
        original = valid.read_bytes()
        self.assertEqual(self.invoke(upgrader, self.root)[0], 0)
        self.assertEqual(valid.read_bytes(), original)
        self.assertEqual(list(self.root.glob('bookmarks-backup-*')), [])
        status, _, stderr = self.invoke(upgrader, self.root, '--apply')
        self.assertEqual(status, 0)
        for name, content in invalid.items():
            self.assertIn(name, stderr)
            self.assertEqual((self.collection / name).read_text(), content)
        backup, = self.root.glob('bookmarks-backup-*')
        self.assertEqual((backup / 'renamed.html').read_bytes(), original)
        self.assertEqual([path.name for path in backup.iterdir()], ['renamed.html'])
        self.assertNotEqual(valid.read_bytes(), original)


    def test_generation_rejects_repeated_id_keys_without_writing(self):
        info, thumbnail = self.root / 'info.json', self.root / 'thumb.png'
        thumbnail.write_bytes(b'image')
        output = self.collection / 'renamed.html'
        for second in (A, B):
            info.write_text('{"id":"' + A + '","id":"' + second + '","title":"Title"}')
            for existing in (False, True):
                with self.subTest(second=second, existing=existing):
                    if existing:
                        output.write_text(document(second))
                    before = {str(p): p.read_bytes() for p in self.collection.rglob('*') if p.is_file()}
                    status, _, _ = self.invoke(generator, info, thumbnail, output)
                    self.assertIsInstance(status, ValueError)
                    self.assertIn("repeated 'id'", str(status))
                    self.assertEqual({str(p): p.read_bytes() for p in self.collection.rglob('*') if p.is_file()}, before)
                    if existing:
                        output.unlink()

    def test_generation_keeps_unrelated_duplicate_fields_compatible(self):
        info, thumbnail = self.root / 'info.json', self.root / 'thumb.png'
        thumbnail.write_bytes(b'image')
        info.write_text('{"id":"' + A + '","title":"old","title":"new"}')
        output = self.collection / 'renamed.html'
        self.assertIsNone(self.invoke(generator, info, thumbnail, output)[0])
        self.assertIn('<h1>new</h1>', output.read_text())

    def test_embedded_repeated_id_keys_fail_closed_across_consumers(self):
        for second in (A, B):
            with self.subTest(second=second):
                bad = self.collection / 'renamed.html'
                bad.write_text('<img src="' + IMAGE + '"><script id="youtube-metadata">' +
                               '{"id":"' + A + '","id":"' + second + '","title":"Title"}</script>')
                for identity in (A, B, C):
                    status, stdout, stderr = self.invoke(finder, self.root, '--id', identity)
                    self.assertEqual((status, stdout), (2, ''))
                    self.assertIn("repeated 'id'", stderr)
                self.assertEqual(self.invoke(finder, self.root, '--audit')[0], 1)
                for operation in (('add', second, 'tag'), ('prune',)):
                    self.assertEqual(self.invoke(tagger, self.root, *operation, '--apply')[0], 2)
                self.assertFalse((self.root / 'catalog.json').exists())
                with patch.object(indexer, 'Image', None):
                    self.assertEqual(self.invoke(indexer, self.root)[0], 1)
                self.assertFalse((self.root / 'index.html').exists())
                original = bad.read_bytes()
                self.assertEqual(self.invoke(upgrader, self.root, '--apply')[0], 1)
                self.assertEqual(bad.read_bytes(), original)
                self.assertEqual(list(self.root.glob('bookmarks-backup-*')), [])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='.capture-test-', dir=PROJECT)
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.library = self.root / 'library'
        self.collection = self.library / 'bookmarks'
        self.collection.mkdir(parents=True)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        temporary = self.root / 'tmp'
        temporary.mkdir()
        stub = bin_dir / 'yt-dlp'
        stub.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
root = pathlib.Path(os.environ['VIDEX_FIXTURE_ROOT'])
(root / 'extractor-called').write_text('yes')
out = pathlib.Path(sys.argv[sys.argv.index('--paths') + 1])
identity = json.loads(os.environ['VIDEX_FIXTURE_ID'])
(out / 'fixture.info.json').write_text(json.dumps({'id': identity, 'title': 'Synthetic title', 'uploader': 'Synthetic channel'}))
if 'VIDEX_FIXTURE_RAW_METADATA' in os.environ:
    (out / 'fixture.info.json').write_text(os.environ['VIDEX_FIXTURE_RAW_METADATA'])
(out / 'fixture.png').write_bytes(b'image')
if os.environ.get('VIDEX_FIXTURE_CONFLICT'):
    (root / 'library' / 'bookmarks' / 'Conflict [AAAAAAAAAAA].html').write_text('<script id="youtube-metadata">{"id":"BBBBBBBBBBB"}</script>')
if os.environ.get('VIDEX_FIXTURE_DUPLICATES'):
    for name in ('duplicate-one.html', 'duplicate-two.html'):
        (root / 'library' / 'bookmarks' / name).write_text('<script id="youtube-metadata">{"id":"BBBBBBBBBBB"}</script>')
if os.environ.get('VIDEX_FIXTURE_BAD_CATALOG'):
    (root / 'library' / 'catalog.json').write_text('{broken')
''', encoding='utf-8')
        stub.chmod(0o700)
        self.environment = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                                TMPDIR=str(temporary), PYTHONDONTWRITEBYTECODE='1',
                                VIDEX_FIXTURE_ROOT=str(self.root), VIDEX_FIXTURE_ID=json.dumps(A))

    def capture(self, url=None, *extra, with_tags=True, bash=None):
        command = [bash or os.environ.get('VIDEX_TEST_BASH', 'bash'), str(SCRIPTS / 'yt-bookmark.sh'),
                   '--output', str(self.library)]
        if with_tags:
            command.extend(['--tag', 'personal'])
        return subprocess.run([*command, *extra, url or 'https://youtu.be/' + A],
                              env=self.environment, cwd=self.root, text=True, capture_output=True, timeout=15)

    def test_invalid_requested_tags_never_create_or_refresh_bookmarks(self):
        existing = self.collection / 'renamed.html'
        existing.write_text(document(A))
        catalog = self.library / 'catalog.json'
        catalog.write_text(json.dumps({'version': 1, 'bookmarks': {A: {'tags': ['keep']}}, 'collections': {}}))
        for invalid in ('x' * 81, 'bad\x1btag', 'bad\x01tag'):
            for url, extra in (('https://youtu.be/' + C, ()),
                               ('https://youtu.be/' + A, ('--overwrite',)),
                               ('https://youtu.be/' + A, ('--overwrite', '--apply'))):
                with self.subTest(tag=invalid, url=url, extra=extra):
                    before = {str(p): (p.read_bytes(), p.stat().st_mode) for p in self.library.rglob('*') if p.is_file()}
                    self.environment['VIDEX_FIXTURE_ID'] = json.dumps(C if url.endswith(C) else A)
                    result = self.capture(url, *extra, '--tag', invalid)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse((self.root / 'extractor-called').exists())
                    self.assertEqual({str(p): (p.read_bytes(), p.stat().st_mode)
                                      for p in self.library.rglob('*') if p.is_file()}, before)
                    self.assertNotIn('Traceback', result.stderr)

    def test_tagged_capture_rejects_bad_catalog_before_new_capture_or_refresh(self):
        existing = self.collection / 'renamed.html'
        existing.write_text(document(A))
        catalog = self.library / 'catalog.json'
        for content in ('{broken', json.dumps({'version': 2, 'bookmarks': {}, 'collections': {}}),
                        json.dumps({'version': 1, 'bookmarks': {A: {'tags': [1]}}, 'collections': {}})):
            catalog.write_text(content)
            for url, extra in (('https://youtu.be/' + C, ()),
                               ('https://youtu.be/' + A, ('--overwrite', '--apply'))):
                with self.subTest(content=content, url=url):
                    before = {str(p): p.read_bytes() for p in self.library.rglob('*') if p.is_file()}
                    result = self.capture(url, *extra)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse((self.root / 'extractor-called').exists())
                    self.assertEqual({str(p): p.read_bytes() for p in self.library.rglob('*') if p.is_file()}, before)
                    self.assertNotIn('Traceback', result.stderr)

    def test_tagged_capture_rechecks_catalog_after_extraction(self):
        existing = self.collection / 'renamed.html'
        existing.write_text(document(A))
        original = existing.read_bytes()
        self.environment['VIDEX_FIXTURE_BAD_CATALOG'] = '1'
        for identity, extra in ((C, ()), (A, ('--overwrite', '--apply'))):
            with self.subTest(identity=identity):
                (self.library / 'catalog.json').write_text(json.dumps({
                    'version': 1, 'bookmarks': {A: {'tags': ['keep']}}, 'collections': {}}))
                self.environment['VIDEX_FIXTURE_ID'] = json.dumps(identity)
                result = self.capture('https://youtu.be/' + identity, *extra)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(list(self.collection.rglob('*.html')), [existing])
                self.assertEqual(existing.read_bytes(), original)
                self.assertEqual((self.library / 'catalog.json').read_text(), '{broken')
                self.assertEqual(list(self.library.glob('catalog.json.bak-*')), [])

    def test_tagged_capture_refuses_catalog_symlink_before_writing(self):
        existing = self.collection / 'renamed.html'
        existing.write_text(document(A))
        original = existing.read_bytes()
        target = self.root / 'synthetic-catalog-target.json'
        target.write_text(json.dumps({'version': 1, 'bookmarks': {}, 'collections': {}}))
        target_bytes = target.read_bytes()
        catalog = self.library / 'catalog.json'
        catalog.symlink_to(target)
        result = self.capture(None, '--overwrite', '--apply')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(catalog.is_symlink())
        self.assertEqual(target.read_bytes(), target_bytes)
        self.assertEqual(existing.read_bytes(), original)
        self.assertFalse((self.root / 'extractor-called').exists())

    def test_valid_tags_keep_normalization_and_length_boundary(self):
        result = self.capture(None, '--tag', '  Multi\n Word\t', '--tag', 'multi word', '--tag', 'é' * 80)
        self.assertEqual(result.returncode, 0, result.stderr)
        catalog = json.loads((self.library / 'catalog.json').read_text())
        self.assertEqual(catalog['bookmarks'][A]['tags'], ['personal', 'Multi Word', 'é' * 80])

    def test_untagged_capture_skip_refresh_and_reindex_on_system_bash(self):
        # Use the platform's actual /bin/bash, not an assumed modern substitute.
        result = self.capture(None, '--re-index', with_tags=False, bash='/bin/bash')
        self.assertEqual(result.returncode, 0, result.stderr)
        saved, = self.collection.rglob('*.html')
        original = saved.read_bytes()
        self.assertTrue((self.library / 'index.html').exists())
        self.assertFalse((self.library / 'catalog.json').exists())
        (self.root / 'extractor-called').unlink()
        skipped = self.capture(with_tags=False, bash='/bin/bash')
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertFalse((self.root / 'extractor-called').exists())
        self.assertEqual(saved.read_bytes(), original)
        refreshed = self.capture(None, '--overwrite', '--re-index', with_tags=False, bash='/bin/bash')
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertEqual(list(self.collection.rglob('*.html')), [saved])
        self.assertFalse((self.library / 'catalog.json').exists())

    def test_untagged_capture_does_not_start_validating_catalog(self):
        catalog = self.library / 'catalog.json'
        catalog.write_text('{broken')
        result = self.capture(with_tags=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.collection.rglob('*.html'))), 1)
        self.assertEqual(catalog.read_text(), '{broken')

    def test_skipped_renamed_bookmark_tags_metadata_id_without_extractor(self):
        path = self.collection / 'renamed.html'
        path.write_text(document(A), encoding='utf-8')
        original = path.read_bytes()
        preview = self.capture()
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((self.library / 'catalog.json').exists())
        result = self.capture(None, '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'extractor-called').exists())
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(json.loads((self.library / 'catalog.json').read_text())['bookmarks'][A]['tags'], ['personal'])

    def test_initial_conflict_stops_before_extractor_and_overwrite(self):
        path = self.collection / f'Conflict [{A}].html'
        path.write_text(document(B), encoding='utf-8')
        before = path.read_bytes()
        result = self.capture(None, '--overwrite')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / 'extractor-called').exists())
        self.assertEqual(path.read_bytes(), before)

    def test_authoritative_id_checkpoint_refuses_new_conflict(self):
        self.environment['VIDEX_FIXTURE_CONFLICT'] = '1'
        result = self.capture('https://example.invalid/needs-resolution', '--overwrite')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((self.root / 'extractor-called').exists())
        self.assertIn('MISMATCH', result.stderr)
        self.assertEqual(len(list(self.collection.rglob('*.html'))), 1)
        self.assertFalse((self.library / 'catalog.json').exists())

    def test_raw_extractor_id_is_rejected_without_sanitizing(self):
        for identity in (A + '.', A + '\n', 123, None):
            with self.subTest(identity=identity):
                self.environment['VIDEX_FIXTURE_ID'] = json.dumps(identity)
                result = self.capture()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('Extractor returned an invalid video ID', result.stderr)
                self.assertEqual(list(self.collection.rglob('*.html')), [])
                self.assertFalse((self.library / 'catalog.json').exists())

    def test_valid_capture_and_refresh_preserve_id_and_original_path(self):
        result = self.capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved, = self.collection.rglob('*.html')
        self.assertIn('[' + A + ']', saved.name)
        refreshed = self.capture(None, '--overwrite', '--apply')
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        self.assertEqual(list(self.collection.rglob('*.html')), [saved])

    def test_unrelated_duplicate_ids_prevent_capture_without_library_changes(self):
        for name in ('first.html', 'second.html'):
            (self.collection / name).write_text(document(A))
        catalog = self.library / 'catalog.json'
        catalog.write_text(json.dumps({'version': 1, 'bookmarks': {A: {'tags': ['keep']}}, 'collections': {}}))
        self.environment['VIDEX_FIXTURE_ID'] = json.dumps(C)
        for extra in ((), ('--overwrite',), ('--apply',)):
            with self.subTest(extra=extra):
                before = {str(p): p.read_bytes() for p in self.library.rglob('*') if p.is_file()}
                result = self.capture('https://youtu.be/' + C, *extra)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Duplicate video ID ' + A, result.stderr)
                self.assertFalse((self.root / 'extractor-called').exists())
                self.assertEqual({str(p): p.read_bytes() for p in self.library.rglob('*') if p.is_file()}, before)

    def test_unrelated_duplicates_introduced_by_extractor_prevent_writes(self):
        self.environment['VIDEX_FIXTURE_ID'] = json.dumps(C)
        self.environment['VIDEX_FIXTURE_DUPLICATES'] = '1'
        result = self.capture('https://youtu.be/' + C)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((self.root / 'extractor-called').exists())
        self.assertIn('Duplicate video ID ' + B, result.stderr)
        self.assertEqual(sorted(path.name for path in self.collection.rglob('*.html')),
                         ['duplicate-one.html', 'duplicate-two.html'])
        self.assertFalse((self.library / 'catalog.json').exists())

    def test_capture_rejects_conflicting_and_identical_duplicate_json_ids(self):
        for second in (A, B):
            with self.subTest(second=second):
                self.environment['VIDEX_FIXTURE_RAW_METADATA'] = (
                    '{"id":"' + A + '","id":"' + second + '","title":"Title"}')
                result = self.capture()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("repeated 'id'", result.stderr)
                self.assertEqual(list(self.collection.rglob('*.html')), [])
                self.assertFalse((self.library / 'catalog.json').exists())

    def test_capture_accepts_unrelated_duplicate_metadata_fields(self):
        self.environment['VIDEX_FIXTURE_RAW_METADATA'] = (
            '{"id":"' + A + '","title":"old","title":"new","uploader":"Synthetic"}')
        result = self.capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved, = self.collection.rglob('*.html')
        self.assertIn('<h1>new</h1>', saved.read_text())


if __name__ == '__main__':
    unittest.main()
