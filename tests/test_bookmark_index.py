"""Offline index publication tests confined to disposable repository fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(PROJECT / 'scripts')] + sys.path):
    spec = spec_from_file_location('bookmark_index', PROJECT / 'scripts' / 'yt-bookmark-index.py')
    indexer = module_from_spec(spec)
    spec.loader.exec_module(indexer)


@contextmanager
def umask(value):
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


class IndexWriteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='.index-test-', dir=PROJECT)
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.output = self.root / 'index.html'

    def assert_no_staging_files(self):
        self.assertEqual(list(self.root.glob('.index-*.tmp')), [])

    def test_existing_regular_index_preserves_permissions_and_exact_content(self):
        self.output.write_text('original', encoding='utf-8')
        self.output.chmod(0o640)
        with umask(0o077):
            indexer.write_index_atomic(self.output, '<html>café 動画</html>\n')
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o640)
        self.assertEqual(self.output.read_bytes(), '<html>café 動画</html>\n'.encode())
        self.assert_no_staging_files()

    def test_new_index_respects_umask(self):
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(mask=mask), umask(mask):
                output = self.root / f'new-{mask}.html'
                indexer.write_index_atomic(output, 'new index')
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o666 & ~mask)
        self.assert_no_staging_files()

    def test_symlink_destination_uses_umask_without_touching_target(self):
        target = self.root / 'sentinel'
        target.write_text('keep', encoding='utf-8')
        target.chmod(0o600)
        self.output.symlink_to(target)
        with umask(0o027):
            indexer.write_index_atomic(self.output, 'replacement')
        self.assertFalse(self.output.is_symlink())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o640)
        self.assertEqual(target.read_text(), 'keep')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_old_predictable_symlink_is_untouched(self):
        target = self.root / 'sentinel'
        target.write_text('keep', encoding='utf-8')
        old_temp = self.root / '.index.html.tmp'
        old_temp.symlink_to(target)
        indexer.write_index_atomic(self.output, 'new index')
        self.assertTrue(old_temp.is_symlink())
        self.assertEqual(target.read_text(), 'keep')
        self.assertEqual(self.output.read_text(), 'new index')

    def test_exclusive_creation_retries_collision_without_touching_symlink(self):
        target = self.root / 'sentinel'
        target.write_text('keep', encoding='utf-8')
        collision = self.root / '.index-collision.tmp'
        collision.symlink_to(target)
        with patch.object(indexer.secrets, 'token_hex', side_effect=['collision', 'unique']):
            indexer.write_index_atomic(self.output, 'new index')
        self.assertTrue(collision.is_symlink())
        self.assertEqual(target.read_text(), 'keep')
        self.assertFalse((self.root / '.index-unique.tmp').exists())

    def test_write_flush_and_close_failures_preserve_original_and_cleanup(self):
        real_open = Path.open
        for failure in ('write', 'flush', 'close'):
            with self.subTest(failure=failure):
                self.output.write_text('original', encoding='utf-8')

                class FailingStream:
                    def __init__(self, stream): self.stream = stream
                    def __enter__(self): return self
                    def __exit__(self, *args):
                        self.stream.close()
                        if failure == 'close': raise OSError('close failed')
                    def write(self, text):
                        if failure == 'write': raise OSError('write failed')
                        return self.stream.write(text)
                    def flush(self):
                        if failure == 'flush': raise OSError('flush failed')
                        self.stream.flush()
                    def fileno(self): return self.stream.fileno()

                def failing_open(path, mode='r', **kwargs):
                    stream = real_open(path, mode, **kwargs)
                    return FailingStream(stream) if mode == 'x' else stream

                with patch.object(Path, 'open', failing_open):
                    with self.assertRaisesRegex(OSError, failure + ' failed'):
                        indexer.write_index_atomic(self.output, 'replacement')
                self.assertEqual(self.output.read_text(), 'original')
                self.assert_no_staging_files()

    def test_creation_and_replacement_failures_preserve_original(self):
        self.output.write_text('original', encoding='utf-8')
        for target in ('open', 'replace'):
            with self.subTest(target=target):
                owner = Path if target == 'open' else indexer.os
                with patch.object(owner, target, side_effect=PermissionError('denied')):
                    with self.assertRaises(PermissionError):
                        indexer.write_index_atomic(self.output, 'replacement')
                self.assertEqual(self.output.read_text(), 'original')
                self.assert_no_staging_files()

    def test_cleanup_failure_keeps_original_error_and_reports_leftover(self):
        self.output.write_text('original', encoding='utf-8')
        with patch.object(indexer.os, 'replace', side_effect=OSError('replace failed')), \
                patch.object(Path, 'unlink', side_effect=PermissionError('cleanup failed')), \
                redirect_stderr(io.StringIO()) as warnings:
            with self.assertRaisesRegex(OSError, 'replace failed'):
                indexer.write_index_atomic(self.output, 'replacement')
        leftover, = self.root.glob('.index-*.tmp')
        self.assertIn(str(leftover), warnings.getvalue())
        self.assertIn('cleanup failed', warnings.getvalue())
        self.assertEqual(self.output.read_text(), 'original')

    def test_overlapping_builds_use_distinct_complete_staging_files(self):
        barrier = threading.Barrier(2, timeout=5)
        real_replace = os.replace
        staged = []
        payloads = ['<html>' + 'A' * 10000 + '</html>', '<html>' + 'B' * 10000 + '</html>']

        def synchronized_replace(source, destination):
            staged.append((source, source.read_text()))
            barrier.wait()
            real_replace(source, destination)

        with patch.object(indexer.os, 'replace', side_effect=synchronized_replace):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(indexer.write_index_atomic, self.output, text) for text in payloads]
                for future in futures: future.result(timeout=10)
        self.assertEqual(len({path for path, _ in staged}), 2)
        self.assertEqual({text for _, text in staged}, set(payloads))
        self.assertIn(self.output.read_text(), payloads)
        self.assert_no_staging_files()

    def test_cli_publishes_unchanged_generated_html_from_synthetic_bookmark(self):
        collection = self.root / 'bookmarks'
        collection.mkdir()
        metadata = {'id': 'AAAAAAAAAAA', 'title': 'Synthetic video', 'uploader': 'Synthetic channel'}
        (collection / 'fixture.html').write_text(
            '<script id="youtube-metadata">' + json.dumps(metadata) + '</script>', encoding='utf-8')
        real_writer = indexer.write_index_atomic
        with patch.object(sys, 'argv', ['index', str(self.root)]), \
                patch.object(indexer, 'Image', None), \
                patch.object(indexer, 'write_index_atomic', wraps=real_writer) as writer, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(indexer.main(), 0)
        self.assertEqual(self.output.read_text(), writer.call_args.args[1])
        self.assertIn('Synthetic video', self.output.read_text())
        self.assert_no_staging_files()


if __name__ == '__main__':
    unittest.main()
