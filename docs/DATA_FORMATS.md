# Videx data formats

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md) · [Batch import](BATCH_IMPORT.md)

## Library structure

```text
<library>/
├── bookmarks/
│   └── <channel>/<title> [video-id].html
├── catalog.json
├── catalog.json.bak-<timestamp>    # When an existing catalog changes
└── index.html
```

Bookmark HTML is the source for offline indexing and upgrades. `catalog.json` stores personal data separately. `index.html` is a generated snapshot of bookmark metadata, thumbnails, search code, and visible personal tags; it does not replace the source data. Catalog and index files need not exist before the first capture. Untagged capture need not create a catalog.

Individual bookmarks remain readable on their own. Their embedded thumbnail and curated metadata do not require a network connection; external links do. The back-to-library link assumes the bookmark remains in the indexed archive tree.

Capture uses channel/title-based filenames for new bookmarks, with unsafe path characters sanitized; ID-based refresh preserves the existing path. Configuration lives in the source repository's ignored `config.local.json`, selecting a separate library root or falling back to the project root. See [configuration](USAGE.md#library-configuration).

## Bookmark metadata schema 2

Bookmark HTML embeds curated JSON in `<script type="application/json" id="youtube-metadata">` and a thumbnail as an image data URI. The curation helper adds `bookmark_schema_version: 2`, requires a valid ID and a title, and retains present, non-null fields from this allowlist:

```text
id, title, fulltitle, alt_title, webpage_url, original_url,
description, duration, upload_date, release_date, timestamp,
uploader, uploader_id, uploader_url, channel, channel_id,
channel_url, channel_follower_count, creator, artist,
view_count, like_count, comment_count, repost_count,
categories, tags, chapters, language, availability,
live_status, was_live, age_limit, license
```

These are curated archival fields, not a complete extractor manifest. Streaming URLs, format lists, and automatic-caption manifests are omitted; those manifests are not subtitle transcripts. Metadata `tags` are extractor/source tags, distinct from personal catalog tags. Historical counts and metadata reflect capture time rather than live values. The schema marker does not imply that every reader enforces a full formal schema.

Retained archival fields are checked recursively for numeric browser compatibility, including nested arrays and unknown members inside retained objects. `NaN`, positive/negative infinity, exponent overflow that decodes to infinity, and integers outside the browser's finite numeric range are rejected rather than replaced or omitted. Irrelevant top-level extractor fields outside the allowlist remain ignored. Valid finite archival values retain their existing types and values; null top-level fields remain omitted by curation. Catalog numeric rules and identity-oriented metadata decoding are separate from this validation.

Duration and chapter display accepts nonnegative finite integer/float seconds up to `2**53 - 1`. Booleans, numeric strings, negative seconds, and larger finite values are unavailable for display; unsuitable chapters are omitted from the chapter list. Finite fractional values remain in metadata and index data, with truncation only for displayed timestamps and chapter links. Displayed integer counts exclude booleans. These display rules do not rewrite retained finite metadata. Integers above JavaScript's exact-integer range but within its finite range retain historical numeric representation; exact browser round-tripping of those integers is not promised.

Both standalone embedded archival metadata and index payloads use strict JSON serialization (`allow_nan=False`) followed by the existing HTML-script escaping. Numeric compatibility checks do not change metadata schema 2, require a title for otherwise-supported index fallbacks, or change the identity and duplicate-member rules below.

## Catalog schema 1

A minimal catalog has this structure:

```json
{
  "version": 1,
  "bookmarks": {
    "AAAAAAAAAAA": {"tags": ["Python", "Research"]}
  },
  "collections": {}
}
```

The loader requires an object with version `1`, a `bookmarks` object, and a `collections` object. Bookmark keys must be valid video IDs and records must be objects. If present, `tags` must be an array of strings; an omitted tag array is treated as empty. Validation covers unrelated records and orphan records too. Missing catalogs are supported as empty catalogs.

Catalog object members must have unique decoded keys at every nesting level, including objects inside arrays, reserved collections data, and unknown fields. Identical and conflicting duplicates are both rejected. Escaped spellings such as `"tags"` and `"\u0074ags"` are the same decoded key; keys are not trimmed, casefolded, or Unicode-normalized. Case-distinct IDs and keys remain distinct. Unambiguous unknown fields remain preserved by ordinary tag mutations. This catalog rule does not change bookmark metadata's separate duplicate-member compatibility below.

`collections` is required. An empty object represents a catalog without Collections. A present file missing the key is invalid, while a missing file is treated as an empty catalog. Tag-only readers and writers continue treating the object contents as opaque.

### Collection records

```json
{
  "version": 1,
  "bookmarks": {},
  "collections": {
    "5bfa4a67-8796-45f6-96c3-b5deec914d16": {
      "name": "Field Notes",
      "description": "Videos to revisit.",
      "members": ["BBBBBBBBBBB", "AAAAAAAAAAA", "CCCCCCCCCCC"]
    }
  }
}
```

Each key is a lowercase, hyphenated UUIDv4. An applied creation generates the ID, and renaming preserves it. A record requires `name` and `members` and may include `description`. Edits preserve unknown fields in valid records and elsewhere in the catalog. Collection-aware consumers validate every Collection before using the section. Malformed records, including opaque reserved payloads that do not follow this representation, cause a refusal. A well-formed member ID remains valid catalog data when its file is absent.

Names use Unicode whitespace trimming and NFC normalization. After normalization they must contain 1–80 characters. Control characters, surrogate code points, and line and paragraph separators are invalid. Display case and interior spacing are preserved. Name lookup and uniqueness use `NFC(name.casefold())`; a case-only rename of the same record is valid. Descriptions normalize CRLF and CR to LF, trim outer whitespace, and allow internal newlines and tabs. Other control characters and surrogate code points are invalid. Omit absent or blank descriptions. Stored values must already be canonical.

Members are distinct canonical case-sensitive video IDs. A bookmark may be in several Collections and needs no personal tag record. New members append in first-occurrence input order and removals preserve the surviving order. Empty arrays are valid. This order is a storage property. Existing schema-1 tag writers that round-trip the whole catalog preserve valid Collection records, including during tag pruning.

The generated index contains a read-only projection of each Collection's ID, name, optional description, and visible member IDs. Cards are stored once in the index's general bookmark data and shared across browsing contexts. A member must resolve to exactly one bookmark identity before presentation validation and pass normal indexing and visibility checks. The index derives visible member totals from the projected IDs. CLI reports still use stored membership. See [index publication and statuses](SAFETY.md#index-publication-and-statuses).

### Collection JSON report version 1

`collection OPERATION --json` emits one object with `report_version`, `operation`, `library` (absolute path or null), `catalog.status` (`present`, `missing`, `unavailable`, or `not_read`), `mode` (`read`, `preview`, or `apply`), `result`, `issues`, and `exit_status`. Result values are `ok`, `partial`, `would_change`, `applied`, `no_change`, `refused`, and `interrupted`. Issues contain a category and message and may carry relevant paths or IDs.

`list` returns summaries in `collections`, each with `id`, `name`, and `member_count`. `show` returns the selected `collection` with its stored members, plus ordered `member_findings` with `id`, `status`, and absolute `paths`. Mutations carry `before` and `after` with the ID and documented record fields, using null for a missing side or for a proposed UUID in a creation preview. They also report `catalog.path`, `catalog.backup`, and `catalog.publication` (the latter is `not_published`, `published`, or `unknown`). The status field describes the catalog read at preflight. Argument syntax errors use argparse diagnostics.

`from-import` adds `proposal` after decoding a valid handoff. It includes the
source `title` and `description`, a normalized `candidate` with ordered
`members` and available valid metadata, `metadata_issues` keyed by field,
`omitted` entries with position, ID, outcome and reason, `repeats` with position,
ID and `duplicate_of`, handoff `status`, and a `partial` boolean for omissions.
An invalid draft can be inspected here even when the mutation reports `refused`.

### Playlist Collection report extension

The [import report version 1](BATCH_IMPORT.md#reports-and-recovery) adds
`collection_proposal` only with `--propose-collection` on a playlist import.

| Field | Values |
|---|---|
| `acquisition` | `preview` for completed discovery without capture, `completed` for an applied batch that ran to completion, and `stopped` for discovery/acquisition refusal, interruption, or batch-wide stop |
| `status` | `preview`, `eligible`, `empty`, or `refused`. An import that completed but could no longer confirm its original library or members reports `completed` and `refused`. |
| `reason` | Text for `empty` and `refused`, otherwise null |
| `library_identity` | For `eligible`, an object with `root` and `bookmarks` objects, each containing integer `device` and `inode` fields. Otherwise null. The existing top-level `library` supplies the canonical path. |

Membership comes from the ordered `created` and `skipped_existing` entries, with
subsequent occurrences reported as `duplicate_input`. Failed, invalid, and
unavailable entries are omissions. The consumer checks JSON syntax and unique
keys, report version, source and field types, ordered positions, canonical IDs,
duplicate references, counts, outcome consistency and the library binding.
Only applied, completed, eligible reports can supply Collection membership. The
index result remains separate. Reports are local handoff artifacts with a library
binding. Collection records retain the existing schema and contain no source
playlist or report identity.

## Personal tag normalization

Whitespace is collapsed and duplicates are compared case-insensitively. Normalized tags must be nonempty, at most 80 characters, and free of control characters. Display capitalization is retained except that the reserved tag `hidden` normalizes to lowercase.

When a tag operation changes a video's record, its tags are saved alphabetically, case-insensitively, preserving display capitalization and case-insensitive deduplication (`hidden` retains its lowercase normalization and sorts normally). This also applies to capture-time tagging. Unrelated records and no-op requests do not have their tags reordered.

Index badges and opt-in reports use normalized tags without rewriting catalog contents. Missing catalogs or records yield no badge row; orphan catalog records are ignored by the index. `hidden` excludes a bookmark only from the rebuilt index, not the filesystem or CLI reports. See [visibility behavior](USAGE.md#rebuild-and-browse-the-offline-index).

## Identity resolution

Video IDs are case-sensitive 11-character strings containing letters, digits, `_` or `-`. A filename ID must agree with embedded metadata. Renamed bookmarks use their metadata ID. Lookup, tagging, and Collection management support legacy filename-only bookmarks when the metadata element is absent. Present but malformed metadata never falls back to the filename. Lookup and catalog changes refuse conflicting, ambiguous, or incomplete inventories (status `2`); capture stops on lookup failure and validates the extractor's resolved ID before writing. Ordinary capture can follow an extractor-resolved redirect and rechecks that ID against the library; bulk import additionally requires the extracted ID to equal the requested ID. Indexing and offline upgrades warn and skip damaged or conflicting pages, including legacy pages lacking the metadata they require. Audit reports conflicts separately from resolved IDs. No identity is automatically repaired.

Embedded and incoming metadata JSON reject repeated `id` keys even when their values match; other repeated keys retain their existing last-value behavior. Multiple or unterminated metadata elements are invalid. Filename identity is taken from a trailing `[VIDEO_ID].html`; IDs themselves remain case-sensitive.

Leading-hyphen IDs are valid data. Their CLI syntax differs by command; preserve the [capture/lookup examples](USAGE.md#capture-and-refresh) and [tag argument ordering](USAGE.md#personal-catalog-management).

## JSON report version 1

This section describes lookup/audit reports. The importer has a separate [report version 1 contract](BATCH_IMPORT.md#reports-and-recovery); its `--json` does not require `--show-tags`.

`--json` requires `--show-tags` and emits one JSON document with `report_version: 1`, `operation`, `library`, `catalog.status`, `bookmarks` (`id`, absolute `paths`, `tags`), `issues`, and `exit_status`. Audit JSON also includes `summary` counts. Catalog status is `present`, `missing`, `unavailable`, or `not_read`. Missing catalogs/records yield empty tag lists and are never created. Tag whitespace, case-insensitive deduplication, and `hidden` normalization match the tag utility; catalog contents are not changed.

The normal `library` field is an absolute library path. Configuration-error JSON uses `library: null`, catalog status `not_read`, a `CONFIG_ERROR` issue, and exit status `2`. Issue objects carry a category and message plus relevant paths or IDs; mismatch issues include filename and metadata IDs. Audit summary keys are `scanned`, `unique_video_ids`, `duplicate_groups`, `extra_copies`, `mismatches`, `unknown_ids`, and `unreadable_files`.

Catalog failures retain audit findings, include a `CATALOG_ERROR` issue, and represent unavailable tags as `null`, not `[]`. A missing catalog or missing record instead produces an empty list. Lookup failures leave the catalog unread. Argument syntax errors use argparse diagnostics rather than promising a JSON document. See [failure statuses](SAFETY.md#lookup-audit-and-report-statuses).

## Compatibility and migration

Catalog schema **1**, bookmark metadata schema **2**, and JSON report version **1** remain unchanged. No automatic library migration or recapture is required for current tag search and visibility behavior. Legacy filename-only bookmarks remain usable for lookup/tagging when metadata is absent; indexing/upgrading require embedded metadata. Rebuild the index explicitly to include current catalog tags and search behavior.

Offline upgrades regenerate HTML from existing embedded metadata and thumbnails; they do not contact YouTube or fetch missing content. See [upgrade workflow](USAGE.md#offline-bookmark-upgrades), [upgrade safety](SAFETY.md#offline-upgrades), and [moving an existing library](USAGE.md#moving-an-existing-library).
