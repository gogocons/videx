#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
LIBRARY_DIR=""
EXPLICIT_OUTPUT=0

usage() {
  cat <<'EOF'
Usage: yt-bookmark.sh [--output LIBRARY_DIRECTORY] [--overwrite] [--re-index] [--tag TAG ...] [--apply] URL
       yt-bookmark.sh [--output LIBRARY_DIRECTORY] [--overwrite] [--re-index] [--tag TAG ...] [--apply] --id TEXT

Create one self-contained HTML bookmark from a YouTube URL or video ID.
Default library: config.local.json library, otherwise <project>/
Bookmarks are saved under <library>/bookmarks/<channel>/.

Options:
  -o, --output DIR  Select a library root (not its bookmarks/ directory)
      --id TEXT     Accept a bare 11-character ID or [VIDEO_ID] in text
      --tag TAG     Add a tag (repeat for multiple tags)
      --apply       Apply tag changes to existing bookmarks (default: preview)
      --overwrite   Refresh an existing video ID at its original file path
      --re-index    Rebuild the selected library index after capture or skip
  -h, --help        Show this help

An existing video ID is skipped by default, even if its title has changed.
The index is not rebuilt unless --re-index is specified.
EOF
}

OVERWRITE=0
RE_INDEX=0
APPLY=0
TAGS=()
ID_MODE=0
URL=""
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o|--output)
      (($# >= 2)) || { echo 'ERROR: --output needs a directory' >&2; exit 2; }
      LIBRARY_DIR="$2"; EXPLICIT_OUTPUT=1; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    --apply) APPLY=1; shift ;;
    --tag)
      (($# >= 2)) && [[ -n "${2//[[:space:]]/}" ]] || { echo 'ERROR: --tag needs a nonempty value' >&2; exit 2; }
      TAGS+=("$2"); shift 2 ;;
    --re-index) RE_INDEX=1; shift ;;
    --id)
      ((ID_MODE == 0)) || { echo 'ERROR: --id specified more than once' >&2; exit 2; }
      (($# >= 2)) || { echo 'ERROR: --id needs a video ID or text' >&2; exit 2; }
      ID_MODE=1
      if [[ -n "$URL" ]]; then echo 'ERROR: Supply only one input' >&2; exit 2; fi
      URL="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "$URL" ]]; then echo 'ERROR: Supply only one input' >&2; exit 2; fi
      URL="$1"; shift ;;
  esac
done
if (($#)); then
  if [[ -n "$URL" || $# -ne 1 ]]; then usage >&2; exit 2; fi
  URL="$1"
fi
[[ -n "$URL" ]] || { usage >&2; exit 2; }

command -v python3 >/dev/null || { echo 'ERROR: Missing python3' >&2; exit 1; }
if (( ! EXPLICIT_OUTPUT )); then
  LIBRARY_DIR="$(python3 -B "$SCRIPT_DIR/videx_config.py")" || exit 2
fi

# --id accepts exactly one bare ID or one distinct bracketed ID in text.
# Reject ambiguous inputs rather than accidentally bookmarking another video.
if ((ID_MODE)); then
  video_id="$(python3 - "$URL" <<'PYID'
import re
import sys

value = sys.argv[1].strip()
if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
    print(value)
else:
    matches = set(re.findall(r"\[([A-Za-z0-9_-]{11})\]", value))
    if len(matches) == 1:
        print(next(iter(matches)))
    else:
        message = ("Multiple distinct bracketed video IDs found"
                   if matches else "No valid YouTube video ID found")
        print(f"ERROR: {message}", file=sys.stderr)
        sys.exit(2)
PYID
  )" || exit 2
  URL="https://www.youtube.com/watch?v=$video_id"
fi
for file in yt-bookmark-generate.py yt-bookmark-find.py; do
  [[ -f "$SCRIPT_DIR/$file" ]] || { echo "ERROR: Missing $file" >&2; exit 1; }
done
if ((${#TAGS[@]})); then
  [[ -f "$SCRIPT_DIR/yt-bookmark-tag.py" ]] || { echo 'ERROR: Missing yt-bookmark-tag.py' >&2; exit 1; }
fi
if ((RE_INDEX)); then
  [[ -f "$SCRIPT_DIR/yt-bookmark-index.py" ]] || { echo 'ERROR: Missing yt-bookmark-index.py' >&2; exit 1; }
fi

# --output specifies the library root, not the bookmarks directory.
if [[ "${LIBRARY_DIR%/}" == */bookmarks ]]; then
  echo 'ERROR: --output expects the library root, not its bookmarks/ directory.' >&2
  exit 2
fi
mkdir -p "$LIBRARY_DIR"
LIBRARY_DIR="$(cd -- "$LIBRARY_DIR" && pwd)"
OUTPUT_DIR="$LIBRARY_DIR/bookmarks"
mkdir -p "$OUTPUT_DIR"

reindex() {
  if ((RE_INDEX)); then
    printf 'Rebuilding library index...\n'
    python3 -B "$SCRIPT_DIR/yt-bookmark-index.py" "$LIBRARY_DIR"
  fi
}

# Exit status: 0 = found (path on stdout), 1 = not found,
# 2 = ambiguous/error, 3 = URL needs yt-dlp resolution.
lookup() {
  local result status
  result="$(python3 -B "$SCRIPT_DIR/yt-bookmark-find.py" "$LIBRARY_DIR" "$@" )" && status=0 || status=$?
  case "$status" in
    0) printf '%s\n' "$result"; return 0 ;;
    1|3) return "$status" ;;
    *) echo 'ERROR: Bookmark lookup failed; refusing to create or overwrite.' >&2; return 2 ;;
  esac
}

# A new capture already writes to disk, so its requested tags are applied
# automatically. For existing bookmarks, tags are previewed unless --apply.
# The tag utility validates membership and manages catalog backups.
tag_existing() {
  local vid="$1"
  if ((${#TAGS[@]})); then
    if ((APPLY)); then
      python3 -B "$SCRIPT_DIR/yt-bookmark-tag.py" "$LIBRARY_DIR" add "$vid" "${TAGS[@]}" --apply
    else
      python3 -B "$SCRIPT_DIR/yt-bookmark-tag.py" "$LIBRARY_DIR" add "$vid" "${TAGS[@]}"
    fi
  fi
}
tag_new() {
  local vid="$1"
  if ((${#TAGS[@]})); then
    python3 -B "$SCRIPT_DIR/yt-bookmark-tag.py" "$LIBRARY_DIR" add "$vid" "${TAGS[@]}" --apply
  fi
}

existing=""
lookup_status=0
existing="$(lookup --url "$URL")" || lookup_status=$?
if ((lookup_status == 2)); then exit 1; fi
if ((lookup_status == 0 && !OVERWRITE)); then
  printf 'SKIPPED: Video already bookmarked: %s\n' "$existing"
  # --url may be an alternate URL; use lookup by ID from the existing file
  # when the URL is not a simple watch URL. The tagger can only accept IDs.
  if ((${#TAGS[@]})); then
    known_id="$(python3 - "$existing" <<'PYKNOWN'
import re, sys
from pathlib import Path
m = re.search(r'\[([A-Za-z0-9_-]{11})\]\.html$', Path(sys.argv[1]).name)
if m: print(m.group(1))
PYKNOWN
)"
    if [[ -z "$known_id" ]]; then
      echo 'ERROR: Cannot determine ID from existing filename; use --id VIDEO_ID for tagging.' >&2
      exit 2
    fi
    tag_existing "$known_id"
  fi
  reindex
  exit 0
fi

command -v yt-dlp >/dev/null || { echo 'ERROR: Missing yt-dlp' >&2; exit 1; }
tmpdir="$(mktemp -d)"
trap 'rm -rf -- "$tmpdir"' EXIT

printf '[1/4] Fetching metadata and thumbnail...\n'
yt-dlp --no-playlist --skip-download --write-thumbnail --write-info-json \
  --paths "$tmpdir" --output '%(title)s [%(id)s].%(ext)s' "$URL"

printf '[2/4] Locating files...\n'
info_json="$(find "$tmpdir" -type f -name '*.info.json' -print -quit)"
[[ -n "$info_json" ]] || { echo 'ERROR: Missing metadata JSON' >&2; exit 1; }
thumbnail="$(find "$tmpdir" -type f ! -name '*.info.json' -print -quit)"
[[ -n "$thumbnail" ]] || { echo 'ERROR: Missing thumbnail' >&2; exit 1; }

# Use Python for cross-platform path safety, including slashes in titles and channel names.
fields="$(python3 - "$info_json" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding='utf-8'))
def safe(x):
    x=str(x or 'Unknown').replace('/', '⧸').replace('\\', '⧹').replace('\0','')
    x=''.join(c for c in x if ord(c)>=32 and c not in ':*?"<>|')
    return x.strip(' .')[:180] or 'Unknown'
print(safe(m.get('uploader') or m.get('channel') or 'Unknown Channel'))
print(safe(m.get('title') or 'Unknown Title'))
print(safe(m.get('id') or 'Unknown ID'))
PY
)"
channel="$(printf '%s\n' "$fields" | sed -n '1p')"
title="$(printf '%s\n' "$fields" | sed -n '2p')"
video_id="$(printf '%s\n' "$fields" | sed -n '3p')"

# Recheck using yt-dlp's authoritative ID, including URLs that couldn't be
# parsed locally and URLs that redirect to a different video.
resolved_existing=""
resolved_status=0
resolved_existing="$(lookup --id "$video_id")" || resolved_status=$?
if ((resolved_status == 2)); then exit 1; fi
if ((resolved_status == 0)); then
  if ((OVERWRITE)); then
    output="$resolved_existing"
    printf 'REFRESH: Existing bookmark: %s\n' "$output"
  else
    printf 'SKIPPED: Video already bookmarked: %s\n' "$resolved_existing"
    tag_existing "$video_id"
    reindex
    exit 0
  fi
else
  channel_dir="$OUTPUT_DIR/$channel"
  output="$channel_dir/$title [$video_id].html"
  if [[ -e "$output" ]]; then
    echo "ERROR: Destination already exists but was not identified as the same video: $output" >&2
    exit 1
  fi
fi

printf '[3/4] Building bookmark: %s\n' "$title"
back_link="$(python3 - "$output" "$LIBRARY_DIR" <<'PYBACK'
import os, sys
output, root = sys.argv[1:]
print(os.path.relpath(os.path.join(root, "index.html"), os.path.dirname(output)))
PYBACK
)"
python3 -B "$SCRIPT_DIR/yt-bookmark-generate.py" "$info_json" "$thumbnail" "$output" --back-link "$back_link"
printf '[4/4] Complete: %s\n' "$output"
if ((resolved_status == 0)); then
  tag_existing "$video_id"
else
  tag_new "$video_id"
fi
reindex
