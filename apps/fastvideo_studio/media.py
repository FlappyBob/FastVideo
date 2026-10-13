# SPDX-License-Identifier: Apache-2.0
"""Process-local media transactions and atomic publication for Studio jobs.

Callers updating a job must hold ``media_lock(job.id)`` through both the media
operation and the corresponding job state/database update. Media readers use the
same reentrant lock. Multiple-job readers acquire locks in sorted order.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from threading import Lock, RLock
from weakref import WeakValueDictionary

_registry_guard = Lock()
_locks: WeakValueDictionary[str, RLock] = WeakValueDictionary()


def media_lock(job_id: str) -> RLock:
    """Return the shared reentrant lock for a job in this Studio process."""
    with _registry_guard:
        lock = _locks.get(str(job_id))
        if lock is None:
            lock = RLock()
            _locks[str(job_id)] = lock
        return lock


@contextmanager
def media_locks(job_ids: Iterable[str]) -> Iterator[None]:
    """Hold all requested job locks in a consistent order to avoid deadlocks."""
    with ExitStack() as stack:
        for job_id in sorted(set(str(job_id) for job_id in job_ids)):
            stack.enter_context(media_lock(job_id))
        yield


@contextmanager
def atomic_output(out_path: str) -> Iterator[str]:
    """Write to a unique sibling temporary file, then atomically publish it."""
    target = Path(out_path)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        yield temporary
        os.replace(temporary, out_path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
