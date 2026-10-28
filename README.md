# Videx

**An offline-first video bookmarking and reference library.**

Videx began as a way to keep useful YouTube video references without depending on YouTube's bookmarking interface. It saves **metadata and thumbnails, not video files**, as standalone HTML pages and builds a searchable offline index. The project is currently focused on YouTube and uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for extraction. Videx is not affiliated with YouTube or Google.

## Current features (v0.1.0)

- Save a single video's title, channel, description, chapters, dates, tags, historical counts and thumbnail in a self-contained HTML page.
- Browse a generated `index.html` offline; search by title, channel and description, filter channels, and sort by upload date/title/channel.
- Preserve a curated metadata schema without large extractor manifests (formats, automatic caption manifests, etc.).
- Upgrade previously saved bookmark pages **offline**, with a dry run and backups before applying changes.
- Keep your library in a project-relative `bookmarks/` directory or specify a different archive directory.

**Limitations:** Videx does not save video playback or transcripts. Original video links may become unavailable. Playlist importing, personal tags and collections are **planned, not yet implemented**. Existing-bookmark checks use the proposed filename, so changed video titles/channel names can result in duplicate references.

## Requirements

- **Python 3** (required)
- **yt-dlp** (required, installed separately and available on `PATH`)
- **Bash** and common POSIX command-line utilities (`find`, `sed`, `mktemp`)
- **Pillow** (optional Python package): reduces embedded thumbnail sizes in the generated index; without it, original thumbnails are used.
- macOS or Linux; no Node.js, database, web server or `jq` required.

Install yt-dlp using its [official installation instructions](https://github.com/yt-dlp/yt-dlp/wiki/Installation), your system package manager, or `python3 -m pip install -U yt-dlp` in a suitable Python environment. Check dependencies with `yt-dlp --version`, `python3 --version`, and `bash --version`. yt-dlp evolves as video sites change; update it if extraction stops working.

## Quick start

Clone this repository, then from its root run:

```bash
bash scripts/yt-bookmark.sh --help
bash scripts/yt-bookmark.sh 'https://www.youtube.com/watch?v=VIDEO_ID'
python3 -B scripts/yt-bookmark-index.py
```

Open the generated `index.html` in your browser. Bookmarks are created under `bookmarks/<channel>/<title> [video-id].html`. The `bookmarks/` folder is created automatically when saving the first video. No server is needed.

### Other commands

```bash
# Save into a separate archive (the index will be written beside that archive)
bash scripts/yt-bookmark.sh --output '/path/to/archive' 'https://youtu.be/VIDEO_ID'
python3 -B scripts/yt-bookmark-index.py '/path/to/archive'

# Replace an existing bookmark with the same generated filename
bash scripts/yt-bookmark.sh --overwrite 'https://www.youtube.com/watch?v=VIDEO_ID'

# Inspect possible upgrades without changing files
python3 -B scripts/yt-bookmark-upgrade.py

# Apply upgrades; originals are copied to a timestamped sibling backup folder
python3 -B scripts/yt-bookmark-upgrade.py --apply
python3 -B scripts/yt-bookmark-index.py
```

The index is regenerated **manually** after adding or upgrading bookmarks. Index generation requires at least one valid bookmark. The upgrade tool works entirely offline; inspect its dry-run output before applying and retain backups until you verify the results.

## Project layout

```text
videx/
├── scripts/
│   ├── yt-bookmark.sh            # Acquire video metadata and thumbnail
│   ├── yt-bookmark-generate.py   # Render standalone HTML
│   ├── yt-bookmark-metadata.py   # Curate stable metadata (schema v2)
│   ├── yt-bookmark-index.py      # Generate searchable offline index
│   └── yt-bookmark-upgrade.py    # Offline migration and backups
├── bookmarks/                   # Personal library; Git-ignored
└── index.html                   # Generated dashboard; Git-ignored
```

Individual bookmarks remain readable on their own. Their embedded thumbnail and curated metadata do not require a network connection; external links do. The back-to-library link assumes the bookmark remains in the indexed archive tree.

## Privacy and backups

**Your bookmarks are not part of this source repository.** `.gitignore` excludes the local `bookmarks/` directory, generated `index.html`, caches and upgrade backup directories. Bookmarks can contain personal choices and video metadata; review `git status` and `git diff --cached --name-only` before publishing. Git ignore rules do not remove files already committed. Back up the library separately from the source code.

## Project history and roadmap

**Origin:** A simple local alternative to YouTube's bookmark/playlist front end. The first iteration generated a self-contained HTML page for each saved video.

**Current state:** A small suite of independent scripts, a channel-organized archive, curated metadata, a searchable offline index, and a safe offline upgrade workflow. The original `yt-bookmark-*` script names are retained intentionally.

**Next iteration (not implemented):**

1. Import all accessible entries from a YouTube playlist as individual bookmarks, skipping existing video IDs and reporting failures.
2. Maintain one central archive with **personal tags** for topical searching and **collections** for curated browsing; a bookmark may belong to multiple collections.
3. Keep uploader tags separate from personal tags, and store user classifications in a catalog independent of generated HTML.
4. Extend the index with tag/collection filtering and visual collection browsing.

Longer-term possibilities include better batch management and a unified `videx` command, if the existing tools become cumbersome. These are ideas, not commitments.

## License

MIT — see [LICENSE](LICENSE). [yt-dlp](https://github.com/yt-dlp/yt-dlp) is an external dependency with its own license; it is not bundled with Videx.
