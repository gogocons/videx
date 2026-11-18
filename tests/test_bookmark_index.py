"""Offline index publication tests confined to disposable repository fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from html.parser import HTMLParser
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from test_videx_inventory import snapshot
from test_bookmark_tag import duplicate_catalog_documents
from test_bookmark_generate import assert_browser_json, numeric_metadata_documents, strict_json_loads


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
        self.elements = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
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

    def bookmark(self, name='renamed.html', identity='AAAAAAAAAAA', collection=None,
                 metadata=None, thumbnail=None):
        path = (collection or self.collection) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        values = {
            'id': identity, 'title': 'Synthetic title', 'uploader': 'Synthetic channel',
            'description': 'Synthetic description', 'upload_date': '20260102',
        }
        values.update(metadata or {})
        image = f'<img src="{thumbnail}">' if thumbnail else ''
        path.write_text(image + '<script id="youtube-metadata">' + json.dumps(values)
                        + '</script>', encoding='utf-8')
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

    def browser_results(self, cases, tag_overrides=None):
        """Execute the generated script, using a minimal DOM rather than a browser dependency."""
        runtime = shutil.which('deno') or shutil.which('node')
        if runtime is None:
            self.skipTest('Deno or Node.js required for generated JavaScript behavior checks')
        data, parser = self.parsed_output()
        for item in data['items']:
            if tag_overrides and item['id'] in tag_overrides:
                item.pop('personalTags', None)
                item.update(tag_overrides[item['id']])
        script, = [s['text'] for s in parser.scripts if s['attrs'].get('id') != 'bookmark-data']
        harness = r'''
class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.value = ''; this.textContent = '';
        this.handlers = {}; this.hidden = false; this.attributes = {};
    }
    append(...nodes) {
        for (const node of nodes) {
            if (node.tag === 'fragment') this.children.push(...node.children);
            else this.children.push(node);
        }
    }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(event, handler) { this.handlers[event] = handler; }
    focus() { document.activeElement = this; }
}
const elements = Object.fromEntries(
    ['bookmark-data', 'search', 'channel', 'sort', 'grid', 'count', 'empty', 'stats', 'footer',
     'filters', 'filter-panel', 'personal-tag', 'filter-chips', 'clear-filters']
        .map(id => [id, new Element(id)]));
elements['bookmark-data'].textContent = JSON.stringify(input.data);
elements.sort.value = 'upload-desc';
elements['filter-panel'].hidden = true;
elements.filters.setAttribute('aria-expanded', 'false');
globalThis.document = {
    getElementById: id => elements[id],
    createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element('fragment'),
    handlers: {},
    addEventListener(event, handler) { this.handlers[event] = handler; },
    activeElement: null,
};
eval(input.script);
const snapshot = () => ({
    hrefs: elements.grid.children.map(card => card.children[0].href),
    count: elements.count.textContent,
    emptyHidden: elements.empty.hidden,
    stats: elements.stats.textContent,
    channels: elements.channel.children.map(option => option.value),
    tags: elements['personal-tag'].children.map(option => ({key: option.value, label: option.textContent})),
    filters: elements.filters.textContent,
    panelHidden: elements['filter-panel'].hidden,
    expanded: elements.filters.attributes['aria-expanded'],
    chipsHidden: elements['filter-chips'].hidden,
    chips: elements['filter-chips'].children.map(chip => ({text: chip.textContent, name: chip.attributes['aria-label']})),
    focus: Object.keys(elements).find(id => elements[id] === document.activeElement) || null,
    query: elements.search.value, channel: elements.channel.value,
    tag: elements['personal-tag'].value, sort: elements.sort.value,
});
const results = [snapshot()];
for (const test of input.cases) {
    if (test.action === 'escape') {
        document.handlers.keydown({key: 'Escape', preventDefault() {}});
    } else if (test.action === 'chip') {
        const chip = elements['filter-chips'].children[test.index];
        chip.focus();chip.handlers.click();
    } else if (test.action === 'click') {
        elements[test.control].focus();elements[test.control].handlers.click();
    } else {
        // Existing cases describe complete states; actions above preserve state.
        elements.search.value = test.query || '';
        elements.channel.value = test.channel || '';
        elements['personal-tag'].value = test.tag || '';
        elements.sort.value = test.sort || 'upload-desc';
        const control = test.control || 'search';
        elements[control].handlers[control === 'search' ? 'input' : 'change']();
    }
    results.push(snapshot());
}
console.log(JSON.stringify(results));
'''
        if Path(runtime).name == 'deno':
            bootstrap = 'const input = await new Response(Deno.stdin.readable).json();\n'
            command = [runtime, 'eval', bootstrap + harness]
        else:
            bootstrap = "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
            command = [runtime, '-e', bootstrap + harness]
        result = subprocess.run(command, input=json.dumps({'data': data, 'script': script, 'cases': cases}),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_generated_search_matches_individual_tags_and_existing_fields(self):
        self.bookmark('tagged.html', metadata={'title': 'TitleNeedle', 'uploader': 'ChannelNeedle',
                                             'description': 'DescriptionNeedle'})
        self.bookmark('untagged.html', 'BBBBBBBBBBB')
        self.catalog({'AAAAAAAAAAA': {'tags': [' Research ', 'Machine Learning', 'Alpha', 'Beta']}})
        self.assertEqual(self.run_index()[0], 0)
        queries = ['research', 'RESEARCH', '  seARch  ', 'learning', 'alpha', 'beta',
                   'titleneedle', 'channelneedle', 'descriptionneedle', 'aaaaaaa',
                   'alpha beta', 'unmatched', '', '   ']
        results = self.browser_results([{'query': query} for query in queries])
        self.assertEqual(len(results[0]['hrefs']), 2)
        for query, result in zip(queries, results[1:]):
            with self.subTest(query=query):
                expected = [] if query in ('alpha beta', 'unmatched') else (
                    ['bookmarks/tagged.html', 'bookmarks/untagged.html'] if not query.strip()
                    else ['bookmarks/tagged.html'])
                self.assertEqual(result['hrefs'], expected)
                self.assertEqual(result['count'], f'Showing {len(expected)} of 2 bookmarks')
                self.assertEqual(result['emptyHidden'], bool(expected))

    def test_generated_search_handles_missing_and_empty_tags(self):
        self.bookmark('missing.html')
        self.bookmark('empty.html', 'BBBBBBBBBBB')
        self.bookmark('null.html', 'CCCCCCCCCCC')
        self.assertEqual(self.run_index()[0], 0)
        # Missing catalogs already produce []; also exercise absent/null array guards.
        results = self.browser_results([{'query': 'synthetic'}, {'query': 'tag-only'}, {'query': ''}],
                                       {'AAAAAAAAAAA': {}, 'CCCCCCCCCCC': {'personalTags': None}})
        self.assertEqual([len(result['hrefs']) for result in results], [3, 3, 0, 3])
        self.assertEqual(results[2]['count'], 'Showing 0 of 3 bookmarks')
        self.assertFalse(results[2]['emptyHidden'])

    def test_generated_tag_search_preserves_channels_sorting_counts_and_hidden_exclusion(self):
        for name, identity, title, channel, uploaded in (
                ('one', 'AAAAAAAAAAA', 'Zulu', 'Shared', '20260101'),
                ('two', 'BBBBBBBBBBB', 'Alpha', 'Shared', '20260103'),
                ('single', 'CCCCCCCCCCC', 'Middle', 'Solo', '20260102'),
                ('hidden', 'DDDDDDDDDDD', 'HiddenTitle', 'HiddenChannel', '20260104')):
            self.bookmark(name + '.html', identity, metadata={
                'title': title, 'uploader': channel, 'upload_date': uploaded})
        self.catalog({identity: {'tags': tags} for identity, tags in (
            ('AAAAAAAAAAA', ['Topic', 'hidden-topic']), ('BBBBBBBBBBB', ['Topic']),
            ('CCCCCCCCCCC', ['Topic', 'unhidden']),
            ('DDDDDDDDDDD', [' HiDdEn ', 'Topic', 'HiddenExclusiveTag']))})
        self.assertEqual(self.run_index()[0], 0)
        cases = [
            {'query': 'topic', 'channel': 'Shared', 'control': 'channel'},
            {'query': 'topic', 'channel': '__single_entries__', 'control': 'channel'},
            *[{'query': 'topic', 'sort': sort, 'control': 'sort'} for sort in
              ('upload-desc', 'upload-asc', 'title-asc', 'title-desc', 'channel-asc')],
            {'query': 'HiddenExclusiveTag'}, {'query': 'HiddenTitle'},
            {'query': 'DDDDDDDDDDD'}, {'query': 'hidden'},
        ]
        results = self.browser_results(cases)
        orders = [['two', 'single', 'one'], ['two', 'one'], ['single'],
                  ['two', 'single', 'one'], ['one', 'single', 'two'],
                  ['two', 'single', 'one'], ['one', 'single', 'two'],
                  ['one', 'two', 'single'], [], [], [], ['single', 'one']]
        for result, order in zip(results, orders):
            self.assertEqual(result['hrefs'], [f'bookmarks/{name}.html' for name in order])
            self.assertEqual(result['count'], f'Showing {len(order)} of 3 bookmarks')
            self.assertEqual(result['emptyHidden'], bool(order))
            self.assertEqual(result['stats'], '3 bookmarks · 2 channels')
            self.assertEqual(result['channels'], ['__single_entries__', 'Shared'])

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

    def test_tag_filter_exact_unicode_identity_combines_with_search_channel_and_sort(self):
        for name, identity, title, channel, uploaded in (
                ('one', 'AAAAAAAAAAA', 'Zulu', 'Shared', '20260101'),
                ('two', 'BBBBBBBBBBB', 'Alpha', 'Shared', '20260103'),
                ('solo', 'CCCCCCCCCCC', 'Middle', 'Solo', '20260102'),
                ('hidden', 'DDDDDDDDDDD', 'Excluded', 'Secret', '20260104')):
            self.bookmark(name + '.html', identity, metadata={
                'title': title, 'uploader': channel, 'upload_date': uploaded})
        self.catalog({
            'AAAAAAAAAAA': {'tags': [' Straße ', 'Σ', 'Topic']},
            'BBBBBBBBBBB': {'tags': ['STRASSE', 'ς', 'Topic']},
            'CCCCCCCCCCC': {'tags': ['Straßen', 'Topic']},
            'DDDDDDDDDDD': {'tags': ['hidden', 'SecretTag']},
            'EEEEEEEEEEE': {'tags': ['OrphanTag']},
        })
        self.assertEqual(self.run_index()[0], 0)
        data, _ = self.parsed_output()
        self.assertEqual(data['personalTagOptions'], [
            {'key': 'strasse', 'label': 'Straße'}, {'key': 'strassen', 'label': 'Straßen'},
            {'key': 'topic', 'label': 'Topic'}, {'key': 'σ', 'label': 'Σ'}])
        cases = [
            {'tag': 'strasse', 'control': 'personal-tag'},
            {'tag': 'σ', 'control': 'personal-tag'},
            {'tag': 'strasse', 'query': 'alpha', 'channel': 'Shared'},
            {'tag': 'strasse', 'query': 'topic', 'channel': '__single_entries__'},
            {'tag': 'topic', 'query': 'topic', 'channel': '__single_entries__'},
            {'query': 'stra'},  # Partial tag searching is unchanged.
            *[{'tag': 'topic', 'query': 'topic', 'sort': sort, 'control': 'sort'} for sort in
              ('upload-desc', 'upload-asc', 'title-asc', 'title-desc', 'channel-asc')],
        ]
        orders = [['two', 'solo', 'one'], ['two', 'one'], ['two', 'one'], ['two'], [], ['solo'],
                  ['two', 'solo', 'one'], ['two', 'solo', 'one'], ['one', 'solo', 'two'],
                  ['two', 'solo', 'one'], ['one', 'solo', 'two'], ['one', 'two', 'solo']]
        for result, order in zip(self.browser_results(cases), orders):
            self.assertEqual(result['hrefs'], [f'bookmarks/{name}.html' for name in order])
            self.assertEqual(result['count'], f'Showing {len(order)} of 3 bookmarks')
            self.assertEqual(result['emptyHidden'], bool(order))

    def test_panel_chips_clear_and_escape_preserve_independent_state_and_focus(self):
        self.bookmark('one.html', metadata={'uploader': 'Shared'})
        self.bookmark('two.html', 'BBBBBBBBBBB', metadata={'uploader': 'Shared'})
        self.catalog({'AAAAAAAAAAA': {'tags': ['Topic']}})
        self.assertEqual(self.run_index()[0], 0)
        results = self.browser_results([
            {'action': 'click', 'control': 'filters'},
            {'query': 'synthetic', 'channel': 'Shared', 'tag': 'topic', 'sort': 'title-desc'},
            {'action': 'click', 'control': 'filters'},
            {'action': 'click', 'control': 'filters'},
            {'action': 'escape'},
            {'action': 'chip', 'index': 0},
            {'action': 'chip', 'index': 0},
            {'query': 'synthetic', 'channel': 'Shared', 'tag': 'topic', 'sort': 'title-desc'},
            {'action': 'click', 'control': 'filters'},
            {'action': 'click', 'control': 'clear-filters'},
            {'action': 'escape'},
            {'action': 'escape'},
        ])
        self.assertEqual([r['filters'] for r in results], [
            'Filters', 'Filters', 'Filters (2)', 'Filters (2)', 'Filters (2)', 'Filters (2)',
            'Filters (1)', 'Filters', 'Filters (2)', 'Filters (2)', 'Filters', 'Filters', 'Filters'])
        self.assertTrue(results[0]['panelHidden'])
        for index, hidden in ((1, False), (3, True), (4, False), (5, True), (9, False), (10, False)):
            self.assertEqual(results[index]['panelHidden'], hidden)
            self.assertEqual(results[index]['expanded'], str(not hidden).lower())
        self.assertEqual(results[2]['chips'], [
            {'text': 'Channel: Shared ×', 'name': 'Remove Channel: Shared filter'},
            {'text': 'Personal Tag: Topic ×', 'name': 'Remove Personal Tag: Topic filter'}])
        for index in (3, 4, 5):
            self.assertEqual(results[index]['hrefs'], results[2]['hrefs'])
            self.assertEqual(results[index]['tag'], 'topic')
        self.assertEqual(results[6]['tag'], 'topic')
        self.assertEqual(results[6]['channel'], '')
        for index in (5, 6, 7, 11, 12):
            self.assertEqual(results[index]['focus'], 'filters')
        self.assertTrue(results[7]['chipsHidden'])
        self.assertEqual(results[10]['focus'], 'channel')
        self.assertEqual(results[10]['tag'], '')
        self.assertEqual(results[10]['channel'], '')
        for index in range(2, len(results)):
            self.assertEqual(results[index]['query'], 'synthetic')
            self.assertEqual(results[index]['sort'], 'title-desc')

    def test_filter_markup_and_untrusted_long_labels(self):
        label = '<img src=x onerror=alert(1)> ' + 'LongChannel' * 12
        tag = '</script><script>alert(1)</script>' + 'x' * 35
        self.bookmark('one.html', metadata={'uploader': label})
        self.bookmark('two.html', 'BBBBBBBBBBB', metadata={'uploader': label})
        self.catalog({'AAAAAAAAAAA': {'tags': [tag]}})
        self.assertEqual(self.run_index()[0], 0)
        data, parser = self.parsed_output()
        self.assertEqual(data['personalTagOptions'], [{'key': tag.casefold(), 'label': tag}])
        self.assertEqual(len(parser.scripts), 2)
        attrs = {attributes['id']: (element, attributes) for element, attributes in parser.elements
                 if 'id' in attributes}
        self.assertEqual(attrs['filters'][0], 'button')
        self.assertEqual(attrs['filters'][1]['aria-expanded'], 'false')
        self.assertEqual(attrs['filters'][1]['aria-controls'], 'filter-panel')
        self.assertIn('hidden', attrs['filter-panel'][1])
        labels = [attributes.get('for') for element, attributes in parser.elements if element == 'label']
        self.assertIn('channel', labels)
        self.assertIn('personal-tag', labels)
        self.assertEqual(attrs['count'][1]['aria-live'], 'polite')
        self.assertIn('<option value="">All Personal Tags</option>', self.output.read_text())
        result = self.browser_results([{'channel': label, 'tag': tag.casefold()}])[1]
        self.assertEqual(result['filters'], 'Filters (2)')
        self.assertEqual(result['chips'][0]['text'], f'Channel: {label} ×')
        self.assertEqual(result['chips'][1]['text'], f'Personal Tag: {tag} ×')
        self.assertEqual(len(result['hrefs']), 1)
        self.assertNotIn('innerHTML', parser.scripts[1]['text'])

    def test_removing_tag_chip_keeps_channel_and_clear_keeps_unmatched_search(self):
        self.bookmark('one.html', metadata={'uploader': 'Shared'})
        self.bookmark('two.html', 'BBBBBBBBBBB', metadata={'uploader': 'Shared'})
        self.catalog({'AAAAAAAAAAA': {'tags': ['Topic']}})
        self.assertEqual(self.run_index()[0], 0)
        results = self.browser_results([
            {'query': 'synthetic', 'channel': 'Shared', 'tag': 'topic', 'sort': 'upload-asc'},
            {'action': 'chip', 'index': 1},
            {'query': 'unmatched', 'channel': 'Shared', 'tag': 'topic', 'sort': 'upload-asc'},
            {'action': 'click', 'control': 'filters'},
            {'action': 'click', 'control': 'clear-filters'},
        ])
        self.assertEqual(results[2]['channel'], 'Shared')
        self.assertEqual(results[2]['tag'], '')
        self.assertEqual(results[2]['filters'], 'Filters (1)')
        self.assertEqual(results[2]['query'], 'synthetic')
        self.assertEqual(results[2]['sort'], 'upload-asc')
        self.assertEqual(len(results[2]['hrefs']), 2)
        self.assertEqual(results[2]['focus'], 'filters')
        self.assertEqual(results[-1]['filters'], 'Filters')
        self.assertEqual(results[-1]['query'], 'unmatched')
        self.assertEqual(results[-1]['sort'], 'upload-asc')
        self.assertFalse(results[-1]['emptyHidden'])
        self.assertEqual(results[-1]['count'], 'Showing 0 of 2 bookmarks')

    def test_all_hidden_filter_controls_remain_usable(self):
        self.bookmark()
        self.catalog({'AAAAAAAAAAA': {'tags': ['hidden', 'SecretTag']}})
        self.assertEqual(self.run_index()[0], 0)
        data, _ = self.parsed_output()
        self.assertEqual(data['personalTagOptions'], [])
        for result in self.browser_results([
                {'action': 'click', 'control': 'filters'},
                {'action': 'click', 'control': 'clear-filters'},
                {'action': 'escape'}]):
            self.assertEqual(result['hrefs'], [])
            self.assertEqual(result['count'], 'Showing 0 of 0 bookmarks')
            self.assertFalse(result['emptyHidden'])
            self.assertEqual(result['filters'], 'Filters')
            self.assertEqual(result['tags'], [])

    def test_validated_case_sensitive_ids_and_orphans(self):
        self.bookmark(identity='AAAAAAAAAAA')
        self.bookmark('lowercase.html', 'aaaaaaaaaaa')
        self.catalog({'AAAAAAAAAAA': {'tags': [' Python ', 'python', 'Research']},
                      'aaaaaaaaaaa': {'tags': ['lowercase']},
                      'BBBBBBBBBBB': {'tags': ['orphan-only']}})
        self.assertEqual(self.run_index()[0], 0)
        items = {item['id']: item for item in self.parsed_output()[0]['items']}
        self.assertEqual(items['AAAAAAAAAAA']['personalTags'], ['Python', 'Research'])
        self.assertEqual(items['aaaaaaaaaaa']['personalTags'], ['lowercase'])
        self.assertNotIn('BBBBBBBBBBB', items)
        self.assertNotIn('orphan-only', self.output.read_text())

    def test_hidden_records_are_absent_from_entire_html_and_sources_are_preserved(self):
        visible = self.bookmark('visible.html', 'aaaaaaaaaaa')
        thumbnail = 'data:image/png;base64,U3ludGhldGljSGlkZGVuVGh1bWJuYWls'
        markers = ['HiddenExclusiveTitle', 'HiddenExclusiveChannel', 'HiddenExclusiveDescription',
                   'https://example.invalid/HiddenExclusiveURL', 'HiddenExclusiveTag',
                   'AAAAAAAAAAA', 'hidden-exclusive.html', thumbnail,
                   thumbnail.split(',', 1)[1]]
        hidden = self.bookmark('hidden-exclusive.html', metadata={
            'title': markers[0], 'uploader': markers[1], 'description': markers[2],
            'webpage_url': markers[3],
        }, thumbnail=thumbnail)
        catalog = self.catalog({'AAAAAAAAAAA': {'tags': ['hidden', markers[4]]},
                                'aaaaaaaaaaa': {'tags': ['VisibleTag']}})
        sources = (visible, hidden, catalog)
        before = {path: (path.read_bytes(), path.stat().st_mode) for path in sources}
        directories = sorted(path.relative_to(self.root) for path in self.root.rglob('*') if path.is_dir())
        status, stdout, _ = self.run_index()
        self.assertEqual(status, 0)
        self.assertIn('Indexed 1 bookmarks (0 skipped)', stdout)
        self.assertEqual([item['id'] for item in self.parsed_output()[0]['items']], ['aaaaaaaaaaa'])
        html = self.output.read_text()
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, html)
        self.assertIn('VisibleTag', html)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mode) for path in sources}, before)
        self.assertEqual(sorted(path.relative_to(self.root) for path in self.root.rglob('*') if path.is_dir()),
                         directories)
        self.assertEqual(set(self.root.rglob('*')), {self.collection, self.output, *sources})
        self.assert_no_staging_files()

    def test_hidden_tag_normalization_and_exact_match(self):
        self.bookmark()
        for tags in (['hidden'], ['HIDDEN'], ['  HiDdEn  '], ['Hidden', 'hidden', 'topic']):
            with self.subTest(tags=tags):
                self.catalog({'AAAAAAAAAAA': {'tags': tags}})
                self.assertEqual(self.run_index()[0], 0)
                self.assertEqual(self.parsed_output()[0]['items'], [])
        for tags in (['hidden-topic'], ['unhidden'], ['hid den']):
            with self.subTest(tags=tags):
                self.catalog({'AAAAAAAAAAA': {'tags': tags}})
                self.assertEqual(self.run_index()[0], 0)
                self.assertEqual(self.parsed_output()[0]['items'][0]['personalTags'], tags)

    def test_channels_and_counts_use_only_visible_bookmarks(self):
        for name, identity, channel in (
                ('shared-visible.html', 'AAAAAAAAAAA', 'SharedChannel'),
                ('shared-hidden.html', 'BBBBBBBBBBB', 'SharedChannel'),
                ('hidden-only.html', 'CCCCCCCCCCC', 'HiddenOnlyChannel'),
                ('multiple-one.html', 'DDDDDDDDDDD', 'MultipleChannel'),
                ('multiple-two.html', 'EEEEEEEEEEE', 'MultipleChannel')):
            self.bookmark(name, identity, metadata={'uploader': channel})
        self.catalog({identity: {'tags': ['hidden']} for identity in ('BBBBBBBBBBB', 'CCCCCCCCCCC')})
        self.assertEqual(self.run_index()[0], 0)
        data, parser = self.parsed_output()
        items = data['items']
        counts = {item['channel']: sum(other['channel'] == item['channel'] for other in items)
                  for item in items}
        self.assertEqual(len(items), 3)
        self.assertEqual(counts, {'SharedChannel': 1, 'MultipleChannel': 2})
        self.assertNotIn('HiddenOnlyChannel', self.output.read_text())
        script, = [s['text'] for s in parser.scripts if s['attrs'].get('id') != 'bookmark-data']
        # All browser channel options and totals derive from the filtered payload.
        for expression in ('const items=data.items;', 'for (const item of items)',
                           'const channels = [...channelCounts.keys()]',
                           'if (channelCounts.get(name) < 2) continue;',
                           'channelCounts.get(x.channel) === 1',
                           '${items.length.toLocaleString()}', '${channels.length.toLocaleString()}'):
            self.assertIn(expression, script)

    def test_all_hidden_publishes_empty_index_with_or_without_old_index_and_invalid_files(self):
        self.bookmark()
        self.catalog({'AAAAAAAAAAA': {'tags': ['hidden']}})
        for existing, invalid in ((False, False), (True, False), (True, True)):
            with self.subTest(existing=existing, invalid=invalid):
                if existing:
                    self.output.write_text('PreviouslyVisibleSentinel')
                    self.output.chmod(0o640)
                if invalid:
                    (self.collection / 'invalid.html').write_text('not a bookmark')
                status, stdout, stderr = self.run_index()
                self.assertEqual(status, 0)
                self.assertIn(f'Indexed 0 bookmarks ({int(invalid)} skipped)', stdout)
                self.assertEqual(self.parsed_output()[0]['items'], [])
                html = self.output.read_text()
                self.assertNotIn('PreviouslyVisibleSentinel', html)
                self.assertNotIn('Synthetic title', html)
                self.assertIn('No matching bookmarks found.', html)
                if existing:
                    self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o640)
                if invalid:
                    self.assertIn('Skipping invalid.html', stderr)
                self.assert_no_staging_files()

    def test_invalid_only_and_empty_libraries_do_not_publish_even_with_hidden_orphans(self):
        for catalog_records in (None, {}, {'AAAAAAAAAAA': {'tags': ['hidden']}}):
            for invalid in (False, True):
                with self.subTest(invalid=invalid, catalog_records=catalog_records):
                    if catalog_records is not None:
                        self.catalog(catalog_records)
                    if invalid:
                        self.bookmark(metadata={'id': 'invalid-id'})
                    self.output.write_text('keep existing index')
                    self.output.chmod(0o640)
                    with patch.object(indexer, 'write_index_atomic') as writer:
                        status, _, stderr = self.run_index()
                    self.assertEqual(status, 1)
                    self.assertIn('No valid bookmarks found', stderr)
                    writer.assert_not_called()
                    self.assertEqual(self.output.read_text(), 'keep existing index')
                    self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o640)
                    self.assert_no_staging_files()
                    if invalid:
                        (self.collection / 'renamed.html').unlink()

    def test_malformed_catalog_with_hidden_record_preserves_index(self):
        self.bookmark()
        self.catalog({'AAAAAAAAAAA': {'tags': ['hidden']}, 'BBBBBBBBBBB': {'tags': [1]}})
        with patch.object(indexer, 'parse_bookmark', side_effect=AssertionError('should not scan')):
            self.assert_catalog_refused()

    def test_rebuild_applies_hiding_and_visibility_restoration(self):
        self.bookmark()
        self.catalog({'AAAAAAAAAAA': {'tags': ['topic']}})
        self.assertEqual(self.run_index()[0], 0)
        visible_html = self.output.read_bytes()
        self.catalog({'AAAAAAAAAAA': {'tags': ['topic', 'hidden']}})
        self.assertEqual(self.output.read_bytes(), visible_html)
        self.assertEqual(self.run_index()[0], 0)
        self.assertEqual(self.parsed_output()[0]['items'], [])
        hidden_html = self.output.read_bytes()
        self.catalog({'AAAAAAAAAAA': {'tags': ['topic']}})
        self.assertEqual(self.output.read_bytes(), hidden_html)
        self.assertEqual(self.run_index()[0], 0)
        item, = self.parsed_output()[0]['items']
        self.assertEqual(item['id'], 'AAAAAAAAAAA')
        self.assertEqual(item['personalTags'], ['topic'])

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
        self.assertIn('Array.isArray(x.personalTags)', render)
        self.assertIn('[x.title, x.channel, x.description, x.id,', render)
        self.assertIn("c === '__single_entries__'", render)
        self.assertIn('channelCounts.get(x.channel) === 1', render)
        self.assertIn('switch(s)', render)
        self.assertIn('<option value="">All Channels</option>', self.output.read_text())
        self.assertIn('placeholder="Search titles, channels, descriptions, personal tags…"',
                      self.output.read_text())

    def test_build_only_writes_index_not_bookmarks_or_catalog(self):
        bookmark = self.bookmark()
        catalog = self.catalog({'AAAAAAAAAAA': {'tags': ['topic']}})
        before = {path: (path.read_bytes(), path.stat().st_mode) for path in (bookmark, catalog)}
        self.assertEqual(self.run_index()[0], 0)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mode) for path in before}, before)
        self.assertEqual(list(self.root.glob('catalog.json.bak-*')), [])
        self.assert_no_staging_files()

    def test_filesystem_failures_do_not_publish_even_with_valid_bookmarks(self):
        self.bookmark()
        self.output.write_bytes(b'preserve index')
        self.output.chmod(0o640)
        nested = self.collection / 'nested'
        nested.mkdir()
        real_scan = os.scandir
        def scan(path):
            if Path(path) == nested:
                raise PermissionError('synthetic enumeration denied')
            return real_scan(path)
        before = snapshot(self.root)
        with patch.object(indexer.os, 'scandir', side_effect=scan), \
                patch.object(indexer, 'write_index_atomic') as writer:
            self.assertEqual(self.run_index()[0], 2)
            writer.assert_not_called()
        self.assertEqual(snapshot(self.root), before)

    def test_index_named_directory_and_malformed_regular_skip_remain_supported(self):
        self.bookmark(collection=self.collection / 'index.html')
        (self.collection / 'bad.html').write_text('malformed regular page')
        status, stdout, _ = self.run_index()
        self.assertEqual(status, 0)
        self.assertIn('Indexed 1 bookmarks (1 skipped)', stdout)
        self.assertEqual(self.parsed_output()[0]['items'][0]['href'], 'bookmarks/index.html/renamed.html')

    def test_duplicate_catalog_members_preserve_index_and_all_source_files(self):
        self.bookmark()
        self.output.write_bytes(b'old index sentinel')
        self.output.chmod(0o640)
        (self.root / 'catalog.json.bak-existing').write_bytes(b'backup sentinel')
        for content in duplicate_catalog_documents():
            (self.root / 'catalog.json').write_text(content)
            before = snapshot(self.root)
            with patch.object(indexer, 'write_index_atomic') as writer:
                self.assertEqual(self.run_index()[0], 2)
                writer.assert_not_called()
            self.assertEqual(snapshot(self.root), before)

    def test_malformed_archival_numbers_skip_without_changing_sources(self):
        good = self.bookmark()
        bad = self.collection / 'bad.html'
        self.catalog({'AAAAAAAAAAA': {'tags': ['Topic']}, 'BBBBBBBBBBB': {'tags': ['hidden']}})
        for label, raw in numeric_metadata_documents('BBBBBBBBBBB'):
            with self.subTest(label=label):
                bad.write_text('<script id="youtube-metadata">' + raw + '</script>')
                bad.chmod(0o640)
                before = {path: (path.stat().st_mode, path.stat().st_mtime_ns, path.read_bytes())
                          for path in (good, bad, self.root / 'catalog.json')}
                status, stdout, stderr = self.run_index()
                self.assertEqual(status, 0, stderr)
                self.assertIn('Indexed 1 bookmarks (1 skipped)', stdout)
                self.assertIn('bad.html', stderr)
                self.assertEqual([item['id'] for item in self.parsed_output()[0]['items']], ['AAAAAAAAAAA'])
                self.assertEqual({path: (path.stat().st_mode, path.stat().st_mtime_ns, path.read_bytes())
                                  for path in before}, before)
                self.assert_no_staging_files()
        good.unlink()
        self.output.write_bytes(b'preserve all-invalid index')
        before = snapshot(self.root)
        with patch.object(indexer, 'write_index_atomic') as writer:
            self.assertEqual(self.run_index()[0], 1)
            writer.assert_not_called()
        self.assertEqual(snapshot(self.root), before)

    def test_display_projection_and_all_hidden_numeric_compatibility(self):
        for value in (None, True, False, '120', -1, -0.25, 0, 61.75, 2**53 - 1, 2**53, 1e308):
            self.bookmark(metadata={'duration': value, 'title': None, 'formats': [{'ignored': float('nan')}]})
            self.assertEqual(self.run_index()[0], 0)
            item = self.parsed_output()[0]['items'][0]
            expected = value if type(value) in (int, float) and 0 <= value <= 2**53 - 1 else None
            self.assertEqual(item['duration'], expected)
            self.assertEqual(item['title'], 'renamed')
        self.catalog({'AAAAAAAAAAA': {'tags': ['hidden']}})
        self.bookmark('bad.html', 'BBBBBBBBBBB', metadata={'timestamp': float('inf')})
        status, stdout, _ = self.run_index()
        self.assertEqual(status, 0)
        self.assertIn('Indexed 0 bookmarks (1 skipped)', stdout)
        self.assertEqual(self.parsed_output()[0]['items'], [])

    def test_final_json_serialization_failure_preserves_every_existing_file(self):
        self.bookmark()
        self.output.write_bytes(b'existing index sentinel')
        self.output.chmod(0o640)
        before = snapshot(self.root)
        real_parser = indexer.parse_bookmark
        for value in (float('nan'), float('inf'), -float('inf')):
            def parse(*arguments):
                item = real_parser(*arguments)
                item['duration'] = value
                return item
            with patch.object(indexer, 'parse_bookmark', side_effect=parse), \
                    patch.object(indexer, 'write_index_atomic') as writer:
                status, _, stderr = self.run_index()
                self.assertEqual(status, 2)
                self.assertIn('Cannot serialize index JSON', stderr)
                writer.assert_not_called()
            self.assertEqual(snapshot(self.root), before)

    def test_actual_javascript_parses_finite_index_payload(self):
        self.bookmark(metadata={'duration': 61.75, 'timestamp': sys.float_info.max})
        self.assertEqual(self.run_index()[0], 0)
        _, parser = self.parsed_output()
        raw = next(script['text'] for script in parser.scripts if script['attrs'].get('id') == 'bookmark-data')
        self.assertEqual(strict_json_loads(raw)['items'][0]['duration'], 61.75)
        assert_browser_json(self, raw)


if __name__ == '__main__':
    unittest.main()
