"""Offline Collection CLI and catalog publication regressions."""

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
from uuid import UUID

from test_bookmark_tag import A, B, SCRIPTS, bookmark, videx_catalog
from test_videx_inventory import snapshot


C = '-CCCCCCCCCC'
MISSING = 'MMMMMMMMMMM'
SAVED_ID = '5bfa4a67-8796-45f6-96c3-b5deec914d16'
OTHER_ID = '6e7ba1a2-6982-4d59-a145-914a3d268108'
VIDEX = SCRIPTS.parent / 'videx'
with patch.object(sys, 'path', [str(SCRIPTS)] + sys.path):
    spec = spec_from_file_location('bookmark_collection', SCRIPTS / 'yt-bookmark-collection.py')
    cli = module_from_spec(spec)
    spec.loader.exec_module(cli)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name).resolve()
        self.root.joinpath('bookmarks').mkdir()
        self.catalog = self.root / 'catalog.json'

    def video(self, identity, name=None, text=None):
        path = self.root / 'bookmarks' / (name or f'{identity}.html')
        path.write_text(bookmark(identity) if text is None else text, encoding='utf-8')
        return path

    def saved(self):
        return json.loads(self.catalog.read_bytes())

    def seed(self, members=(), **fields):
        catalog = videx_catalog.empty()
        catalog['collections'][SAVED_ID] = {'name': 'Saved', 'members': list(members), **fields}
        self.catalog.write_text(json.dumps(catalog), encoding='utf-8')
        return catalog

    def invoke(self, operation, *args, status=0, human=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        options = [] if human else ['--json']
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(
                cli, 'default_library', side_effect=AssertionError('explicit library must bypass config')):
            actual = cli.main([operation, '--library', str(self.root), *options, *args])
        self.assertEqual(actual, status, stdout.getvalue() + stderr.getvalue())
        if human:
            return stdout.getvalue()
        self.assertEqual(stderr.getvalue(), '')
        report = json.loads(stdout.getvalue())
        self.assertEqual(report['report_version'], 1)
        self.assertEqual(report['exit_status'], status)
        self.assertEqual(report['operation'], operation)
        self.assertEqual(report['library'], str(self.root))
        return report

    def dispatch(self, *args):
        result = subprocess.run([VIDEX, *args], cwd=self.root, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_empty_library_catalog_compatibility_and_create_preview(self):
        before = snapshot(self.root)
        report = self.invoke('list')
        self.assertEqual(report['catalog']['status'], 'missing')
        self.assertEqual(report['collections'], [])
        with patch.object(cli.uuid, 'uuid4', side_effect=AssertionError('preview has no UUID')):
            report = self.invoke('create', 'Empty')
        self.assertEqual(report['result'], 'would_change')
        self.assertEqual(report['after'], {'id': None, 'name': 'Empty', 'members': []})
        self.assertEqual(snapshot(self.root), before)
        self.catalog.write_text(json.dumps(videx_catalog.empty()), encoding='utf-8')
        self.assertEqual(self.invoke('list')['catalog']['status'], 'present')
        self.catalog.write_text('{"version":1,"bookmarks":{}}', encoding='utf-8')
        self.assertEqual(self.invoke('list', status=2)['catalog']['status'], 'unavailable')
        self.catalog.unlink()
        report = self.invoke('create', 'Empty', '--apply')
        self.assertIsNone(report['catalog']['backup'])
        self.assertEqual(report['catalog']['publication'], 'published')
        self.assertEqual(UUID(report['after']['id']).version, 4)
        self.assertEqual(self.saved()['version'], 1)

    def test_edit_lifecycle_unicode_and_unknown_fields(self):
        report = self.invoke('create', '--apply', '--description', '  first\r\nsecond\tline\r ',
                             '  Cafe\u0301  Notes ')
        identity = report['after']['id']
        self.assertEqual(report['after']['name'], 'Café  Notes')
        self.assertEqual(report['after']['description'], 'first\nsecond\tline')
        self.invoke('create', '--apply', 'CAFÉ  NOTES', status=2)
        self.invoke('create', '--apply', 'id:favorites')
        catalog = self.saved()
        catalog['collections'][identity]['future'] = {'X': [1, 2]}
        catalog['bookmarks'][A] = {'tags': ['keep'], 'notes': 'keep'}
        catalog['extra'] = {'keep': True}
        self.catalog.write_text(json.dumps(catalog), encoding='utf-8')
        original = self.catalog.read_bytes()
        report = self.invoke('rename', '--apply', 'CAFE\u0301  NOTES', 'CAFÉ  NOTES')
        self.assertEqual(report['before']['id'], report['after']['id'])
        self.assertEqual(Path(report['catalog']['backup']).read_bytes(), original)
        self.invoke('rename', '--apply', f'id:{identity}', 'ID:FAVORITES', status=2)
        self.invoke('describe', '--apply', f'id:{identity}', '')
        self.assertEqual(self.saved()['collections'][identity], {
            'name': 'CAFÉ  NOTES', 'members': [], 'future': {'X': [1, 2]},
        })
        record = self.invoke('show', 'name:id:favorites')['collection']
        self.assertEqual(record['name'], 'id:favorites')
        self.assertEqual([entry['name'] for entry in self.invoke('list')['collections']],
                         ['CAFÉ  NOTES', 'id:favorites'])
        preview = self.invoke('delete', f'id:{identity}', human=True)
        self.assertIn('DRY RUN: delete', preview)
        self.assertIn(str(self.root), preview)
        self.assertIn(identity, preview)
        self.assertIn('Members:', preview)
        self.invoke('delete', '--apply', f'id:{identity}')
        expected = catalog
        del expected['collections'][identity]
        self.assertEqual(self.saved(), expected)
        self.invoke('delete', '--apply', f'id:{identity}', status=2)
        self.invoke('show', f'id:{identity}', status=1)

    def test_order_shared_members_hidden_and_literal_arguments(self):
        self.seed()
        self.video(A)
        self.video(A.lower(), 'Case-distinct identity.html')
        self.video(B, 'Renamed bookmark.html')
        self.video(C, f'Legacy [{C}].html', '<p>old bookmark</p>')
        self.dispatch('tag', '--apply', '--', str(self.root), 'add', A, 'hidden')
        self.invoke('create', '--apply', '--', '--apply')
        original = snapshot(self.root)
        self.assertEqual(self.invoke('add', '--', '--apply', C, B, C)['after']['members'], [C, B])
        self.assertEqual(snapshot(self.root), original)
        self.invoke('add', '--apply', '--', '--apply', C, B, C, A.lower(), A)
        self.invoke('add', '--apply', 'Saved', A)
        findings = self.invoke('show', '--', '--apply')['member_findings']
        self.assertEqual([(row['id'], row['status']) for row in findings],
                         [(C, 'present'), (B, 'present'), (A.lower(), 'present'), (A, 'present')])
        self.assertEqual(findings[1]['paths'], [str(self.root / 'bookmarks' / 'Renamed bookmark.html')])
        self.invoke('remove', '--apply', '--', '--apply', B, A.lower())
        stored = self.invoke('show', '--', '--apply')['collection']['members']
        self.assertEqual(stored, [C, A])
        self.invoke('delete', '--apply', '--', '--apply')
        self.assertEqual(self.invoke('show', 'Saved')['collection']['members'], [A])
        self.assertEqual(self.saved()['bookmarks'][A]['tags'], ['hidden'])
        self.assertEqual({key: value for key, value in snapshot(self.root).items() if key.startswith('bookmarks')},
                         {key: value for key, value in original.items() if key.startswith('bookmarks')})

    def test_stale_members_restore_and_survive_tag_pruning(self):
        index = self.root / 'index.html'

        def indexed_members():
            self.dispatch('index', str(self.root))
            html = index.read_text(encoding='utf-8')
            payload = html.split('<script id="bookmark-data" type="application/json">', 1)[1]
            return json.loads(payload.split('</script>', 1)[0])['collections']

        catalog = self.seed([A, B])
        catalog['bookmarks'] = {A: {'tags': ['keep']}, B: {'tags': ['old']}}
        self.catalog.write_text(json.dumps(catalog), encoding='utf-8')
        self.video(A)
        findings = self.invoke('show', 'Saved')['member_findings']
        self.assertEqual([item['status'] for item in findings], ['present', 'missing'])
        self.dispatch('tag', str(self.root), 'prune', '--apply')
        self.assertEqual(self.saved()['collections'], catalog['collections'])
        self.assertNotIn(B, self.saved()['bookmarks'])
        self.assertEqual(indexed_members(), [{'id': SAVED_ID, 'name': 'Saved', 'members': [A]}])
        path = self.video(B, 'Restored under another name.html')
        self.assertEqual(self.invoke('show', 'Saved')['member_findings'][1]['paths'], [str(path)])
        self.assertEqual(indexed_members(), [{'id': SAVED_ID, 'name': 'Saved', 'members': [A, B]}])
        before = index.read_bytes()
        path.unlink()
        self.invoke('remove', '--apply', 'Saved', B)
        self.assertEqual(self.saved()['collections'][SAVED_ID]['members'], [A])
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(indexed_members(), [{'id': SAVED_ID, 'name': 'Saved', 'members': [A]}])
        output = self.dispatch('find', str(self.root), '--id', A, '--show-tags', '--json')
        self.assertEqual(json.loads(output)['bookmarks'][0]['tags'], ['keep'])

    def test_no_ops_and_mixed_invalid_members_retain_live_files(self):
        self.seed([A], description='same')
        self.video(A)
        self.video(B)
        before = snapshot(self.root)
        for operation, values in (
                ('add', ['Saved', A, A]), ('remove', ['Saved', MISSING]),
                ('rename', ['Saved', 'Saved']), ('describe', ['Saved', ' same '])):
            with self.subTest(operation=operation):
                for apply in ([], ['--apply']):
                    self.assertEqual(self.invoke(operation, *apply, *values)['result'], 'no_change')
        for operation, values in (
                ('add', ['Saved', B, MISSING]), ('add', ['Saved', B, 'bad']),
                ('remove', ['Saved', A, 'bad'])):
            with self.subTest(operation=operation, values=values):
                for apply in ([], ['--apply']):
                    self.invoke(operation, *apply, *values, status=2)
        self.assertEqual(snapshot(self.root), before)

    def test_ambiguous_partial_and_unavailable_inventories(self):
        self.seed([A, B, MISSING])
        self.video(A)
        self.video(B)
        duplicate = self.video(A, 'duplicate.html')
        report = self.invoke('show', 'Saved', status=1)
        self.assertEqual([item['status'] for item in report['member_findings']],
                         ['ambiguous', 'present', 'missing'])
        bad = self.video(A, 'broken.html', '<script id="youtube-metadata">{</script>')
        before = snapshot(self.root)
        report = self.invoke('show', 'Saved', status=1)
        self.assertEqual([item['status'] for item in report['member_findings']],
                         ['ambiguous', 'unverified', 'unverified'])
        self.assertEqual(self.invoke('list')['collections'][0]['member_count'], 3)
        for operation, args in (('rename', ['Saved', 'Saved']), ('delete', ['Saved']),
                                ('create', ['New']), ('add', ['Saved', A]), ('remove', ['Saved', MISSING])):
            with self.subTest(operation=operation):
                for apply in ([], ['--apply']):
                    report = self.invoke(operation, *apply, *args, status=2)
                    self.assertEqual(report['issues'][0]['category'], 'INVENTORY_ERROR')
        self.assertEqual(snapshot(self.root), before)
        duplicate.unlink()
        bad.unlink()
        with patch.object(os, 'scandir', side_effect=PermissionError('synthetic denial')):
            self.invoke('show', 'Saved', status=2)
            self.invoke('describe', '--apply', 'Saved', 'new', status=2)
        (self.root / 'bookmarks').rename(self.root / 'bookmark-target')
        (self.root / 'bookmarks').symlink_to(self.root / 'bookmark-target')
        self.assertEqual(self.invoke('list', status=2)['issues'][0]['category'], 'LIBRARY_ERROR')

    def test_malformed_collections_and_tag_only_opaque_compatibility(self):
        valid = {'name': 'Saved', 'members': []}
        for structure in (
            {'opaque': [A]}, {SAVED_ID.upper(): valid}, {SAVED_ID: []},
            {SAVED_ID: {'members': []}}, {SAVED_ID: {'name': 'Saved'}},
            {SAVED_ID: {**valid, 'name': ' untrimmed '}}, {SAVED_ID: {**valid, 'name': 'Cafe\u0301'}},
            {SAVED_ID: {**valid, 'name': 'a\u2028b'}}, {SAVED_ID: {**valid, 'name': '\ud800'}},
            {SAVED_ID: {**valid, 'description': ' '}}, {SAVED_ID: {**valid, 'description': None}},
            {SAVED_ID: {**valid, 'description': 'one\rtwo'}},
            {SAVED_ID: {**valid, 'members': [A, A]}}, {SAVED_ID: {**valid, 'members': [1]}},
            {SAVED_ID: valid, OTHER_ID: {'name': 'SAVED', 'members': []}},
        ):
            with self.subTest(structure=structure):
                catalog = {**videx_catalog.empty(), 'collections': structure}
                self.catalog.write_text(json.dumps(catalog), encoding='utf-8')
                before = snapshot(self.root)
                self.assertEqual(self.invoke('list', status=2)['catalog']['status'], 'unavailable')
                self.invoke('create', '--apply', 'New', status=2)
                self.assertEqual(snapshot(self.root), before)
        duplicate = '{"version":1,"bookmarks":{},"collections":{"opaque":{"x":1,"x":2}}}'
        self.catalog.write_text(duplicate, encoding='utf-8')
        issue, = self.invoke('list', status=2)['issues']
        self.assertIn('Duplicate catalog object member', issue['message'])
        self.video(A)
        catalog['collections'] = {'opaque': {'entries': [A]}}
        self.catalog.write_text(json.dumps(catalog), encoding='utf-8')
        self.dispatch('tag', str(self.root), 'add', A, 'personal', '--apply')
        self.assertEqual(self.saved()['collections'], catalog['collections'])
        self.invoke('show', 'Saved', status=2)

    def test_apply_reports_publication_failures_and_completed_backup(self):
        self.seed()
        original = self.catalog.read_bytes()
        replace = os.replace

        def break_copy(source, staged):
            staged.write_bytes(b'partial backup')
            raise OSError('backup copy failed')

        def break_live(source, destination):
            if destination == self.catalog:
                raise OSError('replace failed')
            replace(source, destination)

        for module, target, effect in (
                (videx_catalog.shutil, 'copy2', break_copy),
                (os, 'fsync', OSError('sync failed')),
                (os, 'replace', OSError('backup publication failed'))):
            with self.subTest(target=target), patch.object(module, target, side_effect=effect):
                report = self.invoke('rename', '--apply', 'Saved', 'New', status=2)
                self.assertEqual(report['issues'][0]['category'], 'PUBLICATION_ERROR')
                self.assertEqual(report['catalog']['publication'], 'not_published')
                self.assertIsNone(report['catalog']['backup'])
                self.assertEqual(self.catalog.read_bytes(), original)
                self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])
                self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])
        with patch.object(os, 'replace', side_effect=break_live):
            report = self.invoke('rename', '--apply', 'Saved', 'New', status=2)
        self.assertEqual(Path(report['catalog']['backup']).read_bytes(), original)
        self.assertEqual(self.catalog.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])

    def test_encoding_failure_preserves_catalog_and_backups(self):
        self.seed(extra='\ud800')
        original = self.catalog.read_bytes()
        report = self.invoke('rename', '--apply', 'Saved', 'New', status=2)
        self.assertEqual(report['issues'][0]['category'], 'PUBLICATION_ERROR')
        self.assertEqual(self.catalog.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])
        self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])

    def test_interrupt_at_replace_reports_uncertainty_and_completed_backup(self):
        self.seed()
        replace = os.replace
        original = self.catalog.read_bytes()

        def interrupt_after_replace(source, destination):
            replace(source, destination)
            if destination == self.catalog:
                raise KeyboardInterrupt

        with patch.object(os, 'replace', side_effect=interrupt_after_replace):
            report = self.invoke('rename', '--apply', 'Saved', 'New', status=130)
        self.assertEqual(report['result'], 'interrupted')
        self.assertEqual(report['catalog']['publication'], 'unknown')
        self.assertIn('may have completed', report['issues'][0]['message'])
        self.assertEqual(Path(report['catalog']['backup']).read_bytes(), original)
        self.assertEqual(self.saved()['collections'][SAVED_ID]['name'], 'New')
        self.assertEqual(list(self.root.glob('.catalog-*.tmp')), [])

    def test_dispatch_help_and_json_stdout(self):
        self.assertIn('create', self.dispatch('collection', '--help'))
        self.assertIn('--description', self.dispatch('collection', 'create', '--help'))
        output = self.dispatch('collection', 'create', '--library', str(self.root), '--json', '--apply', 'Saved')
        self.assertEqual(json.loads(output)['result'], 'applied')
        output = self.dispatch('collection', 'describe', '--library', str(self.root), '--json',
                               '--apply', 'Saved', 'A new description')
        self.assertTrue(json.loads(output)['catalog']['backup'])

    def test_configured_default_and_config_error_report(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(cli, 'default_library', return_value=self.root):
            self.assertEqual(cli.main(['list', '--json']), 0)
        self.assertEqual(json.loads(output.getvalue())['library'], str(self.root))
        output = io.StringIO()
        with redirect_stdout(output), patch.object(cli, 'default_library', side_effect=ValueError('bad config')):
            self.assertEqual(cli.main(['list', '--json']), 2)
        report = json.loads(output.getvalue())
        self.assertIsNone(report['library'])
        self.assertEqual(report['catalog']['status'], 'not_read')
        self.assertEqual(report['issues'][0]['category'], 'CONFIG_ERROR')


if __name__ == '__main__':
    unittest.main()
