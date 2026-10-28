#!/usr/bin/env python3
"""Locate existing Videx bookmarks by video ID, without network access."""
import argparse
from collections import defaultdict
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, unquote, urlsplit

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
FILE_ID = re.compile(r"\[([A-Za-z0-9_-]{11})\]\.html$", re.I)


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "youtube-metadata":
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.chunks.append(data)


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


def archived_id(path):
    parser = MetadataParser()
    parser.feed(path.read_text(encoding="utf-8"))
    if not parser.chunks:
        return None
    data = json.loads("".join(parser.chunks))
    return data.get("id") if isinstance(data, dict) else None


def matches(root, video_id):
    found = []
    for path in sorted(root.rglob("*.html")) if root.is_dir() else []:
        if path.name == "index.html":
            continue
        match = FILE_ID.search(path.name)
        if match and match.group(1) == video_id:
            found.append(path)
            continue
        if match:
            continue
        # Fallback for manually renamed Videx bookmarks.
        try:
            if archived_id(path) == video_id:
                found.append(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return found



def audit_library(library):
    root = library / "bookmarks"
    if not root.is_dir():
        print(f"ERROR: Bookmarks directory does not exist: {root}", file=sys.stderr)
        return 2

    files = sorted(path for path in root.rglob("*.html") if path.is_file() and path.name != "index.html")
    groups = defaultdict(list)
    mismatches = []
    unknown = []
    unreadable = []

    for path in files:
        name_match = FILE_ID.search(path.name)
        filename_id = name_match.group(1) if name_match else None
        try:
            metadata_id = archived_id(path)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            unreadable.append((path, str(exc)))
            continue

        valid_metadata_id = metadata_id if isinstance(metadata_id, str) and VIDEO_ID.fullmatch(metadata_id) else None
        if filename_id and valid_metadata_id and filename_id != valid_metadata_id:
            mismatches.append((path, filename_id, valid_metadata_id))

        # Embedded metadata is authoritative for duplicate grouping.
        video_id = valid_metadata_id or filename_id
        if video_id:
            groups[video_id].append(path)
        else:
            unknown.append(path)

    duplicate_groups = [(video_id, paths) for video_id, paths in sorted(groups.items()) if len(paths) > 1]
    print(f"Scanning {len(files)} HTML files in {root}")
    for video_id, paths in duplicate_groups:
        print(f"\nDUPLICATE: {video_id}")
        for path in paths:
            print(f"  {path.relative_to(library)}")
    for path, filename_id, metadata_id in mismatches:
        print(f"\nMISMATCH: {path.relative_to(library)}")
        print(f"  Filename ID: {filename_id}")
        print(f"  Metadata ID: {metadata_id}")
    for path in unknown:
        print(f"\nUNKNOWN: {path.relative_to(library)}")
        print("  No valid video ID found in filename or embedded metadata")
    for path, error in unreadable:
        print(f"\nUNREADABLE: {path.relative_to(library)}")
        print(f"  {error}")

    print(f"\nScanned: {len(files)}")
    print(f"Unique video IDs: {len(groups)}")
    print(f"Duplicate groups: {len(duplicate_groups)}")
    print(f"Extra copies: {sum(len(paths) - 1 for _, paths in duplicate_groups)}")
    print(f"Mismatches: {len(mismatches)}")
    print(f"Unknown IDs: {len(unknown)}")
    print(f"Unreadable files: {len(unreadable)}")
    return 1 if duplicate_groups or mismatches or unknown or unreadable else 0

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("library", type=Path, help="Library root, not bookmarks/")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--url")
    group.add_argument("--id")
    group.add_argument("--audit", action="store_true", help="Audit all bookmarks for duplicates and ID inconsistencies")
    args = p.parse_args()
    if args.audit:
        return audit_library(args.library.expanduser().resolve())
    video_id = extract_id(args.url) if args.url else args.id
    if not video_id or not VIDEO_ID.fullmatch(video_id):
        # URL not resolvable locally: let yt-dlp determine its ID.
        return 3 if args.url else 2
    found = matches(args.library / "bookmarks", video_id)
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
