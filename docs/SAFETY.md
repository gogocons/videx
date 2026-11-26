# Videx safety and failure handling

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md) · [Batch import](BATCH_IMPORT.md)

## Scope and library backups

Videx stores bookmarks locally as standalone HTML files in the selected library’s `bookmarks/` directory. The generated `index.html` provides an offline-searchable interface to your collection.

These files are excluded from Git tracking by default. Your bookmark collection is independent of the Videx source code, so be sure to back it up separately. Duplicate detection and auditing do not replace backups.

`--apply` is operation-specific: catalog edits and upgrades preview by default; capture writes new bookmarks and automatically applies their requested tags. `--overwrite` refreshes existing HTML independently of existing-bookmark tag edits. Index generation writes its output without an apply flag. See [usage](USAGE.md) before running commands.

## Bulk import safety

The [bulk importer](BATCH_IMPORT.md) previews by default without library writes.
TXT preview is offline; public playlist preview uses flat network discovery only,
without individual video extraction or thumbnails. Its `--apply` authorizes new captures only: existing IDs are
reused, with no overwrite, tagging, or Collection assignment. Unsafe/incomplete
inventories refuse or stop the batch; independent extraction failures can continue.
Importer extraction ignores ambient yt-dlp configuration. Requested and extracted
IDs must agree. Complete staged HTML is published with a create-only hard link,
which refuses an existing destination even if it appeared after validation.
Hard-link support is required; there is no overwrite fallback or library-wide lock.

Completed bookmarks survive later failures. Indexing runs once after creation,
including partial-success batches, unless suppressed; index failure preserves the
previous index. Handled interruption stops the capture process group and reports
partial progress. Hard termination may leave a stale index or staging files. Rerun
the TXT file or playlist URL to skip saved IDs and retry remaining entries, then rebuild offline
if needed. Exit statuses are `0` complete, `1` partial, `2` global refusal, and
`130` interrupted. Generic extractor errors are not labeled definitively unavailable.

Playlist discovery is a prerequisite for any capture: nonzero extraction status,
diagnostics, invalid JSON/structure, count or position inconsistencies, and explicit
incomplete/error markers refuse the whole discovery without library changes.
This deliberately includes benign discovery warnings; there is no partial-list
apply override. Playlist metadata stays in reports and is not saved to catalogs.
No authentication, account mutation, playlist synchronization/deletion, or Collection
creation is performed. See [discovery completeness limits](BATCH_IMPORT.md#public-playlist-discovery-and-completeness).

## Filesystem inspection

Bookmark inspection never reads discovered file symlinks, dangling symlinks, FIFOs, sockets, devices, or other non-regular bookmark objects, and never descends through discovered directory symlinks. Bookmark tree roots must be real directories. Real subdirectories named `index.html` are traversed; generated index files are excluded case-insensitively, while bookmark candidates retain case-sensitive `.html` matching. Unrelated ordinary non-HTML files are ignored.

The shared scanner records unsupported discovered symlinks as inventory issues without following them. Special objects with bookmark-candidate `.html` names are rejected without reading; unrelated non-HTML objects are not bookmark candidates. A discovered generated `index.html` entry is excluded case-insensitively unless it is a real directory, which is traversed.

Explicit library aliases remain supported when they select a library with a real `bookmarks/` directory. Index and upgrade accept legacy layouts without a `bookmarks` entry; an existing unsupported entry is an error, not a reason to fall back to scanning another directory. A directly selected bookmark collection root must be a real directory rather than a symlink.

These checks do not provide a snapshot against concurrent filesystem changes. File type checks and later reads/replacements can observe different filesystem states. Do not treat them as a universal concurrency guarantee.

## Inventory completeness and identity

Lookup refuses conflicting or incomplete inventories; duplicate matches for the requested ID are ambiguous. Catalog mutation previews and applied mutations additionally require no duplicate IDs anywhere in the inventory. Capture uses the strict catalog-mutation inventory checks before extraction and again before writing, rejecting duplicates anywhere in the collection. No command automatically repairs identities. See [identity rules](DATA_FORMATS.md#identity-resolution).

Tag `report` can show partial inspection results, while `list` reads only the catalog after validating the library directory and ID. Neither is equivalent to a successful mutation preflight. Confirmed orphan entries do not by themselves make a complete report fail; partial unmatched entries remain unverified.

## Capture and generator validation

Requested tags follow the [normalization and validation rules](DATA_FORMATS.md#personal-tag-normalization). Tagged capture validates tags and catalog state before extraction and rechecks the catalog before a bookmark write. Invalid inputs leave existing bookmarks/catalogs unchanged. Untagged capture does not read the catalog unless `--re-index` is requested.

Capture stops on unsafe lookup failure. It validates the extractor's resolved ID and rechecks the inventory before writing. Ordinary capture can follow redirects to a different resolved video ID; the importer additionally refuses a requested/extracted ID mismatch. The lower-level generator validates the rendered identity and requires an existing regular-file destination to resolve to the same ID; a refresh cannot silently replace a different identity. The shell capture wrapper maps lookup/inventory refusal to status `1` in those paths, rather than universally preserving the helper's status `2`.

The generator rejects existing symlink, directory, or special-file bookmark destinations with controlled status `2`, before reading them or staging output. New destinations and matching regular-file refreshes remain supported.

Malformed retained numeric metadata (non-finite values or browser-overflowing integers) makes generator document construction fail with controlled status `2` before publication. Capture propagates this failure without publishing a bookmark or applying its requested tags. Existing bookmarks, catalogs, indexes, and backups remain unchanged; initial library-directory creation and extractor temporary work retain their existing behavior. Finite values unsuitable only for duration/chapter display are retained, not silently repaired. See [numeric metadata policy](DATA_FORMATS.md#bookmark-metadata-schema-2).

Capture, tagging, and indexing are separate steps, not a transaction across all files. A later tagging or index failure does not imply that an earlier bookmark write was rolled back. Capture refresh does not create the timestamped backups supplied by offline upgrade; retain independent library backups.

## Catalog validation and publication

Every tag operation validates the catalog, including previews and no-op requests. Existing catalogs must be regular files; symlinks (including dangling links), directories, and special files are rejected without reading their contents. Tag arrays in all records, including unrelated records and orphans, must contain only strings. These validation failures produce an error and exit status `2`, leaving catalog paths, targets, and existing backups unchanged without creating backups or temporary publication files. Missing catalogs remain supported.

All mutation previews and applied mutations require a complete validated inventory without duplicate IDs. Pruning refuses incomplete inventories, including unreadable files/directories, malformed metadata, unidentified bookmarks, and symbolic links; generated index files are excluded. Refusal leaves catalog and backups untouched. Pruning removes only confirmed unmatched catalog records, never bookmark HTML.

For a changed catalog, the shared writer stages JSON in a unique sibling `.catalog-*.tmp` file, flushes and fsyncs it, stages a copy of an existing catalog, and atomically publishes the copy under a timestamped `catalog.json.bak-*` name before replacing the live catalog with `os.replace`. A failed backup copy leaves the live catalog in place and publishes no partial backup. A completed backup survives a subsequent replacement failure. Handled failures clean up staging files where possible. A hard interruption can leave staging files or a completed backup. A missing catalog has no previous catalog to back up. No-op requests do not reorder unrelated records or create a replacement. Reporting creates neither catalogs, backups, nor staging files. Coordinate catalog writers and library moves. Atomic replacement provides neither locking nor stale-state detection or merging, and it does not guarantee power-loss durability.

### Collection mutations and inspection

Every mutation previews by default and uses the same catalog and strict inventory preflight as apply, including metadata-only changes and no-ops. The apply invocation validates the complete candidate catalog and publishes it once, with explicit `--apply`. A batch of member changes is all-or-nothing. Preview, queries, and no-ops leave catalog, backups, bookmark HTML, and index untouched. A successful edit changes only the catalog and applicable backup. Deleting a Collection removes only its record. Removing a member leaves the bookmark and its tags intact. Tag pruning remains scoped to bookmark catalog records. A stale member can be removed explicitly or resolve again when its ID returns.

List reads only the catalog after checking the library layout. Show distinguishes confirmed absent IDs from unverified members on an incomplete scan. Ambiguous identities have multiple observed paths. Existing hidden bookmarks remain visible to CLI inspection. Read-only operations and valid previews, applies, and no-ops return `0`; confirmed stale members alone do not make inspection fail. A missing show target or usable partial/duplicate inventory report returns `1`. Syntax, library, catalog, mutation validation, and publication failures return `2`. A handled interruption returns `130`; if it occurs at replacement the report can indicate uncertainty, and the saved catalog can be reread to verify the outcome.

## Index publication and statuses

`./videx open` hands the existing local index to the desktop's default HTML application. Opening is read-only for Videx and leaves regeneration to `./videx index` or the existing capture and import workflows. The saved snapshot can still show videos tagged `hidden` since the last successful rebuild. See [opening and rebuilding](USAGE.md#rebuild-and-browse-the-offline-index).

Index generation writes a unique temporary file beside `index.html`, then atomically replaces the index. Existing regular-file permissions are preserved; new indexes and replaced symlinks use normal creation permissions respecting the process umask, without writing through symlinks. Concurrent builds publish complete files; the last successful replacement wins.

Rebuild the index after adding or removing `hidden` to apply visibility changes. When valid bookmarks exist but all are hidden, rebuilding succeeds and replaces any older index with an empty-state index. A genuinely empty library or one containing no valid bookmarks retains exit status `1` and leaves any existing index untouched. Malformed, unreadable, symlinked, or non-regular catalogs cause exit status `2` before index writing, even when no valid bookmarks exist, leaving any existing index and its permissions untouched.

Index builds return `2` without replacing the existing index when filesystem inspection fails, including unreadable files, unavailable subdirectories, disappearing files, or unsupported objects. Malformed or conflicting regular HTML pages are warned about and skipped; a build with other valid pages can succeed. Successful builds return `0`. Invalid arguments use argparse status `2`.

Non-finite or browser-overflowing numbers in retained archival fields are malformed page content: index builds warn and skip those pages, without changing the sources. Mixed valid/malformed inputs can still publish with status `0`; all-invalid inputs retain status `1` and preserve the old index. All-hidden valid inputs still publish an empty index. A failure of the final strict JSON serialization returns `2` before atomic publication, preserving the old index. Identity lookup and reports remain independent of numeric presentation validation.

The [visibility cautions](USAGE.md#rebuild-and-browse-the-offline-index) still apply: `hidden` is no access control, older copies may expose entries, and a failed rebuild leaves the old snapshot intact.

The index contains a snapshot of both search code and catalog tags. Rebuild after code/tag changes or adding/removing `hidden`; hidden bookmarks contribute no metadata, links, thumbnails, search results, channels, Single Entries grouping, or counts to the new index.

## Lookup, audit, and report statuses

The table describes default command behavior; opt-in catalog reporting can additionally fail as described below.

| Command | Status behavior |
|---|---|
| ID/URL lookup | `0` found; `1` not found; `2` unsafe/invalid or ambiguous; `3` URL needs extraction. |
| Library audit | `0` clean; `1` detected findings; `2` invalid arguments or unavailable bookmarks root. No repairs or writes. |
| Tag `report` | `0` complete and clean; `1` partial inspection or duplicate IDs; `2` invalid arguments, unavailable roots, or invalid catalogs. |
| Tag `list` and mutations | Validation failures return `2`; successful queries/previews/applied changes return `0`. |

Malformed, unreadable, symlinked, or non-regular catalogs produce status `2`. JSON retains any audit findings, includes a `CATALOG_ERROR` issue, and represents unavailable tags as `null`, not `[]`; human reports explain the error on stderr. Lookup failures (`1` not found, `2` unsafe/invalid, `3` URL needs extraction) do not read the catalog. Default lookup/audit behavior and exit codes remain unchanged. Argument syntax errors still use normal argparse diagnostics.

Duplicate decoded catalog keys are invalid recursively, even when their values match or occur in unknown fields. Catalog-dependent operations reject them before catalog mutation, backup, or publication; index builds preserve the old index and tagged capture rejects them during preflight. Read-only tag queries reject them too. Catalog-independent lookup and untagged capture retain their existing behavior; a later requested index rebuild remains a separate stage. No command repairs or normalizes an ambiguous catalog.

Partial tag reports use `Inspected unique IDs (partial)` and `Unverified catalog entries`; only complete inspection labels entries `Catalog entries without matching bookmarks`. Opt-in audit JSON preserves findings when catalog reporting fails. For its fields and null/empty-list distinction, see [report version 1](DATA_FORMATS.md#json-report-version-1).

## Offline upgrades

Upgrade validates bookmark filesystem access before creating backups or replacing pages. Unsupported objects, enumeration/stat failures, unreadable files, and disappearing files return `2` without changes from that validation phase. Malformed or conflicting regular pages are warned about and skipped, including legacy pages without required embedded metadata/thumbnail content.

This content-skip behavior also covers rejected archival numbers. Invalid numeric pages are left unchanged and are not added to backup/replacement candidates. Other valid pages remain eligible, with the same mixed-result exit statuses below; numeric validation does not introduce an all-library transaction or automatic source normalization.

All candidate originals are copied to a timestamped sibling `<bookmarks-directory-name>-backup-<timestamp>` folder before any replacement. The generator stages each rebuilt page in a unique sibling `.bookmark-*.tmp` file and replaces it atomically. This is per-file publication, not an all-library transaction.

Upgrade exit statuses depend on whether candidates remain and whether writes fail:

- No upgrade candidates: `1` if pages were skipped, otherwise `0`.
- Dry run with candidates: `0`, even if other pages were skipped; inspect warnings.
- Applied upgrades: `1` if a replacement failed, otherwise `0`; skipped pages alone do not make this result fail.

Later failures may leave earlier replacements completed; backups are not an automatic rollback. Keep backups until results are verified and rebuild the index afterward. Concurrent filesystem changes after validation are not covered by a snapshot guarantee.
