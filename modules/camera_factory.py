"""Factory helpers for opening camera frame sources."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from modules.capture import (
    FrameSourceError,
    HttpMjpegSource,
    IFrameSource,
    RtspFfmpegSource,
)

try:  # pragma: no cover - optional gstreamer source
    from modules.capture import RtspGstSource  # type: ignore
except Exception:  # pragma: no cover - gstreamer not available
    RtspGstSource = None  # type: ignore
from utils.url import mask_creds, with_rtsp_transport


class StreamUnavailable(Exception):
    """Raised when no capture backend can provide frames."""


__all__ = ["open_capture", "async_open_capture", "async_probe_rtsp", "StreamUnavailable"]


logger = logging.getLogger(__name__)


async def async_probe_rtsp(url: str) -> tuple[str, str, int, int, float]:
    """Probe ``url`` with ``ffprobe`` trying UDP then TCP.

    Returns the working URL (possibly annotated with transport), the chosen
    transport, and the detected width, height and FPS. On failure the original
    URL and default metadata are returned.
    """

    async def _run(u: str) -> tuple[int, int, float]:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate",
            "-of",
            "json",
            u,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode != 0:
            raise RuntimeError(err.decode().strip())
        data = json.loads(out.decode() or "{}")
        stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        fps_txt = stream.get("avg_frame_rate") or "0/1"
        try:
            num, den = fps_txt.split("/", 1)
            fps = float(num) / float(den)
        except Exception:
            fps = 0.0
        return width, height, fps

    try:
        w, h, fps = await _run(url)
        logger.debug("ffprobe succeeded over udp for %s", mask_creds(url))
        return url, "udp", w, h, fps
    except Exception as exc:
        logger.debug("ffprobe udp failed for %s: %s", mask_creds(url), exc)

    tcp_url = with_rtsp_transport(url, "tcp")
    try:
        w, h, fps = await _run(tcp_url)
        logger.debug("ffprobe succeeded over tcp for %s", mask_creds(tcp_url))
        return tcp_url, "tcp", w, h, fps
    except Exception as exc:
        logger.debug("ffprobe tcp failed for %s: %s", mask_creds(tcp_url), exc)

    return url, "udp", 0, 0, 0.0


def _clamp_latency(value: int) -> int:
    return max(50, min(300, int(value)))


async def async_open_capture(
    cfg: dict[str, Any],
    src: str | int | None = None,
    cam_id: int | None = None,
    src_type: str | None = None,
    resolution: tuple[int, int] | None = None,
    rtsp_transport: str | None = None,
    use_gpu: bool = False,
    **kwargs: Any,
) -> tuple[IFrameSource, str]:
    """Asynchronously instantiate and open a frame source."""

    cam_cfg = cfg.get("camera", {})
    cam_id = cam_id if cam_id is not None else 0
    if src is None:
        src = cam_cfg.get("uri", "")
    if src_type is None:
        src_type = cam_cfg.get("mode", "rtsp")

    transport = rtsp_transport
    width, height = (None, None)
    if resolution and len(resolution) == 2:
        width, height = resolution
    latency = _clamp_latency(kwargs.pop("latency_ms", cam_cfg.get("latency_ms", 100)))
    capture_buffer = kwargs.pop("capture_buffer", None)
    if src_type == "rtsp" and transport is None and isinstance(src, str):
        try:
            probed_url, transport, w_p, h_p, _ = await async_probe_rtsp(src)
            src = probed_url
            if not width and not height and w_p and h_p:
                width, height = w_p, h_p
        except Exception as exc:  # pragma: no cover - probe failures are non fatal
            logger.debug("probe failed for %s: %s", mask_creds(str(src)), exc)

    if transport is None:
        transport = "tcp" if cam_cfg.get("tcp", True) else "udp"

    if src_type == "http":
        max_queue = capture_buffer or cam_cfg.get("max_queue", 1)
        cap = HttpMjpegSource(str(src), cam_id=cam_id, max_queue=max_queue)
        await asyncio.to_thread(cap.open)
        return cap, transport
    if src_type != "rtsp":
        raise StreamUnavailable(f"unknown mode {src_type}")

    cap_kwargs: dict[str, Any] = {
        "tcp": transport == "tcp",
        "latency_ms": latency,
        "cam_id": cam_id,
    }
    if width and height:
        cap_kwargs["width"] = width
        cap_kwargs["height"] = height
    while True:
        cap = RtspFfmpegSource(str(src), **cap_kwargs)
        try:
            await asyncio.to_thread(cap.open)
            logger.info(
                "[cap:%s] opened stream using %s transport",
                cam_id,
                transport,
            )
            return cap, transport
        except FrameSourceError as exc:
            if str(exc) == "NO_VIDEO_STREAM" and transport == "tcp":
                logger.info("[cap:%s] no video over TCP, retrying with UDP", cam_id)
                transport = "udp"
                cap_kwargs["tcp"] = False
                continue
            raise StreamUnavailable(str(exc))


def open_capture(
    cfg: dict[str, Any],
    src: str | int | None = None,
    cam_id: int | None = None,
    src_type: str | None = None,
    resolution: tuple[int, int] | None = None,
    rtsp_transport: str | None = None,
    use_gpu: bool = False,
    **kwargs: Any,
) -> tuple[IFrameSource, str]:
    """Instantiate and open a frame source synchronously.

    This is a thin wrapper over :func:`async_open_capture` for contexts where
    running an event loop is acceptable."""

    return asyncio.run(
        async_open_capture(
            cfg,
            src,
            cam_id,
            src_type,
            resolution,
            rtsp_transport,
            use_gpu,
            **kwargs,
        )
    )
