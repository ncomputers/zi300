"""Unified capture sources."""

from .base import Backoff, FrameSourceError, IFrameSource
from .pipeline_ffmpeg import FfmpegPipeline
from .rtsp_ffmpeg import RtspFfmpegSource

__all__ = [
    "IFrameSource",
    "FrameSourceError",
    "Backoff",
    "RtspFfmpegSource",
    "FfmpegPipeline",
]
