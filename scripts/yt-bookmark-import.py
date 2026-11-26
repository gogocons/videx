#!/usr/bin/env python3
"""Preview or apply YouTube bookmarks from TXT or a public playlist."""
import argparse
from collections import Counter
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

sys.dont_write_bytecode = True
from videx_config import default_library
from videx_identity import valid_id
from videx_import_report import OUTCOMES, PLAYLIST_ID, library_identity, member_ids

SCRIPTS = Path(__file__).resolve().parent
spec = spec_from_file_location('import_bookmark_find', SCRIPTS / 'yt-bookmark-find.py')
finder = module_from_spec(spec)
spec.loader.exec_module(finder)


def outcome_label(value):
    return 'Failed or unknown' if value == 'failed' else value.replace('_', ' ').title()


def input_id(value):
    """Strict single-video input adapter. Never resolve arbitrary URLs online."""
    if valid_id(value):
        return value
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Expected one video URL or ID without extra content')
    if any(c in value for c in ',<>[]"`|'):
        raise ValueError('Extra content or unsupported URL characters')
    if re.search(r'%(?![0-9A-Fa-f]{2})', value):
        raise ValueError('Malformed URL escape')
    try:
        url = urlsplit(value)
        if (url.scheme not in ('http', 'https') or url.username is not None
                or url.password is not None or ':' in url.netloc or not url.hostname):
            raise ValueError('Unsupported URL authority or scheme')
        host = url.hostname.lower()
        if url.fragment and not re.fullmatch(r't=(?=[0-9])(?:[0-9]+h)?(?:[0-9]+m)?(?:[0-9]+s?)?', url.fragment):
            raise ValueError('Only timestamp fragments are supported')
        query = parse_qs(url.query, keep_blank_values=True)
        parts = url.path.split('/')
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            if url.path == '/watch':
                values = query.get('v', [])
                candidate = values[0] if len(values) == 1 else ''
            elif len(parts) == 3 and parts[1] in ('shorts', 'live', 'embed'):
                candidate = unquote(parts[2])
            else:
                raise ValueError('Unsupported single-video URL path')
        elif host in ('youtu.be', 'www.youtu.be') and len(parts) == 2:
            candidate = unquote(parts[1])
        else:
            raise ValueError('Unsupported YouTube video URL')
        if not valid_id(candidate):
            raise ValueError('Missing, ambiguous, or invalid video ID')
        if url.path != '/watch' and 'v' in query:
            raise ValueError('Ambiguous video ID in URL path and query')
        return candidate
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def read_txt(path):
    """Return source records; another discovery adapter can supply these later."""
    entries = []
    for number, raw in enumerate(path.read_bytes().decode('utf-8-sig').split('\n'), 1):
        value = raw.strip()
        if not value or value.startswith('#'):
            continue
        entry = dict(line=number, input=value, id=None, outcome='not_processed', path=None)
        try:
            entry['id'] = input_id(value)
        except ValueError as exc:
            entry.update(outcome='invalid_input', message=str(exc))
        entries.append(entry)
    return deduplicate(entries)


def deduplicate(entries):
    """First valid ID wins, independently of the input adapter."""
    seen = {}
    for entry in entries:
        if not valid_id(entry['id']):
            continue
        position = entry.get('position', entry['line'])
        if entry['id'] in seen:
            entry.update(outcome='duplicate_input', duplicate_of=seen[entry['id']])
        else:
            seen[entry['id']] = position
    return entries


def playlist_url(value):
    """Validate a public playlist target and discard video/selection parameters."""
    if (any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in ',<>[]"`|')
            or re.search(r'%(?![0-9A-Fa-f]{2})', value)):
        raise ValueError('Malformed playlist URL')
    url = urlsplit(value)
    hosts = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com')
    if (url.scheme not in ('http', 'https') or url.hostname not in (*hosts, 'youtu.be', 'www.youtu.be')
            or url.username is not None or url.password is not None or ':' in url.netloc
            or url.fragment):
        raise ValueError('Expected a public YouTube playlist URL without credentials, ports, or fragments')
    query = parse_qs(url.query, keep_blank_values=True)
    ids = query.get('list', [])
    if len(ids) != 1 or not PLAYLIST_ID.fullmatch(ids[0]):
        raise ValueError('Missing, ambiguous, or unsupported public playlist ID (account lists and mixes are unsupported)')
    if url.hostname in hosts and url.path == '/playlist':
        if 'v' in query:
            raise ValueError('Ambiguous playlist URL containing a video selector')
    else:
        # Normal watch/share links with list= are accepted only as valid videos.
        input_id(value)
    return ids[0], f'https://www.youtube.com/playlist?list={ids[0]}'


def playlist_document(text):
    """Reject ambiguous/nonstandard JSON rather than silently normalizing it."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Playlist JSON contains duplicate object members')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('Playlist JSON contains a nonstandard numeric value')

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise ValueError('Playlist discovery returned invalid or ambiguous JSON') from None


def check_discovery_errors(obj):
    """Explicit partial-result markers cannot be treated as successful discovery."""
    for key in ('error', 'errors', '_error', 'incomplete', 'truncated', 'has_more',
                'continuation', 'continuation_token', 'next_page', 'next_page_token'):
        if obj.get(key):
            raise ValueError('Playlist discovery indicates errors or incomplete results')
    if obj.get('is_complete') is False or obj.get('complete') is False:
        raise ValueError('Playlist discovery indicates incomplete results')


def playlist_entries(document, expected_id):
    """Validate a fully enumerated flat playlist, then normalize its entries."""
    if not isinstance(document, dict) or document.get('_type') != 'playlist' or not isinstance(document.get('entries'), list):
        raise ValueError('Playlist discovery returned an invalid playlist structure')
    check_discovery_errors(document)
    if document.get('id') is not None and document['id'] != expected_id:
        raise ValueError('Discovered playlist ID does not match the requested playlist')
    if document.get('availability') not in (None, 'public', 'unlisted'):
        raise ValueError('Playlist requires access beyond public unauthenticated discovery')
    for key in ('title', 'description'):
        if document.get(key) is not None and not isinstance(document[key], str):
            raise ValueError('Invalid optional playlist metadata')
    raw_entries = document['entries']
    for key in ('playlist_count', 'n_entries'):
        count = document.get(key)
        if count is not None and (type(count) is not int or count != len(raw_entries)):
            raise ValueError('Playlist discovery count differs from the enumerated entries')
    requested = document.get('requested_entries')
    if requested is not None and (not isinstance(requested, list)
            or any(type(n) is not int or n != i for i, n in enumerate(requested, 1))
            or len(requested) != len(raw_entries)):
        raise ValueError('Playlist discovery returned a partial selection of entries')
    entries = []
    for position, raw in enumerate(raw_entries, 1):
        entry = dict(line=None, position=position, input='', id=None, path=None, outcome='not_processed')
        if not isinstance(raw, dict):
            entry.update(outcome='invalid_input', message='Missing or malformed playlist entry')
        else:
            check_discovery_errors(raw)
            index = raw.get('playlist_index')
            if index is not None and (type(index) is not int or index != position):
                raise ValueError('Playlist discovery returned noncontiguous or reordered positions')
            identity = raw.get('id')
            if valid_id(identity):
                entry.update(id=identity, input=f'https://www.youtube.com/watch?v={identity}')
            unavailable = (raw.get('availability') in ('private', 'premium_only', 'subscriber_only', 'needs_auth')
                           or raw.get('title') in ('[Private video]', '[Deleted video]'))
            if unavailable:
                entry.update(outcome='unavailable', message='Playlist marks this entry unavailable at discovery time')
            elif not valid_id(identity) or raw.get('_type') not in (None, 'url', 'url_transparent', 'video'):
                entry.update(outcome='invalid_input', message='Playlist entry has no valid YouTube video identity')
        entries.append(entry)
    return deduplicate(entries)


def discover_playlist(canonical_url, source):
    """Discover before applying anything; never return a known partial list."""
    try:
        completed = run_command(['yt-dlp', '--ignore-config', '--no-cache-dir', '--no-mark-watched',
            '--flat-playlist', '--yes-playlist', '--simulate', '--dump-single-json',
            '--no-lazy-playlist', '--abort-on-error',
            '--extractor-args', 'youtube:raise_incomplete_data=true', '--', canonical_url])
    except OSError:
        raise ValueError('Could not start playlist discovery; check the installed yt-dlp executable') from None
    if completed.returncode in (-signal.SIGINT, 130):
        raise KeyboardInterrupt
    if completed.returncode:
        raise ValueError(f'Playlist discovery failed (yt-dlp exit status {completed.returncode}); no entries imported')
    # Warnings can include exhausted page retries while yt-dlp still exits zero.
    # Refuse conservatively without echoing untrusted diagnostics or local paths.
    if completed.stderr.strip():
        raise ValueError('yt-dlp reported discovery diagnostics; completeness is unverified; no entries imported')
    document = playlist_document(completed.stdout)
    entries = playlist_entries(document, source['id'])
    source.update(title=document.get('title'), description=document.get('description'),
                  discovered_entries=len(entries), unique_valid_ids=len({e['id'] for e in entries if valid_id(e['id'])}),
                  discovery='complete')
    return entries


def inventory(library):
    """Absent collections are empty; existing unsafe/incomplete ones are errors."""
    try:
        library_mode = library.stat().st_mode
    except FileNotFoundError:
        library_mode = None
    if library_mode is not None and not stat.S_ISDIR(library_mode):
        raise ValueError(f'Library is not a directory: {library}')
    root = library / 'bookmarks'
    try:
        root.lstat()
    except FileNotFoundError:
        return {}
    _, identities, issues, _ = finder.inventory(root)
    if issues:
        raise ValueError('\n'.join(finder.describe_issue(path, issue) for path, issue in issues))
    result = {}
    for path, identity in identities.items():
        if identity in result:
            raise ValueError(f'Duplicate library video ID {identity}: {result[identity]}, {path}')
        result[identity] = str(path)
    return result


def run_command(command):
    """Own the child process group so interruption also stops extraction."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate()
    except KeyboardInterrupt:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def apply_entries(entries, library, report):
    """Execute normalized candidates and return whether the batch ran to completion."""
    active = None
    try:
        with tempfile.TemporaryDirectory(prefix='videx-import-') as temporary:
            for entry in entries:
                if entry['outcome'] != 'would_create':
                    continue
                active = entry
                entry['outcome'] = 'not_processed'
                location = f"playlist position {entry['position']}" if 'position' in entry else f"line {entry['line']}"
                print(f"Capturing {location}: {entry['id']}", file=sys.stderr, flush=True)
                result_path = Path(temporary) / 'result.json'
                if result_path.exists():
                    result_path.unlink()
                completed = run_command([
                    'bash', str(SCRIPTS / 'yt-bookmark.sh'), '--output', str(library),
                    '--batch-result', str(result_path), '--id', entry['id']])
                result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
                if not isinstance(result, dict):
                    raise ValueError('Invalid internal capture result')
                if result.get('outcome') == 'created':
                    entry.update(outcome='created', path=result.get('path'))
                # Re-inspect after every child, including failures. Never continue
                # after a newly incomplete or ambiguous library inventory.
                current = inventory(library)
                outcome = result.get('outcome')
                if entry['id'] in current:
                    entry.update(outcome='created' if outcome != 'skipped_existing' else outcome,
                                 path=current[entry['id']])
                    if completed.returncode:
                        report['errors'].append(f"Capture for {entry['id']} failed after publication")
                else:
                    entry.update(outcome='failed', message=(completed.stderr.strip()
                                 or 'Capture did not publish a bookmark'))
                if completed.returncode in (-signal.SIGINT, 130):
                    raise KeyboardInterrupt
                if result.get('fatal'):
                    report['errors'].append('Capture refused unsafe library state; remaining entries were not processed')
                    return False
                active = None
        return True
    except KeyboardInterrupt:
        report['interrupted'] = True
        report['errors'].append('Interrupted; completed bookmarks are preserved')
        if active is not None:
            try:
                current = inventory(library)
                if active['id'] in current:
                    active.update(outcome='created', path=current[active['id']])
            except (OSError, ValueError) as exc:
                report['errors'].append(str(exc))
    except (OSError, ValueError) as exc:
        if active is not None and active['outcome'] != 'created':
            active.update(outcome='failed', message=str(exc))
        report['errors'].append(str(exc))
    finally:
        for entry in entries:
            if entry['outcome'] == 'would_create':
                entry['outcome'] = 'not_processed'
    return False


def collection_proposal(report, library, completed, initial_binding, binding_error):
    proposal = dict(acquisition='stopped', status='refused', reason=None, library_identity=None)
    if report['source']['discovery'] != 'complete':
        proposal['reason'] = 'Playlist discovery did not complete: ' + report['errors'][0]
    elif report['interrupted']:
        proposal['reason'] = 'Playlist import was interrupted'
    elif not completed or report['exit_status'] == 2:
        proposal['reason'] = 'Playlist acquisition stopped before completion'
    elif not report['apply']:
        proposal.update(acquisition='preview', status='preview')
    else:
        proposal['acquisition'] = 'completed'
        members = member_ids(report['entries'])
        if not members:
            proposal.update(status='empty', reason='No eligible local bookmarks')
        else:
            try:
                if binding_error is not None:
                    raise ValueError(binding_error)
                current = inventory(library)
                binding = library_identity(library)
                if any(binding[key] != value for key, value in initial_binding.items()):
                    raise ValueError('Library directories changed during import')
                missing = [identity for identity in members if identity not in current]
                if missing:
                    raise ValueError('Confirmed bookmarks disappeared during import: ' + ', '.join(missing))
            except (OSError, ValueError) as exc:
                proposal['reason'] = f'Cannot confirm proposal library and membership: {exc}'
            else:
                proposal.update(status='eligible', library_identity=binding)
    return proposal


def emit(report, as_json):
    counts = Counter(entry['outcome'] for entry in report['entries'])
    report['summary'] = {name: counts[name] for name in OUTCOMES}
    proposal = report.get('collection_proposal')
    if as_json:
        print(json.dumps(report, ensure_ascii=proposal is not None))
        return
    source = report.get('source', {})
    if source.get('type') == 'playlist':
        print(f"Source: public YouTube playlist {source.get('id') or '(unresolved)'}")
        print(f"Playlist URL: {json.dumps(source['url'], ensure_ascii=proposal is not None)}")
        if source.get('title') is not None:
            print(f"Playlist title: {json.dumps(source['title'], ensure_ascii=proposal is not None)}")
        if source.get('description') is not None:
            print(f"Playlist description: {json.dumps(source['description'], ensure_ascii=proposal is not None)}")
        print(f"Discovery: {source['discovery']}; discovered entries: {source.get('discovered_entries', 0)}; "
              f"unique valid video IDs: {source.get('unique_valid_ids', 0)}")
    print(f"{'Apply' if report['apply'] else 'Preview'} library: {report['library'] or '(unresolved)'}")
    if not report['apply']:
        print('Would create is a plan only; video availability has not been checked.')
    for entry in report['entries']:
        detail = entry.get('message') or entry.get('path') or ''
        if 'duplicate_of' in entry:
            unit = 'position' if 'position' in entry else 'line'
            detail = f"first occurrence: {unit} {entry['duplicate_of']}"
        elif detail and proposal is not None:
            detail = ascii(detail)
        location = f"Position {entry['position']}" if 'position' in entry else f"Line {entry['line']}"
        print(f"{location}: {outcome_label(entry['outcome'])}: "
              f"{entry['id'] or entry['input']}" + (f' — {detail}' if detail else ''))
    print('Summary: ' + ', '.join(f"{outcome_label(name)}: {count}"
                                for name, count in report['summary'].items()))
    if proposal is not None:
        print(f"Collection proposal: {proposal['status']} ({proposal['acquisition']})")
        if proposal['reason']:
            print(f"  {ascii(proposal['reason'])}")
        if proposal['status'] == 'preview':
            print('  Existing bookmarks are listed as skipped existing. Would create entries await capture.')
        if proposal['status'] == 'eligible':
            print('  Members in playlist order: ' + ', '.join(member_ids(report['entries'])))
            print('  Save the JSON report to review it with collection from-import.')
    print(f"Final index: {report['index']['status']}")
    if report['index']['status'] == 'failed':
        diagnostics = report['index'].get('diagnostics', '').strip()
        print(ascii(diagnostics) if proposal is not None else diagnostics, file=sys.stderr)
    for error in report['errors']:
        print(f'ERROR: {ascii(error) if proposal is not None else error}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--file', type=Path, help='UTF-8 TXT input')
    source.add_argument('--playlist', help='Public YouTube playlist URL (discovery requires network)')
    parser.add_argument('--output', type=Path, help='Library root (default: configured library)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Create missing bookmarks')
    mode.add_argument('--dry-run', action='store_true', help='Read-only preview (default)')
    parser.add_argument('--no-re-index', action='store_true', help='Suppress final index rebuild')
    parser.add_argument('--propose-collection', action='store_true', help='Include a playlist Collection proposal')
    parser.add_argument('--json', action='store_true', help='Print one JSON report')
    args = parser.parse_args(argv)
    if args.propose_collection and args.playlist is None:
        parser.error('--propose-collection requires --playlist')
    report = dict(report_version=1, apply=args.apply, library=None, entries=[], errors=[],
                  interrupted=False, index=dict(status='not_requested'), exit_status=2)
    library = None
    completed = False
    initial_binding, binding_error = {}, None
    try:
        if args.playlist is None:
            report['entries'] = read_txt(args.file.expanduser())
        else:
            report['source'] = dict(type='playlist', url=args.playlist, discovery='pending')
            playlist_id, canonical_url = playlist_url(args.playlist)
            report['source']['id'] = playlist_id
        selected = args.output.expanduser() if args.output is not None else default_library()
        library = selected.resolve()
        report['library'] = str(library)
        if library.name == 'bookmarks':
            raise ValueError('Output must be the library root, not bookmarks/')
        if args.propose_collection and args.apply:
            try:
                initial_binding = library_identity(library, allow_missing=True)
            except (OSError, ValueError) as exc:
                binding_error = str(exc)
        existing = inventory(library)
        if args.playlist is not None:
            report['entries'] = discover_playlist(canonical_url, report['source'])
        for entry in report['entries']:
            if (entry['outcome'] == 'not_processed'
                    or (args.playlist is not None and entry['outcome'] == 'unavailable' and entry['id'] in existing)):
                if entry['id'] in existing:
                    entry.update(outcome='skipped_existing', path=existing[entry['id']])
                else:
                    entry['outcome'] = 'would_create'
        if args.apply and any(e['outcome'] == 'would_create' for e in report['entries']):
            for command in ('bash', 'python3', 'yt-dlp'):
                if not shutil.which(command):
                    raise ValueError(f'Missing required command: {command}')
            completed = apply_entries(report['entries'], library, report)
        else:
            completed = True
        created = any(e['outcome'] == 'created' for e in report['entries'])
        if not args.apply:
            report['index']['status'] = 'preview_no_writes'
        elif args.no_re_index:
            report['index']['status'] = 'suppressed'
        elif created:
            report['index']['status'] = 'pending'
            try:
                completed = run_command([sys.executable, '-B', str(SCRIPTS / 'yt-bookmark-index.py'), str(library)])
                report['index'] = dict(status='rebuilt' if completed.returncode == 0 else 'failed',
                                       exit_status=completed.returncode,
                                       diagnostics=completed.stdout + completed.stderr)
            except OSError as exc:
                report['index'] = dict(status='failed', diagnostics=str(exc))
            if report['index']['status'] == 'failed':
                report['errors'].append('Index rebuild failed; bookmarks and previous index are preserved')
        else:
            report['index']['status'] = 'no_new_bookmarks'
        incomplete = report['errors'] or any(e['outcome'] in
            ('invalid_input', 'unavailable', 'failed', 'not_processed') for e in report['entries'])
        report['exit_status'] = 130 if report['interrupted'] else 1 if incomplete else 0
    except KeyboardInterrupt:
        report.update(interrupted=True, exit_status=130)
        report['errors'].append('Interrupted; completed bookmarks are preserved; rebuild the index offline if needed')
        if report['index']['status'] == 'pending':
            report['index']['status'] = 'interrupted'
        if report.get('source', {}).get('discovery') == 'pending':
            report['source']['discovery'] = 'interrupted'
    except (OSError, ValueError) as exc:
        report['errors'].append(str(exc))
        if report.get('source', {}).get('discovery') == 'pending':
            report['source']['discovery'] = 'failed'
    if args.apply:
        for entry in report['entries']:
            if entry['outcome'] == 'would_create':
                entry['outcome'] = 'not_processed'
    if report['exit_status'] == 2:
        report['index']['status'] = 'preflight_refused'
    if args.propose_collection:
        try:
            report['collection_proposal'] = collection_proposal(
                report, library, completed, initial_binding, binding_error)
        except KeyboardInterrupt:
            report.update(interrupted=True, exit_status=130)
            report['errors'].append('Interrupted; completed bookmarks are preserved')
            report['collection_proposal'] = collection_proposal(
                report, library, False, initial_binding, binding_error)
    emit(report, args.json)
    return report['exit_status']


if __name__ == '__main__':
    sys.exit(main())
