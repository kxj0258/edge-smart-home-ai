"""Bounded nonblocking endpoint dispatch; shutdown never needs a queue sentinel."""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Callable, Any


class EndpointDispatcher:
    def __init__(self, handler: Callable[[Any], None], capacity: int = 16,
                 on_error: Callable[[Exception], None] | None = None):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._queue = Queue(maxsize=capacity)
        self._handler = handler
        self._on_error = on_error
        self._stop = Event()
        self._lock = Lock()
        self._counts = dict(submitted=0, completed=0, failed=0, dropped=0,
                            cancelled=0, rejected_after_close=0, high_water=0)
        self._thread = Thread(target=self._run, name="endpoint-dispatch", daemon=True)
        self._thread.start()

    def submit(self, item: Any) -> bool:
        with self._lock:
            if self._stop.is_set():
                self._counts["rejected_after_close"] += 1
                return False
            try:
                self._queue.put_nowait(item)
            except Full:
                self._counts["dropped"] += 1
                return False
            self._counts["submitted"] += 1
            self._counts["high_water"] = max(self._counts["high_water"], self._queue.qsize())
            return True

    def close(self, timeout: float = 2.0) -> None:
        # In-flight work is not forcibly killed; metrics disclose whether it remains alive.
        with self._lock:
            self._stop.set()
        self._thread.join(timeout)
        with self._lock:
            while True:
                try:
                    self._queue.get_nowait()
                except Empty:
                    break
                self._counts["cancelled"] += 1

    def metrics(self) -> dict:
        with self._lock:
            return {**self._counts, "queue_depth": self._queue.qsize(),
                    "worker_alive": self._thread.is_alive()}

    def _run(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=.05)
            except Empty:
                continue
            with self._lock:
                if self._stop.is_set():
                    self._counts["cancelled"] += 1
                    return
            try:
                self._handler(item)
            except Exception as error:
                with self._lock:
                    self._counts["failed"] += 1
                if self._on_error:
                    try:
                        self._on_error(error)
                    except Exception:
                        pass
            else:
                with self._lock:
                    self._counts["completed"] += 1
