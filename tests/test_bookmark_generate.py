"""Offline regression tests for URL serialization in generated bookmarks."""
from html.parser import HTMLParser
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import unittest


spec = spec_from_file_location(
    'bookmark_generator',
    Path(__file__).resolve().parents[1] / 'scripts' / 'yt-bookmark-generate.py',
)
generator = module_from_spec(spec)
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


if __name__ == '__main__':
    unittest.main()
