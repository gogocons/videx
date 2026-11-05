"""Resolve bookmark identities without concealing damaged embedded metadata."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re


VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
FILE_ID = re.compile(r'\[([A-Za-z0-9_-]{11})\]\.html$', re.I)


def valid_id(value):
    return isinstance(value, str) and VIDEO_ID.fullmatch(value) is not None


class IdentityError(ValueError):
    def __init__(self, reason, category='UNREADABLE', filename_id=None, metadata_id=None):
        super().__init__(reason)
        self.category = category
        self.filename_id = filename_id
        self.metadata_id = metadata_id


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.count = 0
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == 'youtube-metadata':
            self.count += 1
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def resolve_identity(filename, document):
    """Return a case-sensitive ID; fallback requires an absent metadata element."""
    match = FILE_ID.search(Path(filename).name)
    filename_id = match.group(1) if match else None
    parser = MetadataParser()
    parser.feed(document)
    parser.close()
    if parser.count == 0:
        if filename_id:
            return filename_id
        raise IdentityError('no metadata element or valid filename ID', 'UNKNOWN')
    if parser.count != 1 or parser.active:
        raise IdentityError('multiple or unterminated metadata elements')
    try:
        metadata = json.loads(''.join(parser.parts))
    except ValueError as exc:
        raise IdentityError(f'invalid metadata JSON: {exc}') from exc
    if not isinstance(metadata, dict) or not valid_id(metadata.get('id')):
        raise IdentityError('metadata must be an object containing a valid 11-character string ID')
    metadata_id = metadata['id']
    if filename_id and filename_id != metadata_id:
        raise IdentityError('filename and metadata IDs disagree', 'MISMATCH', filename_id, metadata_id)
    return metadata_id


def bookmark_identity(path):
    """Read and resolve one bookmark, propagating filesystem/encoding failures."""
    path = Path(path)
    return resolve_identity(path.name, path.read_text(encoding='utf-8'))
