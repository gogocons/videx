"""Offline tag and prune regressions using synthetic filesystems."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
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
from unittest.mock import Mock, patch
from test_videx_inventory import snapshot


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
with patch.object(sys, 'path', [str(SCRIPTS)] + sys.path):
    import videx_catalog

    spec = spec_from_file_location('bookmark_tag', SCRIPTS / 'yt-bookmark-tag.py')
    tagger = module_from_spec(spec)
    spec.loader.exec_module(tagger)

ROOT = Path('/synthetic-videx')
A, B = 'AAAAAAAAAAA', 'BBBBBBBBBBB'


def bookmark(video_id):
    return '<script id="youtube-metadata">' + json.dumps({'id': video_id}) + '</script>'


def duplicate_catalog_documents():
    """Raw JSON is required: dict fixtures would discard the repeated keys."""
    def catalog(record='{"tags":["keep"]}', collections='{}', extra=''):
        return '{"version":1,"bookmarks":{"' + A + '":' + record + '},"collections":' + collections + extra + '}'

    base = catalog()
    pair = '"' + A + '":{"tags":["keep"]}'
    return [
        base.replace('"version":1', '"version":1,"version":1'),
        base.replace('"version":1', '"version":2,"version":1'),
        base.replace('"bookmarks":', '"bookmarks":{},"bookmarks":'),
        base.replace(pair, pair + ',' + pair),
        base.replace(pair, pair + ',"' + A + '":{"tags":["other"]}'),
        catalog('{"tags":["keep"],"tags":["keep"]}'),
        catalog('{"tags":["hidden"],"tags":["other"]}'),
        catalog('{"tags":[],"\\u0074ags":["keep"]}'),
        catalog('{"tags":[],"notes":"one","notes":"two"}'),
        catalog('{"tags":[{"x":1,"x":2}]}'),
        base.replace('"bookmarks":{', '"bookmarks":{"' + B + '":{"tags":[],"tags":[]},'),
        catalog(extra=',"collections":{"saved":[]}'),
        catalog(collections='{"saved":[],"saved":[]}'),
        catalog(collections='{"saved":{"extra":{"x":1,"x":2}}}'),
        catalog(extra=',"future":{"x":1,"x":1}'),
        catalog(extra=',"future":1,"future":2'),
        catalog(extra=',"future":[{"nested":{"x":1,"x":2}}]'),
        catalog('{"tags":[],"future":[{"x":1,"x":1}]}'),
    ]


class TagOrderingTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        collection = self.root / 'bookmarks'
        collection.mkdir()
        for video_id in (A, B):
            (collection / f'{video_id}.html').write_text(bookmark(video_id), encoding='utf-8')
        self.path = self.root / 'catalog.json'
        self.catalog = {'version': 1, 'bookmarks': {
            A: {'tags': ['Zulu', 'Beta', 'beta'], 'notes': 'keep'},
            B: {'tags': ['Zulu', 'Alpha', 'alpha'], 'extra': [1, 2]},
        }, 'collections': {'saved': [A, B]}, 'extra': {'keep': True}}
        self.path.write_text(json.dumps(self.catalog), encoding='utf-8')

    def invoke(self, *arguments, apply=True):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['tag', str(self.root), *arguments,
                                       *(['--apply'] if apply else [])]), \
                redirect_stdout(output), redirect_stderr(output):
            status = tagger.main()
        self.assertEqual(status, 0, output.getvalue())
        return output.getvalue()

    def saved(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_set_insertion_orders_capitalization_duplicates_and_hidden(self):
        for values in (['Zulu', 'alpha', 'Beta', 'HIDDEN', 'beta', 'ALPHA'],
                       ['HIDDEN', 'Beta', 'alpha', 'ALPHA', 'Zulu', 'beta']):
            with self.subTest(values=values):
                self.invoke('set', A, *values)
                self.assertEqual(self.saved()['bookmarks'][A]['tags'],
                                 ['alpha', 'Beta', 'hidden', 'Zulu'])

    def test_add_normalizes_modified_record_and_subsequent_additions(self):
        self.invoke('add', A, 'alpha', 'ALPHA', 'beta')
        self.assertEqual(self.saved()['bookmarks'][A]['tags'], ['alpha', 'Beta', 'Zulu'])
        self.invoke('add', A, 'HIDDEN', 'Gamma', 'gamma')
        expected = json.loads(json.dumps(self.catalog))
        expected['bookmarks'][A]['tags'] = ['alpha', 'Beta', 'Gamma', 'hidden', 'Zulu']
        self.assertEqual(self.saved(), expected)

    def test_remove_and_rename_sort_only_modified_records(self):
        for arguments, tags in ((('remove', A, 'beta'), ['Zulu']),
                                (('rename', 'Beta', 'alpha'), ['alpha', 'Zulu'])):
            with self.subTest(arguments=arguments):
                self.path.write_text(json.dumps(self.catalog), encoding='utf-8')
                self.invoke(*arguments)
                expected = json.loads(json.dumps(self.catalog))
                expected['bookmarks'][A]['tags'] = tags
                self.assertEqual(self.saved(), expected)

    def test_remove_sorts_remaining_unsorted_tags(self):
        self.invoke('set', A, 'Zulu', 'Beta', 'alpha')
        catalog = self.saved()
        catalog['bookmarks'][A]['tags'] = ['Zulu', 'Beta', 'alpha']
        self.path.write_text(json.dumps(catalog), encoding='utf-8')
        self.invoke('remove', A, 'beta')
        self.assertEqual(self.saved()['bookmarks'][A]['tags'], ['alpha', 'Zulu'])

    def test_preview_shows_sorted_result_without_writing_or_backup(self):
        original = self.path.read_bytes()
        output = self.invoke('add', A, 'alpha', apply=False)
        self.assertIn("-> ['alpha', 'Beta', 'Zulu']", output)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])

    def test_no_ops_do_not_normalize_or_back_up_existing_records(self):
        original = self.path.read_bytes()
        for arguments in (('add', A, 'BETA'), ('remove', A, 'absent'),
                          ('set', A, 'Zulu', 'Beta'), ('rename', 'absent', 'Alpha'),
                          ('rename', 'Beta', 'Beta')):
            with self.subTest(arguments=arguments):
                self.assertIn('No changes needed.', self.invoke(*arguments))
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])

    def test_prune_does_not_sort_surviving_records(self):
        orphan = 'CCCCCCCCCCC'
        self.catalog['bookmarks'][orphan] = {'tags': ['Zulu', 'Alpha']}
        self.path.write_text(json.dumps(self.catalog), encoding='utf-8')
        self.invoke('prune')
        del self.catalog['bookmarks'][orphan]
        self.assertEqual(self.saved(), self.catalog)


class CatalogValidationTests(unittest.TestCase):
    operations = (
        ('add', A, 'new'), ('remove', A, 'keep'), ('set', A, 'new'),
        ('rename', 'keep', 'new'), ('list', A), ('report',), ('prune',),
        # Requests that would otherwise make no changes, plus clearing tags.
        ('add', A, 'keep'), ('remove', A, 'absent'), ('set', A, 'keep'),
        ('rename', 'absent', 'new'), ('set', A),
    )

    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name).resolve()
        collection = self.root / 'bookmarks'
        collection.mkdir()
        for video_id in (A, B):
            (collection / f'{video_id}.html').write_text(bookmark(video_id), encoding='utf-8')
        self.path = self.root / 'catalog.json'
        (self.root / 'catalog.json.bak-existing').write_bytes(b'preserve backup\n')
        self.catalog = {'version': 1, 'bookmarks': {
            A: {'tags': ['keep'], 'notes': 'preserve'},
            B: {'tags': ['keep'], 'extra': [1, 2]},
            'CCCCCCCCCCC': {'tags': ['keep'], 'orphan': True},
        }, 'collections': {'saved': [A, B]}, 'extra': {'preserve': True}}

    def invoke(self, arguments, apply=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['tag', str(self.root), *arguments,
                                       *(['--apply'] if apply else [])]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = tagger.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def snapshot(self):
        # Never open symlinks or special files, even when checking preservation.
        result = {}
        for path in self.root.rglob('*'):
            mode = path.lstat().st_mode
            value = (path.read_bytes() if stat.S_ISREG(mode) else
                     os.readlink(path) if stat.S_ISLNK(mode) else None)
            result[str(path.relative_to(self.root))] = (mode, value)
        return result

    def assert_refused(self, diagnostic):
        before = self.snapshot()
        for arguments in self.operations:
            for apply in (False, True):
                with self.subTest(arguments=arguments, apply=apply), \
                        patch.object(tagger, 'archive_ids') as inventory, \
                        patch.object(tagger, 'write_catalog') as writer, \
                        patch.object(videx_catalog.tempfile, 'NamedTemporaryFile') as staging:
                    status, stdout, stderr = self.invoke(arguments, apply)
                    self.assertEqual(status, 2)
                    self.assertEqual(stdout, '')
                    self.assertIn('ERROR:', stderr)
                    self.assertIn(str(self.path), stderr)
                    self.assertIn(diagnostic, stderr)
                    self.assertNotIn('Traceback', stderr)
                    inventory.assert_not_called()
                    writer.assert_not_called()
                    staging.assert_not_called()
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(list(self.root.glob('catalog.json.bak-*')),
                                     [self.root / 'catalog.json.bak-existing'])
                    self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])

    def test_valid_target_symlink_is_preserved_without_reading(self):
        target = self.root / 'target.json'
        target.write_text(json.dumps(self.catalog), encoding='utf-8')
        self.path.symlink_to(target)
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read catalog')):
            self.assert_refused('regular file')
        self.assertTrue(self.path.is_symlink())

    def test_dangling_symlink_is_preserved_without_creating_target(self):
        target = self.root / 'missing.json'
        self.path.symlink_to(target)
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read catalog')):
            self.assert_refused('regular file')
        self.assertTrue(self.path.is_symlink())
        self.assertFalse(target.exists())

    def test_directory_is_preserved_without_reading(self):
        self.path.mkdir()
        (self.path / 'sentinel').write_bytes(b'preserve directory contents')
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read catalog')):
            self.assert_refused('regular file')

    def test_fifo_is_preserved_without_opening(self):
        os.mkfifo(self.path)
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not open FIFO')):
            self.assert_refused('regular file')

    def test_other_special_file_modes_are_refused_without_reading(self):
        original_lstat = Path.lstat
        for mode in (stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK):
            def lstat(path, *args, **kwargs):
                if path == self.path:
                    return SimpleNamespace(st_mode=mode)
                return original_lstat(path, *args, **kwargs)
            with self.subTest(mode=mode), patch.object(Path, 'lstat', lstat), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('must not read device')):
                self.assert_refused('regular file')

    def test_nonstring_tags_in_selected_unrelated_and_orphan_records(self):
        for video_id in (A, B, 'CCCCCCCCCCC'):
            for value in (1, 1.5, None, True, False, {}, []):
                for tags in ([value], ['keep', value]):
                    with self.subTest(video_id=video_id, tags=tags):
                        catalog = json.loads(json.dumps(self.catalog))
                        catalog['bookmarks'][video_id]['tags'] = tags
                        # Deliberately preserve noncanonical formatting and bytes.
                        self.path.write_bytes(('  ' + json.dumps(catalog) + '\n\n').encode('utf-8'))
                        self.assert_refused(f'tags for {video_id} must contain only strings')

    def test_recursive_duplicate_members_refuse_all_operations_without_writes(self):
        for number, content in enumerate(duplicate_catalog_documents()):
            with self.subTest(number=number):
                self.path.write_bytes(('  ' + content + '\n\n').encode('utf-8'))
                self.assert_refused('Duplicate catalog object member')

    def test_exact_keys_and_unknown_fields_survive_real_applied_mutation(self):
        catalog = json.loads(json.dumps(self.catalog))
        catalog['bookmarks'][A]['Tags'] = ['distinct']
        catalog['bookmarks'][A]['future'] = [{'é': 1, 'e\u0301': 2}]
        catalog['bookmarks'][A.lower()] = {'tags': ['case-sensitive ID']}
        catalog['collections'] = {'Saved': {'members': [A]}, 'saved': {'members': [B]}}
        catalog['future'] = [{' x ': 1, 'x': 2, 'X': 3}]
        self.path.write_text(json.dumps(catalog, ensure_ascii=False))
        original = self.path.read_bytes()
        expected = json.loads(original)
        expected['bookmarks'][A]['tags'] = ['keep', 'new']
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'yt-bookmark-tag.py'),
                                 '--apply', '--', str(self.root), 'add', A, 'new'],
                                cwd=self.root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_text()), expected)
        backups = [p for p in self.root.glob('catalog.json.bak-*') if p.name != 'catalog.json.bak-existing']
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_missing_catalog_previews_and_read_only_operations_do_not_create_it(self):
        for arguments in self.operations:
            with self.subTest(arguments=arguments):
                before = self.snapshot()
                status, _, stderr = self.invoke(arguments)
                self.assertEqual((status, stderr), (0, ''))
                self.assertEqual(self.snapshot(), before)
        for arguments in (('remove', A, 'keep'), ('set', A),
                          ('rename', 'absent', 'new'), ('list', A), ('report',), ('prune',)):
            with self.subTest(arguments=arguments, apply=True):
                before = self.snapshot()
                status, _, stderr = self.invoke(arguments, apply=True)
                self.assertEqual((status, stderr), (0, ''))
                self.assertEqual(self.snapshot(), before)

    def test_missing_catalog_creation_and_regular_catalog_backup(self):
        status, _, stderr = self.invoke(('add', A, ' Zulu ', 'HIDDEN', 'alpha', 'ALPHA'), apply=True)
        self.assertEqual((status, stderr), (0, ''))
        self.assertEqual(json.loads(self.path.read_bytes()), {
            'version': 1, 'bookmarks': {A: {'tags': ['alpha', 'hidden', 'Zulu']}}, 'collections': {}})
        self.assertEqual(list(self.root.glob('catalog.json.bak-*')),
                         [self.root / 'catalog.json.bak-existing'])
        original = self.path.read_bytes()
        status, _, stderr = self.invoke(('add', A, 'Beta'), apply=True)
        self.assertEqual((status, stderr), (0, ''))
        backups = [path for path in self.root.glob('catalog.json.bak-*')
                   if path.name != 'catalog.json.bak-existing']
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(json.loads(self.path.read_bytes())['bookmarks'][A]['tags'],
                         ['alpha', 'Beta', 'hidden', 'Zulu'])
        self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])


class PruneTests(unittest.TestCase):
    def run_prune(self, files, apply=False, traversal_failure=False):
        catalog = {'version': 1, 'bookmarks': {
            A: {'tags': ['keep']}, B: {'tags': ['orphan']}}, 'collections': {}}
        original = json.dumps(catalog)
        collection = ROOT / 'bookmarks'
        entries = []
        for name in files:
            entry = Mock(name=name)
            entry.name = name
            entry.path = str(collection / name)
            entry.stat.return_value = SimpleNamespace(st_mode=stat.S_IFREG)
            entries.append(entry)
        if traversal_failure:
            entry = Mock()
            entry.name = 'inaccessible'
            entry.path = str(collection / entry.name)
            entry.stat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR)
            entries.append(entry)

        def scandir(path):
            if Path(path) != collection:
                raise PermissionError('synthetic traversal denied')
            context = Mock()
            context.__enter__ = Mock(return_value=iter(entries))
            context.__exit__ = Mock(return_value=False)
            return context

        def read_text(path, **kwargs):
            if path == ROOT / 'catalog.json':
                return original
            self.assertEqual(path.parent, collection)
            value = files[path.name]
            if isinstance(value, Exception):
                raise value
            return value

        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', ['tag', str(ROOT), 'prune'] + (['--apply'] if apply else [])))
            stack.enter_context(patch.object(Path, 'resolve', lambda path: path))
            stack.enter_context(patch.object(Path, 'is_dir', return_value=True))
            stack.enter_context(patch.object(Path, 'is_symlink', return_value=False))
            stack.enter_context(patch.object(Path, 'exists', return_value=True))
            stack.enter_context(patch.object(Path, 'lstat', lambda path: SimpleNamespace(
                st_mode=stat.S_IFDIR if path == collection else stat.S_IFREG)))
            stack.enter_context(patch.object(Path, 'read_text', read_text))
            stack.enter_context(patch.object(os, 'scandir', side_effect=scandir))
            writer = stack.enter_context(patch.object(tagger, 'write_catalog'))
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(output))
            status = tagger.main()
        return status, output.getvalue(), writer

    def test_incomplete_inventory_refuses_preview_and_apply(self):
        for files in (
            {f'Video [{A}].html': '<script id="youtube-metadata">{broken</script>'},
            {f'Video [{A}].html': PermissionError('synthetic read denied')},
            {'renamed.html': '<html>No identity</html>'},
        ):
            for apply in (False, True):
                with self.subTest(files=files, apply=apply):
                    status, output, writer = self.run_prune(files, apply)
                    self.assertEqual(status, 2)
                    self.assertIn('Incomplete bookmark inventory', output)
                    writer.assert_not_called()

    def test_inaccessible_subdirectory_refuses_preview_and_apply(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                status, output, writer = self.run_prune(
                    {f'Video [{A}].html': bookmark(A)}, apply, traversal_failure=True)
                self.assertEqual(status, 2)
                self.assertIn('inaccessible', output)
                self.assertIn('synthetic traversal denied', output)
                writer.assert_not_called()

    def test_healthy_inventory_preserves_fallback_and_skips_indexes(self):
        for name, content in ((f'Legacy [{A}].html', '<html>Legacy</html>'),
                              ('renamed.html', bookmark(A))):
            for apply in (False, True):
                with self.subTest(name=name, apply=apply):
                    status, output, writer = self.run_prune({
                        name: content, 'index.html': AssertionError('must not read index'),
                        'INDEX.html': AssertionError('must not read index'),
                        'notes.txt': AssertionError('must not read unrelated file'),
                    }, apply)
                    self.assertEqual(status, 0)
                    if apply:
                        writer.assert_called_once()
                        saved = writer.call_args.args[1]
                        self.assertEqual(saved['bookmarks'], {A: {'tags': ['keep']}})
                    else:
                        writer.assert_not_called()
                        self.assertIn('DRY RUN', output)

    def test_nonprune_inspection_keeps_warning_and_filename_fallback(self):
        paths = [ROOT / f'Legacy [{A}].html', ROOT / 'broken.html']
        with patch.object(tagger, 'scan_bookmarks', return_value=(paths, [], True)), \
                patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
                patch.object(Path, 'read_text', side_effect=['<html>Legacy</html>', ValueError('bad')]), \
                redirect_stderr(io.StringIO()) as warnings:
            ids = tagger.archive_ids(ROOT)
        self.assertEqual(dict(ids), {A: [paths[0]]})
        self.assertIn('WARNING', warnings.getvalue())

    def test_root_traversal_failure_is_not_an_empty_inventory(self):
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR)), \
                patch.object(os, 'scandir', side_effect=PermissionError('cannot enumerate')):
            with self.assertRaisesRegex(ValueError, 'cannot enumerate'):
                tagger.archive_ids(ROOT, strict=True)

    def test_symlink_is_refused_without_following_it(self):
        entry = Mock()
        entry.name = 'external'
        entry.path = str(ROOT / 'bookmarks' / entry.name)
        entry.stat.return_value = SimpleNamespace(st_mode=stat.S_IFLNK)
        context = Mock()
        context.__enter__ = Mock(return_value=iter([entry]))
        context.__exit__ = Mock(return_value=False)
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR)), \
                patch.object(os, 'scandir', return_value=context) as scan:
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                tagger.archive_ids(ROOT, strict=True)
        scan.assert_called_once_with(ROOT / 'bookmarks')
        entry.stat.assert_called_once_with(follow_symlinks=False)

    def test_normal_catalog_write_still_backs_up_before_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            original = b'{"version":1,"bookmarks":{},"collections":{}}\n'
            path.write_bytes(original)
            candidate = {**videx_catalog.empty(), 'extra': 'changed'}
            replace = os.replace
            destinations = []

            def observe(source, destination):
                destinations.append(destination)
                if destination == path:
                    backup, = path.parent.glob('catalog.json.bak-*')
                    self.assertEqual(backup.read_bytes(), original)
                    self.assertEqual(path.read_bytes(), original)
                else:
                    self.assertTrue(source.name.startswith('.catalog-backup-'))
                replace(source, destination)

            stdout = io.StringIO()
            with patch.object(os, 'replace', side_effect=observe), redirect_stdout(stdout):
                publication = tagger.write_catalog(path, candidate)
            self.assertEqual(destinations, [publication.backup, path])
            self.assertEqual(publication.path, path)
            self.assertEqual(json.loads(path.read_bytes()), candidate)
            self.assertEqual(list(path.parent.glob('.catalog-*.tmp')), [])
            self.assertEqual(stdout.getvalue(), '')


class TagArgumentBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='videx-tag-arguments-')
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name).resolve()
        self.collection = self.root / 'bookmarks'
        self.collection.mkdir()
        self.catalog = self.root / 'catalog.json'

    def run_cli(self, operation, *values, apply=False):
        return subprocess.run([sys.executable, '-B', str(SCRIPTS / 'yt-bookmark-tag.py'),
                               *(['--apply'] if apply else []), '--', str(self.root), operation, *values],
                              cwd=self.root, capture_output=True, text=True, timeout=15,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

    def snapshot(self):
        result = {}
        for path in [self.root, *sorted(self.root.rglob('*'))]:
            info = path.lstat()
            content = (os.readlink(path) if path.is_symlink() else
                       path.read_bytes() if path.is_file() else None)
            result[str(path.relative_to(self.root))] = info.st_mode, info.st_mtime_ns, content
        return result

    def test_real_parser_delimits_preview_apply_and_all_tag_operations(self):
        tags = ['Research', '--apply', '--help', '--app', '-topic', '--unknown=value', '--']
        sentinel = self.root / 'catalog.json.bak-existing'
        sentinel.write_bytes(b'existing backup')
        for identity in (A, '-AAAAAAAAAA', '--AAAAAAAAA', '-0AAAAAAAAA', '-1234567890'):
            with self.subTest(identity=identity):
                path = self.collection / f'Video [{identity}].html'
                path.write_text(bookmark(identity))
                path.chmod(0o640)
                if self.catalog.exists():
                    self.catalog.unlink()
                # A missing catalog must remain missing even with a literal --apply.
                before = self.snapshot()
                preview = self.run_cli('add', identity, *tags)
                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertIn('DRY RUN: add', preview.stdout)
                self.assertEqual(self.snapshot(), before)
                created = self.run_cli('add', identity, *tags, apply=True)
                self.assertEqual(created.returncode, 0, created.stderr)
                self.assertEqual(json.loads(self.catalog.read_text())['bookmarks'][identity]['tags'],
                                 sorted(tags, key=str.casefold))
                self.catalog.chmod(0o640)
                operations = [('add', identity, '--new'), ('remove', identity, '--help'),
                              ('set', identity, '--apply'), ('set', identity),
                              ('rename', '--help', '--renamed'), ('list', identity), ('report',)]
                for operation in operations:
                    before = self.snapshot()
                    result = self.run_cli(*operation)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.snapshot(), before)
                listed = self.run_cli('list', identity)
                self.assertEqual(set(listed.stdout.splitlines()), set(tags))
                # Apply remove, rename, and clear through the real nargs='*' parser.
                for operation, expected in (
                        (('remove', identity, '--help'), [t for t in tags if t != '--help']),
                        (('rename', '--app', '--renamed'),
                         [t if t != '--app' else '--renamed' for t in tags if t != '--help']),
                        (('set', identity), [])):
                    previous = self.catalog.read_bytes()
                    backups = set(self.root.glob('catalog.json.bak-*'))
                    result = self.run_cli(*operation, apply=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    saved = json.loads(self.catalog.read_text())['bookmarks'].get(identity, {}).get('tags', [])
                    self.assertEqual(saved, sorted(expected, key=str.casefold))
                    backup, = set(self.root.glob('catalog.json.bak-*')) - backups
                    self.assertEqual(backup.read_bytes(), previous)
                self.assertEqual(path.read_text(), bookmark(identity))
                self.assertEqual(sentinel.read_bytes(), b'existing backup')
                self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])

    def test_delimited_arguments_preserve_catalog_and_identity_refusals(self):
        identity = '-AAAAAAAAAA'
        path = self.collection / f'Video [{identity}].html'
        target = self.root / 'synthetic-target.json'
        target.write_text(json.dumps({'version': 1, 'bookmarks': {}, 'collections': {}}))
        for problem in ('malformed', 'symlink', 'conflict', 'invalid'):
            if self.catalog.exists() or self.catalog.is_symlink():
                self.catalog.unlink()
            path.write_text(bookmark(B if problem == 'conflict' else identity))
            if problem == 'malformed':
                self.catalog.write_text('{broken')
            elif problem == 'symlink':
                self.catalog.symlink_to(target)
            for apply in (False, True):
                with self.subTest(problem=problem, apply=apply):
                    before = self.snapshot()
                    result = self.run_cli('add', '-short' if problem == 'invalid' else identity,
                                          'Research', '--apply', '--help', apply=apply)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_ordinary_cli_keeps_legacy_trailing_apply_option(self):
        (self.collection / f'Video [{A}].html').write_text(bookmark(A))
        command = [sys.executable, '-B', str(SCRIPTS / 'yt-bookmark-tag.py'),
                   str(self.root), 'add', A, 'Python', 'Research']
        before = self.snapshot()
        preview = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=15)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(self.snapshot(), before)
        applied = subprocess.run([*command, '--apply'], cwd=self.root, text=True,
                                 capture_output=True, timeout=15)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(self.catalog.read_text())['bookmarks'][A]['tags'], ['Python', 'Research'])

    def test_duplicate_catalogs_refuse_real_read_only_preview_and_apply_commands(self):
        (self.collection / 'renamed.html').write_text(bookmark(A))
        (self.root / 'catalog.json.bak-existing').write_bytes(b'backup sentinel')
        for number, content in enumerate(duplicate_catalog_documents()):
            self.catalog.write_text(content)
            self.catalog.chmod(0o640)
            before = snapshot(self.root)
            for operation, values, apply in (('list', [A], False), ('report', [], False),
                                             ('add', [A, 'new'], False), ('add', [A, 'new'], True),
                                             ('prune', [], True)):
                with self.subTest(number=number, operation=operation, apply=apply):
                    result = self.run_cli(operation, *values, apply=apply)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('Duplicate catalog object member', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(snapshot(self.root), before)


class TagReportCompletenessTests(unittest.TestCase):
    setUp = TagArgumentBoundaryTests.setUp
    run_cli = TagArgumentBoundaryTests.run_cli

    def seed(self):
        self.good = self.collection / 'renamed.html'
        self.good.write_text(bookmark(A))
        self.catalog.write_text(json.dumps({'version': 1, 'bookmarks': {
            A: {'tags': ['keep', 'hidden']}, B: {'tags': ['unverified']},
            'CCCCCCCCCCC': {'tags': ['orphan']}}, 'collections': {}}))
        self.catalog.chmod(0o640)
        (self.root / 'catalog.json.bak-existing').write_bytes(b'keep backup')

    def invoke(self, operation, *values):
        with patch.object(sys, 'argv', ['tag', str(self.root), operation, *values]), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            status = tagger.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_list_is_catalog_only_even_with_fifo_and_malformed_bookmarks(self):
        self.seed()
        os.mkfifo(self.collection / 'pipe.html')
        (self.collection / 'broken.html').write_text('{broken')
        before = snapshot(self.root)
        with patch.object(tagger, 'inspect_archive', side_effect=AssertionError('must not inspect')), \
                patch.object(tagger, 'scan_bookmarks', side_effect=AssertionError('must not scan')):
            self.assertEqual(self.invoke('list', A), (0, 'keep\nhidden\n', ''))
            self.assertEqual(self.invoke('list', B), (0, 'unverified\n', ''))
        self.assertEqual(snapshot(self.root), before)

    def test_partial_report_uses_unverified_records_and_read_only_totals(self):
        self.seed()
        bad = self.collection / f'Bad [{B}].html'
        bad.write_text('<script id="youtube-metadata">{broken</script>')
        for valid in (True, False):
            if not valid:
                self.good.unlink()
            before = snapshot(self.root)
            result = self.run_cli('report')
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('PARTIAL:', result.stdout)
            self.assertIn(f'Inspected unique IDs (partial): {int(valid)}', result.stdout)
            self.assertIn('Unverified catalog entries', result.stdout)
            self.assertNotIn('Catalog entries without matching bookmarks', result.stdout)
            self.assertIn(B, result.stdout)
            self.assertIn('CCCCCCCCCCC', result.stdout)
            self.assertIn('inventory is incomplete', result.stderr)
            if valid:
                self.assertIn('hidden', result.stdout)
            self.assertEqual(snapshot(self.root), before)
        bad.unlink()
        clean = self.run_cli('report')
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn('Bookmarks: 0', clean.stdout)
        self.assertIn('Catalog entries without matching bookmarks (3)', clean.stdout)

    def test_duplicates_are_reported_separately_from_incomplete_identities(self):
        self.seed()
        (self.collection / f'Legacy [{A}].html').write_text('legacy without metadata')
        before = snapshot(self.root)
        duplicate = self.run_cli('report')
        self.assertEqual(duplicate.returncode, 1, duplicate.stderr)
        self.assertIn(f'DUPLICATE: {A}', duplicate.stdout)
        self.assertIn('Bookmarks: 1', duplicate.stdout)
        self.assertIn('Catalog entries without matching bookmarks (2)', duplicate.stdout)
        self.assertNotIn('PARTIAL:', duplicate.stdout)
        self.assertEqual(snapshot(self.root), before)
        (self.collection / f'Conflict [{B}].html').write_text(bookmark('CCCCCCCCCCC'))
        before = snapshot(self.root)
        conflict = self.run_cli('report')
        self.assertEqual(conflict.returncode, 1, conflict.stderr)
        self.assertIn(f'DUPLICATE: {A}', conflict.stdout)
        self.assertIn('Unverified catalog entries (2)', conflict.stdout)
        self.assertNotIn('Catalog entries without matching bookmarks', conflict.stdout)
        self.assertIn('filename ID=', conflict.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_enumeration_and_read_failures_have_distinct_root_status(self):
        self.seed()
        nested = self.collection / 'nested'
        nested.mkdir()
        real_scan = os.scandir
        for denied in (nested, self.collection):
            def scan(path):
                if Path(path) == denied:
                    raise PermissionError('synthetic enumeration denied')
                return real_scan(path)
            before = snapshot(self.root)
            with patch.object(os, 'scandir', side_effect=scan):
                status, stdout, stderr = self.invoke('report')
            self.assertEqual(status, 2 if denied == self.collection else 1)
            self.assertIn('synthetic enumeration denied', stderr)
            if denied == nested:
                self.assertIn('Unverified catalog entries', stdout)
            else:
                self.assertNotIn('Bookmarks:', stdout)
            self.assertEqual(snapshot(self.root), before)
        with patch.object(tagger, 'bookmark_identity', side_effect=PermissionError('synthetic read denied')):
            status, stdout, stderr = self.invoke('report')
        self.assertEqual(status, 1)
        self.assertIn('Inspected unique IDs (partial): 0', stdout)
        self.assertIn('synthetic read denied', stderr)
        with patch.object(Path, 'resolve', side_effect=PermissionError('synthetic root denied')):
            status, _, stderr = self.invoke('report')
        self.assertEqual(status, 2)
        self.assertIn('synthetic root denied', stderr)

    def test_index_named_directory_is_present_in_reports_and_strict_mutations(self):
        self.seed()
        directory = self.collection / 'index.html'
        directory.mkdir()
        self.good.rename(directory / self.good.name)
        before = snapshot(self.root)
        report = self.run_cli('report')
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn('Bookmarks: 1', report.stdout)
        self.assertIn('Catalog entries without matching bookmarks (2)', report.stdout)
        preview = self.run_cli('add', A, 'new')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn('1 bookmark(s) to change', preview.stdout)
        self.assertEqual(snapshot(self.root), before)


if __name__ == '__main__':
    unittest.main()
