"""Offline regression tests for URL serialization in generated bookmarks."""
from html.parser import HTMLParser
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch


spec = spec_from_file_location(
    'bookmark_generator',
    Path(__file__).resolve().parents[1] / 'scripts' / 'yt-bookmark-generate.py',
)
generator = module_from_spec(spec)
with patch.object(sys, 'path', [str(Path(spec.origin).parent)] + sys.path):
    spec.loader.exec_module(generator)


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = {'attrs': dict(attrs), 'text': ''}
            self.scripts.append(self.active)

    def handle_data(self, data):
        if self.active is not None:
            self.active['text'] += data

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = None


def strict_json_loads(text):
    def reject_constant(value):
        raise ValueError(f'Non-standard JSON constant: {value}')
    return json.loads(text, parse_constant=reject_constant)


def assert_browser_json(test, text):
    """Exercise actual JS parsing, including finite values after conversion."""
    runtime = shutil.which('deno') or shutil.which('node')
    if runtime is None:
        test.skipTest('Deno or Node.js required for JSON.parse numeric compatibility')
    bootstrap = ('const source=await new Response(Deno.stdin.readable).text();' if Path(runtime).name == 'deno'
                 else "const source=require('fs').readFileSync(0,'utf8');")
    script = bootstrap + '''
const value=JSON.parse(source);
function inspect(x){
  if(typeof x==='number'&&!Number.isFinite(x))throw new Error('non-finite browser number');
  if(x&&typeof x==='object')for(const child of Object.values(x))inspect(child);
}
inspect(value);
'''
    result = subprocess.run([runtime, 'eval' if Path(runtime).name == 'deno' else '-e', script],
                            input=text, text=True, capture_output=True, timeout=15)
    test.assertEqual(result.returncode, 0, result.stderr)


def numeric_metadata_documents(identity='AAAAAAAAAAA'):
    """Include literal overflow exponents, not only Python's Infinity spelling."""
    documents = []
    for token in ('NaN', 'Infinity', '-Infinity', '1e999', '-1e999', str(10**400), str(-10**400)):
        for field in ('duration', 'view_count', 'like_count', 'timestamp', 'channel_follower_count'):
            raw = '{"id":' + json.dumps(identity) + ',"title":"Numeric fixture","' + field + '":' + token + '}'
            documents.append((field + '/' + token, raw))
        for fragment in ('"start_time":' + token, '"end_time":' + token,
                         '"future":[{"value":' + token + '}]'):
            raw = '{"id":' + json.dumps(identity) + ',"title":"Numeric fixture","chapters":[{' + fragment + '}]}'
            documents.append(('chapters/' + fragment, raw))
    return documents


def numeric_cli_documents(identity='AAAAAAAAAAA'):
    """Exercise each failure kind through real CLIs without repeating every field."""
    return [(label, raw) for label, raw in numeric_metadata_documents(identity)
            if label.startswith('duration/') or label in (
                'view_count/NaN', 'timestamp/1e999', 'chapters/"start_time":Infinity',
                'chapters/"future":[{"value":-Infinity}]')]


class ClipboardURLTests(unittest.TestCase):
    def assert_url_round_trip(self, url):
        document = generator.make_document(
            {'id': 'AAAAAAAAAAA', 'title': 'Test bookmark', 'webpage_url': url},
            'data:image/png;base64,',
        )
        parser = ScriptParser()
        parser.feed(document)
        parser.close()
        executable = [
            script for script in parser.scripts
            if script['attrs'].get('type') != 'application/json'
        ]
        self.assertEqual(len(parser.scripts), 2)
        self.assertEqual(len(executable), 1, 'URL introduced an executable script')
        script = executable[0]['text']
        encoded = script.split('const u=', 1)[1]
        decoded, end = json.JSONDecoder().raw_decode(encoded)
        self.assertEqual(decoded, url)
        self.assertTrue(encoded[end:].startswith(';if(navigator.clipboard'))
        self.assertIn('navigator.clipboard.writeText(u)', script)
        self.assertIn("prompt('Copy URL:',u)", script)

    def test_script_closing_urls_do_not_introduce_executable_scripts(self):
        for suffix in (
            '</script><script>window.__videxInjected=1</script>',
            '</ScRiPt><script>window.__videxInjected=1</script>',
            '</script ><script>window.__videxInjected=1</script>',
        ):
            with self.subTest(suffix=suffix):
                self.assert_url_round_trip('https://example.invalid/' + suffix)

    def test_ordinary_urls_round_trip(self):
        for url in (
            'https://www.youtube.com/watch?v=AAAAAAAAAAA',
            'https://example.invalid/watch?a=1&b=2#chapter',
            'https://example.invalid/"quoted"/\'single\'/back\\slash',
            'https://example.invalid/café/動画?q=✓',
        ):
            with self.subTest(url=url):
                self.assert_url_round_trip(url)


class ArchivalNumberTests(unittest.TestCase):
    def raw_metadata(self, document):
        parser = ScriptParser()
        parser.feed(document)
        return next(script['text'] for script in parser.scripts if script['attrs'].get('id') == 'youtube-metadata')

    def test_invalid_archival_numbers_are_rejected_recursively_with_locations(self):
        for label, raw in numeric_metadata_documents():
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, label.split('/')[0]):
                generator.make_document(json.loads(raw), 'data:image/png;base64,')
        metadata = {'id': 'AAAAAAAAAAA', 'title': 'Title', 'chapters': [{'future': [{'value': float('nan')}]}]}
        with self.assertRaisesRegex(ValueError, r"chapters\[0\]\['future'\]\[0\]\['value'\]"):
            generator.curate(metadata)

    def test_browser_range_boundaries_and_ignored_extractor_fields(self):
        limit = generator.metadata_helpers.MAX_BROWSER_INTEGER
        for number in (limit, -limit, sys.float_info.max, -sys.float_info.max, 2**60 + 1):
            metadata = {'id': 'AAAAAAAAAAA', 'title': 'Title', 'timestamp': number,
                        'formats': [{'ignored': float('nan')}], 'future': {'ignored': 10**400}}
            curated = generator.curate(metadata)
            self.assertEqual(curated['timestamp'], number)
            self.assertNotIn('formats', curated)
            self.assertNotIn('future', curated)
        for number in (limit + 1, -limit - 1):
            with self.assertRaisesRegex(ValueError, 'browser finite numeric range'):
                generator.curate({'id': 'AAAAAAAAAAA', 'title': 'Title', 'timestamp': number})

    def test_display_helper_preserves_fractions_and_excludes_unsafe_values(self):
        helper = generator.metadata_helpers.display_seconds
        limit = 2**53 - 1
        for value in (0, 1, 1.75, limit, float(limit)):
            with self.subTest(value=value):
                self.assertEqual(helper(value), value)
                self.assertEqual(type(helper(value)), type(value))
        for value in (None, True, False, '120', 'NaN', -1, -0.25, limit + 1,
                      sys.float_info.max, 10**400, float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                self.assertIsNone(helper(value))
                self.assertEqual(generator.duration(value), 'Unknown')
        self.assertEqual(generator.duration(61.75), '1:01')

    def test_finite_metadata_is_retained_while_only_display_is_restricted(self):
        for value in (None, True, False, '120', -1, -0.25, 61.75, 2**53, 1e308):
            metadata = {'id': 'AAAAAAAAAAA', 'title': 'Title', 'duration': value,
                        'view_count': True, 'like_count': False,
                        'chapters': [{'start_time': value, 'end_time': 2.75, 'title': 'Chapter'}]}
            document = generator.make_document(metadata, 'data:image/png;base64,')
            saved = strict_json_loads(self.raw_metadata(document))
            if value is None:
                self.assertNotIn('duration', saved)
            else:
                self.assertEqual(saved['duration'], value)
            self.assertEqual(saved['chapters'], metadata['chapters'])
            self.assertIn('<span>Views at capture</span><strong>Unavailable</strong>', document)
            self.assertIn('<span>Likes at capture</span><strong>Unavailable</strong>', document)
            if value == 61.75:
                self.assertIn('t=61', document)
                self.assertIn('1:01', document)
            else:
                self.assertNotIn('<h2>Chapters</h2>', document)
        document = generator.make_document({'id': 'AAAAAAAAAAA', 'title': 'Title',
                                            'view_count': 1234, 'like_count': 0}, 'data:image/png;base64,')
        self.assertIn('<span>Views at capture</span><strong>1,234</strong>', document)
        self.assertIn('<span>Likes at capture</span><strong>0</strong>', document)

    def test_final_standalone_serialization_is_strict_even_if_validation_is_bypassed(self):
        with patch.object(generator, 'curate', return_value={'id': 'AAAAAAAAAAA', 'title': 'Title',
                                                            'timestamp': float('nan')}):
            with self.assertRaises(ValueError):
                generator.make_document({}, 'data:image/png;base64,')

    def test_archival_json_is_parseable_and_finite_in_actual_javascript(self):
        document = generator.make_document({'id': 'AAAAAAAAAAA', 'title': '</script> café',
            'duration': 61.75, 'timestamp': generator.metadata_helpers.MAX_BROWSER_INTEGER,
            'view_count': 2**60 + 1, 'chapters': [{'start_time': 1.25, 'end_time': 1e308}]},
            'data:image/png;base64,')
        raw = self.raw_metadata(document)
        strict_json_loads(raw)
        self.assertNotIn('</script>', raw)
        assert_browser_json(self, raw)


if __name__ == '__main__':
    unittest.main()
