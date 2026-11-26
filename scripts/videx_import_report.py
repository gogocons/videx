"""Version 1 playlist import handoff and its local filesystem binding."""
from collections import Counter
import json
import math
from pathlib import Path
import re
import stat

from videx_identity import valid_id


PLAYLIST_ID = re.compile(r'(?:PL|EC|UU|FL|UL|TL|PU|OLAK5uy_)[A-Za-z0-9_-]{10,}')
OUTCOMES = ('created', 'would_create', 'skipped_existing', 'duplicate_input',
            'invalid_input', 'unavailable', 'failed', 'not_processed')
MEMBER_OUTCOMES = {'created', 'skipped_existing'}
OMISSION_OUTCOMES = {'invalid_input', 'unavailable', 'failed'}


def library_identity(library, *, allow_missing=False):
    binding = {}
    for key, path in (('root', library), ('bookmarks', library / 'bookmarks')):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'{path}: expected a real directory')
        binding[key] = dict(device=info.st_dev, inode=info.st_ino)
    return binding


def member_ids(entries):
    return [entry['id'] for entry in entries if entry['outcome'] in MEMBER_OUTCOMES]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key {key!r}')
        result[key] = value
    return result


def finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON number')
    return number


def read_report(filename, stdin):
    if filename == '-':
        binary = getattr(stdin, 'buffer', None)
        raw = stdin.read() if binary is None else binary.read().decode('utf-8')
    else:
        path = Path(filename).expanduser()
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Import report must be a regular file or stdin')
        raw = path.read_text(encoding='utf-8')
    try:
        report = json.loads(raw, object_pairs_hook=unique_object, parse_constant=finite_number,
                            parse_float=finite_number)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f'Invalid import report JSON: {exc}') from None
    validate_report(report)
    return report


def validate_entries(report, source, acquisition):
    entries = report.get('entries')
    if not isinstance(entries, list):
        raise ValueError('Import report entries must be an array')
    seen = {}
    for position, entry in enumerate(entries, 1):
        if (not isinstance(entry, dict) or type(entry.get('position')) is not int
                or entry['position'] != position or 'line' not in entry or entry['line'] is not None):
            raise ValueError(f'Invalid playlist entry position at row {position}')
        identity = entry.get('id')
        if 'id' not in entry or (identity is not None and not valid_id(identity)):
            raise ValueError(f'Invalid canonical video ID at position {position}')
        outcome = entry.get('outcome')
        if outcome not in OUTCOMES:
            raise ValueError(f'Invalid outcome at position {position}')
        first = seen.get(identity)
        if first is not None:
            if (outcome != 'duplicate_input' or type(entry.get('duplicate_of')) is not int
                    or entry['duplicate_of'] != first):
                raise ValueError(f'Invalid duplicate reference at position {position}')
        elif outcome == 'duplicate_input' or 'duplicate_of' in entry:
            raise ValueError(f'Unexpected duplicate reference at position {position}')
        elif identity is not None:
            seen[identity] = position
        expected_input = '' if identity is None else f'https://www.youtube.com/watch?v={identity}'
        path = entry.get('path')
        expected_path = bool(isinstance(path, str) and path) if outcome in MEMBER_OUTCOMES else path is None
        if (entry.get('input') != expected_input or 'path' not in entry
                or ('message' in entry and not isinstance(entry['message'], str))
                or (identity is None and outcome not in ('invalid_input', 'unavailable'))
                or not expected_path
                or (acquisition == 'completed' and outcome in ('would_create', 'not_processed'))
                or (acquisition == 'preview' and outcome in ('created', 'failed', 'not_processed'))):
            raise ValueError(f'Inconsistent playlist outcome at position {position}')
    if source['discovery'] == 'complete':
        for key, count in (('discovered_entries', len(entries)), ('unique_valid_ids', len(seen))):
            if type(source.get(key)) is not int or source[key] != count:
                raise ValueError(f'Inconsistent playlist {key}')
    elif entries:
        raise ValueError('Incomplete playlist discovery cannot have reported entries')
    counts = Counter(entry['outcome'] for entry in entries)
    summary = report.get('summary')
    if (not isinstance(summary, dict) or any(type(summary.get(key)) is not int
            or summary[key] != counts[key] for key in OUTCOMES)):
        raise ValueError('Import report summary disagrees with entry outcomes')
    return counts


def validate_report(report):
    if not isinstance(report, dict):
        raise ValueError('Import report must be a JSON object')
    if type(report.get('report_version')) is not int or report['report_version'] != 1:
        raise ValueError('Unsupported import report version; expected 1')
    if 'collection_proposal' not in report:
        raise ValueError('Import report has no Collection proposal; use import --propose-collection')
    proposal = report['collection_proposal']
    if not isinstance(proposal, dict) or not {'acquisition', 'status', 'reason', 'library_identity'} <= proposal.keys():
        raise ValueError('Invalid Collection proposal fields')
    acquisition, status, reason = (proposal[key] for key in ('acquisition', 'status', 'reason'))
    if (acquisition not in ('preview', 'completed', 'stopped')
            or status not in ('preview', 'eligible', 'empty', 'refused')
            or (status in ('empty', 'refused') and not (isinstance(reason, str) and reason))
            or (status in ('preview', 'eligible') and reason is not None)):
        raise ValueError('Invalid Collection proposal status or reason')
    source = report.get('source')
    if (not isinstance(source, dict) or source.get('type') != 'playlist'
            or not isinstance(source.get('url'), str)
            or source.get('discovery') not in ('complete', 'failed', 'interrupted')):
        raise ValueError('Expected a playlist import report with a discovery status')
    if source['discovery'] == 'complete' and (
            not source['url'] or not isinstance(source.get('id'), str) or not PLAYLIST_ID.fullmatch(source['id'])
            or any(key not in source or (source[key] is not None and not isinstance(source[key], str))
                   for key in ('title', 'description'))):
        raise ValueError('Invalid playlist source identity or metadata types')
    apply, interrupted, exit_status = (report.get(key) for key in ('apply', 'interrupted', 'exit_status'))
    errors, index = report.get('errors'), report.get('index')
    library = report.get('library')
    if (type(apply) is not bool or type(interrupted) is not bool or type(exit_status) is not int
            or exit_status not in (0, 1, 2, 130) or interrupted != (exit_status == 130)
            or not isinstance(errors, list) or any(not isinstance(error, str) for error in errors)
            or not isinstance(index, dict)
            or 'library' not in report
            or (library is not None and (not isinstance(library, str) or '\x00' in library
                                        or not Path(library).is_absolute()))):
        raise ValueError('Invalid import report state or library path')
    index_status = index.get('status')
    if (index_status not in ('not_requested', 'preview_no_writes', 'suppressed', 'rebuilt', 'failed',
                             'no_new_bookmarks', 'interrupted', 'preflight_refused')
            or ('diagnostics' in index and not isinstance(index['diagnostics'], str))
            or ('exit_status' in index and type(index['exit_status']) is not int)
            or (index_status == 'rebuilt' and index.get('exit_status') != 0)
            or (index_status == 'failed' and (index.get('exit_status') == 0 or exit_status == 0))
            or (index_status == 'preflight_refused') != (exit_status == 2)):
        raise ValueError('Invalid or inconsistent import index result')
    counts = validate_entries(report, source, acquisition)
    has_members = any(counts[outcome] for outcome in MEMBER_OUTCOMES)
    if acquisition == 'stopped':
        consistent = (status == 'refused' and exit_status != 0
                      and (exit_status != 1 or (apply and source['discovery'] == 'complete')))
    else:
        expected_exit = int(bool(errors) or any(counts[outcome] for outcome in OMISSION_OUTCOMES))
        consistent = source['discovery'] == 'complete' and library is not None and exit_status == expected_exit
        if acquisition == 'preview':
            consistent &= not apply and status == 'preview' and index_status == 'preview_no_writes'
        else:
            final_index = ('rebuilt', 'failed') if counts['created'] else ('no_new_bookmarks',)
            consistent &= (apply and status in ('refused', 'eligible' if has_members else 'empty')
                           and index_status in (*final_index, 'suppressed'))
    if not consistent:
        raise ValueError('Inconsistent acquisition completion or proposal eligibility')
    binding = proposal['library_identity']
    if status == 'eligible':
        if (not isinstance(binding, dict) or set(binding) != {'root', 'bookmarks'}
                or any(not isinstance(identity, dict) or set(identity) != {'device', 'inode'}
                       or any(type(identity.get(key)) is not int
                       or identity[key] < 0 for key in ('device', 'inode'))
                       for identity in binding.values())):
            raise ValueError('Missing or invalid proposal library identity')
    elif binding is not None:
        raise ValueError('Unexpected library identity for an ineligible proposal')
