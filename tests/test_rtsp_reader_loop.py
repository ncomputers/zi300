import os
import queue
import threading
import time

import numpy as np

from modules.capture.rtsp_ffmpeg import RtspFfmpegSource


class FakeStdout:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def readinto(self, mv):
        if not self.chunks:
            return 0
        data = self.chunks.pop(0)
        n = min(len(data), len(mv))
        mv[:n] = data[:n]
        if len(data) > n:
            self.chunks.insert(0, data[n:])
        return n

    def close(self):
        pass

class FakeProc:
    def __init__(self, chunks):
        self.stdout = FakeStdout(chunks)
        self.stderr = None


def run_source(chunks, env=None):
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    src = RtspFfmpegSource("rtsp://example", width=2, height=1)
    src.proc = FakeProc(chunks)
    src._stop_event = threading.Event()

    class AutoStopQueue(queue.Queue):
        def __init__(self):
            super().__init__(maxsize=1)

        def put_nowait(self, item):
            super().put_nowait(item)
            src._stop_event.set()

    src._frame_queue = AutoStopQueue()
    src._start_proc = lambda: None
    src._backoff.next = lambda: 0
    t = threading.Thread(target=src._reader_loop, daemon=True)
    t.start()
    frame = None
    try:
        frame = src._frame_queue.get(timeout=1)
    except queue.Empty:
        src._stop_event.set()
    t.join(timeout=1)
    if env:
        for k, v in old_env.items():
            if v is None:
                del os.environ[k]
            else:
                os.environ[k] = v
    return src, frame


def test_partial_read_accumulates():
    chunks = [b"abc", b"def"]  # 6 bytes total for 2x1 frame
    src, frame = run_source(chunks, {"RTSP_FIRST_FRAME_GRACE_SEC": "0"})
    assert frame is not None
    assert src.frames_total == 1
    assert src.partial_reads == 0
    np.testing.assert_array_equal(frame, np.array([[[97, 98, 99], [100, 101, 102]]], dtype=np.uint8))


def test_eof_counts_partial_and_restart(monkeypatch):
    monkeypatch.setattr("modules.capture.rtsp_ffmpeg.FIRST_FRAME_GRACE_SEC", 0)
    monkeypatch.setattr("modules.capture.rtsp_ffmpeg.MAX_PARTIAL_READS", 1)
    chunks = [b"abc"]  # EOF before full frame
    src, _ = run_source(chunks)
    assert src.partial_reads == 1
