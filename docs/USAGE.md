# Videx usage

[Back to README](../README.md) · [Usage](USAGE.md) · [Development](DEVELOPMENT.md) · [Data formats](DATA_FORMATS.md) · [Safety](SAFETY.md) · [Batch import](BATCH_IMPORT.md)

Run commands from the repository root. Replace `VIDEO_ID` or `AAAAAAAAAAA` with an actual 11-character YouTube video ID and `/path/to/library` with your library root. Capture, refresh, and playlist discovery use the network; TXT previews and local maintenance work offline. Videx saves metadata and thumbnails, not videos or transcripts.

## Command interface

Run `./videx --help` to see the available commands. From another working directory, invoke the executable by its checkout path, for example `/path/to/videx/videx open`. Relative input and library paths are interpreted from the directory where you run the command.

| Command | Purpose |
|---|---|
| `./videx open` | Open the existing offline index. |
| `./videx capture` | Save or refresh a single video bookmark. |
| `./videx import` | Preview or apply a TXT or public-playlist import. |
| `./videx index` | Build the offline search index. |
| `./videx find` | Look up a video or audit the library. |
| `./videx tag` | Report or edit personal tags. |
| `./videx collection` | Inspect or edit local Collections. |
| `./videx upgrade` | Preview or apply offline bookmark upgrades. |

Use `./videx COMMAND --help` for the command's own options. You can still use the direct script invocations shown throughout this guide.

## Library configuration

Videx source code and personal bookmark data can live in different folders. Copy `config.example.json` to `config.local.json` in the project root, then edit it:

```json
{
  "library": "~/Documents/Video Bookmarks"
}
```

`config.local.json` is ignored by Git. The `library` value must be an absolute path or begin with `~/`, and must point to the **library root**, not `bookmarks/`. When the file is absent, Videx falls back to its original project-local library. An invalid configuration fails with an error instead of silently selecting another location. Explicit paths still override the configured default.

Commands with a configured default:

```bash
bash scripts/yt-bookmark.sh --re-index 'https://youtu.be/VIDEO_ID'
python3 -B scripts/yt-bookmark-index.py
python3 -B scripts/yt-bookmark-find.py --audit
python3 -B scripts/yt-bookmark-tag.py report
python3 -B scripts/yt-bookmark-upgrade.py
```

The tag tool also accepts its original `LIBRARY OPERATION ...` syntax; `yt-bookmark.sh --output DIR` and positional directory arguments on the other utilities continue to override the default. The generator accepts explicit metadata, thumbnail, and output paths and needs no library configuration. Metadata curation is an imported helper, not a separate command-line workflow.

**Important:** The `--output` flag on the capture script creates the target library directory if needed. Check the configured path carefully before your first capture. The offline index and audit commands will not create missing bookmark directories.

The capture option `-o DIR` is an alias for `--output DIR`. Index and upgrade commands also accept a bookmark directory; prefer the library root for ordinary use. See [legacy layouts and aliases](SAFETY.md#filesystem-inspection) before using unusual layouts.

## Capture and refresh

For a backlog or public playlist, use the bulk importer:

```bash
python3 -B scripts/yt-bookmark-import.py --file backlog.txt --dry-run
python3 -B scripts/yt-bookmark-import.py --file backlog.txt --apply
python3 -B scripts/yt-bookmark-import.py --file backlog.txt --output '/path/to/library' --apply --no-re-index --json
python3 -B scripts/yt-bookmark-import.py --playlist 'https://www.youtube.com/playlist?list=PLAYLIST_ID'
python3 -B scripts/yt-bookmark-import.py --playlist 'https://www.youtube.com/playlist?list=PLAYLIST_ID' --apply
```

Choose exactly one of `--file` and `--playlist`; replace `PLAYLIST_ID` with an
actual public playlist ID. Preview is the default. TXT preview works offline;
playlist preview requires network access for flat discovery. Neither writes to
the library or fetches individual video metadata. Apply reuses existing
bookmarks and rebuilds the index once after creating bookmarks. See the
[complete batch format, playlist discovery, and recovery reference](BATCH_IMPORT.md). Importer `--apply`
authorizes new captures. Bulk import accepts `--no-re-index` to suppress the default
final rebuild, and has no `--re-index` option. A batch with no new bookmarks does
not rebuild. Individual capture below rebuilds only with explicit `--re-index`
and does not accept `--no-re-index` or `--dry-run`.

From the repository root, run:

```bash
# View capture options
bash scripts/yt-bookmark.sh --help

# Save a bookmark and update the library index in one command
bash scripts/yt-bookmark.sh --re-index 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Run `./videx open` to browse the generated index. Bookmarks are created under `<library>/bookmarks/<channel>/<title> [video-id].html`. The `bookmarks/` folder is created automatically when saving the first video.

```bash
# Save a bookmark without rebuilding the index
bash scripts/yt-bookmark.sh 'https://youtu.be/VIDEO_ID'

# Save into another library and rebuild its index
bash scripts/yt-bookmark.sh --output '/path/to/library' --re-index 'https://youtu.be/VIDEO_ID'

# Refresh an existing bookmark in place, preserving its path
bash scripts/yt-bookmark.sh --overwrite 'https://youtu.be/VIDEO_ID'

# Refresh a bookmark and rebuild the index afterward
bash scripts/yt-bookmark.sh --overwrite --re-index 'https://youtu.be/VIDEO_ID'

# Rebuild the index of another library without capturing a bookmark
python3 -B scripts/yt-bookmark-index.py '/path/to/library'
```

Capture accepts repeated `--tag` options. New bookmarks receive their requested tags automatically in `catalog.json`; existing bookmarks preview tag changes unless `--apply` is supplied. `--overwrite` independently refreshes the bookmark HTML; `--apply` controls existing-bookmark tag changes only.

Capture consumes the argument after each `--tag` literally, including `--apply`, `--help`, `--app`, `-topic`, and `--unknown=value`. For example, `--tag Research --tag --apply` previews both tags on an existing bookmark; add a separate standalone `--apply` to save them. A tag named `--apply` never enables writes by itself.

```bash
# New bookmark: save both tags automatically; existing bookmark: preview additions
bash scripts/yt-bookmark.sh --tag Python --tag Research 'https://youtu.be/AAAAAAAAAAA'

# Existing bookmark: apply tag additions without refreshing its HTML
bash scripts/yt-bookmark.sh --tag Research --apply 'https://youtu.be/AAAAAAAAAAA'

# Refresh existing HTML and apply requested tag additions
bash scripts/yt-bookmark.sh --overwrite --tag Research --apply 'https://youtu.be/AAAAAAAAAAA'
```

Videx identifies existing bookmarks by **video ID**, rather than their title or channel directory. An existing ID is skipped by default; adding `--re-index` still rebuilds the index after a skip. With `--overwrite`, Videx refreshes the matching HTML file at its existing path instead of creating a renamed copy. If multiple matches are found, Videx refuses to choose one automatically.

IDs beginning with one or two hyphens are valid. Capture uses its existing two-argument syntax, for example `bash scripts/yt-bookmark.sh --id '-AAAAAAAAAA'`; it does not accept `--id=VALUE`. Direct lookup uses the equals form for these IDs: `python3 -B scripts/yt-bookmark-find.py '/path/to/library' --id=-AAAAAAAAAA`. Quoting a separate lookup argument does not prevent option interpretation; `--id -- VALUE` is not a substitute for the equals form.

Capture `--id TEXT` accepts a bare ID or one distinct bracketed ID in text. Multiple distinct bracketed IDs are rejected. Supply one URL or one `--id TEXT` input. Capture does not import playlists.

Tag values follow the [normalization rules](DATA_FORMATS.md#personal-tag-normalization). See [capture validation](SAFETY.md#capture-and-generator-validation) for preflight checks and catalog independence when no tags are requested.

## Lower-level offline generation

The generator renders one bookmark from an existing metadata JSON file and thumbnail, without yt-dlp or network access. It takes three required positional paths and an optional relative back link:

```bash
python3 -B scripts/yt-bookmark-generate.py /path/to/info.json /path/to/thumbnail.png '/path/to/library/bookmarks/Synthetic/Example [AAAAAAAAAAA].html' --back-link ../../index.html
python3 -B scripts/yt-bookmark-index.py '/path/to/library'
```

For a disposable demonstration library, supply synthetic metadata such as `{"id":"AAAAAAAAAAA","title":"Example","uploader":"Synthetic"}` and a synthetic thumbnail. Replace the example paths with those fixtures and a separate temporary library. The generator creates parent directories and writes HTML immediately; it has no preview/`--apply` mode. A matching existing regular bookmark can be replaced without an upgrade backup. It does not add personal tags or rebuild the index. Here and in older CLI messages, “collection index” refers to the library-wide index.

## Rebuild and browse the offline index

### Open an existing snapshot

```bash
./videx open
./videx open --library '/path/to/library'
```

The opener uses `config.local.json` or falls back to the checkout root when there is no config. An explicit `--library DIR` bypasses local config, even if the config is invalid. Give the library root and quote paths that contain spaces or shell metacharacters. Home-relative paths such as `'~/Documents/Video Bookmarks'` are expanded.

The command requires a readable, nonempty, regular `index.html` and rejects index symlinks. It hands the file to the default HTML application using `open` on macOS or `xdg-open` on Linux. Linux requires a graphical session with `DISPLAY` or `WAYLAND_DISPLAY` set. Opening uses the saved snapshot, so changes to bookmarks, tags, or Collections appear after a separate rebuild. The opener needs no network, local server, catalog, or bookmark scan.

Status `0` means the desktop launcher accepted the request. Status `1` means the target or launcher could not be used, or the platform is unsupported. Status `2` means the arguments or configuration are invalid.

### Rebuild the snapshot

Without `--re-index`, bookmark capture does **not** rebuild the index. You can rebuild it separately at any time:

```bash
./videx index
./videx index '/path/to/library'
```

Rebuilding the index excludes every bookmark carrying the reserved personal tag `hidden` (case-insensitive, with surrounding whitespace normalized). Excluded bookmarks contribute no embedded metadata, links, thumbnails, search results, channel listings, or counts. Channels and Single Entries groups are calculated exclusively from visible bookmarks. Other normalized personal tags from the selected library's `catalog.json` appear as wrapping badges on bookmark cards and are searchable through the existing search bar. Each tag supports case-insensitive partial matches. Missing catalogs or tag records produce no tag row; orphan records are ignored. Generated indexes remain self-contained offline.

Search stays visible and matches a trimmed, case-insensitive phrase within a title, channel, description, video ID, or individual personal tag. Open **Filters** to select a Channel and/or Personal Tag. Channel offers **All Channels**, **Single Entries** (channels with exactly one visible bookmark), and individual channels with at least two visible bookmarks. Personal Tag offers **All Personal Tags** and tags from visible bookmarks only. Tag selection matches exact membership using the catalog's Python Unicode case-folding identities; display capitalization is retained. Search, Channel, and Personal Tag combine with AND: every active condition must match. Filter options and Single Entries grouping remain based on the whole visible library, regardless of the current results.

The panel starts closed and closing it preserves selections. **Filters (1)** or **Filters (2)** counts active Channel and Personal Tag selections. Remove an individual chip below the toolbar to clear only that filter. **Clear Filters** clears both structured filters while preserving Search, Sort, and the current Collection. Results and their announced count update immediately.

All browsing contexts share **Newest**, **Oldest**, **Title A-Z**, **Title Z-A**, and **Channel A-Z**. **Newest** is the default. **Newest** and **Oldest** use the video's upload date. Switching contexts retains the active sort, and the stored member order has no effect on card order. Selections are not saved to URLs or local storage.

Use Tab to move through Search, Filters, and Sort. Enter or Space toggles Filters. When the panel is open, Tab after Sort reaches Collection (when available), Channel, Personal Tag, and Clear Filters. The closed panel is skipped. Closing with the Filters button or Escape returns focus to Filters. Removing a chip also returns focus to Filters. Clear Filters moves focus to Channel in the open panel.

If Tab skips buttons or selects, check your browser and operating system's keyboard navigation settings. In Firefox on macOS, look for **Use the tab key to move focus between form controls and links** in Settings.

Regenerate the index to pick up updated browsing code, catalog tags, or Collections. The saved `index.html` contains the code and visible snapshot data. You can rebuild and browse offline without a local web server.

`hidden` is an organizational visibility control, not a security feature. Bookmark files, directories, catalog records, and CLI lookup/tag reports remain unchanged and accessible. Older index copies can still contain previously visible bookmarks, and failed rebuilds leave the existing index intact.

To control embedded thumbnail size:

```bash
python3 -B scripts/yt-bookmark-index.py '/path/to/library' --thumbnail-width 360
```

The default width is 360 pixels; accepted values are 64–1920. Optional Pillow reduces embedded thumbnail sizes; without it the original images are embedded. Conversion failures warn and fall back to the original image.

All-hidden libraries publish an empty-state index; libraries with no valid bookmarks leave the existing index untouched. See [index publication and statuses](SAFETY.md#index-publication-and-statuses).

### Browse Collections

Open **Filters** to find **Collection** above Channel and Personal Tag. The selector appears when the saved index contains Collections and starts at **All Bookmarks**. Select a Collection to see its name, optional description, and cards in the same grid. The result count shows how many cards match and how many visible members the Collection contains. Closing Filters keeps the selection, summary, and count visible in the browsing view. To return to the library-wide view, reopen Filters and select **All Bookmarks**. Search, Channel, and Personal Tag selections carry across contexts and apply within the current Collection. Header statistics and filter options continue to describe the whole visible library.

Missing, skipped, ambiguous, and hidden members are omitted from the grid and visible member total. Their references stay in the catalog. Empty Collections and Collections whose members are all unavailable remain selectable. The index distinguishes **This Collection has no visible bookmarks** from **No matching bookmarks found** when Search or Filters narrow a Collection that has visible members.

## Personal catalog management

`yt-bookmark-tag.py` supports `add`, `remove`, `set`, `rename`, `list`, `report`, and `prune`. Editing operations preview changes by default; repeat with `--apply` to save. Applied changes back up an existing catalog before replacement. Examples below use the configured library (or project-local fallback):

```bash
python3 -B scripts/yt-bookmark-tag.py add AAAAAAAAAAA Python Research
python3 -B scripts/yt-bookmark-tag.py add AAAAAAAAAAA Python Research --apply
python3 -B scripts/yt-bookmark-tag.py remove AAAAAAAAAAA Research
python3 -B scripts/yt-bookmark-tag.py set AAAAAAAAAAA Reference
python3 -B scripts/yt-bookmark-tag.py rename Reference Research
python3 -B scripts/yt-bookmark-tag.py list AAAAAAAAAAA
python3 -B scripts/yt-bookmark-tag.py report
python3 -B scripts/yt-bookmark-tag.py prune
```

`remove` removes the requested tags; `set` replaces all personal tags for that video (with no tag arguments, it previews clearing them). `rename` previews replacement across matching library bookmarks. `list` and `report` are read-only. `prune` previews removal of catalog entries without matching bookmarks and refuses incomplete inventories; it never deletes bookmark HTML. None of these commands rebuilds the index automatically.

`list` is a catalog-only query after library-directory, catalog, and ID validation: it does not scan or read bookmarks, and can show a catalog record even when that bookmark is damaged or absent. `report` counts successfully inspected unique video IDs, includes hidden bookmarks, and identifies duplicate-ID groups separately. A complete, clean report returns `0`; partial inspection or duplicate-ID findings return `1`; invalid arguments, unavailable roots, or invalid catalogs return `2`.

Partial reports label their totals `Inspected unique IDs (partial)` and unmatched records `Unverified catalog entries`. Those records are not confirmed orphans: an unreadable file or uninspected subtree may contain a match. Only complete inspection uses `Catalog entries without matching bookmarks`. Confirmed orphan records alone do not make a complete report fail. Reporting never creates or modifies catalogs, backups, or publication staging files. All mutation previews and applied mutations still require a complete, validated inventory without duplicate IDs.

Tag ordering and normalization are described in [data formats](DATA_FORMATS.md#personal-tag-normalization); catalog validation and backup behavior are described in [safety](SAFETY.md#catalog-validation-and-publication).

Place an explicit library root before the operation to override configuration:

```bash
python3 -B scripts/yt-bookmark-tag.py '/path/to/library' add AAAAAAAAAAA Research --apply
```

For hyphen-prefixed IDs or option-like tags, put genuine options first, then `--`, then **all** positional arguments, including an explicit library root:

```bash
# Preview: --apply and --help below are literal tags
python3 -B scripts/yt-bookmark-tag.py -- '/path/to/library' add -AAAAAAAAAA Research --apply --help

# Save those tags: the genuine write flag is before the separator
python3 -B scripts/yt-bookmark-tag.py --apply -- '/path/to/library' add -AAAAAAAAAA Research --apply --help
```

Use the same ordering for `remove`, `set`, `list`, and option-like `rename` values. Do not append the write flag after `--`, where it is data. Keeping options before all positional arguments also avoids Python-version differences when argparse handles options mixed with a variable-length positional list. The existing configured-library shorthand remains supported for ordinary arguments; the examples above use an explicit library so no shorthand parsing change is needed.

`python3 -B scripts/yt-bookmark-tag.py prune` previews catalog entries without matching bookmarks; add `--apply` to remove them, backing up the catalog first. Both modes refuse with status `2` if the `bookmarks/` inventory is incomplete: unreadable files or directories, malformed metadata, unidentified bookmarks, or symbolic links. Generated `index.html` files are excluded. Resolve the reported paths before retrying; refusal leaves the catalog and backups untouched.

## Collection management

Collections group existing bookmarks by video ID, independently of personal tags. Use the configured library or put `--library DIR` after the operation to select an existing root containing a real `bookmarks/` directory. An empty bookmark directory is valid. Explicit selection bypasses local configuration.

```bash
./videx collection list
./videx collection show 'Field Notes'
./videx collection create --description 'Videos to revisit.' 'Field Notes'
./videx collection create --apply --description 'Videos to revisit.' 'Field Notes'
./videx collection add --apply 'Field Notes' AAAAAAAAAAA BBBBBBBBBBB
./videx collection rename --apply 'Field Notes' 'Field Research'
./videx collection describe --apply 'Field Research' 'A new description'
./videx collection describe --apply 'Field Research' ''
./videx collection remove --apply 'Field Research' AAAAAAAAAAA
./videx collection delete 'Field Research'
```

Only list and show are read-only operations. Every mutation previews until you supply `--apply`. List shows stored member counts, and show lists each member's availability and observed paths, including hidden bookmarks. An absent bookmark leaves its reference in the Collection. Restore the same video ID to make it available again, or remove that ID explicitly. Add and remove accept multiple IDs and refuse the whole change on invalid input. Deleting a Collection leaves member bookmarks, tags, and other Collections intact.

Targets accept a case-insensitive normalized name, `id:UUID`, or `name:TEXT` for explicit name selection. Place options before `--` to pass literal option-like inputs, for example `./videx collection add --apply -- 'Field Notes' -AAAAAAAAAA`. All operations accept `--json`. Use `./videx collection --help` or `./videx collection OPERATION --help` for usage. See [catalog reports](DATA_FORMATS.md#collection-json-report-version-1) and [Collection safety](SAFETY.md#collection-mutations-and-inspection) for the output and exit-status contracts.

Collection edits never rebuild the index automatically. Run `./videx index '/path/to/library'` for an explicitly selected library, then reload or reopen the saved index to see the changes. See [Browse Collections](#browse-collections).

## Lookup and library integrity audit

Direct lookup accepts either a video ID or a locally recognized URL:

```bash
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --id AAAAAAAAAAA
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --url 'https://youtu.be/AAAAAAAAAAA'
```

Lookup is read-only and never contacts an extractor. Its statuses are `0` found, `1` not found, `2` unsafe/invalid, and `3` URL needs extractor resolution. For leading-hyphen IDs, use `--id=VALUE` as explained above.

Use the standalone helper to inspect an existing library without modifying its files:

```bash
# Audit the project library
python3 -B scripts/yt-bookmark-find.py . --audit

# Audit a separate library
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --audit
```

The audit scans the selected library's `bookmarks/` tree and reports:

- **DUPLICATE:** more than one bookmark with the same video ID.
- **MISMATCH:** a video ID in the filename disagrees with its embedded metadata.
- **UNKNOWN:** no valid video ID can be identified.
- **UNREADABLE:** a file cannot be read or its metadata cannot be parsed.

It summarizes scanned files, unique video IDs, duplicate groups, extra copies and other findings. A clean audit exits with status `0`; detected issues return `1`; invalid arguments or a missing bookmarks directory return `2`. **The audit is read-only**: it does not rename, delete or repair files.

## Personal tags in lookup and audit reports

Add `--show-tags` to include personal tags from the selected library's `catalog.json`. Without this flag, lookup still prints only the matching path and never reads the catalog. The existing `yt-bookmark-tag.py report` command remains available.

```bash
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --id AAAAAAAAAAA --show-tags
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --id=-AAAAAAAAAA --show-tags --json
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --audit --show-tags
python3 -B scripts/yt-bookmark-find.py '/path/to/library' --audit --show-tags --json
```

Human lookup reports show `Bookmark:`, `Video ID:`, and `Tags: ["Python", "Research"]`. Human audit reports retain identity findings and append personal tags, grouping duplicate paths under their resolved ID. Conflicting identities are never assigned tags, and `hidden` does not filter results.

`--json` requires `--show-tags`. See [JSON report version 1](DATA_FORMATS.md#json-report-version-1) for fields, missing versus unavailable tags, and catalog status; see [report failures](SAFETY.md#lookup-audit-and-report-statuses) for exit behavior.

## Offline bookmark upgrades

```bash
# Inspect possible upgrades without changing files
python3 -B scripts/yt-bookmark-upgrade.py

# Apply upgrades; originals are copied to a timestamped sibling backup folder
python3 -B scripts/yt-bookmark-upgrade.py --apply

# Rebuild the index after upgrading
python3 -B scripts/yt-bookmark-index.py
```

The upgrade tool works entirely offline. Inspect its dry-run output before applying changes and retain backups until you verify the results. Index generation requires at least one valid bookmark.

An explicit directory overrides configuration:

```bash
python3 -B scripts/yt-bookmark-upgrade.py '/path/to/library'
python3 -B scripts/yt-bookmark-upgrade.py '/path/to/library' --apply
```

See [upgrade failure handling](SAFETY.md#offline-upgrades) before applying a mixed-result preview.

## Moving an existing library

1. Back up your current library before moving anything. Quit any programs editing the files.
2. Create a directory for your external library, such as `~/Documents/Video Bookmarks/`. Move **the entire `bookmarks/` directory**, `catalog.json`, any `catalog.json.bak-*` files, and `index.html` from the project root into it. Do not move scripts or Git files.
3. Update `config.local.json` to point to your chosen library root.
4. Verify `python3 -B scripts/yt-bookmark-find.py --audit`, then regenerate with `python3 -B scripts/yt-bookmark-index.py`. Open the new `index.html` and test an existing bookmark's Back to Library link.
5. Only after verification, remove obsolete project-local generated index or bookmark copies. Keep your backup until you are satisfied. Do not keep two active libraries with the same bookmarks.

## Troubleshooting

| Symptom | Action |
|---|---|
| Extraction stops working | Check `yt-dlp --version`, `python3 --version`, and `bash --version`; update yt-dlp using its official installation instructions or your package manager. |
| Files appear in the wrong location | Check the explicit command path and `config.local.json`; paths select the library root. Invalid configuration fails rather than silently falling back. |
| `videx open` reports a missing index | Verify the selected library root. To build the snapshot, run `./videx index` or `./videx index '/path/to/library'` for an explicit library. |
| `videx open` reports a launcher or desktop failure | Run it from a graphical desktop with `open` (macOS) or `xdg-open` (Linux) available on `PATH`. You can also open the reported local index file manually. |
| Tags or search behavior look stale | Rebuild `index.html`; it contains a snapshot of search code and catalog tags. |
| A bookmark is absent from the index | Check `hidden`, run the read-only audit, and inspect index warnings for malformed pages. |
| A catalog operation refuses to proceed | Resolve reported catalog or inventory errors before retrying. Do not treat partial-report unmatched entries as confirmed orphans. |
| An old index remains after failure | Inspect the command's error/status. Failed rebuilds deliberately retain the previous index, which may still contain now-hidden entries. |
| Back to Library fails after moving files | Preserve the indexed archive tree and move the complete library together; verify the link after rebuilding. |

Use `--help` on capture, import, index, find, tag, upgrade, or the lower-level generator for their supported options. For index and audit commands, missing bookmark directories are not created automatically.
