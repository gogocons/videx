#!/usr/bin/env python3
"""Generate a standalone, offline index.html for yt-bookmark HTML archives."""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from urllib.parse import quote
from videx_catalog import load, unique_tags, validate_collections
from videx_config import default_library
from videx_identity import loads_metadata, resolve_identity
from videx_inventory import read_bookmark_text, scan_bookmarks


def load_metadata_helpers():
    spec = spec_from_file_location('bookmark_metadata', Path(__file__).with_name('yt-bookmark-metadata.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metadata_helpers = load_metadata_helpers()

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


def parse_bookmark(path, source, video_id, width, library_root):
    parser = BookmarkParser()
    parser.feed(source)
    if not parser.metadata_chunks:
        raise ValueError('no embedded youtube-metadata JSON')
    metadata = loads_metadata(''.join(parser.metadata_chunks))
    if not isinstance(metadata, dict):
        raise ValueError('metadata is not an object')
    metadata_helpers.validate_archival_numbers(metadata)
    duration = metadata_helpers.display_seconds(metadata.get('duration'))
    uploaded = metadata.get('upload_date') or ''
    if not re.fullmatch(r'\d{8}', str(uploaded)):
        uploaded = ''
    relative = path.relative_to(library_root).as_posix()
    # URL encode each path segment, preserving the slashes between directories.
    href = '/'.join(quote(segment, safe='') for segment in relative.split('/'))
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
.collection-picker{max-width:26rem;margin-bottom:14px}
.collection-summary{margin:0 0 17px;overflow-wrap:anywhere;white-space:pre-wrap}
.collection-summary h2{font-size:1.2rem;margin:0}.collection-summary p{margin:5px 0 0}
.toolbar{display:grid;grid-template-columns:minmax(200px,1fr) auto minmax(145px,200px);gap:12px;margin-bottom:17px}
input,select,button{min-width:0;background:#1d222c;color:#f3f5fa;border:1px solid #343b49;border-radius:10px;padding:13px 14px;font:inherit;outline-offset:3px}
input,select{width:100%}button{cursor:pointer}input:focus,select:focus,button:focus-visible{border-color:#90a9e8}
#filters:focus-visible{outline:2px solid #90a9e8}
[hidden]{display:none!important}.filter-panel{background:#171b23;border:1px solid #343b49;border-radius:12px;padding:16px;margin-bottom:14px}
.filter-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:14px}
.filter-field{min-width:0}.filter-field label{display:block;margin-bottom:7px}.filter-panel button{margin-top:14px}
.filter-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}.filter-chip{max-width:100%;text-align:left;overflow-wrap:anywhere;padding:7px 10px;font-size:.85rem}
.count{color:#9ca5b5;font-size:.88rem;margin:10px 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:25px 19px}
.card{min-width:0}.thumb-link{display:block;position:relative;aspect-ratio:16/9;background:#232832;border-radius:12px;overflow:hidden;text-decoration:none}
.thumb-link img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .2s}.thumb-link:hover img{transform:scale(1.035)}
.duration{position:absolute;right:9px;bottom:9px;background:#07090bdf;color:#fff;padding:3px 6px;border-radius:4px;font-size:.77rem;font-weight:600}
.details{padding:11px 2px}.title{font-size:.98rem;line-height:1.42;font-weight:650;margin:0 0 7px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
a{color:inherit}.title a{text-decoration:none}.title a:hover{text-decoration:underline}.channel{color:#aab3c2;font-size:.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meta{color:#818c9e;font-size:.78rem;margin-top:5px}.actions{display:flex;gap:15px;margin-top:10px;font-size:.79rem}.actions a{color:#a9c5ff;text-decoration:none}.actions a:hover{text-decoration:underline}
.personal-tags{margin-top:8px}.personal-tags-label{display:block;font-size:.7rem;opacity:.7;margin-bottom:4px}.personal-tag-list{display:flex;flex-wrap:wrap;gap:4px}.personal-tag{max-width:100%;overflow-wrap:anywhere;font-size:.72rem;line-height:1.4;padding:2px 6px;border-radius:8px;color:inherit;background:rgba(127,127,127,.12);border:1px solid rgba(127,127,127,.35)}
.empty{text-align:center;color:#aab3c2;padding:70px 10px}.footer{color:#778295;font-size:.8rem;margin-top:42px;text-align:center}
@media(max-width:690px){main{padding:20px 14px 50px}.toolbar{grid-template-columns:1fr 1fr}.toolbar input{grid-column:1/-1}.grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:17px 12px}.title{font-size:.9rem}.stats{width:100%}}
</style>
</head>
<body><main>
<header><div class="brand"><span class="play" aria-hidden="true">▶</span><div><h1>Videx</h1><div class="sub">Your offline video reference library</div></div></div><div class="stats" id="stats"></div></header>
<section id="collection-summary" class="collection-summary" aria-labelledby="collection-name" hidden><h2 id="collection-name"></h2><p id="collection-description" class="muted" hidden></p></section>
<div class="toolbar">
<input id="search" type="search" placeholder="Search titles, channels, descriptions, personal tags…" aria-label="Search bookmarks" autocomplete="off">
<button id="filters" type="button" aria-expanded="false" aria-controls="filter-panel">Filters</button>
<select id="sort" aria-label="Sort bookmarks"><option value="upload-desc">Newest</option><option value="upload-asc">Oldest</option><option value="title-asc">Title A-Z</option><option value="title-desc">Title Z-A</option><option value="channel-asc">Channel A-Z</option></select>
</div>
<section id="filter-panel" class="filter-panel" aria-label="Filters" hidden>
<div class="filter-field collection-picker" id="collection-picker" hidden>
<label for="collection">Collection</label>
<select id="collection"><option value="">All Bookmarks</option><optgroup id="collection-options" label="Collections"></optgroup></select>
</div>
<div class="filter-fields">
<div class="filter-field"><label for="channel">Channel</label><select id="channel"><option value="">All Channels</option></select></div>
<div class="filter-field"><label for="personal-tag">Personal Tag</label><select id="personal-tag"><option value="">All Personal Tags</option></select></div>
</div>
<button id="clear-filters" type="button">Clear Filters</button>
</section>
<div id="filter-chips" class="filter-chips" role="group" aria-label="Active filters" hidden></div>
<div class="count" id="count" aria-live="polite"></div><section class="grid" id="grid" aria-label="Bookmarks"></section><div class="empty" id="empty" hidden>No matching bookmarks found.</div><div class="footer" id="footer"></div>
</main>
<script id="bookmark-data" type="application/json">__BOOKMARK_DATA__</script>
<script>
(()=>{'use strict';
const data=JSON.parse(document.getElementById('bookmark-data').textContent);
const items=data.items;
const search=document.getElementById('search'),channel=document.getElementById('channel'),sort=document.getElementById('sort'),grid=document.getElementById('grid'),count=document.getElementById('count'),empty=document.getElementById('empty');
const filters=document.getElementById('filters'),panel=document.getElementById('filter-panel'),personalTag=document.getElementById('personal-tag'),chips=document.getElementById('filter-chips');
const collectionPicker=document.getElementById('collection-picker');
const collection=document.getElementById('collection'),collectionOptions=document.getElementById('collection-options');
const collectionSummary=document.getElementById('collection-summary'),collectionName=document.getElementById('collection-name'),collectionDescription=document.getElementById('collection-description');
const collections=new Map();
for(const entry of data.collections){
    collections.set(entry.id,{...entry,members:new Set(entry.members)});
    const opt=node('option','',entry.name);
    opt.value=entry.id;collectionOptions.append(opt);
}
if(collections.size)collectionPicker.hidden=false;
else collectionPicker.remove();
const tagLabels=new Map(data.personalTagOptions.map(tag=>[tag.key,tag.label]));
for(const tag of data.personalTagOptions){
    const opt=document.createElement('option');
    opt.value=tag.key;opt.textContent=tag.label;personalTag.append(opt);
}
function setPanelOpen(open){
    panel.hidden=!open;
    filters.setAttribute('aria-expanded',String(open));
    if(!open)filters.focus();
}
filters.addEventListener('click',()=>setPanelOpen(panel.hidden));
document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!panel.hidden){event.preventDefault();setPanelOpen(false)}
});
document.getElementById('clear-filters').addEventListener('click',()=>{
    channel.value='';personalTag.value='';render();
    // Keep focus in the open panel so keyboard users can choose another filter.
    channel.focus();
});
function renderFilters(){
    const active=[];
    if(channel.value)active.push({control:channel,label:`Channel: ${channel.value==='__single_entries__'?'Single Entries':channel.value}`});
    if(personalTag.value)active.push({control:personalTag,label:`Personal Tag: ${tagLabels.get(personalTag.value)}`});
    filters.textContent=active.length?`Filters (${active.length})`:'Filters';
    chips.hidden=active.length===0;chips.replaceChildren();
    for(const entry of active){
        const button=node('button','filter-chip',`${entry.label} ×`);
        button.type='button';button.setAttribute('aria-label',`Remove ${entry.label} filter`);
        button.addEventListener('click',()=>{
            entry.control.value='';render();
            // Rendering replaces chips; return focus to a stable visible control.
            filters.focus();
        });
        chips.append(button);
    }
}
// Count bookmarks belonging to each channel.
const channelCounts = new Map();

for (const item of items) {
    channelCounts.set(
        item.channel,
        (channelCounts.get(item.channel) || 0) + 1
    );
}

// Preserve the complete channel list for library statistics.
const channels = [...channelCounts.keys()]
    .sort((a, b) => a.localeCompare(b));

// Add the Single Entries filter.
const singleOption = document.createElement('option');
singleOption.value = '__single_entries__';
singleOption.textContent = 'Single Entries';
channel.append(singleOption);

// Add individual channels with at least two bookmarks.
for (const name of channels) {
    if (channelCounts.get(name) < 2) continue;

    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    channel.append(opt);
}
document.getElementById('stats').textContent=`${items.length.toLocaleString()} bookmarks · ${channels.length.toLocaleString()} channels`;
document.getElementById('footer').textContent=`Index generated ${data.generatedAt} · Offline archive`;
function duration(seconds){if(seconds==null)return '';const n=Math.floor(seconds),h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${m}:${String(s).padStart(2,'0')}`}
function date(x){if(!x)return '';const y=String(x);return `${y.slice(0,4)}-${y.slice(4,6)}-${y.slice(6,8)}`}
function node(tag,cls,txt){const el=document.createElement(tag);if(cls)el.className=cls;if(txt!==undefined)el.textContent=txt;return el}
function link(label,href){const a=node('a','',label);a.href=href;return a}
function makeCard(x){const card=node('article','card');const imageLink=link('',x.href);imageLink.className='thumb-link';imageLink.setAttribute('aria-label',`Open archived bookmark: ${x.title}`);if(x.thumbnail){const img=node('img');img.src=x.thumbnail;img.alt='';img.loading='lazy';imageLink.append(img)}else{imageLink.append(node('span','muted','No thumbnail'))}if(x.duration!=null)imageLink.append(node('span','duration',duration(x.duration)));card.append(imageLink);
const details=node('div','details');const title=node('h2','title');title.append(link(x.title,x.href));details.append(title,node('div','channel',x.channel));const dateText=x.uploadDate?`Uploaded ${date(x.uploadDate)}`:'Upload date unavailable';details.append(node('div','meta',dateText));
if(Array.isArray(x.personalTags)&&x.personalTags.length){
const tags=node('div','personal-tags');tags.append(node('span','personal-tags-label','Personal tags'));
const list=node('div','personal-tag-list');
for(const tag of x.personalTags)list.append(node('span','personal-tag',tag));
tags.append(list);details.append(tags);
}
const actions=node('div','actions');actions.append(link('Open bookmark',x.href));if(/^https?:\/\//i.test(x.url)){const external=link('YouTube ↗',x.url);external.target='_blank';external.rel='noopener noreferrer';actions.append(external)}details.append(actions);card.append(details);return card}
function render() {
    const q = search.value.trim().toLocaleLowerCase();
    const c = channel.value;
    const selected = collections.get(collection.value);
    const total = selected ? selected.members.size : items.length;
    collectionSummary.hidden = !selected;
    collectionName.textContent = selected ? selected.name : '';
    collectionDescription.textContent = selected?.description || '';
    collectionDescription.hidden = !selected?.description;

    let matches = items.filter(x => {
        if (selected && !selected.members.has(x.id)) return false;
        const channelMatch =
            !c ||
            (c === '__single_entries__'
                ? channelCounts.get(x.channel) === 1
                : x.channel === c);

        const searchMatch =
            !q ||
            [x.title, x.channel, x.description, x.id,
             ...(Array.isArray(x.personalTags) ? x.personalTags : [])]
                .some(v => v.toLocaleLowerCase().includes(q));

        const tagMatch = !personalTag.value || x.personalTagKeys.includes(personalTag.value);
        return channelMatch && searchMatch && tagMatch;
    });

    const s = sort.value;
matches.sort((a,b)=>{switch(s){case 'upload-desc':return b.uploadDate.localeCompare(a.uploadDate);case 'upload-asc':return a.uploadDate.localeCompare(b.uploadDate);case 'title-asc':return a.title.localeCompare(b.title);case 'title-desc':return b.title.localeCompare(a.title);case 'channel-asc':return a.channel.localeCompare(b.channel);default:return b.uploadDate.localeCompare(a.uploadDate)}});
    count.textContent=`Showing ${matches.length.toLocaleString()} of ${total.toLocaleString()} bookmarks`;
    empty.textContent=selected && total===0 ? 'This Collection has no visible bookmarks.' : 'No matching bookmarks found.';
    empty.hidden=matches.length!==0;
    grid.replaceChildren();
    const fragment=document.createDocumentFragment();
    for(const item of matches)fragment.append(makeCard(item));
    grid.append(fragment);renderFilters();
}
search.addEventListener('input',render);channel.addEventListener('change',render);personalTag.addEventListener('change',render);sort.addEventListener('change',render);collection.addEventListener('change',render);render();
})();
</script></body></html>'''


def write_index_atomic(output, content):
    """Publish a complete index using an exclusively created sibling file."""
    output = Path(output)
    try:
        previous = output.lstat()
    except FileNotFoundError:
        mode = None
    else:
        mode = stat.S_IMODE(previous.st_mode) if stat.S_ISREG(previous.st_mode) else None
    temp = None
    try:
        for _ in range(100):
            candidate = output.parent / f'.index-{secrets.token_hex(16)}.tmp'
            try:
                # Exclusive creation uses 0666 filtered by umask, without changing it.
                stream = candidate.open('x', encoding='utf-8')
            except FileExistsError:
                continue
            temp = candidate
            break
        else:
            raise FileExistsError('Cannot create a unique index temporary file')
        with stream:
            stream.write(content)
            stream.flush()
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
        os.replace(temp, output)
    except BaseException:
        if temp is not None:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f'WARNING: Cannot remove temporary index {temp}: {exc}', file=sys.stderr)
        raise


def load_index_catalog(library_root):
    catalog = load(library_root / 'catalog.json')
    collections = catalog['collections']
    names = validate_collections(collections)
    tags = {vid: unique_tags(record.get('tags', []))
            for vid, record in catalog['bookmarks'].items()}
    return tags, [(identity, collections[identity]) for _, identity in sorted(names.items())]


def visible_collections(collections, items, identity_counts, tags):
    visible_ids = {item['id'] for item in items if identity_counts[item['id']] == 1}
    reported = set()
    result = []
    for identity, record in collections:
        members = []
        for vid in record['members']:
            if vid in visible_ids:
                members.append(vid)
            elif vid not in reported and 'hidden' not in tags.get(vid, []):
                if identity_counts[vid] > 1:
                    reason = 'ambiguous bookmark identity'
                elif identity_counts[vid] == 1:
                    reason = 'bookmark skipped during indexing'
                else:
                    reason = 'no resolved bookmark'
                print(f'  Omitting Collection member {vid}: {reason}', file=sys.stderr)
                reported.add(vid)
        entry = {'id': identity, 'name': record['name'], 'members': members}
        if 'description' in record:
            entry['description'] = record['description']
        result.append(entry)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        epilog='Personal catalog tags are included automatically. Rebuilding excludes bookmarks tagged hidden from index data, search, channels, and counts. This is an organizational visibility control, not a security feature. Invalid catalogs or bookmark filesystem failures return status 2 and leave the existing index untouched. Bookmark symlinks and special files are rejected without reading; malformed regular pages, including non-finite or browser-overflowing archival numbers, are skipped. Final JSON serialization failures return 2 without publication.')
    parser.add_argument(
    'directory',
    nargs='?',
    default=None,
    help='Library root or its bookmarks/ directory (default: configured library)'
)
    parser.add_argument('--thumbnail-width', type=int, default=360, help='Embedded preview width in pixels (default: 360)')
    args = parser.parse_args()
    try:
        requested = Path(args.directory).expanduser() if args.directory else default_library()
        selected = requested.resolve()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if not selected.is_dir():
        parser.error(f'Not a directory: {selected}')

    # Support both a library root and its bookmarks/ subdirectory.
    if selected.name == 'bookmarks':
        root = selected
        library_root = selected.parent
    else:
        library_root = selected
        root = selected / 'bookmarks'
        try:
            root.lstat()
        except FileNotFoundError:
            root = selected
        except OSError as exc:
            print(f'ERROR: Cannot inspect {root}: {exc}; existing index left untouched.', file=sys.stderr)
            return 2
    if root == selected and requested.is_symlink():
        print('ERROR: Bookmark collection root cannot be a symlink; existing index left untouched.', file=sys.stderr)
        return 2
    if args.thumbnail_width < 64 or args.thumbnail_width > 1920:
        parser.error('--thumbnail-width must be between 64 and 1920')
    try:
        tags_by_id, collections = load_index_catalog(library_root)
    except (OSError, ValueError, RecursionError) as exc:
        print(f'ERROR: Cannot load personal tags or Collections; existing index left untouched: {exc}', file=sys.stderr)
        return 2
    if Image is None:
        print('Note: Pillow not installed; original full-size thumbnails will be embedded. Install Pillow for smaller index files.', file=sys.stderr)
    paths, filesystem_issues, _ = scan_bookmarks(root)
    if filesystem_issues:
        for path, exc in filesystem_issues:
            print(f'ERROR: Cannot inspect {path}: {exc}; existing index left untouched.', file=sys.stderr)
        return 2
    print(f'Scanning {len(paths)} HTML files in {root}', flush=True)
    items = []
    identity_counts = Counter()
    valid_count = 0
    skipped = 0
    for path in paths:
        try:
            source = read_bookmark_text(path)
            video_id = resolve_identity(path.name, source)
            # A second resolved copy is ambiguous even if its card cannot render.
            identity_counts[video_id] += 1
            item = parse_bookmark(path, source, video_id, args.thumbnail_width, library_root)
            item['personalTags'] = tags_by_id.get(item['id'], [])
            valid_count += 1
            if 'hidden' in item['personalTags']:
                continue
            items.append(item)
        except (OSError, UnicodeError) as exc:
            print(f'ERROR: Cannot read {path}: {exc}; existing index left untouched.', file=sys.stderr)
            return 2
        except ValueError as exc:
            skipped += 1
            print(f'  Skipping {path.relative_to(root)}: {exc}', file=sys.stderr)
    # All-hidden libraries must replace an older index with an empty one.
    if not valid_count:
        print('ERROR: No valid bookmarks found; existing index left untouched.', file=sys.stderr)
        return 1
    # Derived snapshot data only: use the catalog's Python casefold identities,
    # rather than approximating Unicode case folding in the browser.
    tag_labels = {}
    for item in items:
        item['personalTagKeys'] = [tag.casefold() for tag in item['personalTags']]
        for tag, key in zip(item['personalTags'], item['personalTagKeys']):
            tag_labels.setdefault(key, tag)
    tag_options = [{'key': key, 'label': tag_labels[key]} for key in sorted(tag_labels)]
    collection_options = visible_collections(collections, items, identity_counts, tags_by_id)
    try:
        payload = json.dumps({'generatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                              'items': items, 'personalTagOptions': tag_options,
                              'collections': collection_options},
                             ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        print(f'ERROR: Cannot serialize index JSON: {exc}; existing index left untouched.', file=sys.stderr)
        return 2
    # Escape < so user-supplied metadata cannot terminate the JSON script element.
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = HTML_TEMPLATE.replace('__BOOKMARK_DATA__', payload)
    output = library_root / 'index.html'
    write_index_atomic(output, html)
    print(f'Indexed {len(items)} bookmarks ({skipped} skipped).')
    print(f'Wrote {output} ({output.stat().st_size / 1024 / 1024:.2f} MiB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
