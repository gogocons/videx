#!/usr/bin/env python3
"""Manage Videx personal tags in a library-level catalog.json (offline)."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
FILENAME_ID = re.compile(r'\[([A-Za-z0-9_-]{11})\]\.html$', re.I)


def empty():
    return {'version': 1, 'bookmarks': {}, 'collections': {}}


def load(path):
    if not path.exists():
        return empty()
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or obj.get('version') != 1 or not isinstance(obj.get('bookmarks'), dict) or not isinstance(obj.get('collections'), dict):
        raise ValueError('Unsupported or invalid catalog schema; refusing to modify')
    for video_id, record in obj['bookmarks'].items():
        if not ID.fullmatch(video_id) or not isinstance(record, dict) or not isinstance(record.get('tags', []), list):
            raise ValueError(f'Invalid catalog entry: {video_id}')
    return obj


def normalized(tag):
    tag = ' '.join(tag.strip().split())
    if not tag or any(ord(c) < 32 for c in tag) or len(tag) > 80:
        raise ValueError('Tags must be nonempty, at most 80 characters, and contain no control characters')
    return 'hidden' if tag.casefold() == 'hidden' else tag


def unique(tags):
    out = {}
    for value in tags:
        tag = normalized(value)
        out.setdefault(tag.casefold(), tag)
    return list(out.values())


def archive_ids(root):
    from html.parser import HTMLParser
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(); self.active = False; self.parts = []
        def handle_starttag(self, tag, attrs):
            if tag == 'script' and dict(attrs).get('id') == 'youtube-metadata': self.active = True
        def handle_endtag(self, tag):
            if tag == 'script': self.active = False
        def handle_data(self, data):
            if self.active: self.parts.append(data)
    ids = defaultdict(list)
    for path in sorted((root / 'bookmarks').rglob('*.html')):
        if path.name == 'index.html': continue
        try:
            parser = Parser(); parser.feed(path.read_text(encoding='utf-8'))
            meta = json.loads(''.join(parser.parts)) if parser.parts else {}
            vid = meta.get('id') if isinstance(meta, dict) else None
            if not isinstance(vid, str) or not ID.fullmatch(vid):
                match = FILENAME_ID.search(path.name)
                vid = match.group(1) if match else None
            if vid: ids[vid].append(path)
        except (OSError, UnicodeError, ValueError, TypeError):
            print(f'WARNING: Cannot inspect {path}', file=sys.stderr)
    return ids


def write_catalog(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.catalog-', suffix='.tmp', delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            backup = path.with_name(f'catalog.json.bak-{datetime.now().strftime("%Y%m%d-%H%M%S-%f")}')
            shutil.copy2(path, backup)
            print(f'BACKUP: {backup}')
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists(): tmp.unlink()


def report(obj, ids):
    counts = Counter()
    for vid in ids:
        for tag in unique(obj['bookmarks'].get(vid, {}).get('tags', [])):
            counts[tag] += 1
    print('Videx tag report\n')
    for tag, n in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold())):
        print(f'{tag:<30} {n:>5}')
    tagged = sum(bool(obj['bookmarks'].get(vid, {}).get('tags')) for vid in ids)
    print(f'\nBookmarks: {len(ids)} | Tagged: {tagged} | Untagged: {len(ids)-tagged} | Unique tags: {len(counts)}')
    orphans = sorted(set(obj['bookmarks']) - set(ids))
    if orphans:
        print(f'\nCatalog entries without matching bookmarks ({len(orphans)}):')
        for vid in orphans: print(f'  {vid}')
    folded = defaultdict(list)
    for tag in counts: folded[tag.casefold()].append(tag)
    for variants in folded.values():
        if len(variants) > 1: print('CASE VARIANTS:', ', '.join(variants))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library', type=Path, help='Library root containing bookmarks/')
    parser.add_argument('operation', choices=['add', 'remove', 'set', 'rename', 'list', 'report', 'prune'])
    parser.add_argument('values', nargs='*', help='For add/remove/set: VIDEO_ID TAG...; rename: OLD NEW; list: VIDEO_ID')
    parser.add_argument('--apply', action='store_true', help='Write changes; default is preview only')
    args = parser.parse_args()
    root = args.library.expanduser().resolve()
    if not (root / 'bookmarks').is_dir(): parser.error(f'Not a library: missing {root / "bookmarks"}')
    path = root / 'catalog.json'
    try:
        catalog = load(path)
        ids = archive_ids(root)
        op, vals = args.operation, args.values
        if op == 'report':
            if vals: parser.error('report takes no additional arguments')
            report(catalog, ids); return 0
        if op == 'prune':
            if vals: parser.error('prune takes no additional arguments')
            orphans = sorted(set(catalog['bookmarks']) - set(ids))
            print(f'{"APPLY" if args.apply else "DRY RUN"}: prune | {len(orphans)} orphaned catalog entry/entries')
            for vid in orphans:
                tags = catalog['bookmarks'][vid].get('tags', [])
                print(f'  {vid}: tags={tags}')
            if not orphans:
                print('No changes needed.')
                return 0
            if args.apply:
                for vid in orphans:
                    del catalog['bookmarks'][vid]
                write_catalog(path, catalog)
                print(f'UPDATED: {path}')
            else:
                print('No files modified. Repeat with --apply to save.')
            return 0
        if op == 'list':
            if len(vals) != 1 or not ID.fullmatch(vals[0]): parser.error('list requires VIDEO_ID')
            print('\n'.join(catalog['bookmarks'].get(vals[0], {}).get('tags', [])) or '(no tags)'); return 0
        if op == 'rename':
            if len(vals) != 2: parser.error('rename requires OLD_TAG NEW_TAG')
            old, new = normalized(vals[0]), normalized(vals[1]); targets = list(ids)
        else:
            if len(vals) < (2 if op in ('add', 'remove') else 1): parser.error(f'{op} requires VIDEO_ID and tag arguments (set may have no tags)')
            video_id = vals[0]
            if not ID.fullmatch(video_id): parser.error('Invalid 11-character VIDEO_ID')
            if video_id not in ids: parser.error(f'Video ID not present in library: {video_id}')
            targets = [video_id]
            desired = unique(vals[1:])
        changes = []
        for vid in targets:
            current = catalog['bookmarks'].get(vid, {})
            before = unique(current.get('tags', []))
            if op == 'add': after = unique(before + desired)
            elif op == 'remove':
                remove = {t.casefold() for t in desired}; after = [t for t in before if t.casefold() not in remove]
            elif op == 'set': after = desired
            else: after = unique([new if t.casefold() == old.casefold() else t for t in before])
            if after != before:
                changes.append((vid, before, after))
                if after:
                    catalog['bookmarks'].setdefault(vid, {})['tags'] = after
                elif vid in catalog['bookmarks']:
                    catalog['bookmarks'][vid].pop('tags', None)
                    if not catalog['bookmarks'][vid]: del catalog['bookmarks'][vid]
        print(f'{"APPLY" if args.apply else "DRY RUN"}: {op} | {len(changes)} bookmark(s) to change')
        for vid, before, after in changes:
            print(f'  {vid}: {before} -> {after}')
        if not changes: print('No changes needed.'); return 0
        if args.apply:
            write_catalog(path, catalog)
            print(f'UPDATED: {path}')
            print('Rebuild index separately if needed.')
        else: print('No files modified. Repeat with --apply to save.')
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr); return 2

if __name__ == '__main__': sys.exit(main())
