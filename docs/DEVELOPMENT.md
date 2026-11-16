# Videx development

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md)

## Architecture and data flow

Videx is a small suite of Bash and Python scripts. Capture uses separately installed yt-dlp to obtain metadata and a thumbnail in a temporary directory, resolves the video's identity, and renders a standalone bookmark. The generator curates metadata and embeds the thumbnail and JSON in HTML. Requested personal tags are maintained separately in the library catalog. Optional indexing reads saved bookmarks and catalog tags to produce a self-contained offline snapshot.

The index is generated output; bookmark HTML and personal catalog data are persistent library data. Rebuilding the index does not refresh source metadata. Counts represent capture-time snapshots. Lookup, audit, tagging, index generation, and upgrades operate locally. External video/channel links need internet access.

## Source layout and responsibilities

```text
videx/
├── scripts/
│   ├── yt-bookmark.sh            # Capture bookmarks and optionally rebuild the index
│   ├── yt-bookmark-find.py       # Video-ID lookup and read-only library audit
│   ├── yt-bookmark-tag.py        # Personal catalog management and tag reports
│   ├── yt-bookmark-generate.py   # Render standalone HTML
│   ├── yt-bookmark-metadata.py   # Curate stable metadata (schema v2)
│   ├── yt-bookmark-index.py      # Generate searchable offline index
│   ├── yt-bookmark-upgrade.py    # Offline migration and backups
│   ├── videx_config.py          # Configured/default library resolution
│   ├── videx_identity.py        # Shared identity and metadata JSON validation
│   └── videx_inventory.py       # Shared bookmark enumeration and checked reading
├── tests/                       # Offline regressions with synthetic fixtures
├── config.example.json          # Sample library path (tracked)
├── config.local.json            # Personal default library path (Git-ignored)
└── .gitignore
```

- `yt-bookmark.sh` coordinates extraction, duplicate checks, refreshes, tagging, and optional indexing.
- `yt-bookmark-find.py` provides offline URL/ID lookup, identity auditing, and opt-in personal-tag reports.
- `yt-bookmark-tag.py` validates and manages catalog data and provides catalog-only lookup and inventory reports.
- `yt-bookmark-generate.py` renders standalone HTML from explicit metadata, thumbnail, and output paths; `--back-link` accepts a relative index path.
- `yt-bookmark-metadata.py` curates schema 2 metadata; it is an imported helper rather than a CLI.
- `yt-bookmark-index.py` builds offline search, channel filtering, sorting, tag badges, and hidden exclusion.
- `yt-bookmark-upgrade.py` regenerates saved pages offline using their embedded metadata and thumbnail.
- `videx_config.py` resolves the configured/default library; its direct script entry point prints that path.
- `videx_identity.py` centralizes ID validation, metadata JSON parsing, and identity resolution.
- `videx_inventory.py` enumerates bookmark candidates and checks file types before reading.

Normal user workflows use capture, find, tag, index, and upgrade. See [data formats](DATA_FORMATS.md) and [safety](SAFETY.md) for their shared contracts and command-specific differences.

## Testing and recorded runtime validation

**Runtime validation for v0.3.0:** the offline suite and synthetic capture workflows were tested on macOS (Apple Silicon) with CPython **3.15.2** and Apple **Bash 3.2.57**, including captures without tags. Other Python/Bash versions and Linux have not been exercised in this release-validation pass; Linux remains an intended platform, not a separately verified result. No minimum Python version beyond Python 3 has been qualified by a version matrix. Browser rendering and live yt-dlp extraction are separate checks; the offline capture tests use local extractor stubs.

From the repository root, run the complete offline regression suite with:

```bash
python3 -B -m unittest discover -s tests -v
```

Tests use disposable synthetic libraries, never your configured library. The `-B` option suppresses bytecode output. No network, yt-dlp installation, browser, or additional test framework is required.

These are recorded release-validation results, not new validation performed for this documentation change. No additional minimum Python version or broader platform matrix is claimed.

## Development guidance

Follow [AGENTS.md](../AGENTS.md). Preserve offline functionality, macOS/Linux portability, existing command interfaces, and bookmark formats. Keep application source separate from user libraries and prefer focused changes without unnecessary dependencies or external services.

Use disposable synthetic fixtures for regressions; never run development checks against personal or production bookmark collections without explicit authorization. Preserve preview defaults, explicit `--apply` where supported, backups, and persistent metadata, tags, and catalog entries. Update documentation when installation, configuration, or user-facing behavior changes.

## Project history and current implementation

**Origin:** A simple local alternative to YouTube's bookmark/playlist front end. The first iteration generated a self-contained HTML page for each saved video.

**Current state (v0.3.0):** A small suite of scripts with channel-organized bookmarks, curated metadata, a separate personal-tag catalog, capture tagging, opt-in text/JSON tag reports, and catalog-aware offline index cards. Identity checks, duplicate prevention, in-place refreshes, offline upgrades, previews and backups preserve existing formats. The original `yt-bookmark-*` script names are retained intentionally.

Current index behavior includes personal-tag searching and `hidden` exclusion. The historical notes below describe the release snapshot; they do not override the [current features](../README.md#current-features) or [current index workflow](USAGE.md#rebuild-and-browse-the-offline-index). Original script names remain intentional.

## Historical release notes

### v0.3.0 release notes

These historical notes describe v0.3.0 at release; see the current features above for subsequent index search and visibility behavior.

- Added personal catalog management and repeatable capture tags, with preview/apply behavior for existing bookmarks.
- Added opt-in lookup/audit tag reports and JSON output without changing unflagged lookup output.
- Added normalized personal-tag badges to the self-contained offline index. Tags are display-only; `hidden` is not excluded.
- Hardened bookmark identity validation, repeated JSON `id` detection, inventory traversal, pruning, and atomic index publication.
- Capture now rejects invalid requested tags or applicable catalog state before bookmark writes, including refreshes. Untagged capture is covered on Apple Bash 3.2.57.

Compatibility: catalog schema **1**, bookmark metadata schema **2**, and JSON report version **1** are unchanged. No automatic library migration or recapture is required. Legacy filename-only bookmarks remain usable for lookup/tagging; indexing/upgrading still require embedded metadata. Rebuild the index explicitly to display catalog tags.

## Future roadmap

**Future work (not included in v0.3.0):**

1. Import all accessible entries from a YouTube playlist as individual bookmarks, using the existing video-ID checks to skip duplicates and reporting failures.
2. Collections for curated browsing, with bookmarks belonging to multiple collections.
3. Dedicated personal-tag filtering and visual collection browsing in the index.

Longer-term possibilities include better batch management and a unified `videx` command, if the existing tools become cumbersome. These are ideas, not commitments.
