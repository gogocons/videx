"""Load and publish library catalogs, with opt-in Collection validation."""

from contextlib import suppress
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import NamedTuple, Optional
import unicodedata

from videx_identity import valid_id


COLLECTION_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')


def empty():
    return {'version': 1, 'bookmarks': {}, 'collections': {}}


def catalog_present(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError(f'{path}: catalog must be a regular file, not a symlink or special file')
    return True


def validate_structure(path, obj):
    if (not isinstance(obj, dict) or obj.get('version') != 1
            or not isinstance(obj.get('bookmarks'), dict)
            or not isinstance(obj.get('collections'), dict)):
        raise ValueError('Unsupported or invalid catalog schema; refusing to modify')
    for video_id, record in obj['bookmarks'].items():
        if not valid_id(video_id) or not isinstance(record, dict) or not isinstance(record.get('tags', []), list):
            raise ValueError(f'Invalid catalog entry: {video_id}')
        if any(not isinstance(value, str) for value in record.get('tags', [])):
            raise ValueError(f'{path}: tags for {video_id} must contain only strings')


def load_catalog(path):
    """Return the catalog status and document without interpreting Collection records."""
    if not catalog_present(path):
        return 'missing', empty()

    def catalog_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'{path}: Duplicate catalog object member {key!r}')
            result[key] = value
        return result

    obj = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=catalog_object)
    validate_structure(path, obj)
    return 'present', obj


def load(path):
    """Read a catalog for tag consumers that only need the document."""
    return load_catalog(path)[1]


def collection_name(value):
    if not isinstance(value, str) or any(
            unicodedata.category(char) in {'Cc', 'Cs', 'Zl', 'Zp'} for char in value):
        raise ValueError('name must be text without control characters, surrogates, or line separators')
    name = unicodedata.normalize('NFC', value.strip())
    if not 1 <= len(name) <= 80:
        raise ValueError('name must contain 1–80 characters after trimming and normalization')
    return name


def collection_name_key(name):
    return unicodedata.normalize('NFC', name.casefold())


def collection_description(value):
    if not isinstance(value, str):
        raise ValueError('description must be text')
    text = value.replace('\r\n', '\n').replace('\r', '\n')
    if any(char not in '\n\t' and unicodedata.category(char) in {'Cc', 'Cs'} for char in text):
        raise ValueError('description contains an unsupported control character or surrogate')
    return text.strip() or None


def validate_collections(collections):
    """Validate the whole section and return normalized name-to-ID lookup keys."""
    names = {}
    for identity, record in collections.items():
        try:
            if not COLLECTION_ID.fullmatch(identity):
                raise ValueError('key must be a lowercase, hyphenated UUIDv4')
            if not isinstance(record, dict):
                raise ValueError('record must be an object')
            name = collection_name(record.get('name'))
            if name != record['name']:
                raise ValueError('name must already be trimmed and NFC-normalized')
            key = collection_name_key(name)
            if key in names:
                raise ValueError(f'name conflicts with Collection {names[key]}')
            if 'description' in record:
                description = collection_description(record['description'])
                if description is None or description != record['description']:
                    raise ValueError('description must be canonical and omitted when blank')
            members = record.get('members')
            if not isinstance(members, list) or any(not valid_id(video_id) for video_id in members):
                raise ValueError('members must be an array of canonical video IDs')
            if len(set(members)) != len(members):
                raise ValueError('members must not contain duplicate video IDs')
        except ValueError as exc:
            raise ValueError(f'Invalid Collection {identity!r}: {exc}') from exc
        names[key] = identity
    return names


class CatalogPublication(NamedTuple):
    path: Path
    backup: Optional[Path]


class CatalogWriteError(OSError):
    def __init__(self, cause, backup):
        super().__init__(str(cause))
        self.backup = backup


class CatalogWriteInterrupted(KeyboardInterrupt):
    def __init__(self, backup, publication):
        self.backup = backup
        self.publication = publication


def write_catalog(path, obj):
    """Stage a catalog and its backup, then publish each with atomic replacement."""
    staged = backup_stage = backup_path = backup = None
    publication = 'not_published'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.catalog-', suffix='.tmp', delete=False) as stream:
            staged = Path(stream.name)
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if catalog_present(path):
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            backup_path = path.with_name(f'catalog.json.bak-{timestamp}')
            suffix = 0
            while os.path.lexists(backup_path):
                suffix += 1
                backup_path = path.with_name(f'catalog.json.bak-{timestamp}-{suffix}')
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.catalog-backup-',
                                             suffix='.tmp', delete=False) as stream:
                backup_stage = Path(stream.name)
            shutil.copy2(path, backup_stage)
            os.replace(backup_stage, backup_path)
            backup = backup_path
        publication = 'unknown'
        os.replace(staged, path)
        publication = 'published'
        return CatalogPublication(path, backup)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise CatalogWriteError(exc, backup) from exc
    except KeyboardInterrupt as exc:
        if backup is None and backup_stage is not None:
            # An interrupt can arrive just after the atomic backup rename.
            with suppress(OSError):
                if not backup_stage.exists() and backup_path.is_file():
                    backup = backup_path
        raise CatalogWriteInterrupted(backup, publication) from exc
    finally:
        for candidate in (staged, backup_stage):
            if candidate is not None:
                with suppress(OSError):
                    candidate.unlink()
