#!/usr/bin/env python3
"""Locate existing Videx bookmarks by video ID, without network access."""
import argparse
from collections import defaultdict
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



def audit_library(library):
    root = library / "bookmarks"
    files, identities, issues, available = inventory(root)
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

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("library", type=Path, nargs="?", help="Library root (default: configured library)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--id")
    group.add_argument("--audit", action="store_true", help="Audit all bookmarks for duplicates and ID inconsistencies")
    args = p.parse_args()
    try:
        args.library = args.library.expanduser().resolve() if args.library else default_library()
    except ValueError as exc:
        p.error(str(exc))
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
