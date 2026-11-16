#!/usr/bin/env python3
"""Curated, stable metadata schema shared by acquisition and offline migration.

Only preserve fields with durable archival value. Streaming URLs, caption manifests,
and format lists are intentionally omitted; they are not subtitle transcripts.
"""
import math
import sys
from videx_identity import valid_id

SCHEMA_VERSION = 2
MAX_DISPLAY_SECONDS = 2**53 - 1
MAX_BROWSER_INTEGER = int(sys.float_info.max)
FIELDS = (
    'id', 'title', 'fulltitle', 'alt_title', 'webpage_url', 'original_url',
    'description', 'duration', 'upload_date', 'release_date', 'timestamp',
    'uploader', 'uploader_id', 'uploader_url', 'channel', 'channel_id',
    'channel_url', 'channel_follower_count', 'creator', 'artist',
    'view_count', 'like_count', 'comment_count', 'repost_count',
    'categories', 'tags', 'chapters', 'language', 'availability',
    'live_status', 'was_live', 'age_limit', 'license',
)


def validate_archival_numbers(metadata):
    """Reject browser-nonfinite numbers only in retained archival fields."""
    def inspect(value, location):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f'{location}: archival numbers must be finite')
        if isinstance(value, int) and abs(value) > MAX_BROWSER_INTEGER:
            raise ValueError(f'{location}: archival integer exceeds the browser finite numeric range')
        if isinstance(value, dict):
            for key, child in value.items():
                inspect(child, f'{location}[{key!r}]')
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                inspect(child, f'{location}[{index}]')

    for field in FIELDS:
        if field in metadata:
            inspect(metadata[field], field)


def display_seconds(value):
    """Return original seconds suitable for display; never coerce or truncate."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_DISPLAY_SECONDS:
        return None
    return value


def curate(metadata):
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be a JSON object')
    if not metadata.get('id') or not metadata.get('title'):
        raise ValueError('metadata is missing video ID or title')
    if not valid_id(metadata['id']):
        raise ValueError('metadata ID must be an 11-character string containing only letters, digits, _ or -')
    result = {k: metadata[k] for k in FIELDS if k in metadata and metadata[k] is not None}
    validate_archival_numbers(result)
    result['bookmark_schema_version'] = SCHEMA_VERSION
    return result
