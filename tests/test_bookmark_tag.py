"""Offline tag and prune regressions using synthetic filesystems."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
with patch.object(sys, 'path', [str(SCRIPTS)] + sys.path):
    spec = spec_from_file_location('bookmark_tag', SCRIPTS / 'yt-bookmark-tag.py')
    tagger = module_from_spec(spec)
    spec.loader.exec_module(tagger)

ROOT = Path('/synthetic-videx')
A, B = 'AAAAAAAAAAA', 'BBBBBBBBBBB'


def bookmark(video_id):
    return '<script id="youtube-metadata">' + json.dumps({'id': video_id}) + '</script>'


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
                        patch.object(tagger.tempfile, 'NamedTemporaryFile') as staging:
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
            stack.enter_context(patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG)))
            stack.enter_context(patch.object(Path, 'read_text', read_text))
            stack.enter_context(patch.object(tagger.os, 'scandir', side_effect=scandir))
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
        with patch.object(Path, 'rglob', return_value=iter(paths)), \
                patch.object(Path, 'read_text', side_effect=['<html>Legacy</html>', ValueError('bad')]), \
                redirect_stderr(io.StringIO()) as warnings:
            ids = tagger.archive_ids(ROOT)
        self.assertEqual(dict(ids), {A: [paths[0]]})
        self.assertIn('WARNING', warnings.getvalue())

    def test_root_traversal_failure_is_not_an_empty_inventory(self):
        with patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(tagger.os, 'scandir', side_effect=PermissionError('cannot enumerate')):
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
        with patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(tagger.os, 'scandir', return_value=context) as scan:
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                tagger.archive_ids(ROOT, strict=True)
        scan.assert_called_once_with(ROOT / 'bookmarks')
        entry.stat.assert_called_once_with(follow_symlinks=False)

    def test_normal_catalog_write_still_backs_up_before_replace(self):
        stream = io.StringIO()
        stream.name = str(ROOT / '.catalog-synthetic.tmp')
        context = Mock()
        context.__enter__ = Mock(return_value=stream)
        context.__exit__ = Mock(return_value=False)
        operations = Mock()
        with patch.object(Path, 'mkdir'), patch.object(Path, 'exists', side_effect=[True, False]), \
                patch.object(tagger.tempfile, 'NamedTemporaryFile', return_value=context), \
                patch.object(tagger.os, 'fsync'), patch.object(stream, 'fileno', return_value=123), \
                patch.object(tagger.shutil, 'copy2', operations.backup), \
                patch.object(tagger.os, 'replace', operations.replace), redirect_stdout(io.StringIO()):
            tagger.write_catalog(ROOT / 'catalog.json', tagger.empty())
        self.assertEqual([call[0] for call in operations.mock_calls], ['backup', 'replace'])
        self.assertEqual(operations.backup.call_args.args[0], ROOT / 'catalog.json')
        self.assertEqual(operations.replace.call_args.args[1], ROOT / 'catalog.json')


if __name__ == '__main__':
    unittest.main()
