"""Process-lifetime advisory ownership; PID reuse/age never proves death."""

import errno
import os
from pathlib import Path


def acquire(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            raise RuntimeError("Safe run recovery requires supported process-lifetime file locks.")
        return fd
    except OSError as error:
        os.close(fd)
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return None
        raise
    except BaseException:
        os.close(fd)
        raise


def release(fd):
    # Closing an owned descriptor releases its advisory lock, including on
    # process death. Never unlink a lock file: another opener could otherwise
    # acquire a different inode while the original owner still runs.
    os.close(fd)
