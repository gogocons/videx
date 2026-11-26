"""Open the existing Videx library index with the default HTML application."""

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys

from videx_config import default_library


def check_index(index):
    if not stat.S_ISREG(index.lstat().st_mode):
        raise ValueError('expected a regular index file, without symlinks')

    # Check read access. Avoid following a link or blocking on a FIFO if the
    # entry is replaced after lstat, and validate the opened file itself.
    fd = os.open(index, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('expected a regular index file')
        if not info.st_size:
            raise ValueError('index file is empty')
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='videx open', description=__doc__, allow_abbrev=False)
    parser.add_argument('--library', metavar='DIR', help='Library root (default: configured library)')
    args = parser.parse_args(argv)
    try:
        if args.library == '':
            raise ValueError('--library requires a nonempty path')
        selected = default_library() if args.library is None else Path(args.library).expanduser()
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    index = selected / 'index.html'
    try:
        library = selected.resolve()
        index = library / 'index.html'
        if library.name == 'bookmarks':
            parser.error(f'{index}: --library expects the library root, not its bookmarks/ directory')
        if not stat.S_ISDIR(library.stat().st_mode):
            raise ValueError('library path is not a directory')
        if sys.platform == 'darwin':
            launcher = 'open'
        elif sys.platform == 'linux':
            launcher = 'xdg-open'
        else:
            raise ValueError(f'unsupported platform: {sys.platform}')

        check_index(index)
        if launcher == 'xdg-open' and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise ValueError('no graphical desktop session for xdg-open (DISPLAY and WAYLAND_DISPLAY are unset)')
        try:
            subprocess.run([launcher, str(index)], check=True)
        except FileNotFoundError as exc:
            raise ValueError(f'desktop launcher {launcher} is unavailable on PATH') from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f'desktop launcher {launcher} failed (status {exc.returncode})') from exc
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'ERROR: Cannot open {index}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
