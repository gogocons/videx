# Videx social-preview artwork

This directory maintains the image used when sharing the Videx GitHub repository.
It is optional, offline development tooling, independent of bookmark capture,
index building, and Videx's runtime dependencies.

## Files and source of truth

| File | Purpose |
| --- | --- |
| `generate-social-preview.py` | Authoritative artwork template and generation entry point |
| `svg-to-png.swift` | Internal native macOS renderer, invoked by Python |
| `videx-social-preview.svg` | Generated vector artwork for inspection and reuse |
| `videx-social-preview.png` | Approved 1280 × 640 upload asset and visual baseline |
| `README.md` | Supported workflow and review instructions |

Edit the Python template to change the artwork; direct edits to the SVG will be
replaced. Preserve the existing layout, typography, play icon, and Videx colors
unless an artwork change has been approved. The PNG is deliberately retained so
contributors can use the approved image without installing rendering tools.

## Generate

From the repository root on macOS:

```bash
python3 -B docs/social-preview/generate-social-preview.py
```

The command generates SVG, invokes the Swift renderer, normalizes the PNG to
**1280 × 640**, validates both outputs, and compares the rendering with the
approved PNG. No manual conversion commands are needed.

Default outputs always go beside the script, regardless of the working directory.
From another directory, invoke the same script using its absolute path.

Requirements for PNG generation:

- Python 3.8+ (standard library only; validated here with Python 3.15.2).
- macOS with an installed, matching Swift toolchain and Apple SDK, including
  AppKit and WebKit; the Apple development tools must already be configured.
- `sips`, included with macOS.
- A macOS session in which WebKit can render. Headless CI has not been qualified.

Swift may need access to its normal compiler module cache. A restricted sandbox
can block that cache or WebKit; use an appropriately permitted local session.
Tool failures include diagnostics. No tool or package is downloaded automatically.
No Pillow, graphics editor, Python package, or new Videx dependency is required.

## SVG-only mode and Linux

On macOS or Linux:

```bash
python3 -B docs/social-preview/generate-social-preview.py --svg-only
```

This validates and publishes only the SVG. It does not look for or invoke Swift,
AppKit, WebKit, or `sips`, and leaves the PNG unchanged. Linux PNG export is not
supported; the full command exits with a suggestion to use `--svg-only`.
Linux behavior has synthetic platform coverage; a real Linux run is still needed
to qualify that environment.

The existing output option is supported:

```bash
python3 -B docs/social-preview/generate-social-preview.py --svg-only --output /tmp/videx.svg
```

An explicit relative `--output` is relative to the working directory. Its parent
must exist and the filename must end in `.svg`. Full generation writes the PNG
with the same stem and still compares against the approved PNG beside the script.
Output files must not be symbolic links.

## Validation and visual review

Rendering and resizing use disposable temporary storage. Tool errors, missing
tools, timeouts, invalid images, and visual differences return nonzero before
publishing either final output. Publication stages files beside their destinations
and rolls back ordinary partial-publication failures. This is not a guarantee
against a process crash or storage failure during multi-file publication.

The Swift helper has a 30-second rendering deadline; Python limits each tool to
90 seconds, including Swift startup/compilation, and terminates its process group
on timeout.

Validation checks:

- The exact approved SVG structure and content, including canvas and view box.
- PNG signature, chunk structure and checksums, compressed image data, row filters,
  and supported 8-bit RGB/RGBA noninterlaced encoding.
- Exact 1280 × 640 pixels (2:1) and a PNG smaller than 1 MB.
- Decoded pixels and color metadata against the approved repository PNG.

PNG privacy uses an allowlist: `IHDR`, consecutive `IDAT`, `IEND`, and reviewed
color chunks only. `gAMA` must be 45455; `cHRM` must match the approved sRGB white
point and primaries. Optional `sRGB` signaling supports standard rendering intents
0–3 and must be consistent with those values. Color chunks must precede image data.
Arbitrary `iCCP` profiles fail with a review diagnostic; none are approved, and a
profile is never silently discarded.

Text chunks (`tEXt`, `zTXt`, `iTXt`), all text-labeled XMP, EXIF, timestamps,
resolution hints (`pHYs`), private chunks, and other unapproved ancillary metadata
are removed. Invalid structure, ordering, CRCs, prohibited duplicates, unsupported
image features, or trailing data are rejected. Metadata cleanup preserves every
compressed IDAT payload and decoded pixel. File hashes can change when metadata
is removed even though the artwork remains identical.

SVG privacy accepts only the reviewed elements, exact attributes and values,
visible text, font stack, and six descriptive comments. Metadata, scripts, images,
external/CSS resource references, declarations, processing instructions, extra
namespaces, local paths, and unapproved content are rejected. Intentional artwork
changes require review of both the Python template and the independent validation
policy; direct SVG edits are not authoritative.

The baseline and native renderer output are checked before resizing, with the
resized candidate and final PNG checked again before publication. Generated SVG
is validated before rendering. Privacy failures return nonzero, preserve existing
outputs, and retain no unsanitized review files. Raw intermediate files remain
in disposable temporary storage. These policies govern the unified Python command;
the Swift file is an internal rendering helper.

If rendering matches, the approved PNG's image encoding is retained, with only
that metadata cleanup. If pixels or color metadata differ, approved outputs are
preserved and the command reports the differing pixel count, largest channel
change, and color-metadata status. It saves `candidate.svg`, `candidate.png`, and
`approved.png` in a separate system temporary review directory and prints its path.
Both PNGs are sanitized and the SVG is validated before any review directory is
created. These privacy-safe review files are retained outside this directory.

Inspect candidates beside the baseline, including text, spacing, icon edges,
colors, and antialiasing. Font and WebKit changes can affect rendering between
macOS environments. Pixel-identical rendering across platforms is not promised.
There is no automatic acceptance flag: intentional design or rendering changes
require explicit visual approval before manually replacing the baseline PNG and
corresponding generated SVG. Rerun the command afterward and review the diff.
Remove temporary review directories when finished.

## Upload the approved image

After local validation and visual approval, open the repository on GitHub and go
to **Settings → General → Social preview → Edit → Upload an image**. Select
`videx-social-preview.png` and save the change. Appropriate repository permissions
are required. The generator does not upload, publish, or change repository settings.

The asset uses the documented 1280 × 640 recommended size, exceeds the documented
640 × 320 minimum, and leaves generous margins around important content. Confirm
GitHub's current upload requirements when uploading; generation itself stays offline.

## Offline regression checks

From the repository root:

```bash
python3 -B -m unittest discover -s tests -p test_social_preview.py -v
```

These tests use synthetic PNGs, disposable directories, and simulated tools. They
cover deterministic SVG output, working directories, missing tools, timeouts,
failed rendering/normalization, invalid PNGs, metadata cleanup, visual-review
protection, and publication rollback without requiring native rendering tools.
Native rendering and visual comparison should also be checked on the maintainer's
Mac when changing the renderer or artwork.
