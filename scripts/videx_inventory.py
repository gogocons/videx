"""Bookmark-only filesystem inspection; callers decide how to handle issues."""
import os
from pathlib import Path
import stat


def scan_bookmarks(root):
    """Return (regular HTML paths, [(path, error)], root_available).

    Do not resolve the root or follow discovered links. A successful scan is
    not a snapshot against concurrent filesystem changes.
    """
    root = Path(root)
    paths, issues = [], []
    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise OSError('bookmark collection must be a real directory, not a symlink or special file')
    except OSError as exc:
        return paths, [(root, exc)], False

    def walk(directory):
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            issues.append((directory, exc))
            return
        for entry in children:
            path = Path(entry.path)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    walk(path)
                elif path.name.lower() == 'index.html':
                    continue
                elif stat.S_ISLNK(mode):
                    raise OSError('symbolic links prevent a complete local inventory')
                elif path.suffix == '.html':
                    if not stat.S_ISREG(mode):
                        raise OSError('bookmark is not a regular file')
                    paths.append(path)
            except OSError as exc:
                issues.append((path, exc))

    walk(root)
    return sorted(paths), issues, not any(path == root for path, _ in issues)


def read_bookmark_text(path, *, preserve_newlines=False):
    """Read UTF-8 only after checking that the final entry is a regular file."""
    path = Path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise OSError(f'{path}: bookmark must be a regular file, not a symlink or special file')
    if preserve_newlines:
        with path.open(encoding='utf-8', newline='') as source:
            return source.read()
    return path.read_text(encoding='utf-8')
