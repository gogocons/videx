"""Identity resolution tests using HTML strings only."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'scripts')] + sys.path):
    from videx_identity import IdentityError, resolve_identity, valid_id

A, B = 'AAAAAAAAAAA', 'BBBBBBBBBBB'


def embedded(payload):
    return '<script id="youtube-metadata">' + payload + '</script>'


class IdentityTests(unittest.TestCase):
    def test_valid_identity_matrix(self):
        for filename, document in (
            (f'Title [{A}].html', embedded('{"id":"' + A + '"}')),
            ('renamed.html', embedded('{"id":"' + A + '"}')),
            (f'Legacy [{A}].html', '<html>legacy without metadata</html>'),
            (f'Legacy [{A}].html', '<script type="application/json">{broken</script>'),
        ):
            with self.subTest(filename=filename, document=document):
                self.assertEqual(resolve_identity(filename, document), A)

    def test_present_invalid_metadata_never_uses_filename_fallback(self):
        documents = [embedded(payload) for payload in (
            '', '   ', '{broken', 'null', '[]', '"text"', '{}', '{"id":null}',
            '{"id":123}', '{"id":true}', '{"id":[]}', '{"id":"short"}',
        )]
        documents += [embedded('{"id":"' + A + '"}') * 2,
                      '<script id="youtube-metadata">{"id":"' + A + '"}']
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaises(IdentityError) as caught:
                    resolve_identity(f'Valid filename [{A}].html', document)
                self.assertEqual(caught.exception.category, 'UNREADABLE')

    def test_conflict_reports_both_ids(self):
        with self.assertRaises(IdentityError) as caught:
            resolve_identity(f'Title [{A}].html', embedded('{"id":"' + B + '"}'))
        self.assertEqual(caught.exception.category, 'MISMATCH')
        self.assertEqual(caught.exception.filename_id, A)
        self.assertEqual(caught.exception.metadata_id, B)

    def test_unknown_and_case_sensitive_ids(self):
        with self.assertRaises(IdentityError) as caught:
            resolve_identity('renamed.html', '<html>no identity</html>')
        self.assertEqual(caught.exception.category, 'UNKNOWN')
        with self.assertRaises(IdentityError):
            resolve_identity(f'Title [{A}].html', embedded('{"id":"' + A.lower() + '"}'))
        for invalid in (None, 123, True, '', 'short', A + '\n', ' ' + A):
            self.assertFalse(valid_id(invalid))
        self.assertTrue(valid_id('aB_09-xYz12'))


if __name__ == '__main__':
    unittest.main()
