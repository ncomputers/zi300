from __future__ import annotations

import asyncio
from typing import Optional

from .rtsp_client import ffprobe_check

CANDIDATES = [
    "/Streaming/Channels/101",
    "/cam/realmonitor?channel=1&subtype=0",
    "/live",
    "/stream1",
]


def _check(url: str) -> bool:
    try:
        code, out, _ = asyncio.run(ffprobe_check(url, 5000))
    except Exception:
        return False
    return code == 0 and b"codec_type=video" in out


def probe_rtsp_base(host: str, user: Optional[str] = None, password: Optional[str] = None) -> str:
    """Return first working RTSP URL built from ``host`` and ``CANDIDATES``.

    ``host`` may include a port. Credentials are optional.
    """
    auth = ""
    if user:
        auth = f"{user}:{password or ''}@"
    base = f"rtsp://{auth}{host}"
    for path in CANDIDATES:
        url = base + path
        if _check(url):
            return url
    raise RuntimeError("No RTSP stream found")
