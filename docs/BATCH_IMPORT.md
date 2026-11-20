# TXT batch import

[Back to README](../README.md) · [Usage](USAGE.md) · [Safety](SAFETY.md)

Import a backlog as individual standalone bookmarks. This command supports TXT
files only; playlist discovery and Collections remain future work. It uses the
same capture, metadata curation, and HTML generation pipeline as single-video
capture, without changing bookmark or catalog formats.

## Commands

```bash
# Preview using the configured library; no network or writes
python3 -B scripts/yt-bookmark-import.py --file backlog.txt

# Explicit preview against a selected library
python3 -B scripts/yt-bookmark-import.py --file backlog.txt --output '/path/to/library' --dry-run

# Capture new IDs and rebuild the index once
python3 -B scripts/yt-bookmark-import.py --file backlog.txt --apply

# Apply without rebuilding; emit one JSON report
python3 -B scripts/yt-bookmark-import.py --file backlog.txt --apply --no-re-index --json
```

`--file` is required. `--output` selects the library root, never its `bookmarks/`
subdirectory. Otherwise the existing `config.local.json` setting or project-root
fallback applies. Invalid configuration refuses the operation; explicit output
overrides configuration. `--apply` and `--dry-run` are mutually exclusive.

## Durable TXT format

Use UTF-8 plain text, optionally with a leading UTF-8 BOM. LF and CRLF line endings
are supported. Each nonblank, noncomment line contains exactly one supported
YouTube video URL or case-sensitive 11-character ID. Surrounding whitespace is
trimmed. A line whose first non-whitespace character is `#` is a comment.

Complete example (synthetic IDs; replace them with real video IDs):

```text
# Backlog: one video per line

AAAAAAAAAAA
https://www.youtube.com/watch?v=BBBBBBBBBBB
https://youtu.be/CCCCCCCCCCC?t=30
https://m.youtube.com/shorts/DDDDDDDDDDD
https://music.youtube.com/watch?v=EEEEEEEEEEE
https://youtube.com/live/FFFFFFFFFFF
https://www.youtube.com/embed/GGGGGGGGGGG

# This is reported as duplicate input, referring to the first ID's line.
https://youtu.be/AAAAAAAAAAA
```

IDs contain exactly 11 ASCII letters, digits, underscores, or hyphens. IDs differing
only in capitalization are different videos. Leading-hyphen IDs are supported.
Input order and physical line numbers are preserved; only the first occurrence
of an ID is processed.

Supported URL boundaries:

- HTTP or HTTPS with exact hosts `youtube.com`, `www.youtube.com`,
  `m.youtube.com`, or `music.youtube.com`: `/watch?v=ID`, `/shorts/ID`,
  `/live/ID`, or `/embed/ID`.
- HTTP or HTTPS with `youtu.be` or `www.youtu.be`: `/ID`.
- Ordinary query parameters such as timestamps and share tokens are accepted and
  discarded when constructing the canonical video URL. `watch` requires exactly
  one `v` value. A `list` parameter on a video URL does not import a playlist.
- Timestamp fragments such as `#t=30s` are accepted; other fragments are rejected.
- Credentials, explicit ports, unsupported hosts/schemes, extra path segments,
  and ambiguous/malformed IDs are rejected.

No CSV, Markdown links, bracketed IDs in prose, titles, extra columns, or inline
comments are supported. For example, `AAAAAAAAAAA # watch later` is invalid.
Invalid lines are reported with line numbers; valid independent lines can still
be planned or imported. An unreadable file or invalid UTF-8 refuses the batch.

## Preview and apply

Preview validates the entire file and the existing library inventory. It creates
no directories, bookmarks, catalogs, backups, or indexes, and does not invoke
yt-dlp. **Would create is a plan, not verified video availability.** A missing
library is treated as empty; unsafe or incomplete existing inventories refuse
the operation.

Apply captures unique new IDs sequentially. Existing bookmarks, including renamed
ones and bookmarks tagged `hidden`, are reused without extraction or refresh.
Duplicates, conflicts, unreadable bookmark trees, and unsafe filesystem objects
use the established identity/inventory safeguards. A newly unsafe library stops
further writes. Independent extraction or metadata failures allow later entries
to continue. Successfully saved files remain saved.

Importer-controlled yt-dlp extraction ignores ambient configuration, disables
filesystem caching and watched marking, and requests metadata and a thumbnail
without downloading video. Authentication from ambient yt-dlp configuration is
therefore not inherited. The ordinary single-video command keeps its behavior.
No playlist is created, modified, or deleted. No personal tags or Collections are
assigned, and existing catalog data is untouched.

The extracted video ID must match the requested ID. Complete HTML is staged beside
its destination and published with a create-only hard link, so an existing
destination is never silently replaced. This requires hard-link support on the
library filesystem; failure is reported rather than falling back to overwriting.
Checks do not lock the whole library: coordinate capture, moves, and other writers.

After a batch creates bookmarks, the index is rebuilt once, including after partial
success or a handled capture interruption, unless `--no-re-index` is supplied.
No-new-bookmark batches do not rebuild. Index failures preserve completed bookmarks
and the previous index. Existing catalog validation and `hidden` exclusion still
apply to the index.

## Reports and recovery

Human reports and `--json` distinguish created, would create, skipped existing,
duplicate input, invalid input, unavailable, failed/unknown, and not processed.
Stage 1 conservatively reports extractor errors as failed/unknown: it has no
reliable structured evidence to distinguish permanent unavailability from network,
authentication, or extractor problems. The unavailable count is reserved and remains
zero. Index status is separate from per-entry outcomes.

JSON emits one document with `report_version: 1`, `apply`, `library`, ordered
`entries`, `summary`, `errors`, `interrupted`, `index`, and `exit_status`. Entries
include `line`, `input`, `id`, `outcome`, `path`, and optional `message` or
`duplicate_of` (the first occurrence's physical line). Index diagnostics are retained
in the JSON report. Argument syntax errors use argparse diagnostics instead.

Exit statuses:

| Status | Meaning |
|---|---|
| `0` | Complete preview/import; duplicates and existing skips are normal |
| `1` | Invalid lines, failed/unavailable/unprocessed entries, or index failure |
| `2` | Invocation, input-file, configuration, dependency, or global preflight refusal |
| `130` | Interrupted operation |

To recover, fix reported input/library problems and rerun the same file. Saved IDs
are skipped; remaining entries are retried. No checkpoint database is needed. If
the index is stale or its rebuild failed, resolve the reported issue and run:

```bash
python3 -B scripts/yt-bookmark-index.py '/path/to/library'
```

Ctrl-C stops the active capture process group and reports partial progress where
possible. A hard kill or power loss cannot guarantee a final report or index
rebuild and may leave staging files. No completed bookmark is rolled back and no
automatic cleanup of unrelated artifacts is attempted. Atomic publication does
not promise power-loss durability. Keep independent library backups.
