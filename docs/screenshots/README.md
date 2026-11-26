# Documentation screenshots

[Back to README](../../README.md) · [Development](../DEVELOPMENT.md)

`library.png` and `bookmark.png` are the current documentation screenshots,
representing the refreshed Videx demo and current interface, including selective
personal tags in the library. They have passed technical and privacy verification
and are approved for documentation use. The original historical screenshots remain
the reference for reproducing the established rounded-corner transparency treatment.
Preparation creates separate review candidates; it never approves or replaces the
current screenshots. These screenshots are not pixel-exact current UI tests.

## Firefox capture recipe

1. Prepare a disposable synthetic library with the [offline generator](../USAGE.md#lower-level-offline-generation)
   and index command, then open its `index.html` or an individual bookmark in Firefox.
   The repository does not ship a demo library; `demo/` is an optional Git-ignored
   local directory, not a prerequisite. Do not use a personal bookmark library.
2. Enable **Tools → Browser Tools → Responsive Design Mode** and select
   **Laptop with HiDPI screen**. Verify **1440 × 900 CSS pixels** and **DPR 2**;
   preset names and saved settings alone are insufficient evidence.
3. Set page zoom to **100%**. Keep touch simulation off and use the desktop user
   agent. In the page's Web Console, verify
   `[window.innerWidth, window.innerHeight, window.devicePixelRatio]` returns
   `[1440, 900, 2]`. RDM preview scaling is separate from page zoom.
4. Use the page's dark presentation. Reset scroll to the top and clear search.
   Open **Filters** and select **All Bookmarks** if the Collection selector is
   present. Select **All Channels** and **All Personal Tags**, then close Filters
   and set the sort to **Newest**.
   Wait for thumbnails and fonts to render. Move the pointer away from cards so
   hover scaling is inactive; avoid focused controls and open menus.
5. Use RDM's screenshot control to capture **viewport page content only**.
   Do not capture browser controls or choose full-page capture. The detail page
   deliberately continues below the viewport.
6. Record Firefox version, platform, CSS viewport, DPR, zoom, source bookmark ID,
   capture date, library/index revision and visible state in review notes.
   Avoid personal paths or machine names in notes intended for publication.
7. Expect approximately **2880 × 1800 physical pixels** at DPR 2, but inspect the
   actual saved file dimensions. Do not assume them from the preset. A different
   capture mode or DPR override can change the output. Investigate discrepancies
   before processing; never stretch or crop to compensate.
8. Process the capture into a **1440 × 900** review candidate, then compare it
   with the approved baseline. Review layout, fonts, thumbnails, text, tags,
   clipping, colors and transparent corners. Check visible pixels for personal
   information: metadata removal cannot remove information shown in the image.

A recorded inspection of the Firefox 160.0 bundled preset found
1440 × 900, DPR 2, no touch, and no user-agent override. Historical raw captures
were not found in the repository; their dimensions and zoom cannot be proven.
No browser automation is introduced by this workflow.

A local demo may use the Git-ignored `demo/` directory or a separate temporary
library. Screenshot capture does not rebuild or change that library. Rebuild its
index separately when preparing the desired current state. Rebuilding inserts a timestamp; browser/font versions
and thumbnail encoders also affect repeatability. Record these rather than
expecting identical pixels across platforms or versions.

Demo videos and channels are illustrative examples used to demonstrate Videx.
Their inclusion is not an endorsement, affiliation, or requirement. Any suitable
bookmarks can be used. Metadata reflects capture time, and linked videos may
change or become unavailable.

## Prepare candidates offline

Requirements: Python 3.8+ and Pillow with `Image.Resampling.LANCZOS` support
(Pillow 9.1+). Pillow is an optional documentation-tool dependency, not a mandatory
Videx runtime dependency. No packages, browsers, or other tools are downloaded or
installed by the command. Missing Pillow produces an actionable error.

Use an existing, separate review directory with no symlink path components.
On macOS `/tmp` is commonly a symlink; use `/private/tmp` when choosing that
location. These examples run from the repository root with manually captured
inputs in `/private/tmp/videx-captures/`:

```bash
mkdir /private/tmp/videx-screenshot-review

# Read-only: decode, resize and validate in memory; publish nothing.
python3 -B docs/screenshots/prepare-screenshots.py \
  /private/tmp/videx-captures/library.png \
  /private/tmp/videx-captures/bookmark.png \
  --output-dir /private/tmp/videx-screenshot-review

# Explicitly write new candidates after inspecting the dry-run report.
python3 -B docs/screenshots/prepare-screenshots.py \
  /private/tmp/videx-captures/library.png \
  /private/tmp/videx-captures/bookmark.png \
  --output-dir /private/tmp/videx-screenshot-review --apply
```

Output names use the input basename with a lowercase `.png` extension. Inputs
remain unchanged. Existing destinations, duplicate output names, input aliases,
symlink inputs/destinations, and the approved screenshots directory are refused.
Use a new review directory for another batch. Directory creation is a separate,
explicit action; dry-run does not create directories or temporary files.

Only exact **8:5** inputs at **1440 × 900 or larger** are accepted. No cropping,
stretching or upscaling occurs. Larger inputs use Pillow LANCZOS in the image's
RGB/RGBA representation; no linear-light conversion is performed. After resizing,
the tool applies the exact rounded-corner treatment measured from both approved
screenshots in commit `8e2fc0854c847ce743f51153c522c92caabd9a39`. Both originals
have the same symmetric 12 × 12 corner footprint (nominal 12-pixel radius).
The measured alpha samples are embedded in the utility rather than regenerating
an approximate curve with a drawing API. Across all four corners, 92 pixels are
fully transparent and 92 are partially transparent on an otherwise opaque input.

RGB inputs become RGBA. Existing RGBA alpha is capped by the corner mask, so
existing transparency is never made more opaque; repeated preparation is idempotent
at native size. Within the mask, RGB follows the original 8-bit
premultiply/unpremultiply rounding; fully transparent corner pixels have zero RGB.
Every pixel outside the 184 affected corner positions remains unchanged from the
decoded native-size image or the LANCZOS-resized image, including existing alpha.
No Git checkout or reference image is needed at runtime. Final candidates still
require explicit visual approval before replacing tracked screenshots.

Input scope is static, noninterlaced, 8-bit RGB/RGBA PNG, at most 64 MiB and
40 million pixels. Palette, grayscale, 16-bit, animated and `tRNS` images are
unsupported. Prepared outputs are 1440 × 900 RGBA and must be at most **2 MiB**.
The output validator continues to accept sanitized RGB/RGBA images.
This size cap is a project policy, not a hosting-service requirement.

## Privacy and color policy

The independent PNG validator checks signature, chunk bounds/types/order,
checksums, singletons, consecutive IDATs, complete bounded decompression,
scanline lengths/filters, and absence of trailing file or compressed-stream data.
Pillow additionally decodes the cleaned input and final output. No social-preview
artwork generator is imported.

Only these chunks may survive in an output:

- `IHDR`, `IDAT`, `IEND`.
- `sRGB`, with standard rendering intent 0–3.
- Optional reviewed `gAMA` value 45455 and `cHRM` sRGB primaries/white point:
  31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000.

EXIF, XMP, text (`tEXt`, `zTXt`, `iTXt`), timestamps, resolution hints and unknown
ancillary metadata are removed before image decoding. The output is constructed
from fresh pixels and explicit reviewed color chunks. Metadata bytes are never
printed. Filesystem attributes are not copied from the input.

ICC profiles (`iCCP`), HDR color chunks (`cICP`, `mDCV`, `cLLI`), significant-bit
information (`sBIT`), and incompatible gamma/chromaticities are refused. No ICC
profiles are currently approved. Convert unusual color spaces to reviewed sRGB
in a separate, explicitly reviewed workflow. Untagged inputs are interpreted as
sRGB and receive `sRGB` intent 0; compatible gamma/chromaticities are retained.
This assumption must be appropriate for the capture source.

## Publication and failure behavior

The entire batch receives destination/alias preflight checks. Each image is then
processed separately. With `--apply`, sanitized bytes are written to a temporary
file in the pinned review directory, flushed, re-read and validated, then
published using an atomic hard link that cannot replace an existing path.
Temporary files are removed after success or ordinary failure. If completion of
the current publication fails after linking, that current output is rolled back.
If rollback itself fails, the error identifies the retained current output and
includes it in the published count. Cleanup errors identify temporary artifacts
that may need manual recovery.

A later batch failure **retains earlier successful candidates** and exits nonzero
with validated/published counts. Batch publication is not transactional. A process
crash, storage failure, concurrent hostile directory modification or cleanup
permission failure can leave temporary files or require manual recovery. Use a
review directory you control. Filesystems must support hard links and POSIX
directory-relative operations; unsupported publication fails without a fallback
that could overwrite files. PNG encoding can vary between Pillow versions.

The tool does not promote candidates to approved baselines. Replacement requires
separate approval and a reviewed repository diff.

## Tests

```bash
python3 -B -m unittest discover -s tests -p test_screenshot_prepare.py -v
python3 -B -m unittest discover -s tests -v
```

Tests use synthetic images and temporary directories, without browser launches,
network access, demo regeneration, or writes to approved screenshots. Image
processing tests skip when Pillow is unavailable; PNG policy tests still run.
