#!/usr/bin/env python3
"""Inspect and manage local bookmark Collections."""

import argparse
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import stat
import sys
import uuid

from videx_catalog import (
    COLLECTION_ID, CatalogWriteError, CatalogWriteInterrupted,
    collection_description, collection_name, collection_name_key, load_catalog,
    validate_collections, validate_structure, write_catalog,
)
from videx_config import default_library
from videx_identity import valid_id
from videx_import_report import OMISSION_OUTCOMES, library_identity, member_ids, read_report


spec = spec_from_file_location('bookmark_tags', Path(__file__).with_name('yt-bookmark-tag.py'))
tagger = module_from_spec(spec)
spec.loader.exec_module(tagger)

READ_OPERATIONS = {'list', 'show'}
CREATION_OPERATIONS = {'create', 'from-import'}

FROM_IMPORT_HELP = r"""Use a report from a completed import run with --playlist, --apply,
--propose-collection, and --json. See ./videx import --help for the
playlist workflow, including saving the report with >.

Collection creation from the report works offline. Preview first, then use
--apply to save. Applying partial membership requires --accept-partial.
Rebuild the index separately after saving.

Examples
  ./videx collection from-import playlist-import.json
  ./videx collection from-import playlist-import.json --apply
  ./videx index
  ./videx open

  To override the proposed metadata and select an explicit library, preview
  with the following command. Repeat with --apply when ready to save.

  ./videx collection from-import playlist-import.json \
    --library "/path/to/library" --name "Field Notes" \
    --description "Videos to revisit."

  Select the same library used for import with --output DIR. After applying,
  run ./videx index DIR and ./videx open --library DIR for that library.

Put options before -- to pass literal option-like arguments."""


class Refusal(ValueError):
    def __init__(self, category, message, status=2):
        super().__init__(message)
        self.category = category
        self.status = status


def parse_args(argv):
    parser = argparse.ArgumentParser(prog='videx collection', description=__doc__,
                                     allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="""Changes preview by default. Pass --apply to save them, then run
./videx index separately to update the library index.

Each operation accepts --library DIR. By default, Videx uses the library in
config.local.json, or the Videx project directory if the file is absent.

Run ./videx collection OPERATION --help for that operation's options.
For playlist imports, see ./videx collection from-import --help and
./videx import --help.""")
    commands = parser.add_subparsers(dest='operation', required=True)
    operations = {
        'list': 'List Collections and stored member counts',
        'show': 'Inspect a Collection and its member availability',
        'create': 'Create an empty Collection',
        'from-import': 'Preview or create a Collection from a completed playlist import report',
        'rename': 'Change a Collection name',
        'describe': 'Set a description or clear it with empty text',
        'add': 'Append existing bookmarks by video ID',
        'remove': 'Remove member references by video ID',
        'delete': 'Delete a Collection record',
    }
    for operation, help_text in operations.items():
        epilog = (FROM_IMPORT_HELP if operation == 'from-import'
                  else 'Put options before -- to pass literal option-like arguments.')
        command = commands.add_parser(operation, help=help_text, description=help_text,
                                      allow_abbrev=False,
                                      formatter_class=argparse.RawDescriptionHelpFormatter, epilog=epilog)
        command.add_argument('--library', metavar='DIR',
                             help='Library root (default: config.local.json library, or Videx project directory)')
        command.add_argument('--json', action='store_true', help='Write a JSON report to standard output')
        if operation not in READ_OPERATIONS:
            command.add_argument('--apply', action='store_true', help='Save changes (default: preview)')
        if operation not in {'list', *CREATION_OPERATIONS}:
            command.add_argument('target', metavar='TARGET', help='Collection name, id:UUID, or name:TEXT')
        if operation == 'create':
            command.add_argument('name', metavar='NAME', help='Name for the new Collection')
            command.add_argument('--description', metavar='TEXT', default='', help='Optional description')
        if operation == 'from-import':
            command.add_argument('report', metavar='REPORT', help='JSON import report file, or - for standard input')
            command.add_argument('--name', metavar='NAME', help='Override the proposed name')
            command.add_argument('--description', metavar='TEXT',
                                 help='Override the proposed description (empty text clears it)')
            command.add_argument('--accept-partial', action='store_true',
                                 help='Approve omitted playlist entries (requires --apply)')
        if operation == 'rename':
            command.add_argument('name', metavar='NEW_NAME', help='New name for the Collection')
        if operation == 'describe':
            command.add_argument('description', metavar='TEXT', help='New description (empty text clears it)')
        if operation in {'add', 'remove'}:
            command.add_argument('members', metavar='VIDEO_ID', nargs='+', help='One or more video IDs')
    args = parser.parse_args(argv)
    if args.operation == 'from-import' and args.accept_partial and not args.apply:
        parser.error('--accept-partial requires --apply')
    return args


def target_id(target, names, collections):
    if target.startswith('id:'):
        identity = target[3:]
        if not COLLECTION_ID.fullmatch(identity):
            raise ValueError('id: requires a lowercase, hyphenated UUIDv4')
        return identity if identity in collections else None
    if target.startswith('name:'):
        target = target[5:]
    return names.get(collection_name_key(collection_name(target)))


def record_view(identity, record):
    if record is None:
        return None
    view = {'id': identity, 'name': record['name'], 'members': record['members']}
    if 'description' in record:
        view['description'] = record['description']
    return view


def show(library, view, report):
    report.update(collection=view, member_findings=[])
    ids, issues, available = tagger.inspect_archive(library)
    for path, error in issues:
        report['issues'].append(dict(category='INVENTORY_ERROR', message=str(error), path=str(path)))
    for video_id, paths in sorted(ids.items()):
        if len(paths) > 1:
            report['issues'].append(dict(category='DUPLICATE_ID', message=f'Duplicate video ID {video_id}',
                                         id=video_id, paths=[str(path) for path in paths]))
    if not available:
        raise Refusal('INVENTORY_UNAVAILABLE', 'Cannot inspect the bookmarks directory')
    for video_id in view['members']:
        paths = ids.get(video_id, [])
        if len(paths) > 1:
            status = 'ambiguous'
        elif issues:
            status = 'unverified'
        else:
            status = 'present' if paths else 'missing'
        report['member_findings'].append(dict(id=video_id, status=status, paths=[str(path) for path in paths]))
    if report['issues']:
        report.update(result='partial', exit_status=1)
    else:
        report.update(result='ok', exit_status=0)


def ensure_unique_name(name, names, identity=None):
    owner = names.get(collection_name_key(name))
    if owner is not None and owner != identity:
        raise ValueError(f'A Collection already uses the name {name!r}')


def require_members(members, ids):
    missing = [video_id for video_id in members if video_id not in ids]
    if missing:
        raise ValueError('Video IDs not present in library: ' + ', '.join(missing))


def new_record(name, description, members):
    record = {'members': members}
    problems = {}
    try:
        record['name'] = collection_name(name)
    except ValueError as exc:
        problems['name'] = str(exc)
    try:
        normalized = collection_description(description)
        if normalized is not None:
            record['description'] = normalized
    except ValueError as exc:
        problems['description'] = str(exc)
    return record, problems


def edited_record(args, identity, before, names, ids):
    operation = args.operation
    if operation == 'delete':
        return None
    after = before.copy()
    if operation == 'rename':
        name = collection_name(args.name)
        ensure_unique_name(name, names, identity)
        after['name'] = name
    if operation == 'describe':
        description = collection_description(args.description)
        if description is None:
            after.pop('description', None)
        else:
            after['description'] = description
    if operation in {'add', 'remove'}:
        requested = dict.fromkeys(args.members)
        invalid = [value for value in requested if not valid_id(value)]
        if invalid:
            raise ValueError('Invalid 11-character video IDs: ' + ', '.join(map(repr, invalid)))
        if operation == 'add':
            require_members(requested, ids)
            after['members'] = list(dict.fromkeys(before['members'] + list(requested)))
        else:
            after['members'] = [video_id for video_id in before['members'] if video_id not in requested]
    return after


def import_candidate(args, library, report):
    try:
        imported = read_report(args.report, sys.stdin)
    except (OSError, ValueError) as exc:
        raise Refusal('IMPORT_REPORT_ERROR', str(exc)) from exc
    proposal = imported['collection_proposal']
    source = imported['source']
    title, description = source.get('title'), source.get('description')
    members = member_ids(imported['entries'])
    omitted = []
    repeats = []
    for entry in imported['entries']:
        outcome = entry['outcome']
        finding = dict(position=entry['position'], id=entry['id'])
        if outcome == 'duplicate_input':
            repeats.append(dict(finding, duplicate_of=entry['duplicate_of']))
        elif outcome in OMISSION_OUTCOMES or outcome == 'not_processed':
            omitted.append(dict(finding, outcome=outcome, reason=entry.get('message')))
    name_input = title if args.name is None else args.name
    description_input = (description or '') if args.description is None else args.description
    candidate, problems = new_record(name_input, description_input, members)
    if name_input is None:
        problems['name'] = 'Playlist title is missing'
    report['proposal'] = dict(status=proposal['status'], title=title, description=description,
                              candidate=candidate, metadata_issues=problems, omitted=omitted,
                              repeats=repeats, partial=bool(omitted))
    if proposal['status'] != 'eligible':
        reason = proposal['reason'] or 'Import preview cannot authorize Collection creation'
        raise Refusal('PROPOSAL_INELIGIBLE', reason)
    try:
        matches = imported['library'] == str(library) and proposal['library_identity'] == library_identity(library)
    except (OSError, ValueError):
        matches = False
    if not matches:
        raise Refusal('LIBRARY_MISMATCH', f"Report is bound to a different or replaced library: {imported['library']!r}")
    return candidate, problems


def mutate(args, library, path, catalog, names, identity, report, imported_candidate=None):
    collections = catalog['collections']
    before = collections.get(identity)
    report['before'] = record_view(identity, before)
    try:
        ids = tagger.archive_ids(library, strict=True)
    except (OSError, ValueError) as exc:
        raise Refusal('INVENTORY_ERROR', str(exc)) from exc
    creating = args.operation in CREATION_OPERATIONS
    if creating:
        if imported_candidate is None:
            after, problems = new_record(args.name, args.description, [])
            if 'name' in after:
                ensure_unique_name(after['name'], names)
        else:
            after, problems = imported_candidate
            require_members(after['members'], ids)
        if problems:
            if imported_candidate is not None:
                for field, message in problems.items():
                    correction = ('supply a valid --name' if field == 'name'
                                  else "replace with --description or clear with --description=''")
                    report['issues'].append(dict(category='INVALID_PROPOSED_METADATA',
                                                 message=f'{message}; {correction}'))
                return
            raise ValueError(next(iter(problems.values())))
        if imported_candidate is not None:
            ensure_unique_name(after['name'], names)
            if args.apply and report['proposal']['partial'] and not args.accept_partial:
                raise Refusal('PARTIAL_APPROVAL_REQUIRED', 'Review the omissions and apply with --accept-partial')
    else:
        after = edited_record(args, identity, before, names, ids)
    report['after'] = record_view(identity, after)
    if before == after:
        report.update(result='no_change', exit_status=0)
        return
    if not args.apply:
        report.update(result='would_change', exit_status=0)
        return
    if creating:
        identity = str(uuid.uuid4())
        while identity in collections:
            identity = str(uuid.uuid4())
        report['after']['id'] = identity
    if after is None:
        del collections[identity]
    else:
        collections[identity] = after
    validate_structure(path, catalog)
    validate_collections(collections)
    report['catalog']['publication'] = 'unknown'
    publication = write_catalog(path, catalog)
    report['catalog'].update(publication='published', path=str(publication.path),
                             backup=str(publication.backup) if publication.backup else None)
    report.update(result='applied', exit_status=0)


def run(args, report):
    try:
        if args.library == '':
            raise ValueError('--library requires a nonempty path')
        selected = default_library() if args.library is None else Path(args.library).expanduser()
        library = selected.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        category = 'CONFIG_ERROR' if args.library is None else 'LIBRARY_ERROR'
        raise Refusal(category, str(exc)) from exc
    report['library'] = str(library)
    candidate = import_candidate(args, library, report) if args.operation == 'from-import' else None
    root = library / 'bookmarks'
    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError(f'{root}: bookmarks must be a real directory')
    except (OSError, ValueError) as exc:
        raise Refusal('LIBRARY_ERROR', str(exc)) from exc
    path = library / 'catalog.json'
    if report['mode'] != 'read':
        report['catalog']['path'] = str(path)
    try:
        status, catalog = load_catalog(path)
        collections = catalog['collections']
        names = validate_collections(collections)
    except (OSError, ValueError, RecursionError) as exc:
        report['catalog']['status'] = 'unavailable'
        raise Refusal('CATALOG_ERROR', f'{path}: {exc}') from exc
    report['catalog']['status'] = status
    if args.operation == 'list':
        report['collections'] = [
            dict(id=identity, name=collections[identity]['name'],
                 member_count=len(collections[identity]['members']))
            for _, identity in sorted(names.items())
        ]
        report.update(result='ok', exit_status=0)
        return
    identity = None
    if args.operation not in CREATION_OPERATIONS:
        identity = target_id(args.target, names, collections)
        if identity is None:
            raise Refusal('COLLECTION_NOT_FOUND', f'Collection not found: {args.target!r}',
                          1 if args.operation == 'show' else 2)
    if args.operation == 'show':
        show(library, record_view(identity, collections[identity]), report)
    else:
        mutate(args, library, path, catalog, names, identity, report, candidate)


def print_record(label, record):
    if record is None:
        print(f'{label}: (none)')
        return
    print(f'{label}: {record["name"]} [{record["id"] or "ID assigned on apply"}]')
    if 'description' in record:
        print(record['description'])


def safe_text(value):
    return ''.join(char if char.isprintable() or char == '\t' else ascii(char)[1:-1] for char in value)


def print_proposal(proposal):
    candidate = proposal['candidate']
    print(f"Collection proposal: {proposal['status']}")
    if 'name' in candidate:
        print('  Name: ' + safe_text(candidate['name']))
    else:
        title = proposal['title']
        print('  Playlist title: ' + ('(missing)' if title is None else ascii(title)))
    if 'description' in candidate:
        print('  Description:')
        for line in candidate['description'].split('\n'):
            print('    ' + safe_text(line))
    elif 'description' in proposal['metadata_issues']:
        print('  Playlist description: ' + ascii(proposal['description']))
    else:
        print('  Description: (none)')
    print('  Members in playlist order: ' + (', '.join(candidate['members']) or '(none)'))
    for entry in proposal['omitted']:
        reason = entry['reason']
        print(f"  Omitted position {entry['position']} ({entry['id'] or 'unknown ID'}): "
              f"{entry['outcome'].replace('_', ' ')}" + (f' — {ascii(reason)}' if reason else ''))
    for entry in proposal['repeats']:
        print(f"  Position {entry['position']} ({entry['id']}) repeats position {entry['duplicate_of']}")
    if proposal['partial'] and proposal['status'] == 'eligible':
        print('  Applying these omissions requires --accept-partial.')


def emit(args, report):
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
        return
    if args.operation == 'list' and 'collections' in report:
        for item in report['collections']:
            print(f'{item["name"]} [{item["id"]}] ({item["member_count"]} members)')
        if not report['collections']:
            print('No Collections.')
    elif args.operation == 'show' and 'collection' in report:
        print_record('Collection', report['collection'])
        for member in report['member_findings']:
            paths = f' ({", ".join(member["paths"])})' if member['paths'] else ''
            print(f'  {member["id"]}: {member["status"]}{paths}')
        if not report['collection']['members']:
            print('No members.')
    elif report['mode'] != 'read':
        label = 'APPLY' if args.apply else 'DRY RUN'
        print(f'{label}: {args.operation} | {getattr(args, "target", getattr(args, "name", ""))!r}'
              f' | {report["library"] or args.library or "(configured library)"}')
        if args.operation == 'from-import':
            if 'proposal' in report:
                print_proposal(report['proposal'])
            if report['result'] == 'applied':
                print(f"  Collection ID: {report['after']['id']}")
        else:
            for key in ('before', 'after'):
                record = report[key]
                if record is not None or report['result'] in {'no_change', 'would_change', 'applied'}:
                    print_record(key.capitalize(), record)
                    if record is not None:
                        print('  Members: ' + (', '.join(record['members']) or '(none)'))
        catalog = report['catalog']
        if catalog['backup']:
            print(f'BACKUP: {catalog["backup"]}')
        if catalog['publication'] == 'published':
            print(f'UPDATED: {catalog["path"]}')
            print('Index rebuilds are separate.')
        elif report['result'] == 'no_change':
            print('No changes needed.')
        elif report['result'] == 'would_change':
            apply_flags = '--apply --accept-partial' if report.get('proposal', {}).get('partial') else '--apply'
            print(f'No files modified. Repeat with {apply_flags} to save.')
    for issue in report['issues']:
        level = 'WARNING' if report['result'] == 'partial' else 'ERROR'
        location = f'{issue["path"]}: ' if 'path' in issue else ''
        message = issue['message']
        if args.operation == 'from-import':
            message = safe_text(message)
        print(f'{level}: {location}{message}', file=sys.stderr)


def main(argv=None):
    args = parse_args(argv)
    read_only = args.operation in READ_OPERATIONS
    mode = 'read' if read_only else 'apply' if args.apply else 'preview'
    report = dict(report_version=1, operation=args.operation, library=None,
                  catalog=dict(status='not_read'), mode=mode, result='refused',
                  issues=[], exit_status=2)
    if not read_only:
        report.update(before=None, after=None)
        report['catalog'].update(path=None, backup=None, publication='not_published')
    try:
        run(args, report)
    except Refusal as exc:
        report['issues'].append(dict(category=exc.category, message=str(exc)))
        report['exit_status'] = exc.status
    except (ValueError, OSError) as exc:
        category = 'VALIDATION_ERROR'
        if isinstance(exc, CatalogWriteError):
            category = 'PUBLICATION_ERROR'
            report['catalog'].update(publication='not_published', backup=str(exc.backup) if exc.backup else None)
        report['issues'].append(dict(category=category, message=str(exc)))
    except KeyboardInterrupt as exc:
        report.update(result='interrupted', exit_status=130)
        if isinstance(exc, CatalogWriteInterrupted):
            report['catalog'].update(publication=exc.publication, backup=str(exc.backup) if exc.backup else None)
        message = 'Interrupted.'
        if report['catalog'].get('publication') == 'unknown':
            message += ' Catalog replacement may have completed. Reread the catalog to verify.'
        report['issues'].append(dict(category='INTERRUPTED', message=message))
    emit(args, report)
    return report['exit_status']


if __name__ == '__main__':
    sys.exit(main())
