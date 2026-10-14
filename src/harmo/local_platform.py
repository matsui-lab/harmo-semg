"""Small OS boundary for the local browser service. No global environment edits."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys


def data_directory(override=None):
    if override: return Path(override).expanduser().resolve()
    if sys.platform == 'darwin': return Path.home()/'Library/Application Support/Harmo'
    if sys.platform == 'win32': return Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))/'Harmo'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'harmo'


def lock_file(stream, unlock=False):
    if sys.platform == 'win32':
        import msvcrt
        stream.seek(0)
        if not unlock:
            stream.write(b'0'); stream.flush(); stream.seek(0)
        try: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        except OSError as exc: raise BlockingIOError('Application already running') from exc
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX|fcntl.LOCK_NB)


@contextmanager
def exclusive_file(path):
    with Path(path).open('a+b') as stream:
        lock_file(stream)
        try: yield
        finally: lock_file(stream, unlock=True)
