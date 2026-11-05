"""Lookup and audit regressions confined to disposable synthetic libraries."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(PROJECT / 'scripts')] + sys.path):
    spec = spec_from_file_location('bookmark_find', PROJECT / 'scripts' / 'yt-bookmark-find.py')
    finder = module_from_spec(spec)
    spec.loader.exec_module(finder)

A, B, C = 'AAAAAAAAAAA', 'BBBBBBBBBBB', 'CCCCCCCCCCC'


class FindTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='.identity-test-', dir=PROJECT)
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.collection = self.root / 'bookmarks'
        self.collection.mkdir()

    def add(self, name, identity=None, content=None):
        path = self.collection / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            content = '<script id="youtube-metadata">' + json.dumps({'id': identity}) + '</script>'
        path.write_text(content, encoding='utf-8')
        return path

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['find', str(self.root), *args]), \
                patch.object(finder, 'default_library', side_effect=AssertionError('must use fixture')), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = finder.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_legacy_renamed_and_normal_lookup(self):
        for name, content in (
            (f'Channel/Legacy [{A}].html', '<html>legacy</html>'),
            ('Channel/renamed.html', None),
            (f'Channel/Normal [{A}].html', None),
        ):
            with self.subTest(name=name):
                path = self.add(name, A, content)
                self.assertEqual(self.invoke('--id', A), (0, str(path) + '\n', ''))
                path.unlink()  # Only this test's disposable fixture.

    def test_empty_inventory_and_url_statuses(self):
        self.assertEqual(self.invoke('--id', A), (1, '', ''))
        self.assertEqual(self.invoke('--url', 'https://youtu.be/' + A), (1, '', ''))
        self.assertEqual(self.invoke('--url', 'https://example.invalid/video'), (3, '', ''))
        self.assertEqual(self.invoke('--id', 'invalid'), (2, '', ''))
        self.assertEqual(self.invoke('--audit')[0], 0)

    def test_conflict_blocks_both_ids_and_unrelated_lookup(self):
        path = self.add(f'Conflict [{A}].html', B)
        original = path.read_bytes()
        for identity in (A, B, C):
            status, stdout, stderr = self.invoke('--id', identity)
            self.assertEqual((status, stdout), (2, ''))
            self.assertIn('MISMATCH', stderr)
            self.assertIn('Filename ID: ' + A, stderr)
            self.assertIn('Metadata ID: ' + B, stderr)
        self.assertEqual(path.read_bytes(), original)

    def test_duplicates_include_legacy_and_renamed_bookmarks(self):
        first = self.add(f'Legacy [{A}].html', content='legacy')
        second = self.add('renamed.html', A)
        status, stdout, stderr = self.invoke('--id', A)
        self.assertEqual((status, stdout), (2, ''))
        self.assertIn(str(first), stderr)
        self.assertIn(str(second), stderr)
        status, stdout, _ = self.invoke('--audit')
        self.assertEqual(status, 1)
        self.assertIn('Duplicate groups: 1', stdout)

    def test_invalid_and_unknown_files_block_even_when_match_exists(self):
        self.add('valid.html', A)
        for content in ('<script id="youtube-metadata">{broken</script>', '<html>unknown</html>'):
            damaged = self.add('damaged.html', content=content)
            status, stdout, stderr = self.invoke('--id', A)
            self.assertEqual((status, stdout), (2, ''))
            self.assertIn('damaged.html', stderr)
            damaged.unlink()

    def test_read_failure_blocks_lookup_and_is_reported_by_audit(self):
        path = self.add(f'Video [{A}].html', A)
        with patch.object(Path, 'read_text', side_effect=PermissionError('synthetic read denied')):
            status, stdout, stderr = self.invoke('--id', A)
            self.assertEqual((status, stdout), (2, ''))
            self.assertIn(str(path), stderr)
            self.assertIn('synthetic read denied', stderr)
            self.assertEqual(self.invoke('--audit')[0], 1)

    def test_nested_traversal_and_root_enumeration_failures(self):
        denied = self.collection / 'denied'
        denied.mkdir()
        self.add('valid.html', A)
        scandir = finder.os.scandir
        for inaccessible in (denied, self.collection):
            def failing_scan(path):
                if Path(path) == inaccessible:
                    raise PermissionError('synthetic traversal denied')
                return scandir(path)
            with self.subTest(path=inaccessible), patch.object(finder.os, 'scandir', side_effect=failing_scan):
                status, stdout, stderr = self.invoke('--id', A)
                self.assertEqual((status, stdout), (2, ''))
                self.assertIn('synthetic traversal denied', stderr)
                self.assertEqual(self.invoke('--audit')[0], 2 if inaccessible == self.collection else 1)

    def test_missing_collection_returns_error_not_absence(self):
        with patch.object(Path, 'lstat', side_effect=FileNotFoundError('missing')):
            self.assertEqual(self.invoke('--id', A)[0], 2)
            self.assertEqual(self.invoke('--audit')[0], 2)

    def test_symlink_is_not_followed(self):
        target = self.root / 'outside.html'
        target.write_text('do not inspect', encoding='utf-8')
        (self.collection / 'linked.html').symlink_to(target)
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read target')):
            self.assertEqual(self.invoke('--id', A)[0], 2)
        self.assertEqual(target.read_text(), 'do not inspect')

    def test_audit_keeps_conflicts_separate_from_resolved_groups(self):
        self.add(f'Conflict [{A}].html', B)
        self.add('good.html', B)
        status, stdout, stderr = self.invoke('--audit')
        self.assertEqual((status, stderr), (1, ''))
        self.assertIn('Mismatches: 1', stdout)
        self.assertIn('Unique video IDs: 1', stdout)
        self.assertIn('Duplicate groups: 0', stdout)

    def test_only_collection_html_is_inspected_and_indexes_are_excluded(self):
        self.add('good.html', A)
        for name in ('index.html', 'INDEX.html', 'notes.txt'):
            self.add(name, content='not a bookmark')
        (self.root / 'unrelated.html').write_text('outside collection', encoding='utf-8')
        self.assertEqual(self.invoke('--id', A)[0], 0)
        self.assertEqual(self.invoke('--audit')[0], 0)


if __name__ == '__main__':
    unittest.main()
