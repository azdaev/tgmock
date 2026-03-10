from __future__ import annotations

import itertools
import os
import threading

_lock = threading.Lock()
_counter = itertools.count(start=1)


def _worker_offset() -> int:
    """Return a large offset per pytest-xdist worker to avoid user_id collisions."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    try:
        n = int(worker.replace("gw", ""))
    except ValueError:
        n = 0
    return n * 100_000


_BASE = _worker_offset()


def next_user_id() -> int:
    """Return a unique user_id for a test. Thread-safe, xdist-safe."""
    with _lock:
        return _BASE + next(_counter)
