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
│   ├── yt-bookmark-import.py     # Preview/apply TXT batches using single-video capture
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
- `yt-bookmark-import.py` separates TXT discovery from ordered ID orchestration, with read-only planning, conservative failure reports, and once-per-batch indexing. Its private capture contract uses `--batch-result` for structured outcomes, isolated yt-dlp options, expected-ID checks, and generator `--create-only` publication. These internal flags are not part of the ordinary capture workflow. Future playlist discovery can supply the same normalized source records; no Collection schema is introduced.
- `yt-bookmark-find.py` provides offline URL/ID lookup, identity auditing, and opt-in personal-tag reports.
- `yt-bookmark-tag.py` validates and manages catalog data and provides catalog-only lookup and inventory reports.
- `yt-bookmark-generate.py` renders standalone HTML from explicit metadata, thumbnail, and output paths; `--back-link` accepts a relative index path.
- `yt-bookmark-metadata.py` curates schema 2 metadata; it is an imported helper rather than a CLI.
- `yt-bookmark-index.py` builds offline search, a unified Channel/Personal Tag Filters panel, sorting, tag badges, and hidden exclusion.
- `yt-bookmark-upgrade.py` regenerates saved pages offline using their embedded metadata and thumbnail.
- `videx_config.py` resolves the configured/default library; its direct script entry point prints that path.
- `videx_identity.py` centralizes ID validation, metadata JSON parsing, and identity resolution.
- `videx_inventory.py` enumerates bookmark candidates and checks file types before reading.

Normal user workflows use capture, find, tag, index, and upgrade. See [data formats](DATA_FORMATS.md) and [safety](SAFETY.md) for their shared contracts and command-specific differences.

## Testing and recorded runtime validation

**Runtime validation for v0.4.0:** on macOS (Apple Silicon), the full offline suite ran with CPython **3.15.2** and **3.14.2**: **169 tests per runtime, 168 passed and 1 skipped**, with no failures. The skipped Unix-socket test could not bind a socket in the sandbox; actual FIFO/symlink and mocked special-file checks ran. The isolated F4 staged snapshot also passed all 153 of its tests outside the sandbox, including the real Unix-socket test. Apple **Bash 3.2.57** syntax and capture workflows (using local extractor stubs) were checked.

A short Firefox **160.0** headless smoke test used an offline temporary profile and synthetic files. It checked standalone bookmark display and embedded thumbnails, index rendering, personal-tag searching, `hidden` exclusion, and all-hidden empty-index rendering. This is a limited smoke test, not a browser compatibility matrix. Linux and live yt-dlp extraction have not been validated for this release. No minimum Python version beyond Python 3 has been qualified by a broader version matrix.

From the repository root, run the complete offline regression suite with:

```bash
python3 -B -m unittest discover -s tests -v
python3.14 -B -m unittest discover -s tests -v
bash -n scripts/yt-bookmark.sh
```

Tests use disposable synthetic libraries, never your configured library. The `-B` option suppresses bytecode output. No network, yt-dlp installation, browser, or additional test framework is required. Optional Deno or Node.js enables real JavaScript `JSON.parse()` checks and the index interaction harness; these tests report skips if neither runtime is available. Deno was available and these checks ran for v0.4.0; Node.js was not available. The JavaScript harness does not replace browser rendering checks.

These are recorded local release-preparation results. Sandbox restrictions may affect filesystem tests; report skips separately from passes. No broader platform matrix is claimed.

TXT importer regressions live in `tests/test_bookmark_import.py` and run with
`python3 -B -m unittest discover -s tests -p test_bookmark_import.py -v`.
They use synthetic libraries and an executable yt-dlp stub, including a real
interruption of a waiting synthetic capture; no configured library or network is used.
See [batch import](BATCH_IMPORT.md) for the durable input and recovery contract.

## Development guidance

Follow [AGENTS.md](../AGENTS.md). Preserve offline functionality, macOS/Linux portability, existing command interfaces, and bookmark formats. Keep application source separate from user libraries and prefer focused changes without unnecessary dependencies or external services.

Use disposable synthetic fixtures for regressions; never run development checks against personal or production bookmark collections without explicit authorization. Preserve preview defaults, explicit `--apply` where supported, backups, and persistent metadata, tags, and catalog entries. Update documentation when installation, configuration, or user-facing behavior changes.

## Optional social-preview artwork

The [social-preview workflow](social-preview/README.md) maintains the repository
sharing image independently of Videx runtime operations. Run
`python3 -B docs/social-preview/generate-social-preview.py` on macOS for SVG and
validated PNG output, or add `--svg-only` on macOS/Linux. Python is the authoritative
artwork source; native PNG rendering needs Apple's Swift development tools and
`sips`. No additional runtime dependencies or automatic capture/index hooks are
introduced. Rendering differences require visual review before accepting artwork.

## Project history and current implementation

**Origin:** A simple local alternative to YouTube's bookmark/playlist front end. The first iteration generated a self-contained HTML page for each saved video.

**Current state (v0.4.0):** A small suite of scripts with channel-organized bookmarks, curated metadata, a separate personal-tag catalog, capture tagging, opt-in text/JSON tag reports, and catalog-aware offline index cards. Identity checks, duplicate prevention, in-place refreshes, offline upgrades, previews and backups preserve existing formats. The original `yt-bookmark-*` script names are retained intentionally.

Current index behavior includes personal-tag searching, exact personal-tag filtering in a unified Filters panel, and `hidden` exclusion. The historical notes below describe the release snapshot; they do not override the [current features](../README.md#current-features) or [current index workflow](USAGE.md#rebuild-and-browse-the-offline-index). Original script names remain intentional.

## Historical release notes

### v0.4.0 release notes

- Added personal-tag searching and `hidden` exclusion to the offline index; modified catalog tag arrays are stored alphabetically.
- Fixed capture argument boundaries so option-like tags remain data and valid leading-hyphen video IDs work across capture and lookup.
- Unified bookmark filesystem inspection: symlinks and special bookmark files are rejected without content reads, and filesystem failures preserve existing index output and prevent upgrade publication.
- Made tag `list` catalog-only and made partial tag reports distinguish unverified catalog records from confirmed orphans, with explicit completeness and duplicate-ID statuses.
- Hardened catalog loading against symlink/special-file paths, non-string tag elements, and recursively duplicated JSON object members, preserving invalid data without rewriting it.
- Added recursive archival-number validation, shared duration/chapter display rules, and strict browser-compatible JSON serialization while preserving identity-oriented lookup behavior.
- Reorganized documentation into usage, development, data-format, and safety references, with focused offline regressions for these changes.

Compatibility: catalog schema **1**, bookmark metadata schema **2**, and JSON report version **1** remain unchanged. No automatic migration or recapture is required. Rebuild the index to apply current search and visibility behavior. Validation limits are recorded above; existing partial capture and mixed-result upgrade semantics remain documented in [Safety](SAFETY.md).

### v0.3.0 release notes

These historical notes describe v0.3.0 at release; see the current features above for subsequent index search and visibility behavior.

- Added personal catalog management and repeatable capture tags, with preview/apply behavior for existing bookmarks.
- Added opt-in lookup/audit tag reports and JSON output without changing unflagged lookup output.
- Added normalized personal-tag badges to the self-contained offline index. Tags are display-only; `hidden` is not excluded.
- Hardened bookmark identity validation, repeated JSON `id` detection, inventory traversal, pruning, and atomic index publication.
- Capture now rejects invalid requested tags or applicable catalog state before bookmark writes, including refreshes. Untagged capture is covered on Apple Bash 3.2.57.

Compatibility: catalog schema **1**, bookmark metadata schema **2**, and JSON report version **1** are unchanged. No automatic library migration or recapture is required. Legacy filename-only bookmarks remain usable for lookup/tagging; indexing/upgrading still require embedded metadata. Rebuild the index explicitly to display catalog tags.

## Future roadmap

**Future work (not included in v0.4.0):**

1. Import all accessible entries from a YouTube playlist as individual bookmarks, using the existing video-ID checks to skip duplicates and reporting failures.
2. Collections for curated browsing, with bookmarks belonging to multiple collections.
3. Visual collection browsing in the index. Dedicated personal-tag filtering is now implemented in the unified Filters panel.

Longer-term possibilities include better batch management and a unified `videx` command, if the existing tools become cumbersome. These are ideas, not commitments.
