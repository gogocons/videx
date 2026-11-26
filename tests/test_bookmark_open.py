"""Offline opener tests with disposable checkouts and harmless desktop launchers."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_bookmark_import import PROJECT, SCRIPTS, load

opener = load('yt-bookmark-open')


class OpenTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='videx-open-')
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name).resolve()
        self.checkout = self.root / 'Videx checkout 日本語'
        scripts = self.checkout / 'scripts'
        scripts.mkdir(parents=True)
        self.command = shutil.copy2(PROJECT / 'videx', self.checkout)
        for name in ('yt-bookmark-open.py', 'videx_config.py'):
            shutil.copy2(SCRIPTS / name, scripts)
        self.config = self.checkout / 'config.local.json'
        self.library = self.root / """Library π '" # % ; $(echo wrong) * ? [x] &"""
        self.library.mkdir()
        self.index = self.library / 'index.html'
        self.index.write_bytes(b'An existing snapshot')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'bash').symlink_to('/bin/bash')
        (self.bin / 'python3').symlink_to(sys.executable)
        for name in ('open', 'xdg-open'):
            stub = self.bin / name
            stub.write_text('#!/bin/bash\nprintf "%s\\0" "${0##*/}" "$@"\n', encoding='utf-8')
            stub.chmod(0o700)

    def snapshot(self):
        result = {}
        for path in self.root.rglob('*'):
            info = path.lstat()
            content = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
            result[path] = info.st_mode, info.st_mtime_ns, content
        return result

    def run_cli(self, *args, cwd=None):
        return subprocess.run([self.command, 'open', *args], capture_output=True, text=True,
                              cwd=cwd or self.checkout, timeout=10,
                              env={'PATH': str(self.bin), 'HOME': str(self.root),
                                   'DISPLAY': ':fixture'})

    def run_open(self, *args, platform='darwin', desktop=None, launch_error=None,
                 unreadable=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('videx_config.CONFIG_PATH', self.config), \
                patch('videx_config.PROJECT_ROOT', self.checkout), \
                patch.object(sys, 'platform', platform), \
                patch.dict(os.environ, desktop or {}, clear=True), \
                patch.object(opener.subprocess, 'run', side_effect=launch_error) as launcher, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            if unreadable:
                with patch.object(opener.os, 'open', side_effect=PermissionError('synthetic denial')):
                    status = opener.main(args)
            else:
                status = opener.main(args)
        return status, stdout.getvalue(), stderr.getvalue(), launcher

    @unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'native opener platform')
    def test_library_selection_and_exact_native_launcher_argument(self):
        # A catalog is unnecessary, even if present and malformed.
        (self.library / 'catalog.json').write_text('{invalid', encoding='utf-8')
        fallback = self.checkout / 'index.html'
        fallback.write_bytes(b'Project fallback')
        launcher = 'open' if sys.platform == 'darwin' else 'xdg-open'
        runs = (
            (None, (), self.checkout, fallback),
            ({'library': '~/' + self.library.name}, (), self.root, self.index),
            ({'library': str(self.checkout)}, ('--library', self.library), self.root, self.index),
            ('{invalid', ('--library', self.library.name), self.root, self.index),
            ('{invalid', ('--library', '~/' + self.library.name), self.checkout, self.index),
        )
        for config, args, cwd, expected in runs:
            with self.subTest(config=config, args=args, cwd=cwd):
                if config is not None:
                    text = config if isinstance(config, str) else json.dumps(config)
                    self.config.write_text(text, encoding='utf-8')
                before = self.snapshot()
                result = self.run_cli(*args, cwd=cwd)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                self.assertEqual(result.stdout.split('\0'), [launcher, str(expected), ''])
                self.assertEqual(self.snapshot(), before)

    def test_help_and_invalid_inputs_do_not_launch(self):
        self.config.write_text('{invalid', encoding='utf-8')
        for args, status, message in (
            (('--help',), 0, '--library DIR'),
            (('--unknown',), 2, 'unrecognized arguments'),
            (('--library',), 2, 'expected one argument'),
            (('--library', ''), 2, 'nonempty path'),
            ((), 2, 'Cannot read'),
            (('--library', self.library / 'bookmarks'), 2, 'expects the library root'),
        ):
            with self.subTest(args=args):
                before = self.snapshot()
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, status)
                self.assertIn(message, result.stdout if status == 0 else result.stderr)
                if status != 0:
                    self.assertEqual(result.stdout, '')
                self.assertEqual(self.snapshot(), before)

    def test_library_and_index_must_exist(self):
        for path in (self.root / 'missing', self.bin, self.index):
            with self.subTest(path=path):
                status, _, stderr, launcher = self.run_open('--library', str(path))
                self.assertEqual(status, 1)
                self.assertIn(str(path / 'index.html'), stderr)
                launcher.assert_not_called()

        (self.checkout / 'index.html').write_bytes(b'Fallback must not be selected')
        self.config.write_text(json.dumps({'library': str(self.bin)}), encoding='utf-8')
        status, _, stderr, launcher = self.run_open()
        self.assertEqual(status, 1)
        self.assertIn(str(self.bin / 'index.html'), stderr)
        launcher.assert_not_called()

    def test_unusable_index_entries_never_launch(self):
        def make_fifo():
            try:
                os.mkfifo(self.index)
            except (OSError, AttributeError) as exc:
                self.skipTest(f'FIFO fixture unavailable: {exc}')

        target = self.root / 'target.html'
        target.write_bytes(b'Linked file')
        cases = (
            ('empty', self.index.touch, 'empty'),
            ('directory', self.index.mkdir, 'regular'),
            ('symlink', lambda: self.index.symlink_to(target), 'symlink'),
            ('dangling', lambda: self.index.symlink_to(self.root / 'absent'), 'symlink'),
            ('FIFO', make_fifo, 'regular'),
        )
        self.index.unlink()
        for entry, create, diagnostic in cases:
            with self.subTest(entry=entry):
                create()
                before = self.snapshot()
                status, _, stderr, launcher = self.run_open('--library', str(self.library))
                self.assertEqual(status, 1)
                self.assertIn(str(self.index), stderr)
                self.assertIn(diagnostic, stderr)
                launcher.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                if entry == 'directory':
                    self.index.rmdir()
                else:
                    self.index.unlink()

    def test_unreadable_index_never_launches(self):
        # Mock the read-open operation so this also works for elevated users.
        status, _, stderr, launcher = self.run_open('--library', str(self.library), unreadable=True)
        self.assertEqual(status, 1)
        self.assertIn('synthetic denial', stderr)
        self.assertIn(str(self.index), stderr)
        launcher.assert_not_called()

    def test_platform_launcher_selection_and_failures(self):
        for platform, desktop, expected in (
            ('darwin', {}, 'open'),
            ('linux', {'DISPLAY': ':1'}, 'xdg-open'),
            ('linux', {'WAYLAND_DISPLAY': 'wayland-0'}, 'xdg-open'),
        ):
            with self.subTest(platform=platform, desktop=desktop):
                status, out, stderr, launcher = self.run_open(
                    '--library', str(self.library), platform=platform, desktop=desktop)
                self.assertEqual((status, out, stderr), (0, '', ''))
                launcher.assert_called_once_with([expected, str(self.index)], check=True)
        for platform, error, message in (
            ('unsupported', None, 'unsupported platform'),
            ('linux', None, 'no graphical desktop session'),
            ('darwin', FileNotFoundError(), 'unavailable on PATH'),
            ('darwin', subprocess.CalledProcessError(17, 'open'), 'failed (status 17)'),
            ('darwin', PermissionError('launcher permission denied'), 'permission denied'),
        ):
            with self.subTest(platform=platform, message=message):
                status, out, stderr, launcher = self.run_open(
                    '--library', str(self.library), platform=platform, launch_error=error)
                self.assertEqual((status, out), (1, ''))
                self.assertIn(message, stderr)
                self.assertIn(str(self.index), stderr)
                self.assertEqual(launcher.call_count, int(error is not None))
