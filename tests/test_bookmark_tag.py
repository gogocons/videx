"""Offline prune regressions using an entirely synthetic filesystem."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import stat
import sys
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
