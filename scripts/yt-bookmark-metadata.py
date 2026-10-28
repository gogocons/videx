#!/usr/bin/env python3
"""Curated, stable metadata schema shared by acquisition and offline migration.

Only preserve fields with durable archival value. Streaming URLs, caption manifests,
and format lists are intentionally omitted; they are not subtitle transcripts.
"""
SCHEMA_VERSION = 2
FIELDS = (
    'id', 'title', 'fulltitle', 'alt_title', 'webpage_url', 'original_url',
    'description', 'duration', 'upload_date', 'release_date', 'timestamp',
    'uploader', 'uploader_id', 'uploader_url', 'channel', 'channel_id',
    'channel_url', 'channel_follower_count', 'creator', 'artist',
    'view_count', 'like_count', 'comment_count', 'repost_count',
    'categories', 'tags', 'chapters', 'language', 'availability',
    'live_status', 'was_live', 'age_limit', 'license',
)

def curate(metadata):
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be a JSON object')
    if not metadata.get('id') or not metadata.get('title'):
        raise ValueError('metadata is missing video ID or title')
    result = {k: metadata[k] for k in FIELDS if k in metadata and metadata[k] is not None}
    result['bookmark_schema_version'] = SCHEMA_VERSION
    return result
