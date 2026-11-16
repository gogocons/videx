# Videx data formats

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md)

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

`collections` is reserved structure required by the catalog format. User-facing collection management and collection browsing are future work; the presence of this object does not implement those capabilities.

## Personal tag normalization

Whitespace is collapsed and duplicates are compared case-insensitively. Normalized tags must be nonempty, at most 80 characters, and free of control characters. Display capitalization is retained except that the reserved tag `hidden` normalizes to lowercase.

When a tag operation changes a video's record, its tags are saved alphabetically, case-insensitively, preserving display capitalization and case-insensitive deduplication (`hidden` retains its lowercase normalization and sorts normally). This also applies to capture-time tagging. Unrelated records and no-op requests do not have their tags reordered.

Index badges and opt-in reports use normalized tags without rewriting catalog contents. Missing catalogs or records yield no badge row; orphan catalog records are ignored by the index. `hidden` excludes a bookmark only from the rebuilt index, not the filesystem or CLI reports. See [visibility behavior](USAGE.md#rebuild-and-browse-the-offline-index).

## Identity resolution

Video IDs are case-sensitive 11-character strings containing letters, digits, `_` or `-`. A filename ID must agree with embedded metadata. Renamed bookmarks use their metadata ID; legacy filename-only bookmarks remain supported by lookup and tagging only when the metadata element is absent. Present but malformed metadata never falls back to the filename. Lookup and catalog changes refuse conflicting, ambiguous, or incomplete inventories (status `2`); capture stops on lookup failure and validates the extractor's unchanged ID before writing. Indexing and offline upgrades warn and skip damaged or conflicting pages, including legacy pages lacking the metadata they require. Audit reports conflicts separately from resolved IDs. No identity is automatically repaired.

Embedded and incoming metadata JSON reject repeated `id` keys even when their values match; other repeated keys retain their existing last-value behavior. Multiple or unterminated metadata elements are invalid. Filename identity is taken from a trailing `[VIDEO_ID].html`; IDs themselves remain case-sensitive.

Leading-hyphen IDs are valid data. Their CLI syntax differs by command; preserve the [capture/lookup examples](USAGE.md#capture-and-refresh) and [tag argument ordering](USAGE.md#personal-catalog-management).

## JSON report version 1

`--json` requires `--show-tags` and emits one JSON document with `report_version: 1`, `operation`, `library`, `catalog.status`, `bookmarks` (`id`, absolute `paths`, `tags`), `issues`, and `exit_status`. Audit JSON also includes `summary` counts. Catalog status is `present`, `missing`, `unavailable`, or `not_read`. Missing catalogs/records yield empty tag lists and are never created. Tag whitespace, case-insensitive deduplication, and `hidden` normalization match the tag utility; catalog contents are not changed.

The normal `library` field is an absolute library path. Configuration-error JSON uses `library: null`, catalog status `not_read`, a `CONFIG_ERROR` issue, and exit status `2`. Issue objects carry a category and message plus relevant paths or IDs; mismatch issues include filename and metadata IDs. Audit summary keys are `scanned`, `unique_video_ids`, `duplicate_groups`, `extra_copies`, `mismatches`, `unknown_ids`, and `unreadable_files`.

Catalog failures retain audit findings, include a `CATALOG_ERROR` issue, and represent unavailable tags as `null`, not `[]`. A missing catalog or missing record instead produces an empty list. Lookup failures leave the catalog unread. Argument syntax errors use argparse diagnostics rather than promising a JSON document. See [failure statuses](SAFETY.md#lookup-audit-and-report-statuses).

## Compatibility and migration

Catalog schema **1**, bookmark metadata schema **2**, and JSON report version **1** remain unchanged. No automatic library migration or recapture is required for current tag search and visibility behavior. Legacy filename-only bookmarks remain usable for lookup/tagging when metadata is absent; indexing/upgrading require embedded metadata. Rebuild the index explicitly to include current catalog tags and search behavior.

Offline upgrades regenerate HTML from existing embedded metadata and thumbnails; they do not contact YouTube or fetch missing content. See [upgrade workflow](USAGE.md#offline-bookmark-upgrades), [upgrade safety](SAFETY.md#offline-upgrades), and [moving an existing collection](USAGE.md#moving-an-existing-collection).
