from __future__ import annotations

"""Simple RTSP to MJPEG proxy server.

This module launches an ``mjpeg_streamer`` server and feeds frames decoded
from an RTSP source using ``ffmpeg``. Frames are resized to 640x640 before
being published on the ``/camera`` endpoint.
"""

import threading
from contextlib import suppress
from subprocess import Popen
from typing import Optional

import ffmpeg
import numpy as np
from aiohttp.web_runner import GracefulExit

try:  # pragma: no cover - optional dependency may be missing
    from mjpeg_streamer import MjpegServer, Stream
except Exception:  # pragma: no cover - optional dependency may be missing
    MjpegServer = None  # type: ignore
    Stream = None  # type: ignore


class MjpegRtspServer:
    """Proxy RTSP stream to an MJPEG endpoint."""

    def __init__(self, rtsp_url: str, port: int) -> None:
        if not MjpegServer or not Stream:  # pragma: no cover - runtime guard
            raise RuntimeError("mjpeg_streamer not available")

        self._url = rtsp_url
        self._port = port
        self._server = MjpegServer("0.0.0.0", port)
        self._stream = Stream("camera", size=(640, 640), quality=70, fps=15)
        self._server.add_stream(self._stream)
        self._process: Optional[Popen] = None
        self._thread = threading.Thread(target=self._reader, daemon=True)

    def start(self) -> None:
        """Start the MJPEG server and decoding thread."""
        self._server.start()
        self._thread.start()

    def _reader(self) -> None:
        width = height = 640
        self._process = (
            ffmpeg.input(self._url, rtsp_transport="tcp")
            .output("pipe:", format="rawvideo", pix_fmt="rgb24", s="640x640")
            .run_async(pipe_stdout=True, pipe_stderr=True)
        )
        while True:
            in_bytes = self._process.stdout.read(width * height * 3)
            if not in_bytes:
                break
            frame = np.frombuffer(in_bytes, np.uint8).reshape([height, width, 3])
            self._stream.set_frame(frame)
        if self._process:
            with suppress(Exception):
                self._process.stdout.close()
            with suppress(Exception):
                self._process.wait()

    def stop(self) -> None:
        """Stop decoding and shut down the server."""
        if self._process:
            with suppress(Exception):
                self._process.stdout.close()
            with suppress(Exception):
                self._process.terminate()
            with suppress(Exception):
                self._process.wait()
        try:
            self._server.stop()
        except GracefulExit:  # pragma: no cover - server uses exceptions for shutdown
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2)
