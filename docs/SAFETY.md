# Videx safety and failure handling

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md)

## Scope and library backups

Videx stores bookmarks locally as standalone HTML files in the selected library’s `bookmarks/` directory. The generated `index.html` provides an offline-searchable interface to your collection.

These files are excluded from Git tracking by default. Your bookmark collection is independent of the Videx source code, so be sure to back it up separately. Duplicate detection and auditing do not replace backups.

`--apply` is operation-specific: catalog edits and upgrades preview by default; capture writes new bookmarks and automatically applies their requested tags. `--overwrite` refreshes existing HTML independently of existing-bookmark tag edits. Index generation writes its output without an apply flag. See [usage](USAGE.md) before running commands.

## Filesystem inspection

Bookmark inspection never reads discovered file symlinks, dangling symlinks, FIFOs, sockets, devices, or other non-regular bookmark objects, and never descends through discovered directory symlinks. Collection roots must be real directories. Real subdirectories named `index.html` are traversed; generated index files are excluded case-insensitively, while bookmark candidates retain case-sensitive `.html` matching. Unrelated ordinary non-HTML files are ignored.

The shared scanner records unsupported discovered symlinks as inventory issues without following them. Special objects with bookmark-candidate `.html` names are rejected without reading; unrelated non-HTML objects are not bookmark candidates. A discovered generated `index.html` entry is excluded case-insensitively unless it is a real directory, which is traversed.

Explicit library aliases remain supported when they select a library with a real `bookmarks/` directory. Index and upgrade accept legacy layouts without a `bookmarks` entry; an existing unsupported entry is an error, not a reason to fall back to scanning another directory. A directly selected bookmark collection root must be a real directory rather than a symlink.

These checks do not provide a snapshot against concurrent filesystem changes. File type checks and later reads/replacements can observe different filesystem states. Do not treat them as a universal concurrency guarantee.

## Inventory completeness and identity

Lookup refuses conflicting or incomplete inventories; duplicate matches for the requested ID are ambiguous. Catalog mutation previews and applied mutations additionally require no duplicate IDs anywhere in the inventory. Capture uses the strict catalog-mutation inventory checks before extraction and again before writing, rejecting duplicates anywhere in the collection. No command automatically repairs identities. See [identity rules](DATA_FORMATS.md#identity-resolution).

Tag `report` can show partial inspection results, while `list` reads only the catalog after validating the library directory and ID. Neither is equivalent to a successful mutation preflight. Confirmed orphan entries do not by themselves make a complete report fail; partial unmatched entries remain unverified.

## Capture and generator validation

Requested tags follow the [normalization and validation rules](DATA_FORMATS.md#personal-tag-normalization). Tagged capture validates tags and catalog state before extraction and rechecks the catalog before a bookmark write. Invalid inputs leave existing bookmarks/catalogs unchanged. Untagged capture does not read the catalog unless `--re-index` is requested.

Capture stops on unsafe lookup failure. It checks the extractor's resolved ID and rechecks the inventory before writing. The lower-level generator validates the rendered identity and requires an existing regular-file destination to resolve to the same ID; a refresh cannot silently replace a different identity. The shell capture wrapper maps lookup/inventory refusal to status `1` in those paths, rather than universally preserving the helper's status `2`.

The generator rejects existing symlink, directory, or special-file bookmark destinations with controlled status `2`, before reading them or staging output. New destinations and matching regular-file refreshes remain supported.

Capture, tagging, and indexing are separate steps, not a transaction across all files. A later tagging or index failure does not imply that an earlier bookmark write was rolled back. Capture refresh does not create the timestamped backups supplied by offline upgrade; retain independent library backups.

## Catalog validation and publication

Every tag operation validates the catalog, including previews and no-op requests. Existing catalogs must be regular files; symlinks (including dangling links), directories, and special files are rejected without reading their contents. Tag arrays in all records, including unrelated records and orphans, must contain only strings. These validation failures produce an error and exit status `2`, leaving catalog paths, targets, and existing backups unchanged without creating backups or temporary publication files. Missing catalogs remain supported.

All mutation previews and applied mutations require a complete validated inventory without duplicate IDs. Pruning refuses incomplete inventories, including unreadable files/directories, malformed metadata, unidentified bookmarks, and symbolic links; generated index files are excluded. Refusal leaves catalog and backups untouched. Pruning removes only confirmed unmatched catalog records, never bookmark HTML.

For a changed catalog, the writer stages JSON in a unique sibling `.catalog-*.tmp` file, flushes and fsyncs it, copies an existing catalog to a timestamped `catalog.json.bak-*`, then replaces the catalog with `os.replace`. No-op requests do not reorder unrelated records or create a replacement. A missing catalog is supported and has no previous catalog to back up. Reporting creates neither catalogs, backups, nor staging files. Catalog replacement does not lock concurrent editors or merge competing changes; coordinate writers to avoid lost edits.

## Index publication and statuses

Index generation writes a unique temporary file beside `index.html`, then atomically replaces the index. Existing regular-file permissions are preserved; new indexes and replaced symlinks use normal creation permissions respecting the process umask, without writing through symlinks. Concurrent builds publish complete files; the last successful replacement wins.

Rebuild the index after adding or removing `hidden` to apply visibility changes. When valid bookmarks exist but all are hidden, rebuilding succeeds and replaces any older index with an empty-state index. A genuinely empty library or one containing no valid bookmarks retains exit status `1` and leaves any existing index untouched. Malformed, unreadable, symlinked, or non-regular catalogs cause exit status `2` before index writing, even when no valid bookmarks exist, leaving any existing index and its permissions untouched.

Index builds return `2` without replacing the existing index when filesystem inspection fails, including unreadable files, unavailable subdirectories, disappearing files, or unsupported objects. Malformed or conflicting regular HTML pages are warned about and skipped; a build with other valid pages can succeed. Successful builds return `0`. Invalid arguments use argparse status `2`.

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

All candidate originals are copied to a timestamped sibling `<bookmarks-directory-name>-backup-<timestamp>` folder before any replacement. The generator stages each rebuilt page in a unique sibling `.bookmark-*.tmp` file and replaces it atomically. This is per-file publication, not an all-library transaction.

Upgrade exit statuses depend on whether candidates remain and whether writes fail:

- No upgrade candidates: `1` if pages were skipped, otherwise `0`.
- Dry run with candidates: `0`, even if other pages were skipped; inspect warnings.
- Applied upgrades: `1` if a replacement failed, otherwise `0`; skipped pages alone do not make this result fail.

Later failures may leave earlier replacements completed; backups are not an automatic rollback. Keep backups until results are verified and rebuild the index afterward. Concurrent filesystem changes after validation are not covered by a snapshot guarantee.
