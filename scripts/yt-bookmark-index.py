#!/usr/bin/env python3
"""Generate a standalone, offline index.html for yt-bookmark HTML archives."""
import argparse
import base64
from datetime import datetime, timezone
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None


class BookmarkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_metadata = False
        self.metadata_chunks = []
        self.thumbnail = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('id') == 'youtube-metadata':
            self.in_metadata = True
        if tag == 'img' and self.thumbnail is None:
            src = attrs.get('src', '')
            if src.startswith('data:image/') and ';base64,' in src:
                self.thumbnail = src

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_metadata:
            self.in_metadata = False

    def handle_data(self, data):
        if self.in_metadata:
            self.metadata_chunks.append(data)


def thumbnail_preview(data_uri, width):
    if not data_uri:
        return ''
    if Image is None:
        return data_uri
    try:
        prefix, encoded = data_uri.split(',', 1)
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as original:
            img = ImageOps.exif_transpose(original)
            img.thumbnail((width, round(width * 9 / 16)), Image.Resampling.LANCZOS)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            out = io.BytesIO()
            img.save(out, format='WEBP', quality=76, method=4)
        return 'data:image/webp;base64,' + base64.b64encode(out.getvalue()).decode('ascii')
    except Exception as exc:
        print(f'  Warning: thumbnail conversion failed ({exc}); using original', file=sys.stderr)
        return data_uri


def parse_bookmark(path, root, width):
    parser = BookmarkParser()
    parser.feed(path.read_text(encoding='utf-8'))
    if not parser.metadata_chunks:
        raise ValueError('no embedded youtube-metadata JSON')
    metadata = json.loads(''.join(parser.metadata_chunks))
    if not isinstance(metadata, dict):
        raise ValueError('metadata is not an object')
    video_id = str(metadata.get('id') or '')
    duration = metadata.get('duration')
    if not isinstance(duration, (int, float)) or duration < 0:
        duration = None
    uploaded = metadata.get('upload_date') or ''
    if not re.fullmatch(r'\d{8}', str(uploaded)):
        uploaded = ''
    relative = path.relative_to(root).as_posix()
    # URL encode each path segment, preserving the slashes between directories.
    href = quote(root.name, safe='') + '/' + '/'.join(quote(segment, safe='') for segment in relative.split('/'))
    return {
        'title': str(metadata.get('title') or path.stem),
        'channel': str(metadata.get('uploader') or metadata.get('channel') or path.parent.name),
        'description': str(metadata.get('description') or ''),
        'url': str(metadata.get('webpage_url') or (f'https://www.youtube.com/watch?v={video_id}' if video_id else '')),
        'href': href,
        'id': video_id,
        'duration': duration,
        'uploadDate': str(uploaded),
        'thumbnail': thumbnail_preview(parser.thumbnail, width),
    }


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Videx — Video Reference Library</title>
<style>
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#e9edf4;background:#101217}
*{box-sizing:border-box}body{margin:0}main{max-width:1480px;margin:auto;padding:28px 24px 70px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:25px}
h1{font-size:clamp(1.5rem,3vw,2.2rem);margin:0;letter-spacing:-.035em}.brand{display:flex;align-items:center;gap:12px}.play{display:inline-flex;align-items:center;justify-content:center;width:39px;height:39px;background:#d93636;color:white;border-radius:10px;font-weight:900;font-size:1.1rem;flex-shrink:0}
.sub,.muted{color:#9ca5b5}.sub{font-size:.9rem;margin-top:5px}.stats{font-size:.88rem;color:#cbd2df}
.toolbar{display:grid;grid-template-columns:minmax(200px,1fr) minmax(140px,240px) minmax(145px,200px);gap:12px;margin-bottom:17px}
input,select{width:100%;background:#1d222c;color:#f3f5fa;border:1px solid #343b49;border-radius:10px;padding:13px 14px;font:inherit;outline-offset:3px}
input:focus,select:focus{border-color:#90a9e8}.count{color:#9ca5b5;font-size:.88rem;margin:10px 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:25px 19px}
.card{min-width:0}.thumb-link{display:block;position:relative;aspect-ratio:16/9;background:#232832;border-radius:12px;overflow:hidden;text-decoration:none}
.thumb-link img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .2s}.thumb-link:hover img{transform:scale(1.035)}
.duration{position:absolute;right:9px;bottom:9px;background:#07090bdf;color:#fff;padding:3px 6px;border-radius:4px;font-size:.77rem;font-weight:600}
.details{padding:11px 2px}.title{font-size:.98rem;line-height:1.42;font-weight:650;margin:0 0 7px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
a{color:inherit}.title a{text-decoration:none}.title a:hover{text-decoration:underline}.channel{color:#aab3c2;font-size:.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meta{color:#818c9e;font-size:.78rem;margin-top:5px}.actions{display:flex;gap:15px;margin-top:10px;font-size:.79rem}.actions a{color:#a9c5ff;text-decoration:none}.actions a:hover{text-decoration:underline}
.empty{text-align:center;color:#aab3c2;padding:70px 10px}.footer{color:#778295;font-size:.8rem;margin-top:42px;text-align:center}
@media(max-width:690px){main{padding:20px 14px 50px}.toolbar{grid-template-columns:1fr 1fr}.toolbar input{grid-column:1/-1}.grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:17px 12px}.title{font-size:.9rem}.stats{width:100%}}
</style>
</head>
<body><main>
<header><div class="brand"><span class="play" aria-hidden="true">▶</span><div><h1>Videx</h1><div class="sub">Your offline video reference library</div></div></div><div class="stats" id="stats"></div></header>
<div class="toolbar">
<input id="search" type="search" placeholder="Search titles, channels, descriptions…" aria-label="Search bookmarks" autocomplete="off">
<select id="channel" aria-label="Filter channel"><option value="">All channels</option></select>
<select id="sort" aria-label="Sort bookmarks"><option value="upload-desc">Newest upload</option><option value="upload-asc">Oldest upload</option><option value="title-asc">Title A–Z</option><option value="title-desc">Title Z–A</option><option value="channel-asc">Channel A–Z</option></select>
</div><div class="count" id="count" aria-live="polite"></div><section class="grid" id="grid" aria-label="Bookmarks"></section><div class="empty" id="empty" hidden>No matching bookmarks found.</div><div class="footer" id="footer"></div>
</main>
<script id="bookmark-data" type="application/json">__BOOKMARK_DATA__</script>
<script>
(()=>{'use strict';
const data=JSON.parse(document.getElementById('bookmark-data').textContent);
const items=data.items;
const search=document.getElementById('search'),channel=document.getElementById('channel'),sort=document.getElementById('sort'),grid=document.getElementById('grid'),count=document.getElementById('count'),empty=document.getElementById('empty');
const channels=[...new Set(items.map(x=>x.channel))].sort((a,b)=>a.localeCompare(b));
for(const name of channels){const opt=document.createElement('option');opt.value=name;opt.textContent=name;channel.append(opt)}
document.getElementById('stats').textContent=`${items.length.toLocaleString()} bookmarks · ${channels.length.toLocaleString()} channels`;
document.getElementById('footer').textContent=`Index generated ${data.generatedAt} · Offline archive`;
function duration(seconds){if(seconds==null)return '';const n=Math.floor(seconds),h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`}
function date(x){if(!x)return '';const y=String(x);return `${y.slice(0,4)}-${y.slice(4,6)}-${y.slice(6,8)}`}
function node(tag,cls,txt){const el=document.createElement(tag);if(cls)el.className=cls;if(txt!==undefined)el.textContent=txt;return el}
function link(label,href){const a=node('a','',label);a.href=href;return a}
function makeCard(x){const card=node('article','card');const imageLink=link('',x.href);imageLink.className='thumb-link';imageLink.setAttribute('aria-label',`Open archived bookmark: ${x.title}`);if(x.thumbnail){const img=node('img');img.src=x.thumbnail;img.alt='';img.loading='lazy';imageLink.append(img)}else{imageLink.append(node('span','muted','No thumbnail'))}if(x.duration!=null)imageLink.append(node('span','duration',duration(x.duration)));card.append(imageLink);
const details=node('div','details');const title=node('h2','title');title.append(link(x.title,x.href));details.append(title,node('div','channel',x.channel));const dateText=x.uploadDate?`Uploaded ${date(x.uploadDate)}`:'Upload date unavailable';details.append(node('div','meta',dateText));const actions=node('div','actions');actions.append(link('Open bookmark',x.href));if(/^https?:\/\//i.test(x.url)){const external=link('YouTube ↗',x.url);external.target='_blank';external.rel='noopener noreferrer';actions.append(external)}details.append(actions);card.append(details);return card}
function render(){const q=search.value.trim().toLocaleLowerCase(),c=channel.value;let matches=items.filter(x=>(!c||x.channel===c)&&(!q||[x.title,x.channel,x.description,x.id].some(v=>v.toLocaleLowerCase().includes(q))));const s=sort.value;
matches.sort((a,b)=>{switch(s){case 'upload-desc':return b.uploadDate.localeCompare(a.uploadDate);case 'upload-asc':return a.uploadDate.localeCompare(b.uploadDate);case 'title-asc':return a.title.localeCompare(b.title);case 'title-desc':return b.title.localeCompare(a.title);case 'channel-asc':return a.channel.localeCompare(b.channel);default:return b.uploadDate.localeCompare(a.uploadDate)}});
count.textContent=`Showing ${matches.length.toLocaleString()} of ${items.length.toLocaleString()} bookmarks`;empty.hidden=matches.length!==0;grid.replaceChildren();const fragment=document.createDocumentFragment();for(const item of matches)fragment.append(makeCard(item));grid.append(fragment)}
search.addEventListener('input',render);channel.addEventListener('change',render);sort.addEventListener('change',render);render();
})();
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default=str(Path(__file__).resolve().parent.parent / 'bookmarks'), help='Bookmark root directory (default: project bookmarks/ directory)')
    parser.add_argument('--thumbnail-width', type=int, default=360, help='Embedded preview width in pixels (default: 360)')
    args = parser.parse_args()
    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        parser.error(f'Not a directory: {root}')
    if args.thumbnail_width < 64 or args.thumbnail_width > 1920:
        parser.error('--thumbnail-width must be between 64 and 1920')
    if Image is None:
        print('Note: Pillow not installed; original full-size thumbnails will be embedded. Install Pillow for smaller index files.', file=sys.stderr)
    paths = sorted(p for p in root.rglob('*.html') if p.is_file() and p.name.lower() != 'index.html')
    print(f'Scanning {len(paths)} HTML files in {root}', flush=True)
    items = []
    skipped = 0
    for path in paths:
        try:
            items.append(parse_bookmark(path, root, args.thumbnail_width))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            skipped += 1
            print(f'  Skipping {path.relative_to(root)}: {exc}', file=sys.stderr)
    if not items:
        print('ERROR: No valid bookmarks found; existing index left untouched.', file=sys.stderr)
        return 1
    payload = json.dumps({'generatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'), 'items': items}, ensure_ascii=False, separators=(',', ':'))
    # Escape < so user-supplied metadata cannot terminate the JSON script element.
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = HTML_TEMPLATE.replace('__BOOKMARK_DATA__', payload)
    output = root.parent / 'index.html'
    temp = root.parent / '.index.html.tmp'
    try:
        temp.write_text(html, encoding='utf-8')
        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()
    print(f'Indexed {len(items)} bookmarks ({skipped} skipped).')
    print(f'Wrote {output} ({output.stat().st_size / 1024 / 1024:.2f} MiB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
