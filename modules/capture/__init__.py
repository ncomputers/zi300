"""Unified capture sources."""

from .base import Backoff, FrameSourceError, IFrameSource
from .http_mjpeg import HttpMjpegSource
from .local_cv import LocalCvSource
from .pipeline_ffmpeg import FfmpegPipeline
from .rtsp_ffmpeg import RtspFfmpegSource

__all__ = [
    "IFrameSource",
    "FrameSourceError",
    "Backoff",
    "LocalCvSource",
    "RtspFfmpegSource",
    "HttpMjpegSource",
    "FfmpegPipeline",
]
