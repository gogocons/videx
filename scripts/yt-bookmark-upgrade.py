#!/usr/bin/env python3
"""Safely upgrade archived bookmarks in place, without contacting YouTube."""
import argparse
from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode = True
import os

from importlib.util import spec_from_file_location, module_from_spec

class ArchiveParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.metadata = []
        self.thumbnail = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('id') == 'youtube-metadata':
            self.active = True
        if tag == 'img' and not self.thumbnail:
            src = attrs.get('src', '')
            if src.startswith('data:image/') and ';base64,' in src:
                self.thumbnail = src

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.metadata.append(data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', nargs='?', default=str(Path(__file__).resolve().parent.parent / 'bookmarks'))
    p.add_argument('--apply', action='store_true', help='Back up and replace pages; default is dry-run')
    args = p.parse_args()
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        p.error(f'Not a directory: {root}')
    spec = spec_from_file_location('bookmark_generator', Path(__file__).with_name('yt-bookmark-generate.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = sorted(f for f in root.rglob('*.html') if f.name != 'index.html')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup_root = root.parent / f'{root.name}-backup-{stamp}'
    candidates = []
    original_bytes = 0
    predicted_bytes = 0
    skipped = 0
    unchanged = 0
    for path in paths:
        try:
            parser = ArchiveParser()
            parser.feed(path.read_text(encoding='utf-8'))
            if not parser.metadata or not parser.thumbnail:
                raise ValueError('missing metadata or embedded thumbnail')
            metadata = json.loads(''.join(parser.metadata))
            if not isinstance(metadata, dict):
                raise ValueError('invalid metadata')
            if not metadata.get('id') or not metadata.get('title'):
                raise ValueError('missing video ID or title')
            relative_index = Path(os.path.relpath(root.parent / 'index.html', path.parent)).as_posix()
            document = module.make_document(metadata, parser.thumbnail, relative_index)
            # Validate before touching any source file.
            check = ArchiveParser()
            check.feed(document)
            rebuilt = json.loads(''.join(check.metadata))
            if rebuilt.get('id') != metadata['id'] or rebuilt.get('title') != metadata['title'] or check.thumbnail != parser.thumbnail:
                raise ValueError('rebuilt bookmark validation failed')
            if document == path.read_text(encoding='utf-8'):
                unchanged += 1
                continue
            old_size = path.stat().st_size
            new_size = len(document.encode('utf-8'))
            original_bytes += old_size
            predicted_bytes += new_size
            candidates.append((path, document, old_size, new_size))
        except (OSError, ValueError) as exc:
            skipped += 1
            print(f'SKIP {path.relative_to(root)}: {exc}', file=sys.stderr)
    print(f'Found {len(candidates)} bookmarks requiring upgrade; {unchanged} already current; {skipped} skipped.')
    print(f'Current: {original_bytes / 1048576:.2f} MiB; proposed: {predicted_bytes / 1048576:.2f} MiB; savings: {(original_bytes-predicted_bytes) / 1048576:.2f} MiB')
    for path, _, old, new in candidates:
        print(f'  {path.relative_to(root)}: {old/1048576:.2f} -> {new/1048576:.2f} MiB')
    if skipped:
        print('WARNING: Some HTML files were skipped. Inspect the warnings before applying.', file=sys.stderr)
    if not candidates:
        print('No changes needed.')
        return 1 if skipped else 0
    if not args.apply:
        print('DRY RUN: no files changed. Pass --apply to upgrade and back up originals.')
        return 0
    # Back up all candidates before replacing any original.
    for path, _, _, _ in candidates:
        dest = backup_root / path.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    print(f'Backups saved in: {backup_root}')
    failures = 0
    for path, document, _, _ in candidates:
        try:
            module.write_atomic(path, document)
            print(f'UPGRADED {path.relative_to(root)}')
        except Exception as exc:
            failures += 1
            print(f'FAILED {path.relative_to(root)}: {exc}', file=sys.stderr)
    print(f'Finished: {len(candidates)-failures} upgraded, {failures} failed.')
    return 1 if failures else 0

if __name__ == '__main__':
    sys.exit(main())
