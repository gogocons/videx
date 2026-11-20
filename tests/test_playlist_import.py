"""Public playlist discovery and shared import regressions, entirely offline."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_bookmark_import as batch

importer = batch.importer
A, B, C = batch.A, batch.B, batch.C
PLAYLIST_ID = 'PLsynthetic1234567890'
URL = 'https://www.youtube.com/playlist?list=' + PLAYLIST_ID


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.fixture = batch.ImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.library = self.fixture.library
        self.env = dict(self.fixture.env)
        self.document = dict(_type='playlist', id=PLAYLIST_ID, title='Synthetic playlist',
                             description='Synthetic description', playlist_count=3,
                             entries=[dict(id=identity, playlist_index=i, _type='url', ie_key='Youtube')
                                      for i, identity in enumerate((B, A, B), 1)])
        self.save_document()

    def save_document(self):
        (self.root / 'playlist.json').write_text(json.dumps(self.document), encoding='utf-8')

    def command(self, *options, url=URL):
        return [sys.executable, '-B', str(batch.SCRIPTS / 'yt-bookmark-import.py'),
                '--playlist', url, '--output', str(self.library), '--json', *options]

    def run_playlist(self, *options, **environment):
        result = subprocess.run(self.command(*options), env=dict(self.env, **environment),
                                capture_output=True, text=True, timeout=30)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, report['exit_status'], result.stderr)
        return result.returncode, report

    def test_public_url_forms_normalize_to_one_full_playlist(self):
        for host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            for scheme in ('http', 'https'):
                for path in ('/playlist?list=' + PLAYLIST_ID,
                             '/watch?v=' + A + '&list=' + PLAYLIST_ID + '&index=2'):
                    with self.subTest(host=host, scheme=scheme, path=path):
                        self.assertEqual(importer.playlist_url(scheme + '://' + host + path), (PLAYLIST_ID, URL))
        for host in ('youtu.be', 'www.youtu.be'):
            self.assertEqual(importer.playlist_url('https://' + host + '/' + A + '?list=' + PLAYLIST_ID), (PLAYLIST_ID, URL))
        album = 'OLAK5uy_synthetic1234567890'
        self.assertEqual(importer.playlist_url('https://music.youtube.com/playlist?list=' + album)[0], album)

    def test_invalid_targets_are_rejected_before_extractor_invocation(self):
        values = [URL + '&list=' + PLAYLIST_ID, URL + '&list=', URL + '#fragment',
                  URL.replace('www.youtube.com', 'evil.example'),
                  URL.replace('www.youtube.com', 'youtube.com.evil'),
                  URL.replace('www.youtube.com', 'user@www.youtube.com'),
                  URL.replace('www.youtube.com', 'www.youtube.com:443'),
                  URL.replace('https:', 'file:'), URL.replace('/playlist?', '/channel/foo?'),
                  URL + ' # comment', '[playlist](' + URL + ')',
                  URL + '&v=' + A, URL + '&si=%QQ', URL.replace(PLAYLIST_ID, 'WL'),
                  URL.replace(PLAYLIST_ID, 'RD' + A), URL.replace(PLAYLIST_ID, 'LL'),
                  'ytsearch:playlist', PLAYLIST_ID, 'https://youtube.com/playlist',
                  'https://youtu.be/invalid?list=' + PLAYLIST_ID]
        for value in values:
            with self.subTest(url=value):
                result = subprocess.run(self.command('--apply', url=value), env=self.env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stdout)
        self.assertFalse(self.fixture.calls())
        self.assertFalse(self.library.exists())

    def test_preview_order_duplicates_metadata_and_no_local_writes(self):
        before = self.fixture.snapshot()
        status, report = self.run_playlist('--dry-run')
        self.assertEqual(status, 0, report)
        self.assertEqual([e['id'] for e in report['entries']], [B, A, B])
        self.assertEqual([e['position'] for e in report['entries']], [1, 2, 3])
        self.assertEqual(report['entries'][2]['duplicate_of'], 1)
        self.assertEqual(report['summary']['would_create'], 2)
        self.assertEqual(report['summary']['duplicate_input'], 1)
        self.assertEqual(report['source'], dict(type='playlist', url=URL, id=PLAYLIST_ID,
            title='Synthetic playlist', description='Synthetic description', discovery='complete',
            discovered_entries=3, unique_valid_ids=2))
        calls = self.fixture.calls()
        self.assertEqual(len(calls), 1)
        for flag in ('--ignore-config', '--no-cache-dir', '--no-mark-watched', '--flat-playlist',
                     '--yes-playlist', '--simulate', '--dump-single-json', '--no-lazy-playlist', '--abort-on-error'):
            self.assertIn(flag, calls[0])
        self.assertEqual(calls[0][-2:], ['--', URL])
        for flag in ('--write-thumbnail', '--write-info-json', '--playlist-items', '--max-downloads'):
            self.assertNotIn(flag, calls[0])
        after = self.fixture.snapshot()
        # The executable test stub logs calls; the importer creates nothing.
        after.pop('calls.jsonl')
        self.assertEqual(after, before)
        self.assertFalse(self.library.exists())

    def test_existing_renamed_bookmark_skipped_during_preview(self):
        saved = self.fixture.bookmark(A)
        before = saved.read_bytes(), saved.stat().st_mtime_ns
        status, report = self.run_playlist()
        self.assertEqual(status, 0)
        self.assertEqual(report['entries'][1]['outcome'], 'skipped_existing')
        self.assertEqual((saved.read_bytes(), saved.stat().st_mtime_ns), before)
        self.assertEqual(len(self.fixture.calls()), 1)

    def test_missing_ids_unavailable_and_malformed_entries_are_reported(self):
        self.document['entries'] = [dict(id=A), {}, None, 'invalid', dict(id='bad'),
            dict(title='[Private video]'), dict(id=B, title='[Deleted video]'),
            dict(id=C, availability='needs_auth')]
        self.document['playlist_count'] = 8
        self.save_document()
        status, report = self.run_playlist('--apply', '--no-re-index')
        self.assertEqual(status, 1)
        self.assertEqual(report['summary']['created'], 1)
        self.assertEqual(report['summary']['invalid_input'], 4)
        self.assertEqual(report['summary']['unavailable'], 3)
        self.assertEqual(report['source']['unique_valid_ids'], 3)
        self.assertEqual(len(self.fixture.calls()), 2)

    def test_unavailable_existing_bookmark_is_reused(self):
        self.document['entries'][1]['availability'] = 'private'
        self.save_document()
        saved = self.fixture.bookmark(A)
        status, report = self.run_playlist()
        self.assertEqual(status, 0, report)
        self.assertEqual(report['entries'][1]['outcome'], 'skipped_existing')
        self.assertEqual(Path(report['entries'][1]['path']), saved.resolve())

    def test_optional_metadata_and_empty_playlist(self):
        self.document = dict(_type='playlist', entries=[])
        self.save_document()
        status, report = self.run_playlist('--apply')
        self.assertEqual(status, 0, report)
        self.assertEqual(report['source']['id'], PLAYLIST_ID)
        self.assertIsNone(report['source']['title'])
        self.assertIsNone(report['source']['description'])
        self.assertEqual(report['source']['discovered_entries'], 0)
        self.assertFalse(self.library.exists())

    def test_full_large_flat_result_has_no_page_size_limit(self):
        self.document = dict(_type='playlist', id=PLAYLIST_ID, playlist_count=205,
            entries=[dict(id=f'{i:011d}', playlist_index=i + 1) for i in range(205)])
        self.save_document()
        status, report = self.run_playlist()
        self.assertEqual(status, 0)
        self.assertEqual(report['source']['unique_valid_ids'], 205)
        self.assertEqual(report['entries'][-1]['id'], '00000000204')
        self.assertEqual(report['entries'][-1]['position'], 205)
        self.assertIsNone(report['source']['title'])
        self.assertIsNone(report['source']['description'])
        self.assertEqual(len(self.fixture.calls()), 1)

    def test_discovery_failure_preserves_existing_library_and_index(self):
        self.fixture.bookmark(A)
        (self.library / 'index.html').write_text('existing index')
        (self.library / 'catalog.json').write_text('existing personal data')
        before = self.fixture.snapshot()
        status, report = self.run_playlist('--apply', PLAYLIST_EXIT='1')
        self.assertEqual(status, 2)
        self.assertEqual(report['entries'], [])
        after = self.fixture.snapshot()
        after.pop('calls.jsonl')
        self.assertEqual(after, before)

    def test_missing_discovery_executable_reports_no_extra_filesystem_paths(self):
        with patch.object(importer, 'run_command', side_effect=FileNotFoundError('/private/sensitive/tool')):
            with self.assertRaises(ValueError) as caught:
                importer.discover_playlist(URL, dict(id=PLAYLIST_ID))
        self.assertNotIn('/private/sensitive/tool', str(caught.exception))

    def test_txt_report_contract_is_unchanged(self):
        self.fixture.source.write_text(A + '\ninvalid\n' + A + '\n' + B)
        status, report = self.fixture.run_import()
        self.assertEqual(status, 1)
        self.assertEqual(set(report), {'report_version', 'apply', 'library', 'entries', 'errors',
                                     'interrupted', 'index', 'exit_status', 'summary'})
        self.assertEqual([e['outcome'] for e in report['entries']],
                         ['would_create', 'invalid_input', 'duplicate_input', 'would_create'])
        self.assertEqual([e['line'] for e in report['entries']], [1, 2, 3, 4])
        self.assertEqual(report['entries'][2]['duplicate_of'], 1)
        self.assertFalse(self.fixture.calls())

    def test_discovery_refusal_never_starts_a_partial_import(self):
        invalid = [[], {}, dict(_type='video', id=A),
                   dict(_type='playlist', id=PLAYLIST_ID, entries={}),
                   dict(self.document, id='PLother1234567890'),
                   dict(self.document, playlist_count=4), dict(self.document, playlist_count=True),
                   dict(self.document, n_entries=1), dict(self.document, requested_entries=[1, 3, 4]),
                   dict(self.document, incomplete=True), dict(self.document, truncated=True),
                   dict(self.document, errors=['synthetic']), dict(self.document, complete=False),
                   dict(self.document, has_more=True), dict(self.document, continuation='more'),
                   dict(self.document, title={}), dict(self.document, availability='private')]
        reordered = dict(self.document, entries=[dict(id=A, playlist_index=2), dict(id=B, playlist_index=1)])
        invalid.append(reordered)
        invalid.append(dict(self.document, entries=[dict(id=A, error='extraction failed')]))
        for document in invalid:
            with self.subTest(document=document):
                self.document = document
                self.save_document()
                status, report = self.run_playlist('--apply')
                self.assertEqual(status, 2, report)
                self.assertEqual(report['source']['discovery'], 'failed')
                self.assertEqual(report['entries'], [])
                self.assertFalse(self.library.exists())
        self.assertTrue(all('--dump-single-json' in call for call in self.fixture.calls()))

    def test_bad_json_and_nonzero_exit_and_warnings_are_safe_and_sanitized(self):
        payloads = ['not json', '{"_type":"playlist", "entries":[',
                    '{"_type":"playlist","entries":[],"entries":[]}',
                    '{"_type":"playlist","entries":[],"bad":NaN}']
        for payload in payloads:
            with self.subTest(payload=payload):
                (self.root / 'playlist.json').write_text(payload)
                self.assertEqual(self.run_playlist('--apply')[0], 2)
        self.save_document()
        for env in (dict(PLAYLIST_EXIT='1', PLAYLIST_STDERR='ERROR: /private/sensitive/path'),
                    dict(PLAYLIST_STDERR='WARNING: Incomplete data received /private/sensitive/path'),
                    dict(PLAYLIST_STDERR='ERROR: missing continuation'),
                    dict(PLAYLIST_STDERR='WARNING: Unavailable videos are hidden')):
            status, report = self.run_playlist('--apply', **env)
            self.assertEqual(status, 2, report)
            self.assertNotIn('/private/sensitive/path', json.dumps(report))
            self.assertEqual(report['entries'], [])
            self.assertFalse(self.library.exists())

    def test_apply_repeat_and_mixed_capture_results_share_existing_pipeline(self):
        status, report = self.run_playlist('--apply', IMPORT_MODES=json.dumps({A: 'fail'}))
        self.assertEqual(status, 1, report)
        self.assertEqual([e['outcome'] for e in report['entries']], ['created', 'failed', 'duplicate_input'])
        self.assertEqual(report['index']['status'], 'rebuilt')
        self.assertEqual(len(list((self.library / 'bookmarks').rglob('*.html'))), 1)
        status, report = self.run_playlist('--apply')
        self.assertEqual(status, 0, report)
        self.assertEqual(report['summary']['skipped_existing'], 1)
        self.assertEqual(report['summary']['created'], 1)
        self.assertEqual(report['index']['status'], 'rebuilt')
        self.assertFalse((self.library / 'catalog.json').exists())
        before = (self.library / 'index.html').stat().st_mtime_ns
        status, report = self.run_playlist('--apply')
        self.assertEqual(status, 0)
        self.assertEqual(report['summary']['skipped_existing'], 2)
        self.assertEqual(report['index']['status'], 'no_new_bookmarks')
        self.assertEqual((self.library / 'index.html').stat().st_mtime_ns, before)

    def test_exactly_one_index_after_success_and_suppression(self):
        real = importer.run_command
        for suppress in (False, True):
            library = self.root / ('once-' + str(suppress))
            argv = ['--playlist', URL, '--output', str(library), '--apply', '--json']
            if suppress:
                argv.append('--no-re-index')
            with patch.dict(os.environ, self.env), patch.object(importer, 'run_command', wraps=real) as run, \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(importer.main(argv), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['summary']['created'], 2)
            indexes = [c for c in run.call_args_list if str(batch.SCRIPTS / 'yt-bookmark-index.py') in c.args[0]]
            self.assertEqual(len(indexes), 0 if suppress else 1)

    def test_discovery_interruption_keeps_library_absent(self):
        process = subprocess.Popen(self.command('--apply'), env=dict(self.env, PLAYLIST_SLEEP='1'),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not (self.root / 'waiting').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(.05)
            self.assertTrue((self.root / 'waiting').exists())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(process.returncode, 130, stderr)
        self.assertEqual(json.loads(stdout)['source']['discovery'], 'interrupted')
        self.assertFalse(self.library.exists())

    def test_cli_sources_are_required_and_mutually_exclusive_and_human_report(self):
        result = subprocess.run(self.command('--file', str(self.fixture.source)), env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        result = subprocess.run(self.command()[:-1], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Source: public YouTube playlist', result.stdout)
        self.assertIn('discovered entries: 3; unique valid video IDs: 2', result.stdout)
        self.assertIn('first occurrence: position 1', result.stdout)
        self.assertIn('Position 1: Would Create:', result.stdout)


if __name__ == '__main__':
    unittest.main()
