from __future__ import annotations

# ruff: noqa: E402

"""RTSP capture using FFmpeg.

This source supports optional command-line flags via the ``FFMPEG_EXTRA_FLAGS``
environment variable. ``RTSP_STIMEOUT_USEC`` controls the ``-stimeout``
parameter (in microseconds, default ``20000000``). Stream dimensions are
probed with ``ffprobe`` when not specified explicitly.
"""

import logging
import os
import queue
import shlex
import subprocess
import threading
import time
from collections import deque

import ffmpeg
import numpy as np

from app.core.utils import getenv_num
from utils.logging import log_capture_event
from utils.logx import log_throttled
from utils.url import mask_credentials
import random

from .base import Backoff, FrameSourceError, IFrameSource

logger = logging.getLogger(__name__)


FIRST_FRAME_GRACE_SEC = getenv_num("RTSP_FIRST_FRAME_GRACE_SEC", 10, int)
MAX_PARTIAL_READS = getenv_num("RTSP_MAX_PARTIAL_READS", 3, int)
MAX_RESTART_ATTEMPTS = 5


class RtspFfmpegSource(IFrameSource):
    """Capture frames from an RTSP stream using FFmpeg.

    Frames are read on a background thread into a preallocated buffer. Complete
    frames are pushed into a two-element queue, dropping the oldest when full.
    Consecutive short reads trigger FFmpeg restarts with exponential backoff.
    """

    def __init__(
        self,
        uri: str,
        *,
        width: int | None = None,
        height: int | None = None,
        tcp: bool = True,
        latency_ms: int = 100,
        cam_id: int | str | None = None,
        stimeout_usec: int | None = None,
        extra_flags: list[str] | None = None,
    ) -> None:
        """Initialize the RTSP FFmpeg source.

        Parameters
        ----------
        uri:
            RTSP stream URI.
        width, height:
            Optional frame dimension overrides.
        tcp:
            Use TCP (``True``) or UDP (``False``) transport.
        latency_ms:
            Queue latency in milliseconds.
        cam_id:
            Optional camera identifier for logging.
        stimeout_usec:
            Microseconds to wait for establishing the connection passed as
            ``-stimeout``. Defaults to ``5000000``.
        extra_flags:
            Additional FFmpeg flags inserted before the input URL.
        """
        super().__init__(uri, cam_id=cam_id)
        self.width = width
        self.height = height
        env_tcp = os.getenv("VMS26_RTSP_TCP") == "1"
        self.tcp = tcp or env_tcp
        self.latency_ms = latency_ms
        self.stimeout_usec = stimeout_usec
        self.extra_flags = extra_flags or []
        self.proc: subprocess.Popen[bytes] | None = None
        self._proc_lock = threading.Lock()
        self._stderr_buffer: deque[str] = deque(maxlen=20)
        self._stderr_thread: threading.Thread | None = None
        self._frame_queue: queue.Queue[np.ndarray] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._short_reads = 0
        base = getenv_num("RECONNECT_BACKOFF_MS_MIN", 500, int) / 1000
        max_b = getenv_num("RECONNECT_BACKOFF_MS_MAX", 2000, int) / 1000
        self._backoff = Backoff(base=base, max_sleep=max_b)
        self.restarts = 0
        self.last_frame_ts = 0.0
        self._udp_fallback = False
        self._restart_failures = 0
        self._error: FrameSourceError | None = None
        self.cmd: list[str] | None = None
        self.frames_total = 0
        self.partial_reads = 0
        self.first_frame_ms: int | None = None
        self.first_frame_grace = FIRST_FRAME_GRACE_SEC

    def _probe_resolution(self) -> None:
        """Fill ``self.width`` and ``self.height`` from stream metadata."""
        if self.width and self.height:
            return
        opts = [(15_000_000, 3_000_000), (30_000_000, 6_000_000)]
        for probesize, analyzeduration in opts:
            try:
                info = ffmpeg.probe(
                    self.uri,
                    probesize=probesize,
                    analyzeduration=analyzeduration,
                    rtsp_transport="tcp" if self.tcp else "udp",
                )
                stream = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
                self.width = self.width or int(stream.get("width", 0) or 0)
                self.height = self.height or int(stream.get("height", 0) or 0)
                if self.width and self.height:
                    return
            except Exception as exc:
                logger.debug("ffprobe failed: %s", exc)
        logger.warning("HINT: increase probesize/analyzeduration; try TCP transport.")

    def open(self) -> None:
        self._start_proc()
        self._frame_queue = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        if self.width and self.height:
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()

    def _start_proc(self) -> None:
        with self._proc_lock:
            self._probe_resolution()
            transport = "tcp" if self.tcp else "udp"
            stimeout = (
                self.stimeout_usec
                if self.stimeout_usec is not None
                else getenv_num("RTSP_STIMEOUT_USEC", 20_000_000, int)
            )
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-rtsp_transport",
                transport,
            ]
            if self.tcp:
                cmd.extend(["-rtsp_flags", "prefer_tcp"])
            cmd.extend(["-stimeout", str(stimeout)])
            probesize = getenv_num("FFMPEG_PROBESIZE", 1_000_000, int)
            analyzeduration = getenv_num("FFMPEG_ANALYZEDURATION", 0, int)
            max_delay = getenv_num("FFMPEG_MAX_DELAY", 500_000, int)
            cmd.extend(
                [
                    "-fflags",
                    "nobuffer",
                    "-flags",
                    "low_delay",
                    "-rtbufsize",
                    "64M",
                    "-probesize",
                    str(probesize),
                    "-analyzeduration",
                    str(analyzeduration),
                    "-max_delay",
                    str(max_delay),
                    "-reorder_queue_size",
                    "0",
                    "-avioflags",
                    "direct",
                    "-an",
                    "-i",
                    self.uri,
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "bgr24",
                    "-vf",
                    f"scale={self.width}:{self.height}",
                    "pipe:1",
                ]
            )
            flags: list[str] = []
            env_flags = os.getenv("FFMPEG_EXTRA_FLAGS")
            if env_flags:
                flags.extend(shlex.split(env_flags))
            if self.extra_flags:
                flags.extend(self.extra_flags)
            if flags:
                insert_pos = cmd.index("-i")
                cmd[insert_pos:insert_pos] = flags
            self.cmd = cmd
            logger.debug("ffmpeg cmd: %s", mask_credentials(" ".join(self.cmd)))
            self.proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10**7,
            )
            self._stderr_buffer.clear()
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()
            log_capture_event(
                self.cam_id,
                "opened",
                backend="ffmpeg",
                uri=self.uri,
                cmd=mask_credentials(" ".join(self.cmd)),
            )
            self._short_reads = 0

    def read(self, timeout: float | None = None) -> np.ndarray:
        """Return the latest decoded frame from the internal queue."""
        if self._error:
            raise self._error
        if not self._frame_queue:
            raise FrameSourceError("NOT_OPEN")
        if not self.width or not self.height:
            self._log_stderr()
            stderr = mask_credentials(self.last_stderr)
            if self._invalid_stream():
                logger.warning("ffmpeg stderr:\n%s", stderr)
                raise FrameSourceError(
                    f"INVALID_STREAM: {stderr} -- check credentials or stream format"
                )
            logger.warning("ffmpeg stderr:\n%s", stderr)
            logger.warning("HINT: increase probesize/analyzeduration; try TCP transport.")
            raise FrameSourceError("NO_VIDEO_STREAM")
        try:
            return self._frame_queue.get(timeout=timeout or self.latency_ms / 1000)
        except queue.Empty:
            self._log_stderr()
            stderr = mask_credentials(self.last_stderr)
            if self._invalid_stream():
                logger.warning("ffmpeg stderr:\n%s", stderr)
                raise FrameSourceError(
                    f"INVALID_STREAM: {stderr} -- check credentials or stream format"
                )
            logger.warning("ffmpeg stderr:\n%s", stderr)
            log_capture_event(self.cam_id, "read_timeout", backend="ffmpeg")
            raise FrameSourceError("READ_TIMEOUT")

    def _reader_loop(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        expected = (self.width or 0) * (self.height or 0) * 3
        buf = bytearray(expected)
        mv = memoryview(buf)
        grace_deadline = time.time() + self.first_frame_grace
        first_frame_seen = False
        consecutive_partials = 0
        while self._stop_event and not self._stop_event.is_set():
            if not self.proc or not self.proc.stdout:
                try:
                    self._restart_proc()
                except FrameSourceError:
                    break
                continue
            filled = 0
            while filled < expected:
                try:
                    n = self.proc.stdout.readinto(mv[filled:])
                except (EOFError, BrokenPipeError):
                    n = 0
                except Exception:
                    n = 0
                if not n:
                    break
                filled += n
                if filled < expected:
                    logger.debug("partial read %d/%d", filled, expected)
            if filled != expected:
                now = time.time()
                if not first_frame_seen and now < grace_deadline:
                    continue
                self.partial_reads += 1
                consecutive_partials += 1
                logger.debug("incomplete frame %d/%d", filled, expected)
                if consecutive_partials >= MAX_PARTIAL_READS:
                    try:
                        self._restart_proc()
                    except FrameSourceError:
                        break
                continue
            assert filled == expected
            frame = np.frombuffer(mv, np.uint8).reshape((self.height, self.width, 3)).copy()
            self.frames_total += 1
            consecutive_partials = 0
            self._backoff.reset()
            self._restart_failures = 0
            now = time.time()
            if not first_frame_seen:
                latency_ms = int((now - (grace_deadline - self.first_frame_grace)) * 1000)
                self.first_frame_ms = latency_ms
                log_capture_event(self.cam_id, "first_frame", backend="ffmpeg", latency_ms=latency_ms)
                first_frame_seen = True
            if self._frame_queue:
                if self._frame_queue.full():
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    self._frame_queue.put_nowait(frame)
                except queue.Full:
                    pass
            logger.debug("complete frame %d/%d", filled, expected)
            self.last_frame_ts = now
        self._stop_proc()
        log_capture_event(
            self.cam_id,
            "capture_stop",
            backend="ffmpeg",
            frames=self.frames_total,
            partial_reads=self.partial_reads,
            restarts=self.restarts,
            last_error=str(self._error) if self._error else "",
        )

    def _restart_proc(self) -> None:
        self._log_stderr()
        stderr = mask_credentials(self.last_stderr)
        invalid_stream = "invalid data found when processing input" in stderr.lower()
        perm_denied = "operation not permitted" in stderr.lower()
        self._stop_proc()
        self.restarts += 1
        self._restart_failures += 1
        if invalid_stream:
            logger.warning("ffmpeg stderr:\n%s", stderr)
            self._error = FrameSourceError(
                f"INVALID_STREAM: {stderr} -- check credentials or stream format"
            )
            if self._stop_event:
                self._stop_event.set()
            raise self._error
        if perm_denied:
            logger.warning("ffmpeg connect failed: %s", stderr)
            self._error = FrameSourceError(
                "CONNECT_FAILED: Operation not permitted - check firewall rules, credentials, or camera permissions"
            )
            if self._stop_event:
                self._stop_event.set()
            raise self._error
        if self._restart_failures > MAX_RESTART_ATTEMPTS:
            logger.warning("ffmpeg connect failed: %s", stderr)
            self._error = FrameSourceError(f"CONNECT_FAILED: {stderr}")
            if self._stop_event:
                self._stop_event.set()
            raise self._error
        log_throttled(
            logger.warning,
            f"[rtsp:{self.cam_id}] reconnecting ffmpeg",
            key=f"cap:{self.cam_id}:reconnect",
            interval=5,
        )
        delay = self._backoff.next()
        jitter = delay * random.uniform(0.8, 1.2)
        log_capture_event(
            self.cam_id,
            "stream_retry",
            backend="ffmpeg",
            reason=stderr,
            delay_ms=int(jitter * 1000),
        )
        time.sleep(jitter)
        if self.tcp and not self._udp_fallback:
            logger.warning("TCP failed; retrying with UDP transport")
            self.tcp = False
            self._udp_fallback = True
        self._start_proc()
        if self._frame_queue:
            while not self._frame_queue.empty():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    break

    def info(self) -> dict[str, int | float]:
        return {"w": self.width or 0, "h": self.height or 0, "fps": 0.0}

    def close(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        self._reader_thread = None
        self._stop_proc()
        if self._frame_queue:
            while not self._frame_queue.empty():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    break
            self._frame_queue = None
        log_capture_event(self.cam_id, "closed", backend="ffmpeg")

    def _stop_proc(self) -> None:
        proc: subprocess.Popen[bytes] | None = None
        stderr_thread: threading.Thread | None = None
        with self._proc_lock:
            if self.proc:
                proc = self.proc
                stderr_thread = self._stderr_thread
                self.proc = None
                self._stderr_thread = None
        if not proc:
            self._stderr_buffer.clear()
            return
        try:
            proc.terminate()
        except Exception:
            pass
        if proc.stdout:
            proc.stdout.close()
        stderr = proc.stderr
        if stderr_thread and stderr_thread.is_alive():
            stderr_thread.join(timeout=1)
        if stderr:
            stderr.close()
        self._stderr_buffer.clear()

    def _invalid_stream(self) -> bool:
        return "invalid data found when processing input" in self.last_stderr.lower()

    def _drain_stderr(self) -> None:
        """Drain stderr from ``ffmpeg`` into the internal buffer.

        The subprocess handle is captured at thread start to avoid races where
        ``self.proc`` is cleared by :meth:`_stop_proc` before the thread runs.
        Without this, the background thread may attempt to access
        ``self.proc.stderr`` after ``self.proc`` is set to ``None`` resulting in
        ``AttributeError`` errors.  Using a local reference ensures the thread
        exits cleanly once the process terminates.
        """
        proc = self.proc
        if not proc or not proc.stderr:
            return
        try:
            while True:
                line = proc.stderr.readline()
                if line == b"":
                    break
                sanitized = mask_credentials(line.decode("utf-8", "replace").rstrip())
                self._stderr_buffer.append(sanitized)
        except (ValueError, OSError):
            pass

    def _log_stderr(self) -> None:
        if self._stderr_buffer:
            logger.debug("ffmpeg stderr:\n%s", mask_credentials("\n".join(self._stderr_buffer)))

    @property
    def last_stderr(self) -> str:
        return "\n".join(self._stderr_buffer)
