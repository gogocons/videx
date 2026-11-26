"""Check the dispatcher boundary and representative offline Videx workflows."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_bookmark_import as batch

VIDEX = batch.PROJECT / 'videx'
ROUTES = ('capture', 'import', 'index', 'find', 'tag', 'upgrade')


class DispatchTests(unittest.TestCase):
    def run_command(self, command, **kwargs):
        return subprocess.run(command, capture_output=True, text=True, timeout=15, **kwargs)

    def test_top_level_help_and_errors(self):
        self.assertTrue(os.access(VIDEX, os.X_OK))
        for option in ('-h', '--help'):
            result = self.run_command([VIDEX, option])
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            for command in ('open', *ROUTES):
                self.assertRegex(result.stdout, rf'(?m)^  {command}\s+')
        missing = self.run_command([VIDEX])
        self.assertEqual((missing.returncode, missing.stdout), (2, ''))
        self.assertIn('Usage:', missing.stderr)
        for command in ('typo', 'generate', 'collections', '--library', ''):
            with self.subTest(command=command):
                unknown = self.run_command([VIDEX, command])
                self.assertEqual((unknown.returncode, unknown.stdout), (2, ''))
                self.assertEqual(unknown.stderr, f'videx: unknown command: {command}\n')

    def test_routes_preserve_interpreter_arguments_working_directory_and_streams(self):
        args = ('space word', '', '--', '--apply', """Unicode 日本語, quotes '" and $(false)""", '')
        with tempfile.TemporaryDirectory(prefix='videx-dispatch-') as directory:
            root = Path(directory).resolve()
            checkout = root / 'Videx checkout 日本語'
            scripts = checkout / 'scripts'
            scripts.mkdir(parents=True)
            shutil.copy2(VIDEX, checkout)
            caller = root / 'external directory'
            caller.mkdir()
            relative_command = Path('..') / checkout.name / 'videx'
            environment = dict(os.environ, command='fixture π',
                               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
            environment.pop('PYTHONDONTWRITEBYTECODE', None)
            for command in ROUTES:
                with self.subTest(command=command):
                    if command == 'capture':
                        script = scripts / 'yt-bookmark.sh'
                        script.write_text(
                            '''printf '%s\\0' "$PWD" "$command" "$0" "$@"\n'''
                            'printf "delegated stderr\\n" >&2\nexit 37\n', encoding='utf-8')
                    else:
                        script = scripts / f'yt-bookmark-{command}.py'
                        script.write_text(
                            'import os, sys\n'
                            'assert sys.dont_write_bytecode\n'
                            'sys.stdout.write("\\0".join((os.getcwd(), os.environ["command"], *sys.argv)) + "\\0")\n'
                            'print("delegated stderr", file=sys.stderr)\n'
                            'sys.exit(37)\n', encoding='utf-8')
                    result = self.run_command([relative_command, command, *args],
                                              cwd=caller, env=environment)
                    self.assertEqual((result.returncode, result.stderr), (37, 'delegated stderr\n'))
                    cwd, context, invoked, *forwarded = result.stdout.split('\0')
                    self.assertEqual((cwd, context), (str(caller), 'fixture π'))
                    self.assertEqual(Path(invoked).resolve(), script)
                    self.assertEqual(forwarded, [*args, ''])

    def test_help_is_identical_to_direct_commands(self):
        for command in ROUTES:
            with self.subTest(command=command):
                direct = (['bash', batch.SCRIPTS / 'yt-bookmark.sh'] if command == 'capture'
                          else ['python3', '-B', batch.SCRIPTS / f'yt-bookmark-{command}.py'])
                expected = self.run_command([*direct, '--help'])
                result = self.run_command([VIDEX, command, '--help'])
                self.assertEqual(result.returncode, 0)
                self.assertEqual((result.stdout, result.stderr), (expected.stdout, expected.stderr))


class DispatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = batch.ImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.library = self.fixture.library
        self.index = self.library / 'index.html'

    def run_videx(self, *args, expected_status=0):
        result = subprocess.run([VIDEX, *args], cwd=self.fixture.root,
                                env=self.fixture.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected_status, (result.stdout, result.stderr))
        return result

    def test_capture_tag_index_find_and_upgrade(self):
        # The stub handles only local fixture output. Use a relative library
        # path and a literal option-like tag to exercise both boundaries.
        capture = ('capture', '--output', self.library.name, '--id', batch.A)
        self.run_videx(*capture)
        self.assertFalse(self.index.exists())
        self.assertEqual(len(self.fixture.calls()), 1)
        bookmark, = (self.library / 'bookmarks').rglob('*.html')
        original = bookmark.read_bytes()
        catalog = self.library / 'catalog.json'
        self.assertFalse(catalog.exists())

        skipped = self.run_videx(*capture, '--tag', '--apply', '--re-index')
        self.assertIn('SKIPPED', skipped.stdout)
        self.assertTrue(self.index.is_file())
        self.assertFalse(catalog.exists())
        self.assertEqual(len(self.fixture.calls()), 1)
        self.assertEqual(bookmark.read_bytes(), original)

        tag_args = ('--', self.library, 'add', batch.A, 'Research', '--apply')
        before = self.fixture.snapshot()
        self.run_videx('tag', *tag_args)
        self.assertEqual(self.fixture.snapshot(), before)
        self.run_videx('tag', '--apply', *tag_args)
        previous = self.index.read_bytes()
        self.assertNotIn(b'Research', previous)
        self.run_videx('index', self.library)
        self.assertIn(b'Research', self.index.read_bytes())
        result = self.run_videx('find', self.library, '--audit', '--show-tags', '--json')
        report = json.loads(result.stdout)
        self.assertEqual(result.stderr, '')
        self.assertEqual(report['exit_status'], 0)
        self.assertEqual(report['bookmarks'][0]['tags'], ['--apply', 'Research'])
        self.run_videx('find', self.library, '--url', 'https://example.invalid', expected_status=3)

        # Make a valid saved bookmark require an offline upgrade.
        outdated = original + b'\n<!-- old snapshot -->\n'
        bookmark.write_bytes(outdated)
        before = self.fixture.snapshot()
        self.assertIn('DRY RUN', self.run_videx('upgrade', self.library).stdout)
        self.assertEqual(self.fixture.snapshot(), before)
        self.run_videx('upgrade', self.library, '--apply')
        backup, = self.library.glob('bookmarks-backup-*')
        self.assertEqual((backup / bookmark.relative_to(self.library / 'bookmarks')).read_bytes(),
                         outdated)
        self.assertEqual(bookmark.read_bytes(), original)

    def test_import_preview_apply_rebuild_and_suppression(self):
        command = ('import', '--file', self.fixture.source.name, '--output', self.library, '--json')
        before = self.fixture.snapshot()
        result = self.run_videx(*command)
        self.assertEqual(json.loads(result.stdout)['index']['status'], 'preview_no_writes')
        self.assertEqual(self.fixture.snapshot(), before)

        result = self.run_videx(*command, '--apply')
        report = json.loads(result.stdout)
        self.assertIn('Capturing', result.stderr)
        self.assertEqual(report['summary']['created'], 2)
        self.assertEqual(report['index']['status'], 'rebuilt')
        self.assertEqual(report['index']['diagnostics'].count('Scanning '), 1)
        self.assertTrue(self.index.is_file())

        target = self.fixture.root / 'suppressed index'
        result = self.run_videx('import', '--file', self.fixture.source, '--output', target,
                                '--json', '--apply', '--no-re-index')
        report = json.loads(result.stdout)
        self.assertEqual(report['summary']['created'], 2)
        self.assertEqual(report['index']['status'], 'suppressed')
        self.assertFalse((target / 'index.html').exists())
