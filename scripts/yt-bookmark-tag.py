#!/usr/bin/env python3
"""Manage Videx personal tags in a library-level catalog.json (offline)."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from videx_config import default_library
from videx_identity import VIDEO_ID as ID, IdentityError, bookmark_identity
from videx_inventory import scan_bookmarks


def empty():
    return {'version': 1, 'bookmarks': {}, 'collections': {}}


def load(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return empty()
    if not stat.S_ISREG(mode):
        raise ValueError(f'{path}: catalog must be a regular file, not a symlink or special file')
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or obj.get('version') != 1 or not isinstance(obj.get('bookmarks'), dict) or not isinstance(obj.get('collections'), dict):
        raise ValueError('Unsupported or invalid catalog schema; refusing to modify')
    for video_id, record in obj['bookmarks'].items():
        if not ID.fullmatch(video_id) or not isinstance(record, dict) or not isinstance(record.get('tags', []), list):
            raise ValueError(f'Invalid catalog entry: {video_id}')
        if any(not isinstance(value, str) for value in record.get('tags', [])):
            raise ValueError(f'{path}: tags for {video_id} must contain only strings')
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


def inspect_archive(root):
    """Retain inspection failures so reports cannot mistake them for absence."""
    ids = defaultdict(list)
    paths, issues, available = scan_bookmarks(root / 'bookmarks')
    for path in paths:
        try:
            vid = bookmark_identity(path)
            ids[vid].append(path)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            if isinstance(exc, IdentityError) and exc.category == 'MISMATCH':
                exc = ValueError(f'{exc}; filename ID={exc.filename_id}, metadata ID={exc.metadata_id}')
            issues.append((path, exc))
    return ids, issues, available


def archive_ids(root, strict=False):
    """Keep the capture-facing strict inventory interface."""
    ids, issues, _ = inspect_archive(root)
    failures = [f'{path}: {exc}' for path, exc in issues]
    if strict:
        for vid, paths in ids.items():
            if len(paths) > 1:
                failures.append(f'Duplicate video ID {vid}: ' + ', '.join(str(path) for path in paths))
    if strict and failures:
        raise ValueError('Incomplete bookmark inventory; refusing catalog changes:\n  ' + '\n  '.join(failures))
    if not strict:
        for path, exc in issues:
            print(f'WARNING: Cannot inspect {path}: {exc}; inventory is incomplete', file=sys.stderr)
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


def report(obj, ids, issues=()):
    complete = not issues
    counts = Counter()
    for vid in ids:
        for tag in unique(obj['bookmarks'].get(vid, {}).get('tags', [])):
            counts[tag] += 1
    print('Videx tag report\n')
    if not complete:
        print('PARTIAL: inventory is incomplete; counts cover successfully inspected unique IDs only.')
        for path, exc in issues:
            print(f'WARNING: Cannot inspect {path}: {exc}; inventory is incomplete', file=sys.stderr)
    for tag, n in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold())):
        print(f'{tag:<30} {n:>5}')
    tagged = sum(bool(obj['bookmarks'].get(vid, {}).get('tags')) for vid in ids)
    label = 'Bookmarks' if complete else 'Inspected unique IDs (partial)'
    print(f'\n{label}: {len(ids)} | Tagged: {tagged} | Untagged: {len(ids)-tagged} | Unique tags: {len(counts)}')
    orphans = sorted(set(obj['bookmarks']) - set(ids))
    if orphans:
        label = 'Catalog entries without matching bookmarks' if complete else 'Unverified catalog entries'
        print(f'\n{label} ({len(orphans)}):')
        for vid in orphans: print(f'  {vid}')
    folded = defaultdict(list)
    for tag in counts: folded[tag.casefold()].append(tag)
    for variants in folded.values():
        if len(variants) > 1: print('CASE VARIANTS:', ', '.join(variants))
    duplicates = {vid: paths for vid, paths in ids.items() if len(paths) > 1}
    for vid, paths in sorted(duplicates.items()):
        print(f'\nDUPLICATE: {vid}')
        for path in paths: print(f'  {path}')
    return 1 if issues or duplicates else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        usage='%(prog)s [LIBRARY] OPERATION [VALUES ...] [--apply]',
        epilog='Start with OPERATION to use the configured default library. Changes are previews unless --apply is supplied. '
               'For hyphen-prefixed IDs or tags, use: [--apply] -- LIBRARY OPERATION VALUES ... . '
               'All values after -- are literal; put the write option before --. '
               'Bookmark symlinks and special files are not read. Mutations require a complete inventory. '
               'list reads only the catalog. report returns 0 when complete and clean, 1 for partial inspection '
               'or duplicate IDs, and 2 for invalid arguments, roots, or catalogs. Partial reports label unmatched '
               'records as Unverified catalog entries, not confirmed orphans.')
    parser.add_argument('library', type=Path, help='Library root containing bookmarks/ (omit when starting with an operation)')
    parser.add_argument('operation', choices=['add', 'remove', 'set', 'rename', 'list', 'report', 'prune'])
    parser.add_argument('values', nargs='*', help='For add/remove/set: VIDEO_ID TAG...; rename: OLD NEW; list: VIDEO_ID')
    parser.add_argument('--apply', action='store_true', help='Write changes; default is preview only')
    # Preserve legacy: LIBRARY OPERATION ..., while allowing OPERATION ...
    # to target the configured default library.
    operations = {'add', 'remove', 'set', 'rename', 'list', 'report', 'prune'}
    argv = sys.argv[1:]
    if argv and argv[0] in operations:
        try:
            argv.insert(0, str(default_library()))
        except ValueError as exc:
            parser.error(str(exc))
    args = parser.parse_args(argv)
    try:
        root = args.library.expanduser().resolve()
        path = root / 'catalog.json'
        if not stat.S_ISDIR((root / 'bookmarks').lstat().st_mode):
            raise ValueError(f'Not a library: {root / "bookmarks"} must be a real directory, not a symlink or special file')
        catalog = load(path)
        op, vals = args.operation, args.values
        if op == 'list':
            if len(vals) != 1 or not ID.fullmatch(vals[0]): parser.error('list requires VIDEO_ID')
            print('\n'.join(catalog['bookmarks'].get(vals[0], {}).get('tags', [])) or '(no tags)'); return 0
        if op == 'report':
            if vals: parser.error('report takes no additional arguments')
            ids, issues, available = inspect_archive(root)
            if not available:
                raise ValueError('Cannot inspect bookmark collection:\n  ' + '\n  '.join(f'{p}: {exc}' for p, exc in issues))
            return report(catalog, ids, issues)
        ids = archive_ids(root, strict=True)
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
                after = sorted(after, key=str.casefold)
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
