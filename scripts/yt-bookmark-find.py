#!/usr/bin/env python3
"""Locate existing Videx bookmarks by video ID, without network access."""
import argparse
from collections import defaultdict
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import parse_qs, unquote, urlsplit
from videx_config import default_library
from videx_identity import VIDEO_ID, IdentityError, bookmark_identity


def extract_id(url):
    """Only trust known YouTube hostnames and unambiguous single-video paths."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https"):
            return None
        if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            if parsed.path == "/watch":
                ids = parse_qs(parsed.query).get("v", [])
                candidate = ids[0] if len(ids) == 1 else ""
            elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
                candidate = unquote(parsed.path.split("/")[2])
            else:
                return None
        elif host in ("youtu.be", "www.youtu.be"):
            candidate = unquote(parsed.path.strip("/"))
        else:
            return None
        return candidate if VIDEO_ID.fullmatch(candidate) else None
    except ValueError:
        return None


def inventory(root):
    """Inspect only the bookmark collection, retaining every traversal failure."""
    files, identities, issues = [], {}, []

    def walk(directory):
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
            for entry in children:
                path = Path(entry.path)
                if path.name.lower() == 'index.html':
                    continue
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        raise ValueError('symbolic link prevents a complete local inventory')
                    if stat.S_ISDIR(mode):
                        walk(path)
                    elif path.suffix == '.html':
                        if not stat.S_ISREG(mode):
                            raise ValueError('bookmark is not a regular file')
                        files.append(path)
                except (OSError, ValueError) as exc:
                    issues.append((path, IdentityError(str(exc))))
        except OSError as exc:
            issues.append((directory, IdentityError(str(exc))))

    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError('bookmarks collection must be a regular directory, not a symbolic link')
    except (OSError, ValueError) as exc:
        return files, identities, [(root, IdentityError(str(exc)))], False
    walk(root)
    for path in files:
        try:
            identities[path] = bookmark_identity(path)
        except IdentityError as exc:
            issues.append((path, exc))
        except (OSError, UnicodeError) as exc:
            issues.append((path, IdentityError(str(exc))))
    available = not any(path == root for path, _ in issues)
    return files, identities, issues, available


def describe_issue(path, issue):
    detail = f'{issue.category}: {path}: {issue}'
    if issue.category == 'MISMATCH':
        detail += f'\n  Filename ID: {issue.filename_id}\n  Metadata ID: {issue.metadata_id}'
    return detail



def audit_library(library, snapshot=None):
    root = library / "bookmarks"
    files, identities, issues, available = inventory(root) if snapshot is None else snapshot
    if not available:
        for path, issue in issues:
            print(f'ERROR: {describe_issue(path, issue)}', file=sys.stderr)
        return 2
    groups = defaultdict(list)
    for path, video_id in identities.items():
        groups[video_id].append(path)

    duplicate_groups = [(video_id, paths) for video_id, paths in sorted(groups.items()) if len(paths) > 1]
    print(f"Scanning {len(files)} HTML files in {root}")
    for video_id, paths in duplicate_groups:
        print(f"\nDUPLICATE: {video_id}")
        for path in paths:
            print(f"  {path.relative_to(library)}")
    for path, issue in issues:
        print('\n' + describe_issue(path.relative_to(library), issue))

    print(f"\nScanned: {len(files)}")
    print(f"Unique video IDs: {len(groups)}")
    print(f"Duplicate groups: {len(duplicate_groups)}")
    print(f"Extra copies: {sum(len(paths) - 1 for _, paths in duplicate_groups)}")
    print(f"Mismatches: {sum(issue.category == 'MISMATCH' for _, issue in issues)}")
    print(f"Unknown IDs: {sum(issue.category == 'UNKNOWN' for _, issue in issues)}")
    print(f"Unreadable files: {sum(issue.category == 'UNREADABLE' for _, issue in issues)}")
    return 1 if duplicate_groups or issues else 0


def catalog_tags(library):
    """Read personal tags only when requested; never follow a catalog symlink."""
    path = library / 'catalog.json'
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return 'missing', {}
    if not stat.S_ISREG(mode):
        raise ValueError(f'{path}: catalog must be a regular file, not a symlink or special file')
    spec = spec_from_file_location('bookmark_tags', Path(__file__).with_name('yt-bookmark-tag.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    catalog = module.load(path)
    tags = {}
    for video_id, record in catalog['bookmarks'].items():
        values = record.get('tags', [])
        if any(not isinstance(value, str) for value in values):
            raise ValueError(f'{path}: tags for {video_id} must contain only strings')
        tags[video_id] = module.unique(values)
    return 'present', tags


def tag_report(library, args):
    """Opt-in rendering; default lookup/audit output stays on its original path."""
    report = {'report_version': 1, 'operation': 'audit' if args.audit else 'lookup',
              'library': str(library), 'catalog': {'status': 'not_read'},
              'bookmarks': [], 'issues': []}
    snapshot = None
    status = 0
    groups = defaultdict(list)
    if not args.audit:
        video_id = extract_id(args.url) if args.url else args.id
        if not video_id or not VIDEO_ID.fullmatch(video_id):
            status = 3 if args.url else 2
            report['issues'].append({'category': 'UNRESOLVED_URL' if args.url else 'INVALID_ID',
                                     'message': 'URL requires extractor resolution' if args.url else 'Invalid video ID'})
    if status == 0:
        snapshot = inventory(library / 'bookmarks')
        files, identities, issues, available = snapshot
        for path, issue in issues:
            item = {'category': issue.category, 'path': str(path), 'message': describe_issue(path, issue)}
            if issue.category == 'MISMATCH':
                item.update(filename_id=issue.filename_id, metadata_id=issue.metadata_id)
            report['issues'].append(item)
        for path, identity in identities.items():
            if args.audit or identity == video_id:
                groups[identity].append(path)
        for identity, paths in sorted(groups.items()):
            if len(paths) > 1:
                report['issues'].append({'category': 'DUPLICATE', 'id': identity,
                                         'paths': sorted(map(str, paths)),
                                         'message': f'Multiple bookmarks have video ID {identity}'})
        if args.audit:
            report['summary'] = {
                'scanned': len(files), 'unique_video_ids': len(groups),
                'duplicate_groups': sum(len(paths) > 1 for paths in groups.values()),
                'extra_copies': sum(len(paths) - 1 for paths in groups.values()),
                'mismatches': sum(issue.category == 'MISMATCH' for _, issue in issues),
                'unknown_ids': sum(issue.category == 'UNKNOWN' for _, issue in issues),
                'unreadable_files': sum(issue.category == 'UNREADABLE' for _, issue in issues),
            }
            status = 2 if not available else 1 if report['issues'] else 0
        else:
            status = 2 if report['issues'] else 0 if groups else 1
        if args.audit or status == 0:
            report['bookmarks'] = [{'id': identity, 'paths': sorted(map(str, paths)), 'tags': None}
                                   for identity, paths in sorted(groups.items())]
        if (args.audit and available) or (not args.audit and status == 0):
            try:
                catalog_status, tags = catalog_tags(library)
                report['catalog']['status'] = catalog_status
                for item in report['bookmarks']:
                    item['tags'] = tags.get(item['id'], [])
            except (OSError, ValueError) as exc:
                status = 2
                report['catalog']['status'] = 'unavailable'
                report['issues'].append({'category': 'CATALOG_ERROR', 'path': str(library / 'catalog.json'),
                                         'message': f'Cannot report personal tags: {exc}'})
    report['exit_status'] = status
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        if args.audit:
            audit_library(library, snapshot)
            if report['catalog']['status'] != 'not_read':
                print(f"\nPersonal catalog tags ({report['catalog']['status']}):")
        for issue in report['issues']:
            if issue['category'] == 'CATALOG_ERROR' or (not args.audit and status == 2):
                print(f"ERROR: {issue['message']}", file=sys.stderr)
                for path in issue.get('paths', []):
                    print(f'  {path}', file=sys.stderr)
        if args.audit or status == 0:
            for item in report['bookmarks']:
                for path in item['paths']:
                    print(f'Bookmark: {path}')
                print(f"Video ID: {item['id']}")
                tags = 'unavailable' if item['tags'] is None else json.dumps(item['tags'], ensure_ascii=False)
                print(f'Tags: {tags}')
    return status


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("library", type=Path, nargs="?", help="Library root (default: configured library)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--id")
    group.add_argument("--audit", action="store_true", help="Audit all bookmarks for duplicates and ID inconsistencies")
    p.add_argument('--show-tags', action='store_true', help='Report personal tags from the selected library catalog')
    p.add_argument('--json', action='store_true', help='Emit one JSON report (requires --show-tags)')
    args = p.parse_args()
    if args.json and not args.show_tags:
        p.error('--json requires --show-tags')
    try:
        args.library = args.library.expanduser().resolve() if args.library else default_library()
    except ValueError as exc:
        if args.show_tags and args.json:
            print(json.dumps({'report_version': 1, 'operation': 'audit' if args.audit else 'lookup',
                              'library': None, 'catalog': {'status': 'not_read'}, 'bookmarks': [],
                              'issues': [{'category': 'CONFIG_ERROR', 'message': str(exc)}], 'exit_status': 2}))
            return 2
        p.error(str(exc))
    if args.show_tags:
        return tag_report(args.library, args)
    if args.audit:
        return audit_library(args.library.expanduser().resolve())
    video_id = extract_id(args.url) if args.url else args.id
    if not video_id or not VIDEO_ID.fullmatch(video_id):
        # URL not resolvable locally: let yt-dlp determine its ID.
        return 3 if args.url else 2
    _, identities, issues, _ = inventory(args.library / 'bookmarks')
    if issues:
        for path, issue in issues:
            print(f'ERROR: {describe_issue(path, issue)}', file=sys.stderr)
        print('ERROR: Incomplete or conflicting bookmark inventory; no path selected.', file=sys.stderr)
        return 2
    found = [path for path, identity in identities.items() if identity == video_id]
    if len(found) > 1:
        print(f"ERROR: Multiple bookmarks have video ID {video_id}:", file=sys.stderr)
        for path in found:
            print(f"  {path}", file=sys.stderr)
        return 2
    if found:
        print(found[0])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
