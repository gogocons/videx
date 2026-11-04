"""Resolve Videx's optional, machine-local default library directory."""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config.local.json'


def default_library():
    """Return configured library, or legacy project root if not configured."""
    if not CONFIG_PATH.exists():
        return PROJECT_ROOT
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read {CONFIG_PATH}: {exc}') from exc
    if not isinstance(config, dict) or not isinstance(config.get('library'), str) or not config['library'].strip():
        raise ValueError(f'{CONFIG_PATH}: expected a nonempty string "library"')
    path = Path(config['library']).expanduser()
    if not path.is_absolute():
        raise ValueError(f'{CONFIG_PATH}: library must be an absolute path or start with ~/')
    path = path.resolve()
    if path.name == 'bookmarks':
        raise ValueError(f'{CONFIG_PATH}: specify library root, not bookmarks/')
    return path


if __name__ == '__main__':
    import sys
    try:
        print(default_library())
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
