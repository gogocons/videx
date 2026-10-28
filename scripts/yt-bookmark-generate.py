#!/usr/bin/env python3
"""Build a self-contained offline YouTube bookmark from yt-dlp JSON and thumbnail."""
import argparse
import base64
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import sys
sys.dont_write_bytecode = True
from importlib.util import spec_from_file_location, module_from_spec
from urllib.parse import quote, urlsplit


def load_curator():
    spec = spec_from_file_location('bookmark_metadata', Path(__file__).with_name('yt-bookmark-metadata.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.curate


curate = load_curator()


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def duration(seconds):
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return 'Unknown'
    n = int(seconds)
    h, rem = divmod(n, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m}:{s:02}'


def upload_date(value):
    v = str(value or '')
    return f'{v[:4]}-{v[4:6]}-{v[6:]}' if re.fullmatch(r'\d{8}', v) else 'Unknown'


def safe_url(url):
    try:
        parts = urlsplit(str(url))
        if parts.scheme in ('http', 'https') and parts.netloc:
            return str(url)
    except ValueError:
        pass
    return ''


def timestamp_links(text, video_url):
    """Render description as plain text with safe clickable URLs and timestamps."""
    pattern = re.compile(r'https?://[^\s<>"\']+|(?<!\d)(?:\d{1,2}:)?\d{1,2}:\d{2}(?!\d)')
    result = []
    pos = 0
    for match in pattern.finditer(text):
        result.append(esc(text[pos:match.start()]))
        token = match.group()
        if token.startswith(('http://', 'https://')):
            clean = token.rstrip('.,);]')
            url = safe_url(clean)
            if url:
                result.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(clean)}</a>')
            else:
                result.append(esc(clean))
            result.append(esc(token[len(clean):]))
        elif video_url:
            pieces = [int(x) for x in token.split(':')]
            seconds = sum(v * 60 ** i for i, v in enumerate(reversed(pieces)))
            sep = '&' if '?' in video_url else '?'
            result.append(f'<a href="{esc(video_url + sep + "t=" + str(seconds))}" target="_blank" rel="noopener noreferrer">{esc(token)}</a>')
        else:
            result.append(esc(token))
        pos = match.end()
    result.append(esc(text[pos:]))
    return ''.join(result)


def make_document(metadata, image_uri, back_href=None):
    metadata = curate(metadata)
    title = str(metadata.get('title') or 'Unknown Title')
    uploader = str(metadata.get('uploader') or metadata.get('channel') or 'Unknown Channel')
    video_id = str(metadata.get('id') or '')
    url = safe_url(metadata.get('webpage_url') or (f'https://www.youtube.com/watch?v={quote(video_id)}' if video_id else ''))
    channel_url = safe_url(metadata.get('channel_url') or metadata.get('uploader_url') or '')
    description = str(metadata.get('description') or '')
    chapters = metadata.get('chapters') or []
    chapter_rows = []
    if isinstance(chapters, list):
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            start = ch.get('start_time')
            label = str(ch.get('title') or 'Untitled chapter')
            if isinstance(start, (int, float)) and start >= 0:
                sep = '&' if '?' in url else '?'
                href = url + sep + 't=' + str(int(start)) if url else ''
                stamp = duration(start)
                chapter_rows.append(f'<li><a href="{esc(href)}" target="_blank" rel="noopener noreferrer">{esc(stamp)} — {esc(label)}</a></li>' if href else f'<li>{esc(stamp)} — {esc(label)}</li>')
    fields = [('Duration', duration(metadata.get('duration'))), ('Uploaded', upload_date(metadata.get('upload_date'))),
              ('Video ID', video_id or 'Unknown'), ('Channel', uploader),
              ('Views at capture', f"{metadata['view_count']:,}" if isinstance(metadata.get('view_count'), int) else 'Unavailable'),
              ('Likes at capture', f"{metadata['like_count']:,}" if isinstance(metadata.get('like_count'), int) else 'Unavailable')]
    info = ''.join(f'<div class="stat"><span>{esc(k)}</span><strong>{esc(v)}</strong></div>' for k,v in fields)
    raw = json.dumps(metadata, ensure_ascii=False, indent=2).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    library = f'<a class="secondary" href="{esc(back_href)}">← Back to library</a>' if back_href else ''
    channel = f'<a href="{esc(channel_url)}" target="_blank" rel="noopener noreferrer">{esc(uploader)}</a>' if channel_url else esc(uploader)
    watch = f'<a class="button" href="{esc(url)}" target="_blank" rel="noopener noreferrer">▶ Watch on YouTube ↗</a>' if url else ''
    chapter_section = f'<section class="panel"><h2>Chapters</h2><ol class="chapters">{"".join(chapter_rows)}</ol></section>' if chapter_rows else ''
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark"><title>{esc(title)} — Videx</title>
<style>
:root{{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#e9edf4;background:#101217}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1060px;margin:0 auto;padding:24px 22px 70px}}
a{{color:#a9c5ff}}.top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:20px}}.brand{{display:flex;align-items:center;gap:12px;font-weight:750;letter-spacing:-.02em;font-size:1.2rem}}.play{{display:inline-flex;align-items:center;justify-content:center;width:39px;height:39px;background:#d93636;color:white;border-radius:10px;font-weight:900;font-size:1.1rem;flex-shrink:0}}.secondary{{font-size:.9rem;text-decoration:none}}
.hero{{width:100%;aspect-ratio:16/9;object-fit:contain;background:#20242c;border-radius:14px;display:block}}h1{{font-size:clamp(1.5rem,3.2vw,2.35rem);line-height:1.2;letter-spacing:-.03em;margin:24px 0 12px}}
.byline{{color:#aab3c2;margin:0 0 18px}}.byline a{{text-decoration:none}}.actions{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:25px}}.button,button{{background:#d93636;color:white;border:0;border-radius:9px;padding:11px 15px;font:inherit;font-weight:650;text-decoration:none;cursor:pointer}}button{{background:#252c37;color:#e9edf4}}
.panel{{background:#1b2029;border:1px solid #303745;border-radius:13px;padding:20px;margin-top:20px}}h2{{font-size:1.12rem;margin:0 0 16px}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:18px}}.stat{{min-width:0;display:flex;flex-direction:column;gap:4px}}.stat span{{font-size:.8rem;color:#9ca5b5}}.stat strong{{overflow-wrap:anywhere;font-size:.94rem}}
.description{{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;font-size:.95rem}}.chapters{{list-style:none;margin:0;padding:0}}.chapters li{{padding:8px 0;border-bottom:1px solid #303745}}.chapters a{{text-decoration:none}}details summary{{cursor:pointer;font-weight:650}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.78rem;color:#bfc9da;max-height:400px;overflow:auto}}.foot{{color:#7f8a9b;font-size:.8rem;margin-top:26px}}
</style></head><body><main>
<nav class="top"><div class="brand"><span class="play" aria-hidden="true">▶</span> Videx</div>{library}</nav>
<img class="hero" src="{esc(image_uri)}" alt="Video thumbnail">
<h1>{esc(title)}</h1><p class="byline">{channel}</p>
<div class="actions">{watch}<button type="button" id="copy-url">Copy video URL</button></div>
<section class="panel"><h2>Video details</h2><div class="stats">{info}</div></section>
<section class="panel"><h2>Description</h2><div class="description">{timestamp_links(description,url)}</div></section>
{chapter_section}
<section class="panel"><details><summary>Archived metadata (curated JSON)</summary><pre id="raw-json"></pre></details></section>
<p class="foot">This is an offline archival snapshot. Counts and metadata reflect the time of capture, not live YouTube values.</p>
<script type="application/json" id="youtube-metadata">{raw}</script>
<script>(()=>{{const b=document.getElementById('copy-url');b.addEventListener('click',()=>{{const u={json.dumps(url)};if(navigator.clipboard&&window.isSecureContext){{navigator.clipboard.writeText(u).then(()=>{{b.textContent='Copied!'}}).catch(()=>prompt('Copy URL:',u))}}else{{prompt('Copy URL:',u)}}}});document.querySelector('details').addEventListener('toggle',e=>{{if(e.target.open)document.getElementById('raw-json').textContent=JSON.stringify(JSON.parse(document.getElementById('youtube-metadata').textContent),null,2);else document.getElementById('raw-json').textContent=''}})}})();</script>
</main></body></html>'''


def write_atomic(output, content):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=output.parent, prefix='.bookmark-', suffix='.tmp', delete=False) as f:
            name = f.name
            f.write(content)
        os.replace(name, output)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('info_json', type=Path)
    p.add_argument('thumbnail', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--back-link', help='Relative path to collection index.html')
    args = p.parse_args()
    metadata = json.loads(args.info_json.read_text(encoding='utf-8'))
    mime = mimetypes.guess_type(args.thumbnail.name)[0] or 'application/octet-stream'
    image_uri = f'data:{mime};base64,' + base64.b64encode(args.thumbnail.read_bytes()).decode('ascii')
    write_atomic(args.output, make_document(metadata, image_uri, args.back_link))
    print(f'Generated: {args.output}')


if __name__ == '__main__':
    main()
