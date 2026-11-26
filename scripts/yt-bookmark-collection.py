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


spec = spec_from_file_location('bookmark_tags', Path(__file__).with_name('yt-bookmark-tag.py'))
tagger = module_from_spec(spec)
spec.loader.exec_module(tagger)

READ_OPERATIONS = {'list', 'show'}


class Refusal(ValueError):
    def __init__(self, category, message, status=2):
        super().__init__(message)
        self.category = category
        self.status = status


def parse_args(argv):
    parser = argparse.ArgumentParser(prog='videx collection', description=__doc__,
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest='operation', required=True)
    operations = {
        'list': 'List Collections and stored member counts',
        'show': 'Inspect a Collection and its member availability',
        'create': 'Create an empty Collection',
        'rename': 'Change a Collection name',
        'describe': 'Set a description or clear it with empty text',
        'add': 'Append existing bookmarks by video ID',
        'remove': 'Remove member references by video ID',
        'delete': 'Delete a Collection record',
    }
    for operation, help_text in operations.items():
        command = commands.add_parser(operation, help=help_text, description=help_text,
                                      allow_abbrev=False,
                                      epilog='Put options before -- to pass literal option-like arguments.')
        command.add_argument('--library', metavar='DIR', help='Library root (default: configured library)')
        command.add_argument('--json', action='store_true', help='Print one JSON report')
        if operation not in READ_OPERATIONS:
            command.add_argument('--apply', action='store_true', help='Save changes (default: preview)')
        if operation not in {'list', 'create'}:
            command.add_argument('target', metavar='TARGET', help='Collection name, id:UUID, or name:TEXT')
        if operation == 'create':
            command.add_argument('name', metavar='NAME')
            command.add_argument('--description', metavar='TEXT', default='', help='Optional description')
        if operation == 'rename':
            command.add_argument('name', metavar='NEW_NAME')
        if operation == 'describe':
            command.add_argument('description', metavar='TEXT')
        if operation in {'add', 'remove'}:
            command.add_argument('members', metavar='VIDEO_ID', nargs='+')
    return parser.parse_args(argv)


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


def edited_record(args, identity, before, names, ids):
    operation = args.operation
    if operation == 'delete':
        return None
    after = before.copy() if before is not None else {'members': []}
    if operation in {'create', 'rename'}:
        name = collection_name(args.name)
        owner = names.get(collection_name_key(name))
        if owner is not None and owner != identity:
            raise ValueError(f'A Collection already uses the name {name!r}')
        after['name'] = name
    if operation in {'create', 'describe'}:
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
            missing = [video_id for video_id in requested if video_id not in ids]
            if missing:
                raise ValueError('Video IDs not present in library: ' + ', '.join(missing))
            after['members'] = list(dict.fromkeys(before['members'] + list(requested)))
        else:
            after['members'] = [video_id for video_id in before['members'] if video_id not in requested]
    return after


def mutate(args, library, path, catalog, names, identity, report):
    collections = catalog['collections']
    before = collections.get(identity)
    report['before'] = record_view(identity, before)
    try:
        ids = tagger.archive_ids(library, strict=True)
    except (OSError, ValueError) as exc:
        raise Refusal('INVENTORY_ERROR', str(exc)) from exc
    after = edited_record(args, identity, before, names, ids)
    report['after'] = record_view(identity, after)
    if before == after:
        report.update(result='no_change', exit_status=0)
        return
    if not args.apply:
        report.update(result='would_change', exit_status=0)
        return
    if args.operation == 'create':
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
    if args.operation != 'create':
        identity = target_id(args.target, names, collections)
        if identity is None:
            raise Refusal('COLLECTION_NOT_FOUND', f'Collection not found: {args.target!r}',
                          1 if args.operation == 'show' else 2)
    if args.operation == 'show':
        show(library, record_view(identity, collections[identity]), report)
    else:
        mutate(args, library, path, catalog, names, identity, report)


def print_record(label, record):
    if record is None:
        print(f'{label}: (none)')
        return
    print(f'{label}: {record["name"]} [{record["id"] or "ID assigned on apply"}]')
    if 'description' in record:
        print(record['description'])


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
            print('No files modified. Repeat with --apply to save.')
    for issue in report['issues']:
        level = 'WARNING' if report['result'] == 'partial' else 'ERROR'
        location = f'{issue["path"]}: ' if 'path' in issue else ''
        print(f'{level}: {location}{issue["message"]}', file=sys.stderr)


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
