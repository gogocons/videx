"""Lookup and audit regressions confined to disposable synthetic libraries."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import subprocess
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


class TagReportingTests(unittest.TestCase):
    # Reuse only fixture helpers, without rerunning the existing test methods.
    setUp = FindTests.setUp
    add = FindTests.add
    invoke = FindTests.invoke

    def catalog(self, records=None, **changes):
        value = {'version': 1, 'bookmarks': records or {}, 'collections': {}}
        value.update(changes)
        path = self.root / 'catalog.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def report(self, *args):
        status, stdout, stderr = self.invoke(*args, '--show-tags', '--json')
        value = json.loads(stdout)
        self.assertEqual(value['exit_status'], status)
        return status, value, stderr

    def test_unflagged_lookup_and_audit_never_read_catalog(self):
        path = self.add('renamed.html', A)
        self.catalog({A: {'tags': [123]}})
        baseline = self.invoke('--audit')
        with patch.object(finder, 'catalog_tags', side_effect=AssertionError('unrequested catalog read')):
            self.assertEqual(self.invoke('--id', A), (0, str(path) + '\n', ''))
            self.assertEqual(self.invoke('--id', B), (1, '', ''))
            self.assertEqual(self.invoke('--id', 'invalid'), (2, '', ''))
            self.assertEqual(self.invoke('--url', 'https://example.invalid/'), (3, '', ''))
            self.assertEqual(self.invoke('--audit'), baseline)

    def test_lookup_human_and_json_tags_use_existing_normalization(self):
        path = self.add('renamed.html', A)
        self.catalog({A: {'tags': [' Python  ', 'python', 'HIDDEN', 'café "quoted"']}})
        status, report, stderr = self.report('--url', 'https://youtu.be/' + A)
        self.assertEqual((status, stderr), (0, ''))
        self.assertEqual(report['catalog']['status'], 'present')
        self.assertEqual(report['bookmarks'], [{'id': A, 'paths': [str(path)],
                                               'tags': ['Python', 'hidden', 'café "quoted"']}])
        status, stdout, stderr = self.invoke('--id', A, '--show-tags')
        self.assertEqual((status, stderr), (0, ''))
        self.assertIn('Bookmark: ' + str(path), stdout)
        self.assertIn('Tags: ["Python", "hidden", "café \\"quoted\\""]', stdout)

    def test_missing_catalog_and_missing_tag_records_are_empty_not_unavailable(self):
        self.add(f'Legacy [{A}].html', content='<html>legacy</html>')
        status, report, _ = self.report('--id', A)
        self.assertEqual(status, 0)
        self.assertEqual(report['catalog']['status'], 'missing')
        self.assertEqual(report['bookmarks'][0]['tags'], [])
        self.assertFalse((self.root / 'catalog.json').exists())
        for record in ({}, {A: {}}, {A: {'tags': []}}):
            with self.subTest(record=record):
                self.catalog(record)
                status, report, _ = self.report('--id', A)
                self.assertEqual(status, 0)
                self.assertEqual(report['bookmarks'][0]['tags'], [])

    def test_catalog_errors_are_single_json_with_null_tags(self):
        self.add('renamed.html', A)
        invalid = ['{broken', '[]', json.dumps({'version': 2, 'bookmarks': {}, 'collections': {}}),
                   json.dumps({'version': 1, 'bookmarks': {}, 'collections': []})]
        invalid += [json.dumps({'version': 1, 'bookmarks': {A: {'tags': tags}}, 'collections': {}})
                    for tags in ('text', [1], [None], [{}], [''], ['x' * 81], ['escape\x1b'])]
        path = self.root / 'catalog.json'
        for content in invalid:
            path.write_text(content)
            for mode in (('--id', A), ('--audit',)):
                with self.subTest(content=content, mode=mode):
                    status, report, stderr = self.report(*mode)
                    self.assertEqual((status, stderr), (2, ''))
                    self.assertEqual(report['catalog']['status'], 'unavailable')
                    self.assertIsNone(report['bookmarks'][0]['tags'])
                    self.assertTrue(any(issue['category'] == 'CATALOG_ERROR' for issue in report['issues']))
                    self.assertEqual(path.read_text(), content)

    def test_audit_keeps_identity_findings_when_catalog_is_bad(self):
        self.add('valid.html', A)
        self.add(f'Conflict [{A}].html', B)
        path = self.root / 'catalog.json'
        path.write_text('{broken')
        status, report, _ = self.report('--audit')
        self.assertEqual(status, 2)
        self.assertEqual(report['summary']['mismatches'], 1)
        self.assertEqual({issue['category'] for issue in report['issues']}, {'MISMATCH', 'CATALOG_ERROR'})
        self.assertEqual(len(report['bookmarks']), 1)
        self.assertIsNone(report['bookmarks'][0]['tags'])
        status, stdout, stderr = self.invoke('--audit', '--show-tags')
        self.assertEqual(status, 2)
        self.assertIn('Mismatches: 1', stdout)
        self.assertIn('Tags: unavailable', stdout)
        self.assertIn('Cannot report personal tags', stderr)
        status, stdout, stderr = self.invoke('--id', A, '--show-tags')
        self.assertEqual((status, stdout), (2, ''))

    def test_human_lookup_catalog_error_has_no_selected_path_or_traceback(self):
        self.add('renamed.html', A)
        (self.root / 'catalog.json').write_text('{broken')
        status, stdout, stderr = self.invoke('--id', A, '--show-tags')
        self.assertEqual((status, stdout), (2, ''))
        self.assertIn('Cannot report personal tags', stderr)
        self.assertNotIn('Traceback', stderr)

    def test_lookup_failures_never_read_catalog(self):
        with patch.object(finder, 'catalog_tags', side_effect=AssertionError('must not read catalog')):
            for mode, expected in ((('--id', A), 1), (('--id', 'invalid'), 2),
                                   (('--url', 'https://example.invalid/'), 3)):
                status, report, _ = self.report(*mode)
                self.assertEqual(status, expected)
                self.assertEqual(report['bookmarks'], [])
                self.assertEqual(report['catalog']['status'], 'not_read')
            self.add(f'Conflict [{A}].html', B)
            status, report, _ = self.report('--id', A)
            self.assertEqual(status, 2)
            self.assertEqual(report['bookmarks'], [])
            self.assertEqual(report['issues'][0]['filename_id'], A)

    def test_audit_duplicates_are_one_tag_record_and_lookup_is_still_ambiguous(self):
        first = self.add(f'Legacy [{A}].html', content='legacy')
        second = self.add('renamed.html', A)
        self.catalog({A: {'tags': ['topic']}})
        status, report, _ = self.report('--audit')
        self.assertEqual(status, 1)
        self.assertEqual(report['summary']['duplicate_groups'], 1)
        self.assertEqual(report['bookmarks'], [{'id': A, 'paths': sorted([str(first), str(second)]), 'tags': ['topic']}])
        self.assertEqual(self.report('--audit')[1], report)
        with patch.object(finder, 'catalog_tags', side_effect=AssertionError('ambiguous lookup')):
            status, report, _ = self.report('--id', A)
        self.assertEqual(status, 2)
        self.assertEqual(report['bookmarks'], [])

    def test_catalog_symlink_and_directory_are_refused_without_reading(self):
        self.add('renamed.html', A)
        target = self.root / 'sentinel'
        target.write_text('unchanged')
        path = self.root / 'catalog.json'
        path.symlink_to(target)
        with patch.object(Path, 'read_text', autospec=True) as read:
            with self.assertRaisesRegex(ValueError, 'regular file'):
                finder.catalog_tags(self.root)
            read.assert_not_called()
        self.assertEqual(self.report('--id', A)[0], 2)
        self.assertEqual(target.read_text(), 'unchanged')
        path.unlink()
        path.mkdir()
        self.assertEqual(self.report('--id', A)[0], 2)

    def test_unreadable_catalog_returns_json_error(self):
        self.add('renamed.html', A)
        catalog = self.catalog({A: {'tags': ['tag']}})
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == catalog:
                raise PermissionError('synthetic permission denied')
            return read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            status, report, _ = self.report('--id', A)
        self.assertEqual(status, 2)
        self.assertIsNone(report['bookmarks'][0]['tags'])
        self.assertIn('synthetic permission denied', report['issues'][0]['message'])

    def test_reporting_does_not_write_library_or_backups(self):
        self.add('renamed.html', A)
        self.catalog({A: {'tags': ['topic']}})
        before = {str(p): (p.read_bytes(), p.stat().st_mode) for p in self.root.rglob('*') if p.is_file()}
        for args in (('--id', A), ('--audit',)):
            self.assertEqual(self.report(*args)[0], 0)
            self.assertEqual(self.invoke(*args, '--show-tags')[0], 0)
        after = {str(p): (p.read_bytes(), p.stat().st_mode) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after, before)

    def test_explicit_library_and_configured_default_select_the_same_catalog(self):
        self.add('renamed.html', A)
        self.catalog({A: {'tags': ['selected library']}})
        self.assertEqual(self.report('--id', A)[1]['bookmarks'][0]['tags'], ['selected library'])
        config = self.root / 'synthetic-config.json'
        config.write_text(json.dumps({'library': str(self.root)}))
        with patch.object(sys.modules['videx_config'], 'CONFIG_PATH', config), \
                patch.object(sys, 'argv', ['find', '--id', A, '--show-tags', '--json']), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(finder.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())['bookmarks'][0]['tags'], ['selected library'])

    def test_json_requires_show_tags(self):
        with redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as caught:
                self.invoke('--id', A, '--json')
        self.assertEqual(caught.exception.code, 2)

    def test_existing_tag_report_command_remains_available(self):
        self.add('renamed.html', A)
        self.catalog({A: {'tags': ['topic']}})
        result = subprocess.run([sys.executable, '-B', str(PROJECT / 'scripts' / 'yt-bookmark-tag.py'),
                                 str(self.root), 'report'], cwd=self.root, capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Videx tag report', result.stdout)
        self.assertIn('topic', result.stdout)

    def test_unavailable_collection_and_invalid_config_still_emit_json(self):
        with patch.object(finder, 'inventory', return_value=([], {}, [], False)):
            self.assertEqual(self.report('--audit')[0], 2)
        with patch.object(finder, 'default_library', side_effect=ValueError('synthetic bad config')), \
                patch.object(sys, 'argv', ['find', '--audit', '--show-tags', '--json']), \
                redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(finder.main(), 2)
        self.assertEqual(json.loads(stdout.getvalue())['issues'][0]['category'], 'CONFIG_ERROR')


if __name__ == '__main__':
    unittest.main()
