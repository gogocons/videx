# Videx

![License: MIT](https://img.shields.io/badge/License-MIT-blue)
![Version](https://img.shields.io/badge/Version-v0.4.0-red)
![Python](https://img.shields.io/badge/Python-3-blue)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux-lightgrey)

**An offline-first video bookmarking and reference library.**

Videx saves YouTube **metadata and thumbnails, not video files**, as standalone HTML bookmarks and builds a searchable local index. Keep useful video references without depending on YouTube's bookmarking interface, and browse saved descriptions, chapters, and metadata offline. Original video links may become unavailable; Videx does not preserve playback or transcripts.

The project currently focuses on YouTube and uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for extraction. Videx is not affiliated with YouTube or Google.

## Screenshots

### Video Library

Browse, search, filter channels, and sort saved references offline.

![Videx video library](docs/screenshots/library.png)

### Individual Bookmarks

Each standalone page contains a thumbnail, description, metadata, and links to the original video.

![Videx individual bookmark](docs/screenshots/bookmark.png)

## Current features

- Save titles, channels, descriptions, chapters, dates, source tags, historical counts, and thumbnails in self-contained HTML pages.
- Search the offline index by title, channel, description, video ID, or personal tags; use the compact Filters panel for channels and exact personal tags, with removable active-filter chips. Sort independently by upload date, title, or channel.
- Prevent duplicate captures by video ID, recognize renamed bookmarks through embedded metadata, and refresh existing bookmarks at their original paths.
- Manage personal tags in a separate catalog, add tags during capture, and show searchable tag badges on index cards.
- Manage local groups with `./videx collection` and browse them from **Filters** in the rebuilt offline index. Catalog edits preview first and save with `--apply`. [Collection commands](docs/USAGE.md#collection-management)
- Audit identities and duplicates without changing files, with optional personal-tag and JSON reports.
- Upgrade saved bookmark pages offline, using previews and backups, and keep your library outside the source repository.
- Import a TXT backlog or public YouTube playlist with read-only previews, duplicate checks, per-entry results, and one final index rebuild.

The current source includes manual Collection management, offline Collection browsing, and optional playlist-derived Collections. The v0.4.0 badge identifies the recorded release snapshot. [Collections milestones](docs/DEVELOPMENT.md#collections-milestones)

Preview a TXT backlog with `./videx import --file backlog.txt` or preview a
public playlist with `./videx import --playlist 'https://www.youtube.com/playlist?list=PLAYLIST_ID'`.
Add `--apply` to capture new bookmarks. TXT preview works offline; playlist preview
uses the network for discovery. Neither writes to the library. See the
[batch import format, examples, and recovery guide](docs/BATCH_IMPORT.md).

For optional Collection creation, save a playlist import report with
`--propose-collection --apply --no-re-index --json`. Review it offline with
`./videx collection from-import REPORT`, apply it separately, then rebuild the
index. [Playlist-to-Collection walkthrough](docs/BATCH_IMPORT.md#playlist-to-collection)

## Requirements

- **Python 3**, **Bash**, and common POSIX utilities (`find`, `sed`, `mktemp`).
- **yt-dlp**, installed separately and available on `PATH`, for capture, refresh, and playlist discovery.
- **Pillow**, optionally, to reduce embedded index thumbnail sizes; otherwise original thumbnails are used.
- For `./videx open`, a graphical desktop with `open` on macOS or `xdg-open` on Linux and a default HTML application.
- macOS or Linux; no Node.js, database, web server, or `jq` required. Linux is an intended platform; see the [recorded runtime validation](docs/DEVELOPMENT.md#testing-and-recorded-runtime-validation) for tested environments. No minimum Python version beyond Python 3 has been qualified.

Install yt-dlp using its [official instructions](https://github.com/yt-dlp/yt-dlp/wiki/Installation), your package manager, or `python3 -m pip install -U yt-dlp` in a suitable Python environment. Check `yt-dlp --version`, `python3 --version`, and `bash --version`. Update yt-dlp if site changes break extraction.

## Quick start

Clone this repository and work from its root. Before capturing, choose your library location. Copy `config.example.json` to `config.local.json` and edit it, for example:

```json
{
  "library": "~/Documents/Video Bookmarks"
}
```

This local configuration is ignored by Git. Use an absolute path or one beginning with `~/`, pointing to the **library root**, not its `bookmarks/` subdirectory. Without configuration, Videx uses the project root; invalid configuration fails rather than silently choosing another location. Explicit command paths override the configured default.

```text
<library>/
├── bookmarks/    # Standalone saved HTML bookmarks
├── catalog.json # Personal catalog data, created when needed
└── index.html   # Generated offline index
```

View capture options, then save a bookmark and rebuild the index. Replace `AAAAAAAAAAA` with a real 11-character video ID:

```bash
./videx --help
./videx capture --help
./videx capture --re-index 'https://www.youtube.com/watch?v=AAAAAAAAAAA'
./videx open
```

`./videx open` opens the saved `<library>/index.html` with your desktop's default HTML application. It works offline and opens the snapshot as saved. To open another library, use `./videx open --library '/path/to/library'`. To build or refresh a snapshot, run `./videx index` separately. Capture creates `bookmarks/` automatically and saves new pages under `<library>/bookmarks/<channel>/<title> [video-id].html`. The catalog is separate from embedded source metadata.

## Essential commands

These examples use the configured library or project-local fallback. Tag edits require an existing bookmark with the indicated ID. The existing direct script invocations remain supported with the same command-specific options. See [Usage](docs/USAGE.md) for explicit paths and argument syntax.

```bash
# Capture without rebuilding the index
./videx capture 'https://youtu.be/AAAAAAAAAAA'

# Refresh an existing bookmark and rebuild the index
./videx capture --overwrite --re-index 'https://youtu.be/AAAAAAAAAAA'

# Rebuild the index independently, offline
./videx index

# New bookmark: tags save automatically; existing bookmark: preview additions
./videx capture --tag Python --tag Research 'https://youtu.be/AAAAAAAAAAA'

# Preview, then apply personal tags to an existing bookmark
./videx tag add AAAAAAAAAAA Python Research
./videx tag add AAAAAAAAAAA Python Research --apply

# Inspect tag totals and audit the library without modifying it
./videx tag report
./videx find --audit

# Preview offline upgrades, then apply with backups
./videx upgrade
./videx upgrade --apply

# Rebuild after upgrades or applied tag changes
./videx index
```

The tag tool also supports removing, replacing, and renaming tags, catalog-only listing, and previewing orphan-record pruning. Audit reports duplicates, mismatches, unknown identities, and unreadable files; it does not repair them. Inspect warnings before applying maintenance operations.

## Important usage cautions

- **Internet:** Capture, refresh, and playlist discovery (including preview) need internet access. TXT previews, browsing, indexing, tagging, auditing, and upgrades work offline; external video links still require a connection.
- **Destination:** Capture `--output DIR` selects a library root and can create directories. Check the configured or explicit path before your first capture. Index and audit commands do not create missing bookmark directories.
- **Existing bookmarks:** Existing IDs are skipped by default, even after title/channel changes. `--overwrite` refreshes HTML at its existing path; ambiguous matches are refused.
- **Preview versus writes:** Catalog edits and upgrades preview by default; `--apply` saves those changes. Capture writes new bookmarks and automatically saves their requested tags. For existing capture tags, `--apply` controls tag edits independently of HTML refresh with `--overwrite`.
- **Index updates:** Individual capture rebuilds only with `--re-index`, including after a skip. Applied bulk imports rebuild once after creating bookmarks by default; use `--no-re-index` to suppress that rebuild. No-new-bookmark batches and previews do not rebuild. Rebuild explicitly after upgrades, applied catalog/visibility changes, or index search-code updates. Tag commands never rebuild automatically.
- **Visibility:** The normalized personal tag `hidden` excludes a bookmark from the rebuilt index, not from files, catalogs, or CLI reports. It is no security feature. Older copies may contain hidden entries; failed rebuilds retain the existing index.
- **Backups:** Back up your library independently of the source repository. Generated library files are ignored by Git by default. Duplicate checks and audits do not replace backups; retain upgrade/catalog backups until verified.

## Documentation

- [Usage](docs/USAGE.md) — Detailed commands, configuration, tag workflows, reports, moving libraries, and troubleshooting.
- [Development and project tracking](docs/DEVELOPMENT.md) — Authoritative current status, completed milestones, roadmap, known issues, decisions, architecture, and testing. GitHub Issues are optional supplementary tracking.
- [Data formats](docs/DATA_FORMATS.md) — Library layout, metadata, catalog and report schemas, identity, normalization, and compatibility.
- [Safety](docs/SAFETY.md) — Filesystem checks, validation, backups, publication, failure statuses, and concurrency limits.
- [Batch import](docs/BATCH_IMPORT.md) — TXT format, public playlists, preview/apply commands, reports, and recovery.

## License

MIT — see [LICENSE](LICENSE). [yt-dlp](https://github.com/yt-dlp/yt-dlp) is an external dependency with its own license; it is not bundled with Videx.
