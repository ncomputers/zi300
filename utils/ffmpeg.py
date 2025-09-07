from __future__ import annotations


def build_rtsp_base_cmd(url: str, transport: str = "tcp") -> list[str]:
    """Return the base ffmpeg command for RTSP input without extras."""
    return ["ffmpeg", "-rtsp_transport", transport, "-i", url, "-an"]


def build_preview_cmd(url: str, transport: str, downscale: int | None = None) -> list[str]:
    """Return ffmpeg command for generating an MJPEG preview."""
    cmd = build_rtsp_base_cmd(url, transport)
    if downscale and downscale > 1:
        vf = f"scale=trunc(iw/{downscale}/2)*2:trunc(ih/{downscale}/2)*2"
        cmd += ["-vf", vf]
    cmd += [
        "-f",
        "mpjpeg",
        "pipe:1",
    ]
    return cmd


def build_snapshot_cmd(url: str, transport: str, downscale: int | None = None) -> list[str]:
    """Return ffmpeg command for capturing a single JPEG frame."""
    cmd = build_rtsp_base_cmd(url, transport)
    if downscale and downscale > 1:
        vf = f"scale=trunc(iw/{downscale}/2)*2:trunc(ih/{downscale}/2)*2"
        cmd += ["-vf", vf]
    cmd += [
        "-frames:v",
        "1",
        "-f",
        "image2",
        "pipe:1",
    ]
    return cmd
