"""Fully offline TXT import regressions using disposable libraries and extractors."""
import base64
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = load('yt-bookmark-import')
generator = load('yt-bookmark-generate')
A, B, C = 'AAAAAAAAAAA', 'BBBBBBBBBBB', 'CCCCCCCCCCC'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII=')


class ImportTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='videx-import-test-')
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.library = self.root / 'library'
        self.source = self.root / 'batch.txt'
        self.source.write_text(A + '\n' + B + '\n', encoding='utf-8')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'python3').symlink_to(sys.executable)
        stub = self.bin / 'yt-dlp'
        stub.write_text('#!' + sys.executable + '\n' + '''import base64, json, os, pathlib, signal, sys, time
root = pathlib.Path(os.environ['IMPORT_FIXTURE'])
identity = sys.argv[-1].split('=')[-1]
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if '--dump-single-json' in sys.argv:
    if os.environ.get('PLAYLIST_SLEEP'):
        (root / 'waiting').write_text('discovery')
        time.sleep(30)
    print((root / 'playlist.json').read_text())
    if os.environ.get('PLAYLIST_STDERR'):
        print(os.environ['PLAYLIST_STDERR'], file=sys.stderr)
    sys.exit(int(os.environ.get('PLAYLIST_EXIT', '0')))
mode = json.loads(os.environ.get('IMPORT_MODES', '{}')).get(identity)
if mode == 'sleep':
    def stop(signum, frame):
        (root / 'extractor-stopped').write_text(identity)
        sys.exit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    (root / 'waiting').write_text(identity)
    time.sleep(30)
if mode == 'fail':
    print('ERROR: synthetic network or extractor failure', file=sys.stderr)
    sys.exit(1)
out = pathlib.Path(sys.argv[sys.argv.index('--paths') + 1])
if mode == 'mismatch': identity = 'ZZZZZZZZZZZ'
metadata = {'id': identity, 'title': 'Synthetic ' + identity, 'uploader': 'Synthetic channel'}
if mode == 'bad_metadata': metadata['duration'] = float('nan')
(out / 'video.info.json').write_text(json.dumps(metadata))
if mode != 'no_thumbnail':
    (out / 'video.png').write_bytes(base64.b64decode(os.environ['IMPORT_PNG']))
if mode == 'unsafe_library':
    (root / 'library' / 'bookmarks' / 'unknown.html').write_text('unidentified')
''', encoding='utf-8')
        stub.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        PYTHONDONTWRITEBYTECODE='1', IMPORT_FIXTURE=str(self.root),
                        IMPORT_PNG=base64.b64encode(PNG).decode())

    def command(self, *options):
        return [sys.executable, '-B', str(SCRIPTS / 'yt-bookmark-import.py'),
                '--file', str(self.source), '--output', str(self.library), '--json', *options]

    def run_import(self, *options, modes=None):
        env = dict(self.env, IMPORT_MODES=json.dumps(modes or {}))
        result = subprocess.run(self.command(*options), env=env, capture_output=True,
                                text=True, timeout=30)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, report['exit_status'], result.stderr)
        return result.returncode, report

    def bookmark(self, identity=A, name='renamed.html'):
        path = self.library / 'bookmarks' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<script id="youtube-metadata">' + json.dumps({'id': identity}) + '</script>')
        return path

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.lstat().st_mode, p.lstat().st_mtime_ns,
                p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                for p in self.root.rglob('*')}

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_url_forms_and_case_sensitive_ids(self):
        for host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            for path in ('watch?v=' + A, 'shorts/' + A, 'live/' + A, 'embed/' + A):
                with self.subTest(host=host, path=path):
                    self.assertEqual(importer.input_id('https://' + host + '/' + path), A)
        for host in ('youtu.be', 'www.youtu.be'):
            self.assertEqual(importer.input_id('http://' + host + '/' + A + '?si=abc&t=10#t=10s'), A)
        for identity in (A.lower(), '-AAAAAAAAAA', '--AAAAAAAAA'):
            self.assertEqual(importer.input_id(identity), identity)
        self.assertEqual(importer.input_id('https://youtube.com/watch?v=' + A + '&list=PLx'), A)

    def test_strict_invalid_inputs(self):
        values = [A + ',title', '[' + A + ']', A + ' # note',
                  '[video](https://youtu.be/' + A + ')', 'https://example.com/' + A,
                  'https://youtube.com.evil/watch?v=' + A,
                  'https://user@youtube.com/watch?v=' + A,
                  'https://youtube.com:443/watch?v=' + A,
                  'https://youtube.com/watch?v=' + A + '&v=' + A,
                  'https://youtube.com/watch?v=' + A + '&v=',
                  'https://youtube.com/shorts/' + A + '/extra',
                  'https://youtu.be/' + A + '/', 'https://youtube.com/playlist?list=PLx',
                  'ftp://youtu.be/' + A, 'https://youtu.be/' + A + '#comment',
                  'https://youtu.be/' + A + '#t=hm',
                  'https://youtu.be/' + A + '?v=' + B,
                  'https://youtu.be/' + A + '?si=%GG',
                  'youtube.com/watch?v=' + A, 'AAAAAAAAAA', 'AAAAAAAAAAAA']
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                importer.input_id(value)

    def test_bom_crlf_comments_duplicates_and_physical_lines(self):
        self.source.write_bytes(('\ufeff# Backlog\r\n\r\n  ' + A + '\r\n'
             'https://youtu.be/' + A + '\r\n' + A.lower() + '\r\ninvalid\r\n').encode())
        entries = importer.read_txt(self.source)
        self.assertEqual([e['line'] for e in entries], [3, 4, 5, 6])
        self.assertEqual([e['outcome'] for e in entries],
                         ['not_processed', 'duplicate_input', 'not_processed', 'invalid_input'])
        self.assertEqual(entries[1]['duplicate_of'], 3)
        self.source.write_text(A + '\v' + B + '\n' + C)
        self.assertEqual([e['line'] for e in importer.read_txt(self.source)], [1, 2])

    def test_preview_absent_library_and_explicit_dry_run_write_nothing(self):
        before = self.snapshot()
        for options in ((), ('--dry-run',)):
            status, report = self.run_import(*options)
            self.assertEqual(status, 0)
            self.assertEqual(report['summary']['would_create'], 2)
            self.assertEqual(report['index']['status'], 'preview_no_writes')
            self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.library.exists())

    def test_preview_reuses_renamed_and_legacy_bookmarks_without_writes(self):
        saved = self.bookmark()
        legacy = saved.parent / ('Legacy [' + B + '].html')
        legacy.write_text('legacy')
        before = self.snapshot()
        status, report = self.run_import()
        self.assertEqual(status, 0)
        self.assertEqual(report['summary']['skipped_existing'], 2)
        self.assertEqual(self.snapshot(), before)

    def test_apply_repeat_import_and_isolated_extraction(self):
        status, report = self.run_import('--apply')
        self.assertEqual(status, 0, report)
        self.assertEqual(report['summary']['created'], 2)
        self.assertEqual(report['index']['status'], 'rebuilt')
        index = self.library / 'index.html'
        initial = index.read_bytes(), index.stat().st_mtime_ns
        self.assertFalse((self.library / 'catalog.json').exists())
        for call in self.calls():
            for flag in ('--ignore-config', '--no-cache-dir', '--no-mark-watched',
                         '--no-playlist', '--skip-download'):
                self.assertIn(flag, call)
        status, report = self.run_import('--apply')
        self.assertEqual(status, 0)
        self.assertEqual(report['summary']['skipped_existing'], 2)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), initial)

    def test_invalid_and_duplicate_entries_do_not_block_valid_apply(self):
        self.source.write_text(A + '\ninvalid\nhttps://youtu.be/' + A + '\n' + B)
        status, report = self.run_import('--apply', '--no-re-index')
        self.assertEqual(status, 1)
        for outcome, count in [('created', 2), ('invalid_input', 1), ('duplicate_input', 1)]:
            self.assertEqual(report['summary'][outcome], count)
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse((self.library / 'index.html').exists())

    def test_independent_failures_continue_and_index_successes(self):
        for failure in ('fail', 'mismatch', 'no_thumbnail', 'bad_metadata'):
            with self.subTest(failure=failure):
                # Use distinct roots per run without deleting prior artifacts.
                self.library = self.root / ('library-' + failure)
                status, report = self.run_import('--apply', modes={A: failure})
                self.assertEqual(status, 1, report)
                self.assertEqual(report['summary']['failed'], 1)
                self.assertEqual(report['summary']['unavailable'], 0)
                self.assertEqual(report['summary']['created'], 1)
                self.assertEqual(report['index']['status'], 'rebuilt')
                self.assertEqual(len(list((self.library / 'bookmarks').rglob('*.html'))), 1)

    def test_later_failure_preserves_earlier_bookmark(self):
        status, report = self.run_import('--apply', modes={B: 'fail'})
        self.assertEqual(status, 1)
        self.assertEqual([e['outcome'] for e in report['entries']], ['created', 'failed'])
        self.assertTrue(Path(report['entries'][0]['path']).is_file())
        self.assertEqual(report['index']['status'], 'rebuilt')

    def test_index_failure_preserves_previous_index_and_new_bookmarks(self):
        self.library.mkdir()
        (self.library / 'catalog.json').write_text('{invalid')
        index = self.library / 'index.html'
        index.write_text('previous index')
        status, report = self.run_import('--apply')
        self.assertEqual(status, 1)
        self.assertEqual(report['summary']['created'], 2)
        self.assertEqual(report['index']['status'], 'failed')
        self.assertEqual(index.read_text(), 'previous index')
        self.assertEqual((self.library / 'catalog.json').read_text(), '{invalid')

    def test_unsafe_inventory_preflight_is_global_refusal(self):
        self.bookmark()
        self.bookmark(name='duplicate.html')
        before = self.snapshot()
        status, report = self.run_import('--apply')
        self.assertEqual(status, 2)
        self.assertTrue(report['errors'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.calls())

    def test_hidden_bookmark_reuse_preserves_catalog_and_index_visibility(self):
        path = self.bookmark()
        path.write_text(generator.make_document({'id': A, 'title': 'Hidden synthetic'},
            'data:image/png;base64,' + base64.b64encode(PNG).decode()))
        catalog = self.library / 'catalog.json'
        data = json.dumps({'version': 1, 'bookmarks': {A: {'tags': ['hidden']},
                           B: {'tags': ['Research']}}, 'collections': {'reserved': {'future': True}}})
        catalog.write_text(data)
        original = path.read_bytes()
        status, report = self.run_import('--apply')
        self.assertEqual(status, 0, report)
        self.assertEqual(report['summary']['skipped_existing'], 1)
        self.assertEqual(report['summary']['created'], 1)
        self.assertEqual(catalog.read_text(), data)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.library.glob('catalog.json.bak-*')))
        index = (self.library / 'index.html').read_text()
        self.assertNotIn(A, index)
        self.assertIn(B, index)
        self.assertIn('Research', index)

    def test_inventory_inspection_errors_are_not_treated_as_missing(self):
        self.library.mkdir()
        with patch.object(Path, 'lstat', side_effect=PermissionError('synthetic denied')):
            with self.assertRaises(PermissionError):
                importer.inventory(self.library)

    def test_new_unsafe_inventory_stops_remaining_entries(self):
        self.source.write_text(A + '\n' + B + '\n' + C)
        status, report = self.run_import('--apply', modes={B: 'unsafe_library'})
        self.assertEqual(status, 1)
        self.assertEqual([e['outcome'] for e in report['entries']],
                         ['created', 'failed', 'not_processed'])
        self.assertEqual(len(self.calls()), 2)

    def test_symlinked_collection_refused(self):
        self.library.mkdir()
        (self.library / 'bookmarks').symlink_to(self.root / 'missing')
        status, _ = self.run_import('--apply')
        self.assertEqual(status, 2)
        self.assertFalse(self.calls())

    def test_encoding_missing_file_and_output_errors(self):
        self.source.write_bytes(b'\xff')
        self.assertEqual(self.run_import()[0], 2)
        self.source = self.root / 'missing.txt'
        self.assertEqual(self.run_import()[0], 2)
        self.source.write_text(A)
        self.library = self.root / 'bookmarks'
        self.assertEqual(self.run_import()[0], 2)

    def test_configuration_default_and_explicit_override(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'library': str(self.library)}))
        argv = ['--file', str(self.source), '--json']
        with patch('videx_config.CONFIG_PATH', config), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(importer.main(argv), 0)
        self.assertEqual(json.loads(output.getvalue())['library'], str(self.library.resolve()))
        config.write_text('invalid')
        with patch('videx_config.CONFIG_PATH', config), redirect_stdout(io.StringIO()):
            self.assertEqual(importer.main(argv), 2)
            self.assertEqual(importer.main(argv + ['--output', str(self.library)]), 0)

    def test_rebuild_is_called_once_and_suppression(self):
        real = importer.run_command
        for suppress in (False, True):
            self.library = self.root / ('once-' + str(suppress))
            with patch.object(importer, 'run_command', wraps=real) as runner, \
                    patch.dict(os.environ, self.env), redirect_stdout(io.StringIO()):
                options = ['--file', str(self.source), '--output', str(self.library), '--apply', '--json']
                self.assertEqual(importer.main(options + (['--no-re-index'] if suppress else [])), 0)
            indexes = [c for c in runner.call_args_list if str(SCRIPTS / 'yt-bookmark-index.py') in c.args[0]]
            self.assertEqual(len(indexes), 0 if suppress else 1)

    def test_interruption_terminates_child_and_preserves_partial_progress(self):
        self.assert_import_interruption(self.command('--apply'))

    def test_dispatcher_interruption_terminates_child_and_preserves_partial_progress(self):
        self.assert_import_interruption([str(PROJECT / 'videx'), 'import', *self.command('--apply')[3:]])

    def assert_import_interruption(self, command):
        self.source.write_text(A + '\n' + B + '\n' + C)
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=self.root,
            env=dict(self.env, IMPORT_MODES=json.dumps({B: 'sleep'})))
        try:
            deadline = time.monotonic() + 15
            while not (self.root / 'waiting').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue((self.root / 'waiting').exists())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(process.returncode, 130, stderr)
        report = json.loads(stdout)
        self.assertEqual([e['outcome'] for e in report['entries']],
                         ['created', 'not_processed', 'not_processed'])
        self.assertEqual(report['index']['status'], 'rebuilt')
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual((self.root / 'extractor-stopped').read_text(), B)
        self.assertTrue(Path(report['entries'][0]['path']).is_file())

    def test_human_summary_and_conflicting_modes(self):
        result = subprocess.run(self.command()[:-1], env=self.env, capture_output=True, text=True)
        self.assertIn('availability has not been checked', result.stdout)
        self.assertIn('Would Create: 2', result.stdout)
        result = subprocess.run(self.command('--apply', '--dry-run'), env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.calls())


class CreateOnlyTests(unittest.TestCase):
    def test_unsupported_hard_links_fail_without_fallback_or_staging_leftovers(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'bookmark.html'
            with patch.object(generator.os, 'link', side_effect=OSError('unsupported')):
                with self.assertRaises(OSError):
                    generator.write_atomic(output, 'new bookmark', create_only=True)
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.iterdir()), [])

    def test_atomic_create_only_refuses_late_collision_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'bookmark.html'
            real_link = os.link

            def collision(source, destination):
                Path(destination).write_text('valuable existing bookmark')
                real_link(source, destination)

            with patch.object(generator.os, 'link', side_effect=collision), self.assertRaises(FileExistsError):
                generator.write_atomic(output, 'new bookmark', create_only=True)
            self.assertEqual(output.read_text(), 'valuable existing bookmark')
            self.assertEqual(list(output.parent.glob('.bookmark-*.tmp')), [])

    def test_create_only_success_and_generator_cli_existing_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / ('Video [' + A + '].html')
            generator.write_atomic(output, 'saved', create_only=True)
            info, thumbnail = root / 'info.json', root / 'thumbnail.png'
            info.write_text(json.dumps({'id': A, 'title': 'Synthetic'}))
            thumbnail.write_bytes(PNG)
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'yt-bookmark-generate.py'),
                str(info), str(thumbnail), str(output), '--create-only'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_text(), 'saved')


if __name__ == '__main__':
    unittest.main()
