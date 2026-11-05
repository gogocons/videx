"""Offline index publication tests confined to disposable repository fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from html.parser import HTMLParser
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


class IndexDocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.active = None
        self.styles = []
        self.in_style = False
        self.color_schemes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.active = {'attrs': attrs, 'text': ''}
            self.scripts.append(self.active)
        if tag == 'style':
            self.in_style = True
        if tag == 'meta' and attrs.get('name') == 'color-scheme':
            self.color_schemes.append(attrs.get('content'))

    def handle_endtag(self, tag):
        if tag == 'script': self.active = None
        if tag == 'style': self.in_style = False

    def handle_data(self, data):
        if self.active is not None: self.active['text'] += data
        if self.in_style: self.styles.append(data)


class CatalogIndexTests(unittest.TestCase):
    def setUp(self):
        IndexWriteTests.setUp(self)
        self.collection = self.root / 'bookmarks'
        self.collection.mkdir()

    assert_no_staging_files = IndexWriteTests.assert_no_staging_files

    def bookmark(self, name='renamed.html', identity='AAAAAAAAAAA', collection=None):
        path = (collection or self.collection) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<script id="youtube-metadata">' + json.dumps({
            'id': identity, 'title': 'Synthetic title', 'uploader': 'Synthetic channel',
            'description': 'Synthetic description', 'upload_date': '20260102',
        }) + '</script>', encoding='utf-8')
        return path

    def catalog(self, records, library=None):
        path = (library or self.root) / 'catalog.json'
        path.write_text(json.dumps({'version': 1, 'bookmarks': records, 'collections': {}}), encoding='utf-8')
        return path

    def run_index(self, directory=None, configured=False):
        argv = ['index'] if configured else ['index', str(directory or self.root)]
        with patch.object(sys, 'argv', argv), patch.object(indexer, 'Image', None), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            status = indexer.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def parsed_output(self, output=None):
        parser = IndexDocumentParser()
        parser.feed((output or self.output).read_text(encoding='utf-8'))
        parser.close()
        payload, = [script['text'] for script in parser.scripts if script['attrs'].get('id') == 'bookmark-data']
        return json.loads(payload), parser

    def test_missing_catalog_and_empty_records_yield_no_personal_tags(self):
        self.bookmark()
        self.assertEqual(self.run_index()[0], 0)
        self.assertFalse((self.root / 'catalog.json').exists())
        self.assertEqual(self.parsed_output()[0]['items'][0]['personalTags'], [])
        for record in ({}, {'AAAAAAAAAAA': {}}, {'AAAAAAAAAAA': {'tags': []}}):
            with self.subTest(record=record):
                self.catalog(record)
                self.assertEqual(self.run_index()[0], 0)
                self.assertEqual(self.parsed_output()[0]['items'][0]['personalTags'], [])

    def test_validated_case_sensitive_ids_and_orphans(self):
        self.bookmark(identity='AAAAAAAAAAA')
        self.bookmark('lowercase.html', 'aaaaaaaaaaa')
        self.catalog({'AAAAAAAAAAA': {'tags': [' Python ', 'python', 'HIDDEN']},
                      'aaaaaaaaaaa': {'tags': ['lowercase']},
                      'BBBBBBBBBBB': {'tags': ['orphan-only']}})
        self.assertEqual(self.run_index()[0], 0)
        items = {item['id']: item for item in self.parsed_output()[0]['items']}
        self.assertEqual(items['AAAAAAAAAAA']['personalTags'], ['Python', 'hidden'])
        self.assertEqual(items['aaaaaaaaaaa']['personalTags'], ['lowercase'])
        self.assertNotIn('BBBBBBBBBBB', items)
        self.assertNotIn('orphan-only', self.output.read_text())

    def test_explicit_library_bookmarks_and_configured_external_library(self):
        library = self.root / 'External library'
        collection = library / 'bookmarks'
        self.bookmark(collection=collection)
        self.catalog({'AAAAAAAAAAA': {'tags': ['selected external library']}}, library)
        # A sibling catalog must never be used for the selected external library.
        self.catalog({'AAAAAAAAAAA': {'tags': ['wrong catalog']}})
        for directory in (library, collection):
            with self.subTest(directory=directory), \
                    patch.object(indexer, 'default_library', side_effect=AssertionError('explicit path must override default')):
                self.assertEqual(self.run_index(directory)[0], 0)
                item = self.parsed_output(library / 'index.html')[0]['items'][0]
                self.assertEqual(item['personalTags'], ['selected external library'])
                self.assertEqual(item['href'], 'bookmarks/renamed.html')
        config = self.root / 'synthetic-config.json'
        config.write_text(json.dumps({'library': str(library)}))
        with patch.object(sys.modules['videx_config'], 'CONFIG_PATH', config):
            self.assertEqual(self.run_index(configured=True)[0], 0)
        self.assertEqual(self.parsed_output(library / 'index.html')[0]['items'][0]['personalTags'],
                         ['selected external library'])

    def test_legacy_directory_fallback_still_selects_local_catalog(self):
        selected = self.root / 'old-layout'
        self.bookmark(collection=selected)
        self.catalog({'AAAAAAAAAAA': {'tags': ['old layout']}}, selected)
        self.assertEqual(self.run_index(selected)[0], 0)
        item = self.parsed_output(selected / 'index.html')[0]['items'][0]
        self.assertEqual(item['href'], 'renamed.html')
        self.assertEqual(item['personalTags'], ['old layout'])

    def test_catalog_is_loaded_and_read_once(self):
        self.bookmark()
        self.bookmark('another.html', 'BBBBBBBBBBB')
        catalog = self.catalog({'AAAAAAAAAAA': {'tags': ['topic']}})
        read_text = Path.read_text
        reads = []
        def read(path, *args, **kwargs):
            if path == catalog: reads.append(path)
            return read_text(path, *args, **kwargs)
        with patch.object(indexer, 'load_catalog_tags', wraps=indexer.load_catalog_tags) as loader, \
                patch.object(Path, 'read_text', read):
            self.assertEqual(self.run_index()[0], 0)
        loader.assert_called_once_with(self.root)
        self.assertEqual(reads, [catalog])

    def test_helper_import_does_not_execute_lookup_cli(self):
        self.catalog({'AAAAAAAAAAA': {'tags': ['topic']}})
        with patch('argparse.ArgumentParser.parse_args', side_effect=AssertionError('CLI invoked during import')):
            self.assertEqual(indexer.load_catalog_tags(self.root), {'AAAAAAAAAAA': ['topic']})

    def assert_catalog_refused(self):
        self.output.write_bytes(b'preserve existing index\n')
        self.output.chmod(0o640)
        with patch.object(indexer, 'write_index_atomic') as writer:
            status, _, stderr = self.run_index()
        self.assertEqual(status, 2)
        self.assertIn('Cannot load personal tags', stderr)
        self.assertNotIn('Traceback', stderr)
        writer.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b'preserve existing index\n')
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o640)
        self.assert_no_staging_files()

    def test_invalid_catalogs_preserve_index_before_scanning(self):
        path = self.root / 'catalog.json'
        invalid = ['{broken', '[]', '{"version":2,"bookmarks":{},"collections":{}}',
                   '{"version":1,"bookmarks":{},"collections":[]}']
        invalid += [json.dumps({'version': 1, 'bookmarks': {'AAAAAAAAAAA': {'tags': tags}}, 'collections': {}})
                    for tags in ('text', [1], [None], [{}], [''], ['x' * 81], ['escape\x1b'])]
        # No bookmarks exist: catalog validation must still run, before scanning.
        for content in invalid:
            with self.subTest(content=content):
                path.write_text(content)
                with patch.object(indexer, 'parse_bookmark', side_effect=AssertionError('should not scan')):
                    self.assert_catalog_refused()
                self.assertEqual(path.read_text(), content)

    def test_symlink_directory_and_special_catalogs_are_refused(self):
        catalog = self.root / 'catalog.json'
        sentinel = self.root / 'sentinel'
        sentinel.write_text('unchanged')
        catalog.symlink_to(sentinel)
        self.assert_catalog_refused()
        self.assertTrue(catalog.is_symlink())
        self.assertEqual(sentinel.read_text(), 'unchanged')
        catalog.unlink()
        catalog.mkdir()
        self.assert_catalog_refused()
        catalog.rmdir()
        os.mkfifo(catalog)
        self.assert_catalog_refused()

    def test_unreadable_catalog_preserves_index(self):
        catalog = self.catalog({})
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            if path == catalog: raise PermissionError('synthetic read denied')
            return read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            self.assert_catalog_refused()

    def test_missing_catalog_with_no_bookmarks_keeps_existing_exit_status(self):
        self.output.write_text('keep')
        with patch.object(indexer, 'write_index_atomic') as writer:
            self.assertEqual(self.run_index()[0], 1)
        writer.assert_not_called()
        self.assertEqual(self.output.read_text(), 'keep')

    def test_tags_are_html_safe_and_rendered_as_text_with_wrapping(self):
        self.bookmark()
        tags = ['</script><script>window.injected=1</script>', '<img src=x onerror=alert(1)>', 'café "quoted" & 動画']
        self.catalog({'AAAAAAAAAAA': {'tags': tags}})
        self.assertEqual(self.run_index()[0], 0)
        data, parser = self.parsed_output()
        self.assertEqual(data['items'][0]['personalTags'], tags)
        self.assertEqual(len(parser.scripts), 2)
        self.assertTrue(all('src' not in script['attrs'] for script in parser.scripts))
        script, = [s['text'] for s in parser.scripts if s['attrs'].get('id') != 'bookmark-data']
        self.assertIn('el.textContent=txt', script)
        self.assertNotIn('innerHTML', script)
        self.assertIn("node('span','personal-tag',tag)", script)
        self.assertIn('if(Array.isArray(x.personalTags)&&x.personalTags.length)', script)
        self.assertIn("node('span','personal-tags-label','Personal tags')", script)
        css = ''.join(parser.styles)
        self.assertIn('flex-wrap:wrap', css.split('.personal-tag-list{', 1)[1].split('}', 1)[0])
        badge_css = css.split('.personal-tag{', 1)[1].split('}', 1)[0]
        self.assertIn('color:inherit', badge_css)
        self.assertIn('overflow-wrap:anywhere', badge_css)
        # Preserve the current template's dark scheme; no new theme or toggle.
        self.assertEqual(parser.color_schemes, ['dark'])
        self.assertIn('color:#e9edf4;background:#101217', css)
        render = script.split('function render()', 1)[1]
        self.assertNotIn('personalTags', render)
        self.assertIn('[x.title, x.channel, x.description, x.id]', render)
        self.assertIn("c === '__single_entries__'", render)
        self.assertIn('channelCounts.get(x.channel) === 1', render)
        self.assertIn('switch(s)', render)
        self.assertIn('<option value="">All Channels</option>', self.output.read_text())

    def test_build_only_writes_index_not_bookmarks_or_catalog(self):
        bookmark = self.bookmark()
        catalog = self.catalog({'AAAAAAAAAAA': {'tags': ['topic']}})
        before = {path: (path.read_bytes(), path.stat().st_mode) for path in (bookmark, catalog)}
        self.assertEqual(self.run_index()[0], 0)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mode) for path in before}, before)
        self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])
        self.assert_no_staging_files()


if __name__ == '__main__':
    unittest.main()
