#!/usr/bin/env python3
"""Preview or apply an ordered TXT batch of YouTube bookmarks."""
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

SCRIPTS = Path(__file__).resolve().parent
spec = spec_from_file_location('import_bookmark_find', SCRIPTS / 'yt-bookmark-find.py')
finder = module_from_spec(spec)
spec.loader.exec_module(finder)
OUTCOMES = ('created', 'would_create', 'skipped_existing', 'duplicate_input',
            'invalid_input', 'unavailable', 'failed', 'not_processed')


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
    entries, seen = [], {}
    for number, raw in enumerate(path.read_bytes().decode('utf-8-sig').split('\n'), 1):
        value = raw.strip()
        if not value or value.startswith('#'):
            continue
        entry = dict(line=number, input=value, id=None, outcome='not_processed', path=None)
        try:
            entry['id'] = input_id(value)
        except ValueError as exc:
            entry.update(outcome='invalid_input', message=str(exc))
        else:
            if entry['id'] in seen:
                entry.update(outcome='duplicate_input', duplicate_of=seen[entry['id']])
            else:
                seen[entry['id']] = number
        entries.append(entry)
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
    """Execute normalized candidates without depending on the discovery source."""
    active = None
    try:
        with tempfile.TemporaryDirectory(prefix='videx-import-') as temporary:
            for entry in entries:
                if entry['outcome'] != 'would_create':
                    continue
                active = entry
                entry['outcome'] = 'not_processed'
                print(f"Capturing line {entry['line']}: {entry['id']}", file=sys.stderr, flush=True)
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
                    break
                active = None
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


def emit(report, as_json):
    counts = Counter(entry['outcome'] for entry in report['entries'])
    report['summary'] = {name: counts[name] for name in OUTCOMES}
    if as_json:
        print(json.dumps(report, ensure_ascii=False))
        return
    print(f"{'Apply' if report['apply'] else 'Preview'} library: {report['library'] or '(unresolved)'}")
    if not report['apply']:
        print('Would create is a plan only; video availability has not been checked.')
    for entry in report['entries']:
        detail = entry.get('message') or entry.get('path') or ''
        if 'duplicate_of' in entry:
            detail = f"first occurrence: line {entry['duplicate_of']}"
        print(f"Line {entry['line']}: {outcome_label(entry['outcome'])}: "
              f"{entry['id'] or entry['input']}" + (f' — {detail}' if detail else ''))
    print('Summary: ' + ', '.join(f"{outcome_label(name)}: {count}"
                                for name, count in report['summary'].items()))
    print(f"Final index: {report['index']['status']}")
    if report['index']['status'] == 'failed':
        print(report['index'].get('diagnostics', '').strip(), file=sys.stderr)
    for error in report['errors']:
        print(f'ERROR: {error}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True, type=Path, help='UTF-8 TXT input')
    parser.add_argument('--output', type=Path, help='Library root (default: configured library)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Create missing bookmarks')
    mode.add_argument('--dry-run', action='store_true', help='Read-only preview (default)')
    parser.add_argument('--no-re-index', action='store_true', help='Suppress final index rebuild')
    parser.add_argument('--json', action='store_true', help='Print one JSON report')
    args = parser.parse_args(argv)
    report = dict(report_version=1, apply=args.apply, library=None, entries=[], errors=[],
                  interrupted=False, index=dict(status='not_requested'), exit_status=2)
    try:
        report['entries'] = read_txt(args.file.expanduser())
        selected = args.output.expanduser() if args.output is not None else default_library()
        library = selected.resolve()
        report['library'] = str(library)
        if library.name == 'bookmarks':
            raise ValueError('Output must be the library root, not bookmarks/')
        existing = inventory(library)
        for entry in report['entries']:
            if entry['outcome'] == 'not_processed':
                if entry['id'] in existing:
                    entry.update(outcome='skipped_existing', path=existing[entry['id']])
                else:
                    entry['outcome'] = 'would_create'
        if args.apply and any(e['outcome'] == 'would_create' for e in report['entries']):
            for command in ('bash', 'python3', 'yt-dlp'):
                if not shutil.which(command):
                    raise ValueError(f'Missing required command: {command}')
            apply_entries(report['entries'], library, report)
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
    except (OSError, ValueError) as exc:
        report['errors'].append(str(exc))
    if args.apply:
        for entry in report['entries']:
            if entry['outcome'] == 'would_create':
                entry['outcome'] = 'not_processed'
    if report['exit_status'] == 2:
        report['index']['status'] = 'preflight_refused'
    emit(report, args.json)
    return report['exit_status']


if __name__ == '__main__':
    sys.exit(main())
