# Videx development

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md) · [Batch import](BATCH_IMPORT.md)

## Current status and tracking

This document is the authoritative development status, roadmap, and outstanding-work register. Keep it current when capabilities or decisions change; GitHub Issues may supplement it, but are not required to understand or continue development. Usage contracts belong in [Usage](USAGE.md), [Batch import](BATCH_IMPORT.md), [Safety](SAFETY.md), and [Data formats](DATA_FORMATS.md), rather than separate overlapping roadmaps.

The latest recorded release snapshot is **v0.4.0**. The current source also implements exact personal-tag filtering, TXT batch importing, and direct public YouTube playlist importing. These additions must not be mistaken for future features or evidence of a newer published release. **Collections are planned, not implemented.** There is no Collection CLI or index presentation; the catalog's `collections` object is reserved structure only.

| Completed milestone | Current capability and evidence |
|---|---|
| Standalone capture and library foundations | Offline HTML, curated metadata/thumbnails, external-library configuration, identity-based duplicate prevention, in-place refresh, and offline upgrades; `tests/test_identity_consumers.py` and generator/identity tests. |
| Personal catalog workflows (v0.3.0) | Capture tagging, preview/apply catalog edits, opt-in tag reports, index badges, guarded pruning, script-context escaping, and atomic index publication; tag/find/index regressions. |
| Safety and visibility (v0.4.0) | Catalog path/type and tag-element validation, recursive duplicate-key refusal, bookmark filesystem safeguards, numeric validation, tag searching, and complete `hidden` omission; tag/index/inventory/identity-consumer regressions. |
| Exact tag filtering (after release snapshot) | Unified Channel/Personal Tag Filters panel, chips and reset controls; one exact tag selection combines with channel and free-text search using AND. Unicode tag identity uses Python case-folding; `tests/test_bookmark_index.py`. |
| TXT batch import (after release snapshot) | Offline preview, sequential new captures, existing-ID reuse, per-entry reports, retry by rerunning, and one default final index rebuild after creation; `tests/test_bookmark_import.py`. |
| Direct public playlist import (after release snapshot) | Full flat yt-dlp discovery before capture, conservative completeness refusal, ordered deduplication, and the shared batch pipeline; `tests/test_playlist_import.py`. No Collection assignment or playlist synchronization. |

These milestones describe implementation and offline regression coverage, not live YouTube or cross-platform certification. See [recorded validation](#testing-and-recorded-runtime-validation) and [known limitations](#known-issues-and-limitations).

## Architecture and data flow

Videx is a small suite of Bash and Python scripts. Capture uses separately installed yt-dlp to obtain metadata and a thumbnail in a temporary directory, resolves the video's identity, and renders a standalone bookmark. The generator curates metadata and embeds the thumbnail and JSON in HTML. Requested personal tags are maintained separately in the library catalog. Optional indexing reads saved bookmarks and catalog tags to produce a self-contained offline snapshot.

The index is generated output; bookmark HTML and personal catalog data are persistent library data. Rebuilding the index does not refresh source metadata. Counts represent capture-time snapshots. Lookup, audit, tagging, index generation, and upgrades operate locally. External video/channel links need internet access.

## Source layout and responsibilities

```text
videx/
├── scripts/
│   ├── yt-bookmark.sh            # Capture bookmarks and optionally rebuild the index
│   ├── yt-bookmark-import.py     # TXT/public-playlist adapters using single-video capture
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
- `yt-bookmark-import.py` supplies TXT and public-playlist adapters to shared deduplication and ordered ID orchestration, with read-only planning, conservative failure reports, and once-per-batch indexing. Playlist discovery validates a complete flat yt-dlp JSON response before any capture; source metadata remains report-only. Its private capture contract uses `--batch-result` for structured outcomes, isolated yt-dlp options, expected-ID checks, and generator `--create-only` publication. These internal flags are not part of the ordinary capture workflow. No Collection schema is introduced.
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

**Runtime validation for v0.4.0:** on macOS (Apple Silicon), the full offline suite ran with CPython **3.15.2** and **3.14.2**: **169 tests per runtime, 168 passed and 1 skipped**, with no failures. The skipped Unix-socket test could not bind a socket in the sandbox; actual FIFO/symlink and mocked special-file checks ran. A separate pre-release snapshot passed all 153 of its tests outside the sandbox, including the real Unix-socket test. Apple **Bash 3.2.57** syntax and capture workflows (using local extractor stubs) were checked.

A short Firefox **160.0** headless smoke test used an offline temporary profile and synthetic files. It checked standalone bookmark display and embedded thumbnails, index rendering, personal-tag searching, `hidden` exclusion, and all-hidden empty-index rendering. This is a limited smoke test, not a browser compatibility matrix. Linux and live yt-dlp extraction have not been validated for this release. No minimum Python version beyond Python 3 has been qualified by a broader version matrix.

From the repository root, run the complete offline regression suite with:

```bash
python3 -B -m unittest discover -s tests -v
# Optional comparison runtime, if installed locally
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

Playlist adapter regressions in `tests/test_playlist_import.py` share the synthetic
extractor fixture and run alongside TXT tests with
`python3 -B -m unittest discover -s tests -p 'test_*import.py' -v`.
Fixtures cover full multi-page-sized results, partial-discovery refusal, inaccessible
entries, order/deduplication, shared capture/retry/index behavior, and discovery
interruption. They make no live YouTube requests. Local installed yt-dlp help/source
verification does not constitute live playlist validation.

### Documentation audit validation (2026-11-20)

On macOS Apple Silicon, CPython **3.15.2** ran the current full offline suite: **274 tests, 273 passed, 1 skipped, no failures**. The Unix-socket fixture could not bind in the sandbox (`Operation not permitted`); the other filesystem checks ran. Apple Bash **3.2.57** passed `bash -n scripts/yt-bookmark.sh`. This run includes TXT/playlist stubs, generated JavaScript checks, and synthetic artwork tests; it did not launch a browser, capture screenshots, or contact YouTube.

All user-facing script and documentation-tool `--help` commands returned `0`. An additional disposable synthetic library exercised offline generation/indexing, audit JSON, tag preview/report, upgrade preview, and TXT preview; the preview/query operations preserved library files. Permanent Markdown links/anchors and repository script/test references were checked locally. Dependency installation, live capture/playlist discovery, native artwork rendering, and Linux qualification were not performed in this audit. The optional `python3.14` comparison command remains a recorded workflow, not a claim of a second runtime tested in this run.

## Development guidance

Follow [AGENTS.md](../AGENTS.md). Preserve offline functionality, macOS/Linux portability, existing command interfaces, and bookmark formats. Keep application source separate from user libraries and prefer focused changes without unnecessary dependencies or external services.

Use disposable synthetic fixtures for regressions; never run development checks against personal or production bookmark collections without explicit authorization. Preserve preview defaults, explicit `--apply` where supported, backups, and persistent metadata, tags, and catalog entries. Update documentation when installation, configuration, or user-facing behavior changes.

## Documentation artwork

The [screenshot workflow](screenshots/README.md) describes manual capture from a disposable synthetic library and offline preparation of review candidates. Screenshots are approved illustrations, not executable UI regression baselines.

### Optional social-preview artwork

The [social-preview workflow](social-preview/README.md) maintains the repository
sharing image independently of Videx runtime operations. Run
`python3 -B docs/social-preview/generate-social-preview.py` on macOS for SVG and
validated PNG output, or add `--svg-only` on macOS/Linux. Python is the authoritative
artwork source; native PNG rendering needs Apple's Swift development tools and
`sips`. No additional runtime dependencies or automatic capture/index hooks are
introduced. Rendering differences require visual review before accepting artwork.

## Project history and current implementation

**Origin:** A simple local alternative to YouTube's bookmark/playlist front end. The first iteration generated a self-contained HTML page for each saved video.

**v0.4.0 milestone:** A small suite of scripts with channel-organized bookmarks, curated metadata, a separate personal-tag catalog, capture tagging, opt-in text/JSON tag reports, and catalog-aware offline index cards. Identity checks, duplicate prevention, in-place refreshes, offline upgrades, previews and backups preserve existing formats. The original `yt-bookmark-*` script names are retained intentionally.

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

### Immediate development objectives

The following objectives have no assigned release or delivery date. Proposed priorities remain subject to project planning.

1. **Qualify Linux and supported runtimes.** Run the offline suite and representative synthetic capture, lookup, tag, and index workflows on a real Linux environment. Record distribution, Python/Bash versions, optional dependencies, failures and skips. The historical macOS results do not establish a minimum supported Python version. Track reproducible portability defects separately; a broad CI matrix or new dependencies are not assumed.
2. **Decide the upgrade partial-result contract.** Current mixed valid/skipped runs can return `0`; no candidates with skipped pages returns `1`. [Safety](SAFETY.md#offline-upgrades) records the exact behavior. Decide whether to retain it for compatibility or change it before calling it a defect. Any later change needs caller/compatibility review, all-valid, mixed, all-skipped and operational-error tests in preview and apply modes, and migration guidance. Preserve backups and preview defaults.
3. **Specify Collections before implementation.** Preserve the established requirements below, resolve the remaining data/CLI semantics, then implement safe catalog operations before index browsing. This is planned work, without an assigned release or implementation schedule.

### Planned Collections

Established requirements:

- Collections are independent curated groups, separate from personal tags, channel directories, source playlists, and standalone bookmark metadata.
- Membership is by video identity, so renamed files still refer to the same video.
- A video can belong to multiple Collections.
- Users can manage Collections and membership manually, without requiring a playlist import.
- Eventual playlist-to-Collection workflows should build on the existing importer. Importing a playlist today only saves individual bookmarks; it creates no Collection and saves playlist order/metadata in reports only.

Before implementation, decide Collection naming/identity, ordering, and rename/delete rules. The requirement to use video identity and allow multiple memberships is already established; those points need no new decision. No concrete schema change is approved merely because schema 1 reserves `collections`.

The staged approach is catalog/CLI inspection and manual management first, then offline index browsing after the data operations are tested and accepted. Preserve unrelated tags and catalog fields, external-library selection, preview/`--apply`, backups, atomic writes, and malformed/incomplete-inventory safeguards. Collection operations should not rewrite bookmark HTML. Graphical editing and automatic playlist synchronization are not implied requirements.

### Deferred enhancements and investigation

- **Catalog concurrency investigation (low priority proposal):** identify concrete competing read/modify/write sequences and cloud-sync interactions using disposable catalogs. Atomic replacement prevents partial publication but does not merge competing edits. No lost-update incident or exploit has been demonstrated. Assess severity and recommend minimal locking, stale-state detection, documentation, or justified deferral; propose any code separately. Do not assume distributed locks, automatic sync-conflict resolution, or broad NAS guarantees. This investigation does not block all other work.
- **Graphical tagging and a unified `videx` CLI:** deferred ideas, not approved commitments. Retaining the existing script names is intentional.
- **Further source adapters or batch management improvements:** possible future work, with no additional requirements or schedule approved.

Filename-only legacy indexing, major packaging refactors, broad filesystem/NAS certification, and work on a separate offline playlist HTML extraction utility are outside the current roadmap. Live extraction remains a validation gap, not an approved new feature or a prerequisite for offline operation.

## Known issues and limitations

| Finding | Status and practical consequence |
|---|---|
| Linux/runtime coverage | Open validation work, not a confirmed portability bug; exact environments tested are recorded above. Browser checks are limited smoke tests. |
| Upgrade partial-result statuses | Open contract decision; a successful status can coexist with skipped malformed pages. Inspect warnings before applying. No behavior change is approved. |
| Catalog concurrency | Source-confirmed lack of a lock/stale-state check across read/modify/write; potential lost updates are not runtime-reproduced incidents. Coordinate writers. Ordinary catalog symlink/type validation is already fixed and is distinct from races after checks. |
| Import availability/completeness | Offline stubs validate orchestration, not current YouTube extraction. Playlist discovery refuses even benign stderr warnings and cannot independently prove YouTube exposed every entry when counts are absent. Private/account lists, authentication, mixes, synchronization and playlist deletion are unsupported. See [batch limits](BATCH_IMPORT.md#public-playlist-discovery-and-completeness). |
| Import filesystem and recovery | Create-only publication requires hard links. Completed bookmarks survive later failures; a hard kill can leave staging files or a stale index. Rerun to skip saved IDs and retry; no checkpoint database or automatic rollback. |
| Independent publication stages | Capture, tagging and indexing are not a transaction. Upgrade replacement is per file; catalog/index atomic publication is not library-wide locking or power-loss durability. See [Safety](SAFETY.md). |
| Visibility and archival scope | `hidden` omits entries only from a rebuilt index; files/catalog/reports and older snapshots remain accessible. No video playback or transcript preservation; metadata/counts reflect capture time. |
| Legacy bookmarks | Filename-only identity is supported for lookup/tagging when metadata is absent, not indexing/upgrading. No automatic metadata recovery is planned. |

## Reconciliation of earlier backlog proposals

The nine-item backlog prepared against v0.3.0 has been checked against current source and tests. The dispositions below supersede its earlier status and priority proposals.

| Earlier proposal | Reconciled disposition |
|---|---|
| Reject symlinked/special catalog paths | Completed: tag `load()` uses `lstat()` and rejects non-regular paths, including dangling symlinks. `tests/test_bookmark_tag.py` covers read/preview/apply refusals without target, catalog or backup changes. |
| Reject non-string catalog tag elements | Completed: every record's tag elements are checked, including unrelated/orphan records, with controlled status `2`. Tag regressions cover list/report/mutations and preservation of invalid inputs. |
| Qualify Linux/runtimes | Still open in immediate objectives; older v0.3.0 validation totals are historical, superseded by later recorded runs rather than current certification. |
| Define upgrade skipped-file statuses | Current behavior is documented; a decision on changing the contract remains open. |
| Exclude `hidden` from index | Completed: all bookmark-specific index data, choices, grouping and counts exclude hidden IDs; all-hidden valid libraries publish an empty index. Files/catalog/CLI reports remain unchanged. Index regressions cover these distinctions. |
| Add personal-tag filtering | Completed: free-text search includes tags; exact single-tag filtering combines with channel/search using AND. Earlier open search-scope questions are resolved by current implementation. |
| Add Collections in stages | Still planned; established requirements and undecided details are recorded above. Reserved catalog structure is not implementation. |
| Add playlist ingestion | Completed via the public-playlist importer. Continuation, overall statuses and retry/interruption behavior are documented in [Batch import](BATCH_IMPORT.md#reports-and-recovery); single capture still uses `--no-playlist`. Playlist-to-Collection work remains planned. |
| Investigate catalog concurrency | Retained as a deferred investigation, with no new locking or transaction requirement approved. |

Earlier completed work (external configuration, personal tagging, reports, badges, identity validation, guarded pruning, escaping, capture preflight and atomic index publication) remains covered by the milestone table and historical release notes. GitHub filing procedures and unverified issue metadata are not development requirements. No issue inventory or external tracker is needed to interpret this register.
