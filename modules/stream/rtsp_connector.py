from __future__ import annotations

import os
import queue
import select
import subprocess
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

import numpy as np

from utils import logx
from utils.ffmpeg import build_rtsp_base_cmd
from utils.url import mask_credentials


class OverwriteQueue:
    """A minimal queue that overwrites the oldest item when full."""

    def __init__(self, maxlen: int = 1) -> None:
        self._data: Deque[np.ndarray] = deque(maxlen=maxlen)
        self._cond = threading.Condition()

    def put(self, item: np.ndarray) -> None:
        with self._cond:
            if len(self._data) == self._data.maxlen:
                self._data.popleft()
            self._data.append(item)
            self._cond.notify()

    def put_nowait(self, item: np.ndarray) -> None:
        self.put(item)

    def get(self, block: bool = True, timeout: float | None = None) -> np.ndarray:
        with self._cond:
            if not block:
                if not self._data:
                    raise queue.Empty
                return self._data.popleft()

            end = None if timeout is None else time.time() + timeout
            while not self._data:
                remaining = None if end is None else end - time.time()
                if end is not None and remaining <= 0:
                    raise queue.Empty
                self._cond.wait(remaining)
            return self._data.popleft()

    def get_nowait(self) -> np.ndarray:
        return self.get(block=False)


class RtspConnector:
    """Lightweight FFmpeg based RTSP frame reader.

    Frames are published to subscriber queues.  The connector automatically
    restarts the underlying FFmpeg process using an exponential backoff policy
    and a watchdog timer that triggers when frames stop arriving.
    """

    # states
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RETRYING = "retrying"

    def __init__(
        self,
        url: str,
        width: int,
        height: int,
        fps: float = 30.0,
        camera_id: str | int | None = None,
    ) -> None:
        self.url = url
        self.width = width
        self.height = height
        self._probe_resolution()
        self.frame_size = self.width * self.height * 3
        self.expected_interval = 1.0 / fps if fps > 0 else 0.033
        self.state = self.STOPPED
        self.last_error: str = ""
        self.last_frame_ts: float = 0.0
        self.fps_in: float = 0.0
        self.camera_id = camera_id
        self.topic = (
            f"frames:preview:{camera_id}" if camera_id is not None else "frames:preview:unknown"
        )
        self._seq = 0
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._subs: List[OverwriteQueue] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self.state = self.STOPPED

    def subscribe(self, maxsize: int = 1) -> "OverwriteQueue":
        q = OverwriteQueue(maxlen=maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def stats(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "last_error": self.last_error,
            "fps_in": round(self.fps_in, 2),
            "last_frame_ts": self.last_frame_ts,
            "subscribers": len(self._subs),
        }

    def _probe_resolution(self) -> None:
        """Probe the stream to determine its actual resolution."""
        try:
            out = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=s=x:p=0",
                    self.url,
                ],
                stderr=subprocess.STDOUT,
            )
            w_str, h_str = out.decode().strip().split("x")
            self.width, self.height = int(w_str), int(h_str)
        except Exception:
            pass

    def _launch_proc(self, cmd: list[str]) -> subprocess.Popen[bytes]:
        """Start FFmpeg and return the process handle."""
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def _read_frame(
        self, proc: subprocess.Popen[bytes], timeout: float
    ) -> np.ndarray | None:
        """Read a single frame from the FFmpeg process.

        Returns ``None`` if the frame was incomplete so callers can skip it
        without restarting the process.
        """
        if not proc.stdout:
            raise EOFError("ffmpeg closed stdout")
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError
        data = proc.stdout.read(self.frame_size)
        if not data:
            if proc.poll() is not None:
                raise EOFError("ffmpeg closed stdout")
            return None
        if len(data) < self.frame_size:
            return None
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)

    def _handle_watchdog(self, now: float, start_time: float, masked_url: str) -> bool:
        """Check watchdog timers and decide if restart is needed."""
        interval = 1.0 / self.fps_in if self.fps_in > 0 else self.expected_interval
        watchdog_sec = max(3 * interval, 2.0)
        if not self.last_frame_ts:
            if now - start_time > 10:
                logx.error(
                    "capture_error",
                    camera_id=self.camera_id,
                    mode="stream",
                    url=masked_url,
                    code="READ_TIMEOUT",
                    rc=self._proc.returncode if self._proc else -1,
                    ffmpeg_tail="",
                    since_last_frame_ms=int((now - start_time) * 1000),
                    phase="first",
                )
                logx.warn("STREAM_RETRY", url=masked_url, reason="first_frame")
                self.state = self.RETRYING
                if self._proc:
                    self._proc.kill()
                return True
            return False
        if now - self.last_frame_ts > watchdog_sec:
            logx.error(
                "capture_error",
                camera_id=self.camera_id,
                mode="stream",
                url=masked_url,
                code="READ_TIMEOUT",
                rc=self._proc.returncode if self._proc else -1,
                ffmpeg_tail="",
                since_last_frame_ms=int((now - self.last_frame_ts) * 1000),
                phase="steady",
            )
            logx.warn("STREAM_RETRY", url=masked_url, reason="watchdog")
            self.state = self.RETRYING
            if self._proc:
                self._proc.kill()
            return True
        return False

    # ------------------------------------------------------------------
    def _run(self) -> None:
        backoff = 1
        cmd = build_rtsp_base_cmd(self.url)
        cmd += [
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ]
        masked_url = mask_credentials(self.url)
        logx.event(
            "capture_start",
            camera_id=self.camera_id,
            mode="stream",
            url=masked_url,
            backend="RtspFfmpegSource",
            cmd=mask_credentials(" ".join(cmd)),
        )
        logx.event("NEGOTIATED_SIZE", w=self.width, h=self.height, frame_size=self.frame_size)
        logx.event("STREAM_START", url=masked_url)
        while not self._stop.is_set():
            self.state = self.CONNECTING
            try:
                with self._launch_proc(cmd) as proc:
                    self._proc = proc
                    self.last_frame_ts = 0.0
                    start_time = time.time()
                    while not self._stop.is_set() and proc.poll() is None:
                        try:
                            frame = self._read_frame(proc, timeout=1.0)
                            if frame is None:
                                continue
                        except TimeoutError:
                            if self._handle_watchdog(time.time(), start_time, masked_url):
                                break
                            continue
                        except EOFError:
                            since = int(
                                (
                                    time.time()
                                    - (
                                        start_time
                                        if self.last_frame_ts == 0
                                        else self.last_frame_ts
                                    )
                                )
                                * 1000
                            )
                            phase = "first" if self.last_frame_ts == 0 else "steady"
                            logx.error(
                                "capture_error",
                                camera_id=self.camera_id,
                                mode="stream",
                                url=masked_url,
                                code="SHORT_READ",
                                rc=proc.returncode if proc else -1,
                                ffmpeg_tail="",
                                since_last_frame_ms=since,
                                phase=phase,
                            )
                            logx.warn("STREAM_RETRY", url=masked_url, reason="short_read")
                            self.state = self.RETRYING
                            break
                        now = time.time()
                        if self.state != self.CONNECTED:
                            self.state = self.CONNECTED
                            logx.event(
                                "FIRST_FRAME",
                                camera_id=self.camera_id,
                                latency_ms=int((now - start_time) * 1000),
                            )
                            logx.event("STREAM_CONNECTED", url=masked_url)
                        self._publish(frame)
                        if self.last_frame_ts:
                            dt = now - self.last_frame_ts
                            if dt > 0:
                                inst = 1.0 / dt
                                self.fps_in = (
                                    (self.fps_in * 0.9) + (0.1 * inst) if self.fps_in else inst
                                )
                        self.last_frame_ts = now
            except Exception as exc:
                self.state = self.ERROR
                self.last_error = str(exc)
                logx.error("STREAM_ERROR", url=masked_url, error=self.last_error)
                return
            if self._stop.is_set():
                break
            if self._proc and self._proc.poll() is not None and self.state != self.RETRYING:
                self.state = self.ERROR
                self.last_error = f"ffmpeg exited: {self._proc.returncode}"
                logx.error("STREAM_ERROR", url=masked_url, error=self.last_error)
            self._cleanup_proc()
            if self._stop.is_set():
                break
            self.state = self.RETRYING
            logx.warn("STREAM_RETRY", url=masked_url, sleep=backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 10)
        self._cleanup_proc()

    def _cleanup_proc(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def _publish(self, frame: np.ndarray) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            q.put(frame)
        self._seq += 1
        logx.event(
            "FRAME_PUSH",
            camera_id=self.camera_id,
            topic=self.topic,
            seq=self._seq,
        )
