from __future__ import annotations

"""Exponential backoff helper used by capture modules."""

import os
import time


class Backoff:
    """Simple exponential backoff helper.

    Parameters
    ----------
    base:
        Starting delay before the first retry.
    max_sleep, maximum:
        Optional aliases for the ceiling delay.
        ``max_sleep`` is kept for backwards compatibility.
    """

    def __init__(
        self, base: float = 0.5, *, max_sleep: float | None = None, maximum: float | None = None
    ) -> None:
        if maximum is not None and max_sleep is not None:
            raise ValueError("Specify only one of max_sleep or maximum")
        self.base = base
        if maximum is not None:
            self.maximum = maximum
        elif max_sleep is not None:
            self.maximum = max_sleep
        else:
            self.maximum = float(os.getenv("VMS26_RECONNECT_MAXSLEEP", "8"))
        self._n = 0

    def reset(self) -> None:
        self._n = 0

    def next(self) -> float:
        delay = min(self.base * (2**self._n), self.maximum)
        self._n += 1
        return delay

    def sleep(self) -> None:
        time.sleep(self.next())
