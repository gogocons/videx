"""Filesystem safety checks using disposable objects, never bookmark libraries."""
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'scripts')] + sys.path):
    import videx_inventory as inventory


def snapshot(root):
    """Record objects without opening symlinks, FIFOs, sockets, or devices."""
    root = Path(root)
    result = {}

    def visit(path):
        info = path.lstat()
        content = (path.read_bytes() if stat.S_ISREG(info.st_mode) else
                   os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
        result[str(path.relative_to(root))] = info.st_mode, info.st_mtime_ns, content
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)

    visit(root)
    return result


class InventoryTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='vx-', dir='/tmp')
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.collection = self.root / 'bookmarks'
        self.collection.mkdir()

    def test_regular_files_order_exclusions_and_index_named_directories(self):
        directory = self.collection / 'index.html'
        directory.mkdir()
        expected = [directory / 'nested.html', self.collection / 'z.html', self.collection / 'a.html']
        for path in expected:
            path.write_text('regular')
        (directory / 'INDEX.html').write_text('generated index')
        for name in ('ignored.HTML', 'notes.txt'):
            (self.collection / name).write_text('ignored')
        paths, issues, available = inventory.scan_bookmarks(self.collection)
        self.assertEqual(paths, sorted(expected))
        self.assertEqual(issues, [])
        self.assertTrue(available)
        self.assertEqual(inventory.read_bookmark_text(expected[0]), 'regular')

    def test_read_can_preserve_line_endings_without_relaxing_utf8_validation(self):
        path = self.collection / 'newlines.html'
        text = 'Café\r\nSecond\rThird\n'
        path.write_bytes(text.encode('utf-8'))
        self.assertEqual(inventory.read_bookmark_text(path), 'Café\nSecond\nThird\n')
        self.assertEqual(inventory.read_bookmark_text(path, preserve_newlines=True), text)
        path.write_bytes(b'\xff\r\n')
        for preserve_newlines in (False, True):
            with self.subTest(preserve_newlines=preserve_newlines), self.assertRaises(UnicodeDecodeError):
                inventory.read_bookmark_text(path, preserve_newlines=preserve_newlines)

    def test_links_and_fifo_are_not_read_or_traversed(self):
        target = self.root / 'external'
        target.mkdir()
        external = target / 'secret.html'
        external.write_text('external sentinel')
        file_link = self.collection / 'file.html'
        file_link.symlink_to(external)
        directory_link = self.collection / 'directory'
        directory_link.symlink_to(target, target_is_directory=True)
        dangling = self.collection / 'dangling.html'
        dangling.symlink_to(self.root / 'missing')
        fifo = self.collection / 'pipe.html'
        os.mkfifo(fifo)
        before = snapshot(self.root)
        with patch.object(Path, 'open', side_effect=AssertionError('must not read content')):
            paths, issues, available = inventory.scan_bookmarks(self.collection)
            for path in (file_link, directory_link, dangling, fifo):
                for preserve_newlines in (False, True):
                    with self.subTest(path=path, preserve_newlines=preserve_newlines), self.assertRaises(OSError):
                        inventory.read_bookmark_text(path, preserve_newlines=preserve_newlines)
        self.assertEqual(paths, [])
        self.assertEqual({path for path, _ in issues}, {file_link, directory_link, dangling, fifo})
        self.assertTrue(available)
        self.assertEqual(snapshot(self.root), before)

    def test_socket_is_rejected_without_reading(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        path = self.collection / 'socket.html'
        try:
            sock.bind(str(path))
        except OSError as exc:
            self.skipTest(f'Unix socket fixture unavailable: {exc}')
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read socket')):
            self.assertEqual(inventory.scan_bookmarks(self.collection)[1][0][0], path)
            with self.assertRaises(OSError):
                inventory.read_bookmark_text(path)

    def test_invalid_roots_are_unavailable_without_enumeration(self):
        link = self.root / 'linked'
        link.symlink_to(self.collection, target_is_directory=True)
        regular = self.root / 'regular'
        regular.write_text('keep')
        for root in (link, regular, self.root / 'missing'):
            with self.subTest(root=root), patch.object(inventory.os, 'scandir') as scan:
                paths, issues, available = inventory.scan_bookmarks(root)
                self.assertEqual(paths, [])
                self.assertEqual(issues[0][0], root)
                self.assertFalse(available)
                scan.assert_not_called()

    def test_device_modes_are_rejected_before_reading(self):
        for mode in (stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK, stat.S_IFIFO):
            entry = Mock(name='entry')
            entry.name = 'special.html'
            entry.path = str(self.collection / entry.name)
            entry.stat.return_value = SimpleNamespace(st_mode=mode)
            context = Mock()
            context.__enter__ = Mock(return_value=iter([entry]))
            context.__exit__ = Mock(return_value=False)
            with self.subTest(mode=mode), patch.object(inventory.os, 'scandir', return_value=context):
                paths, issues, available = inventory.scan_bookmarks(self.collection)
                self.assertEqual(paths, [])
                self.assertEqual(len(issues), 1)
                self.assertTrue(available)
                entry.stat.assert_called_once_with(follow_symlinks=False)
            with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=mode)), \
                    patch.object(Path, 'read_text') as reader:
                with self.assertRaises(OSError):
                    inventory.read_bookmark_text(Path(entry.path))
                reader.assert_not_called()

    def test_enumeration_failures_retain_root_and_nested_availability(self):
        nested = self.collection / 'nested'
        nested.mkdir()
        good = self.collection / 'good.html'
        good.write_text('good')
        real_scan = os.scandir
        for denied in (self.collection, nested):
            def scan(path):
                if Path(path) == denied:
                    raise PermissionError('synthetic enumeration failure')
                return real_scan(path)
            with self.subTest(denied=denied), patch.object(inventory.os, 'scandir', side_effect=scan):
                paths, issues, available = inventory.scan_bookmarks(self.collection)
            self.assertEqual(issues[0][0], denied)
            self.assertEqual(available, denied != self.collection)
            self.assertEqual(paths, [good] if available else [])

    def test_stat_disappearance_and_read_failures_are_not_suppressed(self):
        entry = Mock()
        entry.name = 'vanished.html'
        entry.path = str(self.collection / entry.name)
        entry.stat.side_effect = FileNotFoundError('synthetic disappearance')
        context = Mock()
        context.__enter__ = Mock(return_value=iter([entry]))
        context.__exit__ = Mock(return_value=False)
        with patch.object(inventory.os, 'scandir', return_value=context):
            paths, issues, available = inventory.scan_bookmarks(self.collection)
        self.assertEqual(paths, [])
        self.assertIsInstance(issues[0][1], FileNotFoundError)
        self.assertTrue(available)
        good = self.collection / 'good.html'
        good.write_text('good')
        for failure in (PermissionError('denied'), FileNotFoundError('gone'), UnicodeError('bad encoding')):
            with self.subTest(failure=failure), patch.object(Path, 'read_text', side_effect=failure):
                with self.assertRaises(type(failure)):
                    inventory.read_bookmark_text(good)


if __name__ == '__main__':
    unittest.main()
