# Videx development

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md) · [Batch import](BATCH_IMPORT.md)

## Current status and tracking

This document is the authoritative development status, roadmap, and outstanding-work register. Keep it current when capabilities or decisions change; GitHub Issues may supplement it, but are not required to understand or continue development. Usage contracts belong in [Usage](USAGE.md), [Batch import](BATCH_IMPORT.md), [Safety](SAFETY.md), and [Data formats](DATA_FORMATS.md), rather than separate overlapping roadmaps.

The latest recorded release snapshot is **v0.4.0**. The current source also implements exact personal-tag filtering, TXT and public YouTube playlist importing, the unified `videx` command with local index opening, manual Collection management, and offline Collection browsing. Optional playlist-to-Collection creation is planned.

| Completed milestone | Current capability and evidence |
|---|---|
| Standalone capture and library foundations | Offline HTML, curated metadata/thumbnails, external-library configuration, identity-based duplicate prevention, in-place refresh, and offline upgrades; `tests/test_identity_consumers.py` and generator/identity tests. |
| Personal catalog workflows (v0.3.0) | Capture tagging, preview/apply catalog edits, opt-in tag reports, index badges, guarded pruning, script-context escaping, and atomic index publication; tag/find/index regressions. |
| Safety and visibility (v0.4.0) | Catalog path/type and tag-element validation, recursive duplicate-key refusal, bookmark filesystem safeguards, numeric validation, tag searching, and complete `hidden` omission; tag/index/inventory/identity-consumer regressions. |
| Exact tag filtering (after release snapshot) | Unified Channel/Personal Tag Filters panel, chips and reset controls; one exact tag selection combines with channel and free-text search using AND. Unicode tag identity uses Python case-folding; `tests/test_bookmark_index.py`. |
| TXT batch import (after release snapshot) | Offline preview, sequential new captures, existing-ID reuse, per-entry reports, retry by rerunning, and one default final index rebuild after creation; `tests/test_bookmark_import.py`. |
| Direct public playlist import (after release snapshot) | Full flat yt-dlp discovery before capture, conservative completeness refusal, ordered deduplication, and the shared batch pipeline; `tests/test_playlist_import.py`. No Collection assignment or playlist synchronization. |
| Unified command and offline index opening (after release snapshot) | `./videx open` opens the saved index through the desktop's HTML association. Six existing workflows are available through direct delegation. Coverage in `tests/test_bookmark_open.py`, `tests/test_videx_cli.py`, and the importer interruption tests. |
| Collections Milestone 1 (after release snapshot) | `./videx collection` manages local Collection records with preview/apply and shared catalog backups. Coverage in `tests/test_bookmark_collection.py` and the tag, capture, and dispatcher regressions. |
| Collections Milestone 2 (after release snapshot) | The generated index browses Collections with the shared grid, search, filters, and sort controls. Snapshot projection and generated JavaScript are covered in `tests/test_bookmark_index.py`. |

These milestones describe implementation and offline regression coverage, not live YouTube or cross-platform certification. See [recorded validation](#testing-and-recorded-runtime-validation) and [known limitations](#known-issues-and-limitations).

## Architecture and data flow

Videx is a small suite of Bash and Python scripts. Capture uses separately installed yt-dlp to obtain metadata and a thumbnail in a temporary directory, resolves the video's identity, and renders a standalone bookmark. The generator curates metadata and embeds the thumbnail and JSON in HTML. Requested personal tags are maintained separately in the library catalog. Optional indexing reads saved bookmarks and catalog tags to produce a self-contained offline snapshot.

The root `videx` executable gives users one help screen and short entry points. Its Bash 3.2-compatible dispatcher uses `exec` with the existing Bash and Python interpreters. The delegated scripts retain their own parsing, working directory, output, exit statuses, and safety rules. The Python opener reuses the shared library resolver and handles only the existing index and desktop launch. This decision supersedes the earlier unified CLI deferral. Existing script paths remain supported, and deeper implementation consolidation is deferred.

The index is generated output; bookmark HTML and personal catalog data are persistent library data. Rebuilding the index does not refresh source metadata. Counts represent capture-time snapshots. Lookup, audit, tagging, index generation, and upgrades operate locally. External video/channel links need internet access.

## Source layout and responsibilities

```text
videx/
├── videx                       # Public command dispatcher
├── scripts/
│   ├── yt-bookmark-open.py       # Open the existing index through the desktop
│   ├── yt-bookmark.sh            # Capture bookmarks and optionally rebuild the index
│   ├── yt-bookmark-import.py     # TXT/public-playlist adapters using single-video capture
│   ├── yt-bookmark-find.py       # Video-ID lookup and read-only library audit
│   ├── yt-bookmark-tag.py        # Personal catalog management and tag reports
│   ├── yt-bookmark-collection.py # Inspect and manage local Collections
│   ├── yt-bookmark-generate.py   # Render standalone HTML
│   ├── yt-bookmark-metadata.py   # Curate stable metadata (schema v2)
│   ├── yt-bookmark-index.py      # Generate searchable offline index
│   ├── yt-bookmark-upgrade.py    # Offline migration and backups
│   ├── videx_catalog.py         # Shared catalog loading, validation, and publication
│   ├── videx_config.py          # Configured/default library resolution
│   ├── videx_identity.py        # Shared identity and metadata JSON validation
│   └── videx_inventory.py       # Shared bookmark enumeration and checked reading
├── tests/                       # Offline regressions with synthetic fixtures
├── config.example.json          # Sample library path (tracked)
├── config.local.json            # Personal default library path (Git-ignored)
└── .gitignore
```

- `yt-bookmark.sh` coordinates extraction, duplicate checks, refreshes, tagging, and optional indexing.
- `yt-bookmark-import.py` supplies TXT and public-playlist adapters to shared deduplication and ordered ID orchestration, with read-only planning, conservative failure reports, and once-per-batch indexing. Playlist discovery validates a complete flat yt-dlp JSON response before any capture; source metadata remains report-only. Its private capture contract uses `--batch-result` for structured outcomes, isolated yt-dlp options, expected-ID checks, and generator `--create-only` publication. These internal flags are not part of the ordinary capture workflow.
- `yt-bookmark-find.py` provides offline URL/ID lookup, identity auditing, and opt-in personal-tag reports.
- `yt-bookmark-tag.py` manages personal tags and provides catalog-only lookup and inventory reports.
- `yt-bookmark-collection.py` manages local groups and reuses the strict catalog inventory checks.
- `yt-bookmark-generate.py` renders standalone HTML from explicit metadata, thumbnail, and output paths; `--back-link` accepts a relative index path.
- `yt-bookmark-metadata.py` curates schema 2 metadata; it is an imported helper rather than a CLI.
- `yt-bookmark-index.py` builds offline search, a Filters panel for Collection, Channel, and Personal Tag selection, sorting, tag badges, and hidden exclusion.
- `yt-bookmark-upgrade.py` regenerates saved pages offline using their embedded metadata and thumbnail.
- `videx_catalog.py` loads and atomically publishes schema-1 catalogs. Collection record validation is opt-in.
- `videx_config.py` resolves the configured/default library; its direct script entry point prints that path.
- `videx_identity.py` centralizes ID validation, metadata JSON parsing, and identity resolution.
- `videx_inventory.py` enumerates bookmark candidates and checks file types before reading.

See [usage](USAGE.md), [data formats](DATA_FORMATS.md), and [safety](SAFETY.md) for the workflows and their shared contracts.

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

### Unified CLI validation (2026-11-26)

On macOS 26.7.3 (Apple Silicon), CPython 3.15.2 ran the full offline suite with Deno 2.9.8 available for JavaScript checks. Of 286 tests, 285 passed and one was skipped because the sandbox refused to bind the existing Unix socket fixture. There were zero failures and zero errors. Apple Bash 3.2.57 passed syntax and dispatch checks.

The opener tests used temporary libraries and harmless launch stubs, including mocked macOS and Linux launcher selection. Dispatch tests checked argument and stream forwarding, representative offline workflows, and importer interruption with process-group cleanup. Real desktop launching and native Linux validation remain outstanding.

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
3. **Deliver Collections in stages.** Manual catalog operations and offline index browsing are implemented. Optional playlist-to-Collection creation remains planned. No release is assigned.

### Collections milestones

**Milestone 1 — Catalog and manual management (implemented).** Collections are local groups independent of personal tags, channel directories, source playlists, and bookmark HTML. Catalog schema 1 stores stable UUIDv4 records with normalized unique names, optional descriptions, and video-ID membership. A bookmark can belong to several Collections. See the CLI and storage contracts in [Usage](USAGE.md#collection-management), [Data formats](DATA_FORMATS.md#collection-records), and [Safety](SAFETY.md#collection-mutations-and-inspection).

**Ordering decision.** Custom ordering is deferred. The member array preserves first-occurrence append order and the relative order of surviving members. Future playlist proposals preserve source sequence internally. Array order is a storage property. The initial milestones include no manual reorder command, positioning, drag-and-drop, or Collection-specific sort mode.

**Milestone 2 — Offline index browsing (implemented).** The Collection selector is the first control in the Filters panel when the snapshot includes Collections. The panel starts closed and the browsing context starts at All Bookmarks. The existing card grid shows visible members. The selected Collection's name, optional description, and visible member count stay visible when the panel closes. Membership intersects with Search, Channel, and Personal Tag. Clear Filters preserves the selected Collection. Library-wide statistics, filter options, and Single Entries grouping continue to use the whole visible library. See [Browse Collections](USAGE.md#browse-collections).

The index reads the catalog once and projects membership onto visible, successfully indexed bookmarks. It omits missing, skipped, and ambiguous identities. It tracks duplicates before presentation validation, so a skipped copy still makes a resolved duplicate ID ambiguous. The payload contains only visible membership IDs and the browser derives totals from those IDs. Zero-member Collections remain selectable with a distinct empty state. Collection text uses escaped, strict JSON and plain-text rendering. Catalog validation runs before atomic publication, retaining the prior index on validation failure. The all-hidden and zero-valid-bookmark status rules still apply. Changes appear after a successful offline rebuild.

All Bookmarks and Collections share **Newest**, **Oldest**, **Title A-Z**, **Title Z-A**, and **Channel A-Z** and retain the active sort when browsing context changes. Newest remains the default. The shorter Newest and Oldest labels still sort by upload date. Internal sort identifiers, algorithms, and tie-breaking behavior are unchanged.

**Milestone 3 — Optional playlist-to-Collection creation (planned).** Use a completed import report to propose a Collection name, description, and confirmed local members in playlist sequence. Import preview may show candidates and distinguish existing bookmarks from those awaiting capture. Reuse existing bookmarks and successfully captured bookmarks. Users can accept, edit, or skip the proposal and apply it separately after the final outcomes are known. Support offline review and apply. Identify omitted or unavailable entries and require explicit acceptance of any reduced membership after a completed import with per-entry failures. If no member is eligible, skip the proposal and leave empty creation to the manual command. A refused or interrupted discovery supplies no eligible proposal. A capture interruption preserves completed bookmarks, and Collection apply requires a completed import report.

Require a different name or skip on conflicts, including repeated imports. Revalidate the proposed membership against the current inventory and publish a new Collection with all members in one catalog write. Keep source playlist state outside the Collection. Preserve ordinary TXT and playlist import and index rebuild behavior. Users can suppress the import rebuild with `--no-re-index` and rebuild after applying the Collection. Capture success survives later catalog failure. Proposal format and CLI syntax remain Milestone 3 decisions. GUI management and automatic playlist synchronization are outside these milestones.

### Deferred enhancements and investigation

- **Catalog concurrency investigation (low priority proposal):** identify concrete competing read/modify/write sequences and cloud-sync interactions using disposable catalogs. Atomic replacement prevents partial publication but does not merge competing edits. No lost-update incident or exploit has been demonstrated. Assess severity and recommend minimal locking, stale-state detection, documentation, or justified deferral; propose any code separately. Do not assume distributed locks, automatic sync-conflict resolution, or broad NAS guarantees. This investigation does not block all other work.
- **Graphical tagging and deeper CLI consolidation:** deferred. The thin `videx` dispatcher is implemented, and retaining the existing script names and command contracts is intentional.
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
| Add Collections in stages | Manual management and offline index browsing are implemented. Optional playlist-to-Collection creation is planned. |
| Add playlist ingestion | Completed via the public-playlist importer. Continuation, overall statuses and retry/interruption behavior are documented in [Batch import](BATCH_IMPORT.md#reports-and-recovery); single capture still uses `--no-playlist`. Playlist-to-Collection work remains planned. |
| Investigate catalog concurrency | Retained as a deferred investigation, with no new locking or transaction requirement approved. |

Earlier completed work (external configuration, personal tagging, reports, badges, identity validation, guarded pruning, escaping, capture preflight and atomic index publication) remains covered by the milestone table and historical release notes. GitHub filing procedures and unverified issue metadata are not development requirements. No issue inventory or external tracker is needed to interpret this register.
