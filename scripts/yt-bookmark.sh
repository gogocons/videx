#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
LIBRARY_DIR="$PROJECT_DIR"
usage() {
  cat <<'EOF'
Usage: yt-bookmark.sh [--output LIBRARY_DIRECTORY] [--overwrite] URL

Create one self-contained HTML bookmark from a YouTube URL.
Default library: <project>/
Bookmarks are saved under <library>/bookmarks/<channel>/.
EOF
}
OVERWRITE=0
URL=""
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o|--output) (($# >= 2)) || { echo 'ERROR: --output needs a directory' >&2; exit 2; }; LIBRARY_DIR="$2"; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    --) shift; break ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) if [[ -n "$URL" ]]; then echo 'ERROR: Supply one URL' >&2; exit 2; fi; URL="$1"; shift ;;
  esac
done
if (($#)); then if [[ -n "$URL" || $# -ne 1 ]]; then usage >&2; exit 2; fi; URL="$1"; fi
[[ -n "$URL" ]] || { usage >&2; exit 2; }
for cmd in yt-dlp python3; do command -v "$cmd" >/dev/null || { echo "ERROR: Missing $cmd" >&2; exit 1; }; done
[[ -f "$SCRIPT_DIR/yt-bookmark-generate.py" ]] || { echo 'ERROR: Missing yt-bookmark-generate.py' >&2; exit 1; }
# --output specifies the library root, not the bookmarks directory.
if [[ "$LIBRARY_DIR" != "$PROJECT_DIR" && "${LIBRARY_DIR%/}" == */bookmarks ]]; then
  echo 'ERROR: --output expects the library root, not its bookmarks/ directory.' >&2
  exit 2
fi

mkdir -p "$LIBRARY_DIR"
LIBRARY_DIR="$(cd -- "$LIBRARY_DIR" && pwd)"

OUTPUT_DIR="$LIBRARY_DIR/bookmarks"
mkdir -p "$OUTPUT_DIR"
tmpdir="$(mktemp -d)"
trap 'rm -rf -- "$tmpdir"' EXIT
printf '[1/4] Fetching metadata and thumbnail...\n'
yt-dlp --no-playlist --skip-download --write-thumbnail --write-info-json --paths "$tmpdir" --output '%(title)s [%(id)s].%(ext)s' "$URL"
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
channel_dir="$OUTPUT_DIR/$channel"
mkdir -p "$channel_dir"
output="$channel_dir/$title [$video_id].html"
if [[ -e "$output" && "$OVERWRITE" -ne 1 ]]; then
  echo "ERROR: Bookmark already exists: $output" >&2
  echo 'Use --overwrite to replace it.' >&2
  exit 1
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
