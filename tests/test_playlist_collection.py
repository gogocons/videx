"""Offline regressions for the playlist report to Collection handoff."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_playlist_import as playlist
from test_bookmark_collection import cli
from test_bookmark_index import IndexDocumentParser


batch = playlist.batch
importer = playlist.importer
A, B, C = playlist.A, playlist.B, playlist.C
VIDEX = batch.PROJECT / 'videx'


class PlaylistCollectionTests(unittest.TestCase):
    def setUp(self):
        self.playlist = playlist.PlaylistTests()
        self.playlist.setUp()
        self.addCleanup(self.playlist.doCleanups)
        self.fixture = self.playlist.fixture
        self.library = self.fixture.library
        self.catalog = self.library / 'catalog.json'
        self.index = self.library / 'index.html'
        self.report_file = self.fixture.root / 'playlist-import.json'

    def save_entries(self, *entries):
        self.playlist.document.update(entries=list(entries), playlist_count=len(entries))
        self.playlist.save_document()

    def capture(self, *, status=0, **environment):
        actual, report = self.playlist.run_playlist(
            '--propose-collection', '--apply', '--no-re-index', **environment)
        self.assertEqual(actual, status, report)
        self.report_file.write_text(json.dumps(report), encoding='utf-8')
        return report

    def collection(self, *options, status=0, library=None, text=None, human=False):
        output, errors = io.StringIO(), io.StringIO()
        args = ['from-import', str(self.report_file) if text is None else '-']
        args += ['--library', str(self.library if library is None else library)]
        args += ([] if human else ['--json']) + list(options)
        with redirect_stdout(output), redirect_stderr(errors), patch.object(
                sys, 'stdin', io.StringIO(text or '')), patch.object(
                cli, 'default_library', side_effect=AssertionError('must use the explicit library')):
            self.assertEqual(cli.main(args), status, output.getvalue() + errors.getvalue())
        return (output.getvalue(), errors.getvalue()) if human else json.loads(output.getvalue())

    def run_import_with(self, runner, *, status, no_re_index=True):
        args = self.playlist.command('--apply', '--propose-collection')[3:]
        if no_re_index:
            args += ['--no-re-index']
        output = io.StringIO()
        with patch.dict(os.environ, self.playlist.env), patch.object(importer, 'run_command', runner), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(importer.main(args), status)
        report = json.loads(output.getvalue())
        self.report_file.write_text(json.dumps(report), encoding='utf-8')
        return report

    def test_opt_in_is_playlist_only_and_unflagged_reports_remain_compatible(self):
        result = subprocess.run(self.fixture.command('--propose-collection'), env=self.playlist.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires --playlist', result.stderr)
        _, old = self.playlist.run_playlist()
        self.assertNotIn('collection_proposal', old)
        rejected = self.collection(status=2, text=json.dumps(old))
        self.assertIn('no Collection proposal', rejected['issues'][0]['message'])

    def test_import_preview_is_nonmutating_and_cannot_be_applied(self):
        self.fixture.bookmark(A)
        before = self.fixture.snapshot()
        _, report = self.playlist.run_playlist('--propose-collection')
        self.assertEqual(report['collection_proposal'], dict(acquisition='preview', status='preview',
                                                            reason=None, library_identity=None))
        self.assertEqual([entry['outcome'] for entry in report['entries']],
                         ['would_create', 'skipped_existing', 'duplicate_input'])
        refused = self.collection('--apply', status=2, text=json.dumps(report))
        self.assertEqual(refused['issues'][0]['category'], 'PROPOSAL_INELIGIBLE')
        after = self.fixture.snapshot()
        after.pop('calls.jsonl')
        self.assertEqual(after, before)

    def test_existing_only_unavailable_local_bookmark_and_duplicate_positions(self):
        self.fixture.bookmark(A, 'renamed A.html')
        self.fixture.bookmark(B, 'renamed B.html')
        self.playlist.document['entries'][0]['availability'] = 'private'
        self.playlist.save_document()
        report = self.capture()
        self.assertEqual(report['collection_proposal']['acquisition'], 'completed')
        self.assertEqual(report['collection_proposal']['status'], 'eligible')
        self.assertEqual(len(self.fixture.calls()), 1)
        before = self.fixture.snapshot()
        with patch.object(cli.uuid, 'uuid4', side_effect=AssertionError('preview must not allocate a UUID')):
            reviewed = self.collection()
        self.assertEqual(reviewed['after']['members'], [B, A])
        self.assertEqual(reviewed['proposal']['repeats'], [dict(position=3, id=B, duplicate_of=1)])
        self.assertFalse(reviewed['proposal']['partial'])
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(self.collection('--apply')['result'], 'applied')
        before = self.fixture.snapshot()
        self.assertIn('already uses the name', self.collection('--apply', status=2)['issues'][0]['message'])
        self.assertEqual(self.fixture.snapshot(), before)
        alternative = self.collection('--apply', '--name', 'Different name')
        self.assertEqual(alternative['after']['members'], [B, A])

    def test_empty_and_completed_partial_imports(self):
        self.save_entries(dict(id=C, availability='private'))
        empty = self.capture(status=1)
        self.assertEqual(empty['collection_proposal']['status'], 'empty')
        self.assertEqual(self.collection('--apply', status=2)['proposal']['omitted'][0]['id'], C)
        self.assertFalse(self.library.exists())
        self.save_entries(dict(id=B), dict(id=A), dict(id=B), dict(id=C, availability='private'), None)
        report = self.capture(status=1, IMPORT_MODES=json.dumps({A: 'fail'}))
        self.assertEqual(report['collection_proposal']['status'], 'eligible')
        self.assertEqual(report['collection_proposal']['acquisition'], 'completed')
        preview = self.collection()
        self.assertEqual(preview['after']['members'], [B])
        self.assertEqual([(e['position'], e['id'], e['outcome']) for e in preview['proposal']['omitted']],
                         [(2, A, 'failed'), (4, C, 'unavailable'), (5, None, 'invalid_input')])
        self.assertEqual(self.collection('--apply', status=2)['issues'][0]['category'], 'PARTIAL_APPROVAL_REQUIRED')
        self.assertFalse(self.catalog.exists())
        self.fixture.bookmark(A)
        self.assertEqual(self.collection('--apply', '--accept-partial')['after']['members'], [B])
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as refused:
            cli.main(['from-import', str(self.report_file), '--accept-partial'])
        self.assertEqual(refused.exception.code, 2)
        self.assertIn('requires --apply', error.getvalue())

    def test_stopped_acquisition_on_final_entry_even_without_unprocessed_entries(self):
        real = importer.run_command

        def fatal(command):
            if '--batch-result' in command and command[-1] == A:
                result = Path(command[command.index('--batch-result') + 1])
                result.write_text(json.dumps({'fatal': True}), encoding='utf-8')
                return subprocess.CompletedProcess(command, 1, '', 'Synthetic fatal capture refusal')
            return real(command)

        report = self.run_import_with(fatal, status=1)
        self.assertEqual([entry['outcome'] for entry in report['entries']],
                         ['created', 'failed', 'duplicate_input'])
        self.assertEqual(report['collection_proposal']['acquisition'], 'stopped')
        self.assertEqual(report['collection_proposal']['status'], 'refused')
        self.assertEqual(self.collection('--apply', '--accept-partial', status=2)['issues'][0]['category'],
                         'PROPOSAL_INELIGIBLE')
        self.assertTrue(Path(report['entries'][0]['path']).is_file())
        self.assertFalse(self.catalog.exists())

    def test_interrupted_capture_and_refused_discovery_have_no_proposal(self):
        refused = self.capture(status=2, PLAYLIST_EXIT='1')
        self.assertEqual(refused['source']['discovery'], 'failed')
        self.assertEqual(refused['collection_proposal']['status'], 'refused')
        self.assertEqual(self.collection('--apply', status=2)['issues'][0]['category'], 'PROPOSAL_INELIGIBLE')
        real = importer.run_command

        def interrupt(command):
            if '--batch-result' in command and command[-1] == A:
                raise KeyboardInterrupt
            return real(command)

        interrupted = self.run_import_with(interrupt, status=130)
        self.assertEqual(interrupted['collection_proposal']['acquisition'], 'stopped')
        self.assertEqual(interrupted['collection_proposal']['status'], 'refused')
        self.assertEqual(self.collection('--apply', status=2)['issues'][0]['category'], 'PROPOSAL_INELIGIBLE')
        self.assertTrue(Path(interrupted['entries'][0]['path']).is_file())

    def test_index_failure_does_not_disqualify_completed_acquisition(self):
        real = importer.run_command

        def failed_index(command):
            if str(batch.SCRIPTS / 'yt-bookmark-index.py') in command:
                return subprocess.CompletedProcess(command, 2, '', 'Synthetic index failure')
            return real(command)

        report = self.run_import_with(failed_index, status=1, no_re_index=False)
        self.assertEqual(report['index']['status'], 'failed')
        self.assertEqual(report['collection_proposal']['status'], 'eligible')
        self.assertEqual(self.collection('--apply')['after']['members'], [B, A])

    def test_metadata_correction_preserves_members_and_shows_unsafe_originals(self):
        self.playlist.document.update(title='Bad\x1b[31m\ud800', description='Bad\x00description')
        self.playlist.save_document()
        self.capture()
        preview = self.collection(status=2)
        self.assertEqual(preview['proposal']['candidate']['members'], [B, A])
        self.assertEqual(set(preview['proposal']['metadata_issues']), {'name', 'description'})
        output, errors = self.collection(status=2, human=True)
        self.assertIn(r'\x1b[31m\ud800', output)
        self.assertIn(r'\x00description', output)
        self.assertIn('--name', errors)
        self.assertIn('--description', errors)
        preview = self.collection('--name', ' Demo e\u0301 ', '--description', '  first\r\nsecond\tline ')
        self.assertEqual(preview['after']['name'], 'Demo é')
        self.assertEqual(preview['after']['description'], 'first\nsecond\tline')
        applied = self.collection('--apply', '--name', 'Demo é', '--description=')
        self.assertNotIn('description', applied['after'])

    def test_missing_blank_and_long_titles_require_override(self):
        report = self.capture()
        for title in (None, '  ', 'A' * 81):
            with self.subTest(title=title):
                report['source']['title'] = title
                text = json.dumps(report)
                self.assertIn('name', self.collection(status=2, text=text)['proposal']['metadata_issues'])
                preview = self.collection('--name', 'Override', text=text)
                self.assertEqual(preview['after']['description'], 'Synthetic description')

    def test_malformed_unsupported_and_inconsistent_reports_are_refused(self):
        baseline = self.capture()
        for raw, diagnostic in (('{bad', 'JSON'), ('{"a":1,"a":2}', 'Duplicate JSON key'),
                                ('{"report_version":2}', 'Unsupported'), ('[]', 'JSON object')):
            with self.subTest(raw=raw):
                issue = self.collection('--apply', text=raw, status=2)['issues'][0]
                self.assertIn(diagnostic, issue['message'])
        changes = [
            ('entries', 0, 'position', 2), ('entries', 0, 'id', 'bad'),
            ('entries', 2, 'duplicate_of', 2), ('entries', 1, 'outcome', 'not_processed'),
            ('source', 'discovered_entries', 4), ('summary', 'created', True),
            ('collection_proposal', 'library_identity', None),
            ('collection_proposal', 'acquisition', 'preview'),
        ]
        for *path, replacement in changes:
            with self.subTest(path=path):
                changed = copy.deepcopy(baseline)
                node = changed
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = replacement
                issue = self.collection('--apply', text=json.dumps(changed), status=2)['issues'][0]
                self.assertEqual(issue['category'], 'IMPORT_REPORT_ERROR')
        self.assertFalse(self.catalog.exists())

    def test_binding_refuses_other_libraries_and_replaced_bookmark_directory(self):
        self.capture()
        alias = self.fixture.root / 'alias'
        alias.symlink_to(self.library, target_is_directory=True)
        self.assertEqual(self.collection(library=alias)['result'], 'would_change')
        other = self.fixture.root / 'other'
        shutil.copytree(self.library, other)
        self.assertEqual(self.collection('--apply', status=2, library=other)['issues'][0]['category'],
                         'LIBRARY_MISMATCH')
        output = io.StringIO()
        with patch.object(cli, 'default_library', return_value=self.library), redirect_stdout(output):
            self.assertEqual(cli.main(['from-import', str(self.report_file), '--json']), 0)
        self.assertEqual(json.loads(output.getvalue())['after']['members'], [B, A])
        old = self.fixture.root / 'old-bookmarks'
        root = self.library / 'bookmarks'
        root.rename(old)
        shutil.copytree(old, root)
        self.assertEqual(self.collection('--apply', status=2)['issues'][0]['category'], 'LIBRARY_MISMATCH')
        self.assertFalse(self.catalog.exists())

    def test_renamed_bookmark_resolves_but_live_inventory_errors_refuse(self):
        report = self.capture()
        original = Path(report['entries'][0]['path'])
        renamed = original.parent / 'renamed.html'
        original.rename(renamed)
        self.assertEqual(self.collection()['after']['members'], [B, A])
        original.write_bytes(renamed.read_bytes())
        self.assertEqual(self.collection('--apply', status=2)['issues'][0]['category'], 'INVENTORY_ERROR')
        original.unlink()
        original.write_text('<script id="youtube-metadata">{"id":"BBBBBBBBBBB"}</script>', encoding='utf-8')
        conflict = original.with_name(f'Conflict [{C}].html')
        original.rename(conflict)
        self.assertEqual(self.collection(status=2)['issues'][0]['category'], 'INVENTORY_ERROR')
        conflict.write_text('<script id="youtube-metadata">malformed</script>', encoding='utf-8')
        self.assertEqual(self.collection(status=2)['issues'][0]['category'], 'INVENTORY_ERROR')
        conflict.unlink()
        renamed.unlink()
        issue = self.collection('--apply', '--accept-partial', status=2)['issues'][0]
        self.assertIn(f'not present in library: {B}', issue['message'])
        self.assertFalse(self.catalog.exists())

    def test_publication_failure_preserves_captured_bookmarks_and_catalog(self):
        report = self.capture()
        self.catalog.write_text('{"version":1,"bookmarks":{},"collections":{}}', encoding='utf-8')
        before = self.catalog.read_bytes()
        captured = {Path(entry['path']): Path(entry['path']).read_bytes()
                    for entry in report['entries'] if entry['outcome'] == 'created'}
        replace = os.replace

        def fail_catalog(source, destination):
            if destination == self.catalog.resolve():
                raise OSError('Synthetic publication failure')
            replace(source, destination)

        with patch.object(os, 'replace', fail_catalog):
            refused = self.collection('--apply', status=2)
        self.assertEqual(refused['catalog']['publication'], 'not_published')
        self.assertEqual(Path(refused['catalog']['backup']).read_bytes(), before)
        self.assertEqual(self.catalog.read_bytes(), before)
        self.assertEqual({path: path.read_bytes() for path in captured}, captured)
        self.assertFalse(self.index.exists())

    def test_offline_handoff_and_explicit_index_exclude_hidden_payload(self):
        self.fixture.bookmark(A)
        self.catalog.write_text(json.dumps(dict(version=1, bookmarks={A: {'tags': ['hidden']}},
                                                collections={}, extension={'keep': True})), encoding='utf-8')
        previous_catalog = self.catalog.read_bytes()
        report = self.capture()
        self.assertEqual(report['index']['status'], 'suppressed')
        self.assertEqual(self.catalog.read_bytes(), previous_catalog)
        self.assertFalse(self.index.exists())
        offline = self.fixture.root / 'offline-bin'
        offline.mkdir()
        (offline / 'bash').symlink_to(shutil.which('bash'))
        (offline / 'python3').symlink_to(sys.executable)
        environment = dict(os.environ, PATH=str(offline))

        def dispatch(*args, stdin=None):
            result = subprocess.run([VIDEX, *args], env=environment, input=stdin,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return result.stdout

        before = self.fixture.snapshot()
        reviewed = dispatch('collection', 'from-import', str(self.report_file), '--library', str(self.library))
        self.assertIn(f'Members in playlist order: {B}, {A}', reviewed)
        self.assertEqual(self.fixture.snapshot(), before)
        applied = json.loads(dispatch('collection', 'from-import', '-', '--library', str(self.library),
                                      '--apply', '--json', stdin=json.dumps(report)))
        self.assertEqual(applied['catalog']['publication'], 'published')
        backups = list(self.library.glob('catalog.json.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), previous_catalog)
        self.assertFalse(self.index.exists())
        saved = json.loads(self.catalog.read_bytes())
        identity = applied['after']['id']
        self.assertEqual(saved['collections'], {identity: dict(name='Synthetic playlist',
                              description='Synthetic description', members=[B, A])})
        self.assertEqual(saved['extension'], {'keep': True})
        dispatch('index', str(self.library))
        html = self.index.read_text(encoding='utf-8')
        parser = IndexDocumentParser()
        parser.feed(html)
        parser.close()
        payload, = [script['text'] for script in parser.scripts if script['attrs'].get('id') == 'bookmark-data']
        data = json.loads(payload)
        self.assertEqual([item['id'] for item in data['items']], [B])
        self.assertEqual(data['collections'], [dict(id=identity, name='Synthetic playlist',
                              description='Synthetic description', members=[B])])
        self.assertNotIn(A, html)
