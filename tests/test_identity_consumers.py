"""Stage B identity regressions using only disposable synthetic collections."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_videx_inventory import snapshot


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
            A: {'tags': [' Renamed ', 'renamed', 'Research']},
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
        self.assertEqual(items[0]['personalTags'], ['Renamed', 'Research'])
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


class FilesystemConsumerTests(unittest.TestCase):
    setUp = ConsumerTests.setUp
    invoke = ConsumerTests.invoke

    def seed(self):
        self.good = self.collection / f'Good [{A}].html'
        self.good.write_text(document(A))
        self.good.chmod(0o640)
        self.index = self.root / 'index.html'
        self.index.write_bytes(b'old index sentinel')
        self.index.chmod(0o640)
        (self.root / 'catalog.json').write_text(json.dumps({
            'version': 1, 'bookmarks': {A: {'tags': ['keep']}, B: {'tags': ['unverified']}},
            'collections': {}}))
        (self.root / 'catalog.json.bak-existing').write_bytes(b'backup sentinel')
        backup = self.root / 'bookmarks-backup-existing'
        backup.mkdir()
        (backup / 'keep').write_bytes(b'upgrade backup sentinel')

    def cli(self, script, *arguments):
        return subprocess.run([sys.executable, '-B', str(SCRIPTS / script), *map(str, arguments)],
                              cwd=self.root, capture_output=True, text=True, timeout=10,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

    def test_actual_unsupported_objects_never_block_or_publish(self):
        self.seed()
        external = self.root / 'external'
        external.mkdir()
        secret = external / f'External [{B}].html'
        secret.write_text(document(B).replace('Synthetic title', 'ExternalSecretSentinel'))
        info, thumbnail = self.root / 'info.json', self.root / 'thumb.png'
        info.write_text(json.dumps({'id': A, 'title': 'Synthetic title'}))
        thumbnail.write_bytes(b'image')
        bad = self.collection / 'bad.html'
        for kind in ('file-link', 'directory-link', 'dangling', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'fifo':
                    os.mkfifo(bad)
                else:
                    target = secret if kind == 'file-link' else external if kind == 'directory-link' else self.root / 'absent'
                    bad.symlink_to(target, target_is_directory=kind == 'directory-link')
                before = snapshot(self.root)
                commands = [
                    ('yt-bookmark-index.py', [self.root], 2),
                    ('yt-bookmark-upgrade.py', [self.root], 2),
                    ('yt-bookmark-upgrade.py', [self.root, '--apply'], 2),
                    ('yt-bookmark-find.py', [self.root, '--id=' + A], 2),
                    ('yt-bookmark-find.py', [self.root, '--audit', '--show-tags', '--json'], 1),
                    ('yt-bookmark-tag.py', [self.root, 'list', A], 0),
                    ('yt-bookmark-tag.py', [self.root, 'report'], 1),
                    ('yt-bookmark-generate.py', [info, thumbnail, bad], 2),
                ]
                for operation in ('add', 'remove', 'set', 'rename', 'prune'):
                    values = [] if operation == 'prune' else ['keep', 'new'] if operation == 'rename' else [A, 'new']
                    for apply in ([], ['--apply']):
                        commands.append(('yt-bookmark-tag.py', [self.root, operation, *values, *apply], 2))
                for script, arguments, expected in commands:
                    with self.subTest(script=script, arguments=arguments):
                        result = self.cli(script, *arguments)
                        self.assertEqual(result.returncode, expected, result.stderr)
                        self.assertNotIn('Traceback', result.stderr)
                        if script == 'yt-bookmark-tag.py' and arguments[-1] == 'report':
                            self.assertIn('Unverified catalog entries', result.stdout)
                        self.assertEqual(snapshot(self.root), before)
                self.assertNotIn(b'ExternalSecretSentinel', self.index.read_bytes())
                bad.unlink()

    def test_unsupported_collection_entries_never_trigger_legacy_fallback(self):
        self.seed()
        saved = self.root / 'saved-bookmarks'
        self.collection.rename(saved)
        for kind in ('file', 'link', 'dangling', 'fifo'):
            if kind == 'file':
                self.collection.write_text('unsupported entry')
            elif kind == 'fifo':
                os.mkfifo(self.collection)
            else:
                self.collection.symlink_to(saved if kind == 'link' else self.root / 'missing', target_is_directory=True)
            before = snapshot(self.root)
            for script, extra in (('yt-bookmark-index.py', []), ('yt-bookmark-upgrade.py', ['--apply']),
                                  ('yt-bookmark-tag.py', ['report']), ('yt-bookmark-tag.py', ['list', A])):
                with self.subTest(kind=kind, script=script, extra=extra):
                    result = self.cli(script, self.root, *extra)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(snapshot(self.root), before)
            if kind == 'link':
                for script in ('yt-bookmark-index.py', 'yt-bookmark-upgrade.py'):
                    result = self.cli(script, self.collection)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(snapshot(self.root), before)
            self.collection.unlink()

    def test_late_read_failures_leave_index_and_upgrade_backups_untouched(self):
        self.seed()
        later = self.collection / f'Z Later [{B}].html'
        later.write_text(document(B))
        for failure in (PermissionError('denied'), FileNotFoundError('disappeared'), UnicodeError('bad encoding')):
            for module in (indexer, upgrader):
                real_read = module.read_bookmark_text
                def read(path):
                    if path == later:
                        raise failure
                    return real_read(path)
                before = snapshot(self.root)
                with self.subTest(module=module.__name__, failure=failure), \
                        patch.object(module, 'read_bookmark_text', side_effect=read), \
                        patch.object(generator, 'write_atomic') as writer, \
                        patch.object(indexer, 'write_index_atomic') as index_writer, \
                        patch.object(upgrader.shutil, 'copy2') as backup:
                    arguments = [self.root, '--apply'] if module is upgrader else [self.root]
                    status, _, _ = self.invoke(module, *arguments)
                    self.assertEqual(status, 2)
                    writer.assert_not_called()
                    index_writer.assert_not_called()
                    backup.assert_not_called()
                self.assertEqual(snapshot(self.root), before)

    def test_legacy_directory_and_explicit_library_alias_remain_supported(self):
        self.seed()
        alias = self.root / 'library-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for selected in (alias, self.collection):
            result = self.cli('yt-bookmark-index.py', selected)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.cli('yt-bookmark-upgrade.py', selected)
            self.assertEqual(result.returncode, 0, result.stderr)
        legacy = self.root / 'legacy'
        legacy.mkdir()
        (legacy / 'renamed.html').write_text(document(A))
        for script in ('yt-bookmark-index.py', 'yt-bookmark-upgrade.py'):
            result = self.cli(script, legacy)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_generator_rejects_destination_types_before_any_content_read(self):
        directory = self.collection / 'directory.html'
        directory.mkdir()
        info, thumbnail = self.root / 'absent-info.json', self.root / 'absent-thumb.png'
        before = snapshot(self.root)
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read')), \
                patch.object(generator, 'write_atomic') as writer:
            self.assertEqual(self.invoke(generator, info, thumbnail, directory)[0], 2)
            writer.assert_not_called()
        self.assertEqual(snapshot(self.root), before)
        for mode in (stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            with self.subTest(mode=mode), \
                    patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=mode)), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('must not read')), \
                    patch.object(generator, 'write_atomic') as writer:
                self.assertEqual(self.invoke(generator, info, thumbnail, directory)[0], 2)
                writer.assert_not_called()
        self.assertEqual(snapshot(self.root), before)


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
        # Exercise child scripts with the interpreter running this suite.
        (bin_dir / 'python3').symlink_to(sys.executable)
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

    def capture(self, url=None, *extra, with_tags=True, bash=None, id_text=None):
        command = [bash or os.environ.get('VIDEX_TEST_BASH', 'bash'), str(SCRIPTS / 'yt-bookmark.sh'),
                   '--output', str(self.library)]
        if with_tags:
            command.extend(['--tag', 'personal'])
        source = ['--id', id_text] if id_text is not None else [url or 'https://youtu.be/' + A]
        return subprocess.run([*command, *extra, *source],
                              env=self.environment, cwd=self.root, text=True, capture_output=True, timeout=15)

    def library_snapshot(self):
        result = {}
        for path in [self.library, *sorted(self.library.rglob('*'))]:
            info = path.lstat()
            content = (os.readlink(path) if path.is_symlink() else
                       path.read_bytes() if path.is_file() else None)
            result[str(path.relative_to(self.library))] = (info.st_mode, info.st_mtime_ns, content)
        return result

    def test_option_like_tags_preview_and_explicit_apply_preserve_boundaries(self):
        values = ['Research', '--apply', '--help', '--app', '-topic', '--unknown=value', '--']
        tags = [arg for value in values for arg in ('--tag', value)]
        catalog = self.library / 'catalog.json'
        sentinel = self.library / 'catalog.json.bak-existing'
        sentinel.write_bytes(b'preserve existing backup')
        for identity in (A, '-AAAAAAAAAA', '--AAAAAAAAA', '-0AAAAAAAAA', '-1234567890'):
            with self.subTest(identity=identity):
                path = self.collection / f'Renamed [{identity}].html'
                path.write_text(document(identity), encoding='utf-8')
                path.chmod(0o640)
                self.environment['VIDEX_FIXTURE_ID'] = json.dumps(identity)
                if catalog.exists():
                    catalog.unlink()
                for existing_catalog in (False, True):
                    if existing_catalog:
                        catalog.write_text(json.dumps({'version': 1, 'bookmarks': {
                            identity: {'tags': ['keep'], 'notes': 'preserve'}}, 'collections': {}}))
                        catalog.chmod(0o640)
                    before = self.library_snapshot()
                    result = self.capture('https://youtu.be/' + identity, *tags, with_tags=False, bash='/bin/bash')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('DRY RUN: add', result.stdout)
                    self.assertEqual(self.library_snapshot(), before)
                    self.assertFalse((self.root / 'extractor-called').exists())
                original = catalog.read_bytes()
                backups = set(self.library.glob('catalog.json.bak-*'))
                result = self.capture('https://youtu.be/' + identity, *tags, '--apply', with_tags=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(catalog.read_text())['bookmarks'][identity]
                self.assertEqual(record, {'tags': sorted(['keep', *values], key=str.casefold), 'notes': 'preserve'})
                added, = set(self.library.glob('catalog.json.bak-*')) - backups
                self.assertEqual(added.read_bytes(), original)
                self.assertEqual(path.read_text(), document(identity))
                self.assertEqual(sentinel.read_bytes(), b'preserve existing backup')
                self.assertEqual(list(self.library.glob('.catalog-*.tmp')), [])

    def test_leading_hyphen_capture_refresh_and_both_skip_paths(self):
        for identity in ('-AAAAAAAAAA', '--AAAAAAAAA', '-0AAAAAAAAA', '-1234567890'):
            with self.subTest(identity=identity):
                self.environment['VIDEX_FIXTURE_ID'] = json.dumps(identity)
                initial_tags = ['--apply', '--help', '--']
                arguments = [arg for tag in initial_tags for arg in ('--tag', tag)]
                created = self.capture(None, *arguments, with_tags=False, id_text=identity)
                self.assertEqual(created.returncode, 0, created.stderr)
                path, = [
                    p for p in self.collection.rglob('*.html') if p.name.endswith(f'[{identity}].html')]
                self.assertEqual(generator.resolve_identity(path.name, path.read_text()), identity)
                catalog = self.library / 'catalog.json'
                self.assertEqual(json.loads(catalog.read_text())['bookmarks'][identity]['tags'],
                                 sorted(initial_tags, key=str.casefold))
                (self.root / 'extractor-called').unlink()
                before = self.library_snapshot()
                skipped = self.capture('https://youtu.be/' + identity, with_tags=False)
                self.assertEqual(skipped.returncode, 0, skipped.stderr)
                self.assertFalse((self.root / 'extractor-called').exists())
                self.assertEqual(self.library_snapshot(), before)
                post_skip = self.capture('https://example.invalid/resolve', '--tag', '--app',
                                         with_tags=False)
                self.assertEqual(post_skip.returncode, 0, post_skip.stderr)
                self.assertIn('SKIPPED:', post_skip.stdout)
                self.assertIn('DRY RUN: add', post_skip.stdout)
                self.assertTrue((self.root / 'extractor-called').exists())
                self.assertEqual(self.library_snapshot(), before)
                # Refresh the renamed original path, while leaving catalog tagging in preview.
                renamed = path.with_name(f'renamed-{identity}.html')
                path.rename(renamed)
                renamed.write_text(document(identity))
                catalog_before = catalog.stat(), catalog.read_bytes()
                backups = {p: p.read_bytes() for p in self.library.glob('catalog.json.bak-*')}
                refreshed = self.capture(None, '--overwrite', '--tag', '--unknown=value',
                                         with_tags=False, id_text=identity)
                self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
                self.assertIn('REFRESH:', refreshed.stdout)
                self.assertEqual(generator.resolve_identity(renamed.name, renamed.read_text()), identity)
                self.assertNotEqual(renamed.read_text(), document(identity))
                self.assertFalse(path.exists())
                self.assertEqual(catalog.read_bytes(), catalog_before[1])
                self.assertEqual(catalog.stat().st_mode, catalog_before[0].st_mode)
                self.assertEqual(catalog.stat().st_mtime_ns, catalog_before[0].st_mtime_ns)
                self.assertEqual({p: p.read_bytes() for p in self.library.glob('catalog.json.bak-*')}, backups)
                applied = self.capture('https://example.invalid/resolve', '--tag', '--app', '--apply',
                                       with_tags=False)
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(json.loads(catalog.read_text())['bookmarks'][identity]['tags'],
                                 sorted([*initial_tags, '--app'], key=str.casefold))
                refreshed_apply = self.capture(None, '--overwrite', '--tag', '--unknown=value', '--apply',
                                               with_tags=False, id_text=identity)
                self.assertEqual(refreshed_apply.returncode, 0, refreshed_apply.stderr)
                self.assertEqual(json.loads(catalog.read_text())['bookmarks'][identity]['tags'],
                                 sorted([*initial_tags, '--app', '--unknown=value'], key=str.casefold))

    def test_option_like_capture_tags_do_not_bypass_catalog_or_identity_guards(self):
        identity = '-AAAAAAAAAA'
        path = self.collection / f'Original [{identity}].html'
        path.write_text(document(identity))
        catalog = self.library / 'catalog.json'
        target = self.library / 'synthetic-target.json'
        target.write_text(json.dumps({'version': 1, 'bookmarks': {}, 'collections': {}}))
        for problem in ('malformed', 'symlink', 'conflict', 'invalid'):
            with self.subTest(problem=problem):
                if catalog.exists() or catalog.is_symlink():
                    catalog.unlink()
                path.write_text(document(B if problem == 'conflict' else identity))
                if problem == 'malformed':
                    catalog.write_text('{broken')
                elif problem == 'symlink':
                    catalog.symlink_to(target)
                before = self.library_snapshot()
                for apply in (False, True):
                    options = ['--apply'] if apply else []
                    result = self.capture(None, '--tag', 'Research', '--tag', '--apply', *options,
                                          with_tags=False, id_text='-short' if problem == 'invalid' else identity)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.library_snapshot(), before)
                    self.assertFalse((self.root / 'extractor-called').exists())

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
        self.assertEqual(catalog['bookmarks'][A]['tags'], ['Multi Word', 'personal', 'é' * 80])

    def test_new_capture_tag_order_is_independent_of_insertion_order(self):
        for identity, values in ((A, ['Zulu', 'Beta', 'alpha', 'HIDDEN', 'beta']),
                                 (C, ['alpha', 'Beta', 'beta', 'HIDDEN', 'Zulu'])):
            with self.subTest(identity=identity):
                self.environment['VIDEX_FIXTURE_ID'] = json.dumps(identity)
                arguments = [argument for value in values for argument in ('--tag', value)]
                result = self.capture('https://youtu.be/' + identity, *arguments, with_tags=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                catalog = json.loads((self.library / 'catalog.json').read_text())
                self.assertEqual(catalog['bookmarks'][identity]['tags'],
                                 ['alpha', 'Beta', 'hidden', 'Zulu'])

    def test_existing_capture_sorts_only_applied_tag_changes(self):
        path = self.collection / 'renamed.html'
        path.write_text(document(A), encoding='utf-8')
        catalog = self.library / 'catalog.json'
        original = {'version': 1, 'bookmarks': {
            A: {'tags': ['Zulu', 'Beta', 'beta'], 'notes': 'keep'},
            B: {'tags': ['Zulu', 'Alpha'], 'notes': 'unrelated'},
        }, 'collections': {'saved': [A]}, 'extra': True}
        catalog.write_text(json.dumps(original), encoding='utf-8')
        before = catalog.read_bytes()
        preview = self.capture(None, '--tag', 'alpha', with_tags=False)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("-> ['alpha', 'Beta', 'Zulu']", preview.stdout)
        self.assertEqual(catalog.read_bytes(), before)
        self.assertEqual(list(self.library.glob('catalog.json.bak-*')), [])
        applied = self.capture(None, '--tag', 'alpha', '--apply', with_tags=False)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        original['bookmarks'][A]['tags'] = ['alpha', 'Beta', 'Zulu']
        self.assertEqual(json.loads(catalog.read_text()), original)
        backups = list(self.library.glob('catalog.json.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        added = self.capture(None, '--tag', 'HIDDEN', '--tag', 'Gamma', '--apply',
                             with_tags=False)
        self.assertEqual(added.returncode, 0, added.stderr)
        original['bookmarks'][A]['tags'] = ['alpha', 'Beta', 'Gamma', 'hidden', 'Zulu']
        self.assertEqual(json.loads(catalog.read_text()), original)
        self.assertFalse((self.root / 'extractor-called').exists())
        self.assertEqual(path.read_text(encoding='utf-8'), document(A))

    def test_refreshed_capture_sorts_tags_only_with_apply(self):
        path = self.collection / 'renamed.html'
        path.write_text(document(A), encoding='utf-8')
        catalog = self.library / 'catalog.json'
        original = json.dumps({'version': 1, 'bookmarks': {
            A: {'tags': ['Zulu', 'Beta']}}, 'collections': {}})
        catalog.write_text(original, encoding='utf-8')
        preview = self.capture(None, '--overwrite', '--tag', 'alpha', with_tags=False)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(catalog.read_text(encoding='utf-8'), original)
        self.assertEqual(list(self.library.glob('catalog.json.bak-*')), [])
        applied = self.capture(None, '--overwrite', '--tag', 'alpha', '--apply',
                               with_tags=False)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(catalog.read_text())['bookmarks'][A]['tags'],
                         ['alpha', 'Beta', 'Zulu'])
        self.assertEqual(list(self.collection.rglob('*.html')), [path])

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
