"""Thread-safe ring buffer for camera frames."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class FrameInfo:
    """Lightweight structure with frame metadata."""

    w: int = 0
    h: int = 0
    fps: float = 0.0


class FrameBus:
    """Ring buffer holding at most two frames.

    The buffer keeps only the most recent frames to minimise latency. ``put``
    drops the oldest frame when the capacity is exceeded. ``get_latest`` waits
    up to ``timeout_ms`` for a frame and returns ``None`` on timeout.
    """

    def __init__(self) -> None:
        self._buf: deque[np.ndarray] = deque(maxlen=2)
        self._lock = threading.Lock()
        self._cond = asyncio.Condition()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._info = FrameInfo()
        self._last_ts: Optional[float] = None
        self._seq = 0

    # ------------------------------------------------------------------
    def put(self, frame: np.ndarray) -> None:
        """Insert ``frame`` into the buffer, dropping older ones."""
        if frame is None:
            return
        with self._lock:
            self._buf.append(frame)
            h, w = frame.shape[:2]
            if self._info.w != w or self._info.h != h:
                self._info.w, self._info.h = w, h
            now = time.time()
            if self._last_ts is not None:
                dt = now - self._last_ts
                if dt > 0:
                    self._info.fps = 1.0 / dt
            self._last_ts = now
            self._seq += 1
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._wake_waiters)

    # ------------------------------------------------------------------
    async def get_latest_async(self, timeout_ms: int) -> Optional[np.ndarray]:
        """Await the newest frame or ``None`` if none arrives in time."""
        loop = asyncio.get_running_loop()
        if self._loop is None or self._loop.is_closed():
            self._loop = loop
        deadline = loop.time() + timeout_ms / 1000.0
        while True:
            with self._lock:
                if self._buf:
                    return self._buf[-1].copy()
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            if hasattr(asyncio, "timeout"):
                # Python 3.11+
                try:
                    async with asyncio.timeout(remaining):
                        async with self._cond:
                            await self._cond.wait()
                except TimeoutError:
                    return None
            else:
                # Python 3.10 fallback
                async with self._cond:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return None

    # ------------------------------------------------------------------
    def get_latest(self, timeout_ms: int) -> Optional[np.ndarray]:
        """Return the newest frame or ``None`` if none arrives in time."""
        return asyncio.run(self.get_latest_async(timeout_ms))

    # ------------------------------------------------------------------
    def _wake_waiters(self) -> None:
        async def _inner() -> None:
            async with self._cond:
                self._cond.notify_all()

        asyncio.create_task(_inner())

    # ------------------------------------------------------------------
    def info(self) -> FrameInfo:
        """Return the latest frame metadata."""
        with self._lock:
            return FrameInfo(self._info.w, self._info.h, self._info.fps)

    @property
    def seq(self) -> int:
        """Return the last published sequence number."""
        with self._lock:
            return self._seq
