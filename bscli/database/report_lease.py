"""Process-owned report leases; OS releases them even after forced termination."""
from contextlib import contextmanager
import errno
import os


@contextmanager
def report_lease(path, *, blocking=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = open(path, 'a+b')
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(stream.fileno(), mode, 1)
                acquired = True
            except OSError as exc:
                if blocking or exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
