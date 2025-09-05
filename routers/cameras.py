"""Camera management routes."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import shlex
import subprocess
import time
import uuid
from datetime import datetime
from typing import Dict, List
from urllib.parse import urlparse, urlsplit

try:  # pragma: no cover - OpenCV is optional
    import cv2  # type: ignore
except Exception:  # pragma: no cover - dependency may be missing
    cv2 = None  # type: ignore[assignment]
import threading

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.requests import ClientDisconnect

from app.core.utils import getenv_num
from config import PPE_PAIRS, PPE_TASKS, UI_CAMERA_TASKS, VEHICLE_LABELS, config
from core.camera_manager import CameraManager
from core.config import get_config
from core.tracker_manager import save_cameras, start_tracker, stop_tracker
from models.camera import Camera, Orientation, Transport, create_camera
from models.camera import delete_camera as delete_camera_model
from models.camera import get_camera, update_camera
from modules.capture import RtspFfmpegSource
from modules.email_utils import sign_token
from modules.frame_bus import FrameBus
from modules.getinfo import probe_rtsp
from modules.preview.mjpeg_publisher import PreviewPublisher
from modules.rtsp_probe import probe_rtsp_base
from modules.stream.rtsp_connector import RtspConnector
from modules.tracker import tracker

# Import role helpers directly so tests can monkeypatch ``require_roles`` on
# this module and affect the admin dependency used below.
from modules.utils import require_roles
from routers.detections import _build_payload
from routers.visitor_utils import visitor_disabled_response
from schemas.camera import CameraCreate
from utils import logx, require_feature
from utils.api_errors import stream_error_message
from utils.ffmpeg import build_snapshot_cmd
from utils.ffmpeg_snapshot import capture_snapshot
from utils.jpeg import encode_jpeg
from utils.logx import log_throttled
from utils.url import get_stream_type, mask_credentials

# utility for resolving stream dimensions
from utils.video import async_get_stream_resolution

# ruff: noqa


TARGET_FPS = getenv_num("VMS26_TARGET_FPS", 15, int)
FRAME_JPEG_QUALITY = getenv_num("FRAME_JPEG_QUALITY", 80, int)
NO_FRAME_TIMEOUT_MS = getenv_num("NO_FRAME_TIMEOUT_MS", 2000, int)
HEARTBEAT_INTERVAL_MS = getenv_num("HEARTBEAT_INTERVAL_MS", 1500, int)
HEARTBEAT_JPEG = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD5/ooooA//2Q=="
)


def require_admin(request: Request):
    """Ensure the current user has the ``admin`` role."""
    return require_roles(request, ["admin"])


router = APIRouter(dependencies=[Depends(require_admin)])


# Global lock protecting access to the shared ``cams`` list
cams_lock = asyncio.Lock()

# cache preferred transport for camera test probes
TEST_CAMERA_TRANSPORT: dict[str, str] = {}

# track active camera test probes so new tests can cancel old ones
TEST_CAMERA_PROBES: dict[str, asyncio.Task] = {}


# in-memory preview tokens and concurrency guard
TOKEN_TTL = 60
PREVIEW_TOKENS: dict[str, dict[str, float | str]] = {}
preview_semaphore = asyncio.Semaphore(3)

# Preview publisher and RTSP connectors for frame streaming
preview_publisher = PreviewPublisher()
rtsp_connectors: Dict[int, RtspConnector] = {}
_frame_buses: Dict[int, FrameBus] = {}


def _init_preview_stream(cam: dict) -> None:
    """Initialize preview streaming for ``cam``."""
    global rtsp_connectors, _frame_buses, preview_publisher
    try:
        res = cam.get("resolution") or "640x480"
        w, h = (int(x) for x in res.lower().split("x"))
    except Exception:
        w, h = (640, 480)
    cam_id = cam.get("id")
    topic = f"frames:{cam_id}:preview"
    bus = FrameBus()
    conn = RtspConnector(cam.get("url", ""), w, h)
    q = conn.subscribe()
    seq = 0

    def _forward(q=q, bus=bus):
        nonlocal seq
        while True:
            frame = q.get()
            bus.put(frame)
            seq += 1
            preview_publisher._seqs[cam_id] = seq
            logx.event("FRAME_PUSH", camera_id=cam_id, topic=topic, seq=seq)

    threading.Thread(target=_forward, daemon=True).start()
    conn.start()
    _frame_buses[cam_id] = bus
    rtsp_connectors[cam_id] = conn
    preview_publisher._buses[cam_id] = bus


def _cleanup_tokens() -> None:
    now = time.time()
    for tok, info in list(PREVIEW_TOKENS.items()):
        if now - float(info.get("ts", 0)) > TOKEN_TTL:
            PREVIEW_TOKENS.pop(tok, None)


def _issue_preview_token(url: str) -> str:
    _cleanup_tokens()
    token = secrets.token_urlsafe(8)
    PREVIEW_TOKENS[token] = {"url": url, "ts": time.time()}
    return token


def _consume_preview_token(token: str) -> str | None:
    _cleanup_tokens()
    info = PREVIEW_TOKENS.pop(token, None)
    if not info or time.time() - float(info.get("ts", 0)) > TOKEN_TTL:
        return None
    return str(info.get("url"))


# default runtime context
cfg = config
cams: List[dict] = []
trackers_map: Dict[int, object] = {}
redis = None
redisfx = None

# camera manager instance for API routes
start_tracker_fn = lambda cam, cfg, trackers, r, cb=None: start_tracker(cam, cfg, trackers, r, cb)
stop_tracker_fn = lambda cid, trackers: stop_tracker(cid, trackers)
camera_manager = CameraManager(
    cfg,
    trackers_map,
    redis,
    lambda: cams,
    start_tracker_fn,
    stop_tracker_fn,
)
manager = camera_manager


def get_camera_manager() -> CameraManager:  # pragma: no cover - simple accessor
    return camera_manager


def _validation_response(exc: ValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        loc = err.get("loc") or []
        field = "__root__" if not loc else ".".join(str(p) for p in loc)
        errors.append({"field": field, "message": err["msg"]})
    return JSONResponse({"errors": errors}, status_code=422)


class LineConfig(BaseModel):
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)
    orientation: str

    @field_validator("orientation")
    @classmethod
    def _orient(cls, v: str) -> str:
        if v not in {"vertical", "horizontal"}:
            raise ValueError("orientation must be 'vertical' or 'horizontal'")
        return v


async def _resolve_resolution(url: str, res: str | None, timeout: float | None = None) -> str:
    """Return a resolution string, probing the stream when set to ``auto``."""

    res = res or "original"
    if res == "auto":
        if timeout is None:
            timeout = cfg.get("stream_probe_timeout", 10)
        w, h = await async_get_stream_resolution(
            url,
            timeout=timeout,
            fallback_ttl=cfg.get("stream_probe_fallback_ttl"),
        )
        return f"{w}x{h}"
    return res


def collect_health(cam, tracker) -> dict:
    """Return health metrics for ``cam`` using ``tracker``."""
    cam_id = cam.get("id")
    if not tracker or not hasattr(tracker, "get_debug_stats"):
        return {}
    try:
        stats = tracker.get_debug_stats()
    except Exception:
        logger.exception(f"[{cam_id}] debug stats failed")
        return {}

    latency = float(stats.get("latency") or 0)
    if not latency:
        start = stats.get("last_capture_ts")
        end = stats.get("last_process_ts")
        if start and end:
            latency = float(end) - float(start)
    frame_ts = float(stats.get("frame_ts") or stats.get("last_process_ts") or 0)
    packet_loss = int(stats.get("packet_loss") or 0)
    return {"latency": latency, "frame_ts": frame_ts, "packet_loss": packet_loss}


async def _health_loop() -> None:
    """Background task recording tracker health metrics to Redis."""
    while True:
        for cam in list(cams):
            cam_id = cam.get("id")
            tracker = trackers_map.get(cam_id) if trackers_map else None
            stats = collect_health(cam, tracker)
            if not stats:
                continue
            try:
                redis.hset(f"camera:{cam_id}:health", mapping=stats)
            except Exception:
                logger.exception(f"[{cam_id}] failed writing health stats")
        await asyncio.sleep(1)


# init_context routine
def init_context(
    _cfg: dict,
    cameras: List[dict],
    trackers: Dict[int, "PersonTracker"],
    redis_client,
    templates_path,
    redis_facade=None,
):
    global cfg, cams, trackers_map, redis, templates, cams_lock, camera_manager, redisfx
    from config import config as global_config

    cfg = global_config
    cfg = get_config()
    cams = cameras
    trackers_map = trackers
    redis = redis_client
    redisfx = redis_facade
    templates = Jinja2Templates(directory=templates_path)
    templates.env.add_extension("jinja2.ext.do")
    # Recreate the lock for each new application context
    cams_lock = asyncio.Lock()
    camera_manager = CameraManager(
        cfg,
        trackers_map,
        redis,
        lambda: cams,
        start_tracker_fn,
        stop_tracker_fn,
    )
    # set up preview publisher and RTSP connectors
    global preview_publisher, rtsp_connectors, _frame_buses
    _frame_buses = {}
    rtsp_connectors = {}
    for cam in cams:
        _init_preview_stream(cam)
    preview_publisher = PreviewPublisher(_frame_buses)
    # Start background health monitoring task
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_health_loop())
    except RuntimeError:
        # running without an event loop (e.g. during tests)
        pass


# warn if tracker start takes too long
START_TRACKER_WARN_AFTER = 5.0


async def _start_tracker_background(cam, cfg, trackers, redis):
    start = time.perf_counter()
    try:
        tr = await asyncio.to_thread(start_tracker, cam, cfg, trackers, redis)
        if not tr or not getattr(tr, "online", False):
            redis.hset(f"camera:{cam.get('id')}:health", mapping={"status": "offline"})
    except Exception:
        logger.exception(f"[{cam.get('id')}] tracker start failed")
        redis.hset(f"camera:{cam.get('id')}:health", mapping={"status": "offline"})
    duration = time.perf_counter() - start
    if duration > START_TRACKER_WARN_AFTER:
        logger.warning(f"[{cam.get('id')}] start_tracker took {duration:.2f}s")


# _expand_ppe_tasks routine
def _expand_ppe_tasks(tasks: List[str]) -> List[str]:
    """Ensure each selected PPE class includes its paired absence/presence."""
    result = set(tasks)
    for t in list(result):
        pair = PPE_PAIRS.get(t)
        if not pair:
            for k, v in PPE_PAIRS.items():
                if v == t:
                    pair = k
                    break
        if pair:
            result.add(pair)
    return list(result)


def _validate_ffmpeg_flags(flags: str) -> bool:
    """Validate ffmpeg flags by parsing and performing a dry-run."""
    try:
        parts = shlex.split(flags)
    except ValueError:
        return False
    try:
        subprocess.run(
            ["ffmpeg", *parts, "-h"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )
    except FileNotFoundError:
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


@router.post("/api/cameras")
async def create_camera_api(camera: dict):
    """Persist a new camera configuration."""
    try:
        cam_obj = CameraCreate.model_validate(camera, context={"cams": cams, "cfg": cfg})
    except ValidationError as exc:
        return _validation_response(exc)
    sanitized = mask_credentials(cam_obj.url)
    logger.info(
        f"[create_camera_api] saving camera {cam_obj.name} url={sanitized} transport={cam_obj.transport}"
    )
    cam_dict = cam_obj.model_dump()
    cam_dict["site_id"] = cam_dict.get("site_id") or cfg.get("site_id", 1)
    now = datetime.utcnow().isoformat()
    cam_dict["created_at"] = now
    cam_dict["updated_at"] = now

    cam_dict["rtsp_transport"] = cam_dict.pop("transport", "tcp")
    url = cam_dict["url"]
    parsed = urlsplit(url)
    if parsed.scheme in ("rtsp", "rtsps") and parsed.path in ("", "/"):
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        try:
            probed_url = probe_rtsp_base(host, parsed.username, parsed.password)
            logger.info(f"[create_camera_api] auto-probed RTSP path {mask_credentials(probed_url)}")
            cam_dict["url"] = probed_url
            url = probed_url
        except Exception as e:
            logger.error(f"[create_camera_api] auto-probe failed for {sanitized}: {e}")
            return JSONResponse({"error": "RTSP auto-probe failed"}, status_code=400)
    cam_dict["resolution"] = await _resolve_resolution(url, cam_dict.get("resolution"))
    if url.isdigit() or url.startswith("/dev/"):
        cam_dict["type"] = "local"
    else:
        cam_dict["type"] = get_stream_type(url)
    async with cams_lock:
        cam_id = max([c["id"] for c in cams], default=0) + 1
        cam_dict["id"] = cam_id
        cams.append(cam_dict)
        save_cameras(cams, redis)
    if cam_dict.get("enabled") and cfg.get("enable_person_tracking", True):
        manager.redis = redis
        try:
            await manager.start(cam_id)
        except Exception:
            if redis:
                redis.hset(f"camera:{cam_id}", "status", "offline")
    return {
        "id": cam_id,
        "status": "saved",
        "site_id": cam_dict["site_id"],
        "created_at": cam_dict["created_at"],
        "updated_at": cam_dict["updated_at"],
    }


@router.get("/camera/settings/{cam_id}")
async def camera_settings_page(cam_id: int, request: Request):
    async with cams_lock:
        cam = next((c for c in cams if c["id"] == cam_id), None)
    if not cam:
        return JSONResponse({"error": "Not found"}, status_code=404)
    pipeline = redis.hget(f"camera:{cam_id}", "pipeline") or ""
    profile = redis.hget(f"camera:{cam_id}", "profile") or cam.get("profile", "")
    cam = dict(cam)
    cam["profile"] = profile
    return templates.TemplateResponse(
        "camera_settings.html",
        {
            "request": request,
            "cam": cam,
            "pipeline": pipeline,
            "api_base": str(request.base_url).rstrip("/"),
        },
    )


@router.get("/api/camera/{cam_id}/pipeline")
async def get_camera_pipeline(cam_id: int, request: Request):
    pipeline = redis.hget(f"camera:{cam_id}", "pipeline") or ""
    profile = redis.hget(f"camera:{cam_id}", "profile") or ""
    return {"pipeline": pipeline, "profile": profile}


@router.post("/api/camera/{cam_id}/pipeline")
async def set_camera_pipeline(cam_id: int, request: Request):
    data = await request.json()
    pipeline = (data.get("pipeline") or "").strip()
    if len(pipeline) > 200:
        return JSONResponse({"error": "pipeline_too_long"}, status_code=400)
    profile = (data.get("profile") or "").strip()
    key = f"camera:{cam_id}"
    mapping: dict[str, str] = {}
    if pipeline:
        try:
            shlex.split(pipeline)
        except ValueError:
            return JSONResponse({"error": "invalid_pipeline"}, status_code=400)
        mapping["pipeline"] = pipeline
    else:
        redis.hdel(key, "pipeline")
    if profile:
        if profile not in config.get("pipeline_profiles", {}):
            return JSONResponse({"error": "invalid_profile"}, status_code=400)
        mapping["profile"] = profile
    else:
        redis.hdel(key, "profile")

    if "ffmpeg_flags" in data:
        ffmpeg_flags = (data.get("ffmpeg_flags") or "").strip()
        if len(ffmpeg_flags) > 200:
            return JSONResponse({"error": "ffmpeg_flags_too_long"}, status_code=400)
        if ffmpeg_flags:
            if not _validate_ffmpeg_flags(ffmpeg_flags):
                return JSONResponse({"error": "invalid_ffmpeg_flags"}, status_code=400)
            mapping["ffmpeg_flags"] = ffmpeg_flags
        else:
            redis.hdel(key, "ffmpeg_flags")

    for field in ("url", "backend"):
        val = (data.get(field) or "").strip()
        if val:
            mapping[field] = val
    for field in ("ready_timeout", "ready_frames", "ready_duration"):
        if field in data:
            val = data.get(field)
            if val in (None, ""):
                redis.hdel(key, field)
            else:
                mapping[field] = str(val)
    if mapping:
        redis.hset(key, mapping=mapping)
    tr = trackers_map.get(cam_id)
    if tr:
        tr.restart_capture = True
        logger.info(f"[{cam_id}] pipeline reload triggered")
    logger.info(f"[{cam_id}] pipeline set to: {pipeline} profile={profile}")
    return {"updated": True, "pipeline": pipeline, "profile": profile}


@router.post("/api/camera/{cam_id}/reload")
async def reload_camera(cam_id: int, request: Request):
    tr = trackers_map.get(cam_id)
    if not tr:
        return JSONResponse({"error": "Not found"}, status_code=404)
    tr.restart_capture = True
    logger.info(f"[{cam_id}] manual reload requested")
    return {"reloaded": True}


@router.get("/cameras/add")
async def camera_add_page(request: Request):
    """Render camera creation workflow template."""
    return templates.TemplateResponse(
        "camera_create.html",
        {
            "request": request,
            "cfg": config,
            "api_base": str(request.base_url).rstrip("/"),
        },
    )


@router.get("/cameras")
async def cameras_page(request: Request):
    # ensure templates can access ``request.session`` even when the app lacks
    # ``SessionMiddleware`` (as in some tests)
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict) and "session" not in scope:
        scope["session"] = {}
    cam_list = []
    async with cams_lock:
        for c in cams:
            tr = trackers_map.get(c["id"])
            cam_copy = c.copy()
            cam_copy["online"] = tr.online if tr else False
            cam_copy["stream_status"] = getattr(tr, "stream_status", "") if tr else ""
            cam_copy["stream_error"] = getattr(tr, "stream_error", "") if tr else ""
            try:
                h = redis.hgetall(f"camera:{c['id']}:health")
            except Exception:
                h = {}
            cam_copy["latency"] = float(h.get("latency", 0) or 0)
            cam_copy["frame_ts"] = float(h.get("frame_ts", 0) or 0)
            cam_copy["packet_loss"] = int(h.get("packet_loss", 0) or 0)
            cam_copy.setdefault("ppe", False)
            cam_copy.setdefault("visitor_mgmt", False)
            cam_copy.setdefault("enabled", True)
            cam_copy.setdefault("orientation", "vertical")
            cam_copy.setdefault("rtsp_transport", "auto")
            cam_list.append(cam_copy)
    return templates.TemplateResponse(
        "cameras.html",
        {
            "request": request,
            "cams": cam_list,
            "model_classes": UI_CAMERA_TASKS,
            "ppe_pairs": PPE_PAIRS,
            "ppe_tasks": PPE_TASKS,
            "cfg": config,
            "api_base": str(request.base_url).rstrip("/"),
        },
    )


@router.post("/cameras")
async def add_camera(request: Request, manager: CameraManager = Depends(get_camera_manager)):
    data = await request.json()
    lic = cfg.get("license_info", {})
    url = data.get("url")
    src_type = data.get("type", "http")
    ppe = bool(data.get("ppe"))
    visitor = bool(data.get("visitor_mgmt"))
    features = lic.get("features", {})
    counting = data.get("counting", True)
    enabled = bool(data.get("enabled", True))
    tasks: list[str] = []
    if counting:
        if not features.get("in_out_counting", True):
            return JSONResponse({"error": "In/Out counting not licensed"}, status_code=403)
        tasks.extend(["in_count", "out_count", "inout_count"])
    if ppe:
        if not features.get("ppe_detection", True):
            return JSONResponse({"error": "PPE detection not licensed"}, status_code=403)
        tasks += _expand_ppe_tasks(cfg.get("track_ppe", []))
    if visitor:
        if not features.get("visitor_mgmt", True):
            return visitor_disabled_response()

    if visitor:
        tasks.append("visitor_mgmt")
    reverse = bool(data.get("reverse"))
    show = bool(data.get("show", False))
    line_orientation = data.get("line_orientation", "vertical")
    orientation = data.get("orientation", "vertical")
    transport = data.get("transport", "tcp")
    line = data.get("line")
    resolution = await _resolve_resolution(url, data.get("resolution"))
    if not url:
        return JSONResponse({"error": "Missing URL"}, status_code=400)
    if url.isdigit() or url.startswith("/dev/"):
        src_type = "local"
    elif url.startswith("rtsp://"):
        src_type = "rtsp"
    if src_type == "local" and not (url.isdigit() or url.startswith("/dev/")):
        return JSONResponse({"error": "invalid_local_camera"}, status_code=400)
    ready_timeout = data.get("ready_timeout")
    ready_frames = data.get("ready_frames")
    ready_duration = data.get("ready_duration")

    logger.info(f"[add_camera] probing {url}")

    async def _probe_url() -> tuple[bool, str, str, str]:
        if url.isdigit() or url.startswith("/dev/"):

            def _local_capture() -> tuple[bool, str, str, str]:
                cap = cv2.VideoCapture(int(url) if url.isdigit() else url)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                cap.set(cv2.CAP_PROP_FPS, 1)
                ret, frame = cap.read()
                cap.release()
                return ret and frame is not None, "error", "unable to read", ""

            return await asyncio.to_thread(_local_capture)

        transport = TEST_CAMERA_TRANSPORT.get(url)
        transports = [transport] if transport else ["tcp", "udp"]
        last_status = ""
        last_error = ""
        last_hint = ""
        for tr in transports:
            logger.info(f"[add_camera] probing {url} via {tr}")

            def _net_capture(tr: str) -> tuple[bool, str, str, str, str]:
                cap = RtspFfmpegSource(url, tcp=(tr == "tcp"))
                try:
                    cap.open()
                    frame = cap.read()
                    ret = frame is not None
                except Exception:
                    ret = False
                finally:
                    cap.close()
                status = ""
                err = ""
                hint = ""
                return ret, status, err, hint, tr

            ok, status, err, hint, tr_used = await asyncio.to_thread(_net_capture, tr)
            if ok:
                if url not in TEST_CAMERA_TRANSPORT:
                    TEST_CAMERA_TRANSPORT[url] = tr_used
                logger.info(f"[add_camera] probe succeeded for {url} via {tr_used}")
                return True, "ok", "", ""
            logger.warning(f"[add_camera] probe failed for {url} via {tr}: {status} {err}")
            last_status, last_error, last_hint = status, err, hint
        return False, last_status, last_error, last_hint

    ok, status, err, hint = await _probe_url()
    if not ok:
        msg = stream_error_message(status) or err or "unable to read"
        if status == "auth":
            return JSONResponse({"error": msg, "hint": hint}, status_code=401)
        if status == "timeout":
            return JSONResponse({"error": msg, "hint": hint}, status_code=504)
        if status == "dns":
            return JSONResponse({"error": msg, "hint": hint}, status_code=502)
        return JSONResponse({"error": msg, "hint": hint}, status_code=500)

    async with cams_lock:
        if lic:
            max_cams = lic.get("max_cameras")
            if max_cams is not None and len(cams) >= max_cams:
                return JSONResponse({"error": "Camera limit reached"}, status_code=403)
        name = data.get("name") or f"Camera{len(cams)+1}"
        max_id = max([c["id"] for c in cams], default=0)
        if redis:
            new_id = await asyncio.to_thread(redis.incr, "camera:id_seq")
            if int(new_id) <= max_id:
                diff = max_id - int(new_id) + 1
                new_id = await asyncio.to_thread(redis.incrby, "camera:id_seq", diff)
            cam_id = int(new_id)
        else:
            cam_id = max_id + 1
        cam_uuid = str(uuid.uuid4())
        site_id = data.get("site_id") or cfg.get("site_id", 1)
        lat = data.get("latitude")
        lng = data.get("longitude")
        try:
            latitude = float(lat) if lat is not None else None
        except (TypeError, ValueError):
            latitude = None
        try:
            longitude = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            longitude = None
        now = datetime.utcnow().isoformat()
        cam = {
            "id": cam_id,
            "uuid": cam_uuid,
            "name": name,
            "url": url,
            "type": src_type,
            "tasks": tasks,
            "ppe": ppe,
            "visitor_mgmt": visitor,
            "enabled": enabled,
            "show": show,
            "reverse": reverse,
            "line_orientation": line_orientation,
            "line": line,
            "orientation": orientation,
            "rtsp_transport": transport,
            "resolution": resolution,
            "site_id": site_id,
            "created_at": now,
            "updated_at": now,
            "archived": False,
        }
        if latitude is not None:
            cam["latitude"] = latitude
        if longitude is not None:
            cam["longitude"] = longitude
        if ready_timeout is not None:
            try:
                cam["ready_timeout"] = float(ready_timeout)
            except (TypeError, ValueError):
                pass
        if ready_frames is not None:
            try:
                cam["ready_frames"] = int(ready_frames)
            except (TypeError, ValueError):
                pass
        if ready_duration is not None:
            try:
                cam["ready_duration"] = float(ready_duration)
            except (TypeError, ValueError):
                pass
        cams.append(cam)
        save_cameras(cams, redis)
        create_camera(
            Camera(
                id=cam_uuid,
                name=name,
                type=src_type,
                url=url,
                analytics={},
                line=None,
                orientation=Orientation(orientation),
                transport=Transport(transport),
                resolution=resolution,
                reverse=reverse,
                show=show,
                site_id=site_id,
                enabled=enabled,
                archived=False,
                created_at=datetime.fromisoformat(now),
                updated_at=datetime.fromisoformat(now),
                latitude=latitude,
                longitude=longitude,
            ),
        )
        _init_preview_stream(cam)
    if enabled and cfg.get("enable_person_tracking", True):
        try:
            await manager.start(cam_uuid)
        except Exception:
            logger.exception(f"[add_camera] tracker start failed for {cam_uuid}")
            return JSONResponse({"error": "Tracker start failed"}, status_code=500)
    return {"added": True, "camera": cam}


@router.delete("/cameras/{cam_id}")
async def delete_camera(cam_id: int, request: Request):
    global cams, rtsp_connectors, _frame_buses
    async with cams_lock:
        if not any(c["id"] == cam_id for c in cams):
            return {"error": "Not found"}
        cams[:] = [c for c in cams if c["id"] != cam_id]
        save_cameras(cams, redis)

    await asyncio.to_thread(delete_camera_model, str(cam_id), redis)

    if redis:
        keys = list(redis.scan_iter(f"camera:{cam_id}*"))
        if keys:
            redis.delete(*keys)
        redis.delete(
            f"camera_pipeline:{cam_id}",
            f"camera_ffmpeg_flags:{cam_id}",
            f"camera_profile:{cam_id}",
            f"camera:{cam_id}:health",
        )

    conn = rtsp_connectors.pop(cam_id, None)
    if conn:
        conn.stop()
    _frame_buses.pop(cam_id, None)
    preview_publisher.stop_show(cam_id)
    preview_publisher._buses.pop(cam_id, None)
    preview_publisher._clients.pop(cam_id, None)

    await asyncio.to_thread(camera_manager.stop_tracker_fn, cam_id, trackers_map)
    return {"deleted": True}


@router.patch("/cameras/{cam_id}/show")
async def toggle_show(cam_id: int, request: Request):
    async with cams_lock:
        for cam in cams:
            if cam["id"] == cam_id:
                cam["show"] = not cam.get("show", False)
                save_cameras(cams, redis)
                return {"show": cam["show"]}
    return {"error": "Not found"}


@router.patch("/cameras/{cam_id}/enabled")
async def toggle_enabled(cam_id: int, request: Request):
    async with cams_lock:
        for cam in cams:
            if cam["id"] == cam_id:
                cam["enabled"] = not cam.get("enabled", True)
                save_cameras(cams, redis)
                cam_obj = cam
                break
        else:
            return {"error": "Not found"}
    if not cam_obj["enabled"]:
        await asyncio.to_thread(camera_manager.stop_tracker_fn, cam_id, trackers_map)
    elif cfg.get("enable_person_tracking", True):
        try:
            await camera_manager.start(cam_id)
        except Exception:
            logger.exception(f"[toggle_enabled] tracker start failed for {cam_id}")
            return JSONResponse({"error": "Tracker start failed"}, status_code=500)
    return {"enabled": cam_obj["enabled"]}


@router.post("/api/cameras/{cam_id}/activate")
async def activate_camera(cam_id: int, request: Request):
    """Enable a camera and start tracking if permitted."""
    async with cams_lock:
        for cam in cams:
            if cam["id"] == cam_id:
                cam["enabled"] = True
                save_cameras(cams, redis)
                cam_obj = cam
                break
        else:
            return {"error": "Not found"}
    if cfg.get("enable_person_tracking", True):
        try:
            await camera_manager.start(cam_id)
        except Exception:
            logger.exception(f"[activate_camera] tracker start failed for {cam_id}")
            return JSONResponse({"error": "Tracker start failed"}, status_code=500)
    return {"activated": cam_obj["enabled"]}


@router.post("/cameras/{cam_id}/ppe")
@require_feature("ppe_detection")
async def toggle_ppe(cam_id: int, request: Request):
    async with cams_lock:
        for cam in cams:
            if cam["id"] == cam_id:
                cam["ppe"] = not cam.get("ppe", False)
                save_cameras(cams, redis)
                return {"ppe": cam["ppe"]}
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="Not found")


@router.put("/cameras/{cam_id}")
async def update_camera(
    cam_id: int, request: Request, manager: CameraManager = Depends(get_camera_manager)
):
    data = await request.json()
    lic = cfg.get("license_info", {})
    restart_needed = False
    needs_tracker = False
    async with cams_lock:
        for cam in cams:
            if cam["id"] == cam_id:
                url_check = data.get("url", cam.get("url", ""))
                type_check = data.get("type")
                if type_check is None:
                    if "url" in data:
                        if data["url"].isdigit() or data["url"].startswith("/dev/"):
                            type_check = "local"
                        elif data["url"].startswith("rtsp://"):
                            type_check = "rtsp"
                        else:
                            type_check = "http"
                    else:
                        type_check = cam.get("type", "http")
                if type_check == "local" and not (
                    url_check.isdigit() or url_check.startswith("/dev/")
                ):
                    return JSONResponse({"error": "invalid_local_camera"}, status_code=400)
                ppe = data.get("ppe") if "ppe" in data else cam.get("ppe", False)
                visitor = (
                    data.get("visitor_mgmt")
                    if "visitor_mgmt" in data
                    else cam.get("visitor_mgmt", False)
                )

                line = data.get("line")
                if line is None:
                    line = cam.get("line") or cam.get("inout_line")
                if line and (not cam.get("line") or cam.get("line") != line):
                    cam["line"] = line
                if "ppe" in data:
                    cam["ppe"] = bool(ppe)
                if "visitor_mgmt" in data:
                    cam["visitor_mgmt"] = bool(visitor)

                if "enabled" in data:
                    cam["enabled"] = bool(data["enabled"])
                if "latitude" in data:
                    try:
                        cam["latitude"] = float(data["latitude"])
                    except (TypeError, ValueError):
                        cam.pop("latitude", None)
                if "longitude" in data:
                    try:
                        cam["longitude"] = float(data["longitude"])
                    except (TypeError, ValueError):
                        cam.pop("longitude", None)
                if any(
                    k in data
                    for k in [
                        "counting",
                        "ppe",
                        "visitor_mgmt",
                        "tasks",
                    ]
                ):
                    counting = data.get("counting", "in_count" in cam.get("tasks", []))
                    tasks_upd: list[str] = []
                    features = lic.get("features", {})
                    if counting:
                        if not features.get("in_out_counting", True):
                            return JSONResponse(
                                {"error": "In/Out counting not licensed"},
                                status_code=403,
                            )
                        tasks_upd.extend(["in_count", "out_count", "inout_count"])
                    if cam.get("ppe"):
                        if not features.get("ppe_detection", True):
                            return JSONResponse(
                                {"error": "PPE detection not licensed"}, status_code=403
                            )
                        tasks_upd += _expand_ppe_tasks(cfg.get("track_ppe", []))
                    if cam.get("visitor_mgmt"):
                        if not features.get("visitor_mgmt", True):
                            return visitor_disabled_response()
                        tasks_upd.append("visitor_mgmt")
                    cam["tasks"] = tasks_upd

                if "url" in data:
                    cam["url"] = data["url"]
                    restart_needed = True
                    if "type" not in data:
                        if cam["url"].isdigit() or cam["url"].startswith("/dev/"):
                            cam["type"] = "local"
                        elif cam["url"].startswith("rtsp://"):
                            cam["type"] = "rtsp"
                        else:
                            cam["type"] = "http"
                if "type" in data:
                    cam["type"] = data["type"]
                    restart_needed = True
                if "show" in data:
                    cam["show"] = bool(data["show"])
                if "reverse" in data:
                    cam["reverse"] = bool(data["reverse"])
                if "line_orientation" in data:
                    cam["line_orientation"] = data["line_orientation"]
                if "orientation" in data:
                    cam["orientation"] = data["orientation"]
                    restart_needed = True
                if "transport" in data:
                    cam["rtsp_transport"] = data["transport"]
                    restart_needed = True
                if "resolution" in data:
                    cam["resolution"] = await _resolve_resolution(
                        data.get("url", cam["url"]), data["resolution"]
                    )
                    restart_needed = True
                if "ready_timeout" in data:
                    try:
                        cam["ready_timeout"] = float(data["ready_timeout"])
                    except (TypeError, ValueError):
                        cam.pop("ready_timeout", None)
                    restart_needed = True
                if "ready_frames" in data:
                    try:
                        cam["ready_frames"] = int(data["ready_frames"])
                    except (TypeError, ValueError):
                        cam.pop("ready_frames", None)
                    restart_needed = True
                if "ready_duration" in data:
                    try:
                        cam["ready_duration"] = float(data["ready_duration"])
                    except (TypeError, ValueError):
                        cam.pop("ready_duration", None)
                    restart_needed = True

                save_cameras(cams, redis)
                cam_uuid = cam.get("uuid")
                if cam_uuid:
                    db_cam = get_camera(cam_uuid)
                    if db_cam:
                        db_cam.name = cam["name"]
                        db_cam.url = cam["url"]
                        db_cam.show = cam.get("show", db_cam.show)
                        db_cam.enabled = cam.get("enabled", db_cam.enabled)
                        db_cam.orientation = Orientation(
                            cam.get("orientation", db_cam.orientation.value)
                        )
                        db_cam.transport = Transport(
                            cam.get("rtsp_transport", db_cam.transport.value)
                        )
                        db_cam.latitude = cam.get("latitude", db_cam.latitude)
                        db_cam.longitude = cam.get("longitude", db_cam.longitude)
                        update_camera(db_cam)
                required = {"in_count", "out_count", "visitor_mgmt"} | set(PPE_TASKS)
                needs_tracker = cam.get("enabled", True) and bool(set(cam["tasks"]) & required)
                break
        else:
            return {"error": "Not found"}

    tr = trackers_map.get(cam_id)
    if needs_tracker:
        if tr:
            tr.update_cfg(
                {
                    "tasks": cam["tasks"],
                    "type": cam["type"],
                    "reverse": cam["reverse"],
                    "line_orientation": cam["line_orientation"],
                    "resolution": cam["resolution"],
                }
            )
            if restart_needed:
                tr.restart_capture = True
        elif cam.get("enabled", True) and cfg.get("enable_person_tracking", True):
            await manager.start(cam_id)
    elif tr:
        await asyncio.to_thread(camera_manager.stop_tracker_fn, cam_id, trackers_map)
    return {"updated": True}


@router.post("/camera/{cam_id}")
async def update_camera_restart(
    cam_id: int, request: Request, manager: CameraManager = Depends(get_camera_manager)
):
    """Persist camera changes and restart its tracker."""
    data = await request.json()
    async with cams_lock:
        cam = next((c for c in cams if c["id"] == cam_id), None)
        if not cam:
            return JSONResponse({"error": "Not found"}, status_code=404)
        if data.get("resolution") == "auto":
            data["resolution"] = await _resolve_resolution(data.get("url", cam["url"]), "auto")
        cam.update(data)
        save_cameras(cams, redis)
    await manager.restart(cam_id)
    restarted = cam.get("enabled", True) and cfg.get("enable_person_tracking", True)
    return {"updated": True, "restarted": restarted}


@router.get("/cameras/export")
async def export_cameras(request: Request):
    """Export camera list as JSON."""
    from fastapi.responses import JSONResponse

    async with cams_lock:
        data = list(cams)
    return JSONResponse(data)


@router.post("/cameras/import")
async def import_cameras(request: Request):
    """Replace cameras with uploaded list."""
    data = await request.json()
    if not isinstance(data, list):
        return {"error": "invalid"}
    lic = cfg.get("license_info", {})
    max_cams = lic.get("max_cameras")
    if max_cams is not None and len(data) > max_cams:
        return JSONResponse({"error": "Camera limit reached"}, status_code=403)
    # stop existing trackers
    for cid in list(trackers_map.keys()):
        await asyncio.to_thread(camera_manager.stop_tracker_fn, cid, trackers_map)
    for cam in data:
        cam.setdefault("ppe", False)
        cam.setdefault("visitor_mgmt", False)
        cam.setdefault("enabled", False)
        counting = cam.get(
            "counting",
            cam.get("in_count", True)
            or cam.get("out_count", True)
            or ("in_count" in cam.get("tasks", [])),
        )
        tasks: list[str] = []
        features = lic.get("features", {})
        if counting:
            if not features.get("in_out_counting", True):
                return JSONResponse({"error": "In/Out counting not licensed"}, status_code=403)
            tasks.extend(["in_count", "out_count"])
        if cam["ppe"]:
            if not features.get("ppe_detection", True):
                return JSONResponse({"error": "PPE detection not licensed"}, status_code=403)
            tasks += _expand_ppe_tasks(cfg.get("track_ppe", []))
        if cam["visitor_mgmt"]:
            if not features.get("visitor_mgmt", True):
                return visitor_disabled_response()
            tasks.append("visitor_mgmt")
        cam["tasks"] = tasks
        cam.pop("in_count", None)
        cam.pop("out_count", None)
        cam.pop("counting", None)
    async with cams_lock:
        cams[:] = data
        save_cameras(cams, redis)
        cams_copy = list(cams)
    if cfg.get("enable_person_tracking", True):
        for cam in cams_copy:
            if cam.get("enabled", False):
                try:
                    await camera_manager.start(cam["id"])
                except Exception:
                    logger.exception(f"[import_cameras] tracker start failed for {cam['id']}")
    return {"imported": True}


@router.post("/api/cameras/test")
async def api_camera_test(request: Request):
    """Mint a temporary token for camera preview with stream metadata."""
    try:
        data = await request.json()
    except ClientDisconnect:
        return Response(status_code=499)
    url = data.get("url")
    if not url:
        return JSONResponse({"error": "missing url"}, status_code=400)
    token = _issue_preview_token(url)
    return {"notes": f"/api/cameras/preview?token={token}"}


@router.post("/api/cameras/snapshot")
async def api_camera_snapshot(request: Request):
    """Return a single-frame JPEG snapshot from an RTSP URL."""
    try:
        data = await request.json()
    except ClientDisconnect:
        return Response(status_code=499)
    url = data.get("url")
    if not url:
        return JSONResponse({"error": "missing url"}, status_code=400)
    try:
        img = capture_snapshot(url)
    except Exception:
        logger.exception("snapshot failed")
        return JSONResponse({"error": "snapshot failed"}, status_code=400)
    return Response(content=img, media_type="image/jpeg")


@router.get("/api/cameras/preview")
async def api_camera_preview(token: str | None = None):
    """Return a single JPEG frame using a previously minted token."""
    url = _consume_preview_token(token)
    if not url:
        return JSONResponse({"error": "invalid token"}, status_code=400)
    if preview_semaphore.locked():
        return JSONResponse({"error": "Please close other previews"}, status_code=409)
    await preview_semaphore.acquire()
    cmd = build_snapshot_cmd(url, "tcp")
    # HTTP/HTTPS sources do not support the ``-rtsp_transport`` flag
    if url.startswith(("http://", "https://")):
        try:
            idx = cmd.index("-rtsp_transport")
            del cmd[idx : idx + 2]
        except ValueError:
            pass
    logger.info("preview cmd: {}", mask_credentials(" ".join(cmd)))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return JSONResponse({"error": "snapshot timeout"}, status_code=504)
        if proc.returncode != 0 or not out:
            if err:
                logger.warning(
                    "preview stderr: {}",
                    mask_credentials(err.decode(errors="ignore")[-200:]),
                )
            return JSONResponse({"error": "snapshot failed"}, status_code=400)
        return Response(content=out, media_type="image/jpeg")
    finally:
        if proc.returncode is None:
            proc.kill()
        preview_semaphore.release()


@router.get(
    "/api/cameras/{camera_id}/mjpeg",
    summary="Stream MJPEG feed",
)
async def camera_mjpeg(
    camera_id: int,
):
    """Stream an MJPEG feed for the given camera."""
    cam = next((c for c in cams if c.get("id") == camera_id), None)
    if not cam or not cam.get("url"):
        raise HTTPException(status_code=404, detail="camera not found")

    if not preview_publisher.is_showing(camera_id):
        preview_publisher.start_show(camera_id)

    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    return StreamingResponse(
        preview_publisher.stream(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers=headers,
    )


@router.post("/api/cameras/{camera_id}/show")
async def camera_show(camera_id: int):
    """Enable preview streaming for the camera."""
    preview_publisher.start_show(camera_id)
    return {"showing": preview_publisher.is_showing(camera_id)}


@router.post("/api/cameras/{camera_id}/hide")
async def camera_hide(camera_id: int):
    """Disable preview streaming for the camera."""
    preview_publisher.stop_show(camera_id)
    return {"showing": preview_publisher.is_showing(camera_id)}


@router.get("/api/cameras/{camera_id}/stats")
async def camera_stats(camera_id: int):
    """Return RTSP connector stats with preview state."""
    conn = rtsp_connectors.get(camera_id)
    data = conn.stats() if conn else {}
    data["preview"] = preview_publisher.is_showing(camera_id)
    return data


@router.post("/cameras/test")
async def test_camera(request: Request):
    """Return a single frame for previewing a camera stream."""
    headers = {"Access-Control-Allow-Origin": "*"}
    try:
        data = await request.json()
    except ClientDisconnect:
        return Response(status_code=499, headers=headers)

    url = data.get("url")
    width = data.get("width")
    height = data.get("height")
    downscale = data.get("downscale")
    transport = data.get("transport")
    connect_timeout = data.get("timeout")
    stream = bool(data.get("stream"))
    width = int(width) if width is not None else None
    height = int(height) if height is not None else None
    downscale = int(downscale) if downscale is not None else None
    transport = str(transport).lower() if transport else None
    connect_timeout = float(connect_timeout) if connect_timeout is not None else None
    if not url:
        return JSONResponse({"error": "missing url"}, status_code=400, headers=headers)

    test_url = url
    if test_url.startswith("rtsp://") and "subtype=" not in test_url:
        sep = "&" if "?" in test_url else "?"
        test_url = f"{test_url}{sep}subtype=1"

    async def _probe() -> tuple[
        str | None,
        bytes | None,
        str,
        str,
        str,
        str,
        str | None,
        list[str] | None,
    ]:
        """Execute the actual probe in a background task."""

        if url.isdigit() or url.startswith("/dev/"):

            def _local_capture() -> bytes | None:
                cap = cv2.VideoCapture(int(url) if url.isdigit() else url)
                if width and height:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                cap.set(cv2.CAP_PROP_FPS, 1)
                ret, frame = cap.read()
                cap.release()
                if not ret or frame is None:
                    return None
                return encode_jpeg(frame)

            buf = await asyncio.to_thread(_local_capture)
            if buf is None:
                return None, None, "local", "unable to read", "", "", None, None
            return None, buf, "ok", "", "", "", None, None

        transport_pref = transport or TEST_CAMERA_TRANSPORT.get(url)
        transports = [transport_pref] if transport_pref else ["tcp", "udp"]

        last_status = ""
        last_error = ""
        last_hint = ""
        last_stderr = ""
        last_cmd = ""
        last_tr = None
        for tr in transports:
            last_tr = tr

            def _net_capture(
                tr: str,
            ) -> tuple[str, bytes | None, str, str, str, str, str]:
                cap = RtspFfmpegSource(test_url, tcp=(tr == "tcp"))
                try:
                    cap.open()
                    frame = cap.read()
                    ret = frame is not None
                except Exception:
                    ret = False
                    frame = None
                finally:
                    cap.close()
                status = err = hint = stderr = cmd = ""
                if not ret or frame is None:
                    return tr, None, status, err, hint, stderr, cmd
                return tr, encode_jpeg(frame), status, err, "", "", cmd

            tr_used, result, status, err, hint, stderr, cmd = await asyncio.to_thread(
                _net_capture, tr
            )
            if result is not None:
                if url not in TEST_CAMERA_TRANSPORT:
                    TEST_CAMERA_TRANSPORT[url] = tr_used
                    logger.info(f"[test_camera] using {tr_used.upper()} transport")
                return tr_used, result, "ok", "", "", "", cmd, transports

            last_status = status
            last_error = err
            last_hint = hint
            last_stderr = stderr
            last_cmd = cmd

        return (
            last_tr,
            None,
            last_status,
            last_error,
            last_hint,
            last_stderr,
            last_cmd,
            transports,
        )

    # cancel any existing probe for this URL before starting a new one
    if url in TEST_CAMERA_PROBES:
        existing = TEST_CAMERA_PROBES[url]
        if not existing.done():
            existing.cancel()
    probe_task = asyncio.create_task(_probe())
    TEST_CAMERA_PROBES[url] = probe_task
    loop = asyncio.get_running_loop()
    start = loop.time()
    probe_timeout = 10.0
    try:
        while True:
            if probe_task.done():
                break
            if await request.is_disconnected():
                probe_task.cancel()
                return Response(status_code=499, headers=headers)
            if loop.time() - start > probe_timeout:
                probe_task.cancel()
                return JSONResponse({"error": "unable to read"}, headers=headers)
            await asyncio.sleep(0.1)
        tr_used, result, status, err, hint, stderr, cmd, transports = await probe_task

    except asyncio.CancelledError:
        raise
    finally:
        TEST_CAMERA_PROBES.pop(url, None)

    duration_ms = int((loop.time() - start) * 1000)
    cam_name = data.get("name") or data.get("cam_name")
    url_host = urlparse(url).hostname or url
    transport_used = tr_used or transport
    log_data = {
        "cam_name": cam_name,
        "url_host": url_host,
        "transport": transport_used,
        "timeout_sec": connect_timeout,
        "outcome": "success" if result else "failure",
        "error_code": status if not result else "",
        "startup_ms": duration_ms,
    }
    logger.info("[test_camera] {}", log_data)
    mask_cmd = mask_credentials(cmd or "")
    stderr_debug = mask_credentials(stderr or "").splitlines()[-2:]
    if mask_cmd or stderr_debug:
        logger.debug(
            "[test_camera] cmd={} stderr_tail={}",
            mask_cmd,
            "\n".join(stderr_debug),
        )

    if not result:
        tail_lines = mask_credentials(stderr or "").splitlines()[-50:]
        stderr_tail = "\n".join(tail_lines)
        msg = stream_error_message(status) or err
        code = 400
        suggestion = "Check stream URL or camera accessibility"

        if status == "auth":
            code = 401
            suggestion = "Verify camera credentials"
        elif status == "timeout":
            msg = msg or "Connection timed out"
            suggestion = "Check network connectivity or increase timeout"
        elif status == "dns":
            msg = msg or "DNS lookup failed"
            suggestion = "Verify hostname or DNS settings"
        elif status == "network":
            msg = msg or "Network error"
            suggestion = "Check network connectivity or try different transport"
        else:
            msg = msg or "unable to read"
        payload = {"error": msg}
        if cmd:
            payload["ffmpeg_cmd"] = mask_credentials(cmd)
        if stderr_tail:
            payload["stderr_tail"] = stderr_tail
        if transports:
            payload["transports"] = transports
        payload["hint"] = hint or suggestion
        payload["suggestion"] = payload["hint"]
        return JSONResponse(payload, status_code=code, headers=headers)

    if stream:
        token = _issue_preview_token(test_url)
        notes = f"/api/cameras/preview?token={token}"
        return JSONResponse({"notes": notes}, headers=headers)

    # Return a single JPEG frame so the caller can quickly validate camera setup
    # without initiating a full preview stream.
    return Response(result, media_type="image/jpeg", headers=headers)


@router.post("/cameras/probe")
async def camera_probe(request: Request):
    """Return basic stream info for a given RTSP URL."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    url = data.get("url")
    if not isinstance(url, str):
        return JSONResponse({"error": "missing url"}, status_code=400)

    try:
        summary = await asyncio.to_thread(probe_rtsp, url)
    except Exception as e:  # pragma: no cover - best effort
        return JSONResponse({"error": str(e)}, status_code=400)

    return summary


@router.post("/cameras/capabilities")
async def camera_capabilities(request: Request):
    """Return stream resolution, FPS and license flags.

    Uses ``cv2.VideoCapture`` to probe the stream and reports the
    resulting width, height and frame rate.  License information is
    included so the frontend can enable or disable features accordingly.
    """

    data = await request.json()
    url = data.get("url")
    if not url:
        return JSONResponse({"error": "missing url"}, status_code=400)

    def _probe(u: str):
        cap = cv2.VideoCapture(int(u) if u.isdigit() else u)
        ok, frame = cap.read()
        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        cap.release()
        return ok and frame is not None, int(w), int(h), float(fps)

    ok, w, h, fps = await asyncio.to_thread(_probe, url)
    if not ok:
        return JSONResponse({"error": "unable to read"}, status_code=400)

    lic_feats = cfg.get("license_info", {}).get("features", {})
    license_info = {
        "ppe_detection": bool(lic_feats.get("ppe_detection", True)),
        "visitor_mgmt": bool(lic_feats.get("visitor_mgmt", True)),
        "in_out_counting": bool(lic_feats.get("in_out_counting", True)),
    }

    return {
        "resolution": {"width": w, "height": h},
        "fps": fps,
        "license": license_info,
    }


@router.get("/camera/settings/{cam_id}")
async def camera_settings_page(cam_id: int, request: Request):
    async with cams_lock:
        cam = next((c for c in cams if c["id"] == cam_id), None)
    if not cam:
        return RedirectResponse("/cameras", status_code=302)
    pipeline = redis.hget(f"camera:{cam_id}", "pipeline") or ""
    return templates.TemplateResponse(
        "camera_settings.html",
        {
            "request": request,
            "cam": cam,
            "pipeline": pipeline,
            "api_base": str(request.base_url).rstrip("/"),
        },
    )


@router.post("/api/cameras/{cam_id}/pipeline")
async def set_camera_pipeline(cam_id: int, request: Request):
    data = await request.json()
    pipeline = str(data.get("pipeline", "")).strip()
    if len(pipeline) > 200:
        return JSONResponse({"error": "pipeline_too_long"}, status_code=400)
    key = f"camera:{cam_id}"
    mapping: dict[str, str] = {}
    if pipeline:
        try:
            shlex.split(pipeline)
        except ValueError:
            return JSONResponse({"error": "invalid_pipeline"}, status_code=400)
        mapping["pipeline"] = pipeline
    else:
        redis.hdel(key, "pipeline")

    if "ffmpeg_flags" in data:
        ffmpeg_flags = (data.get("ffmpeg_flags") or "").strip()
        if len(ffmpeg_flags) > 200:
            return JSONResponse({"error": "ffmpeg_flags_too_long"}, status_code=400)
        if ffmpeg_flags:
            if not _validate_ffmpeg_flags(ffmpeg_flags):
                return JSONResponse({"error": "invalid_ffmpeg_flags"}, status_code=400)
            mapping["ffmpeg_flags"] = ffmpeg_flags
        else:
            redis.hdel(key, "ffmpeg_flags")

    for field in ("url", "backend"):
        val = (data.get(field) or "").strip()
        if val:
            mapping[field] = val
    if mapping:
        redis.hset(key, mapping=mapping)
    if pipeline:
        logger.info(f"[{cam_id}] custom pipeline set: {pipeline}")
    else:
        logger.info(f"[{cam_id}] custom pipeline cleared")
    tr = trackers_map.get(cam_id)
    if tr:
        tr.restart_capture = True
        logger.info(f"[{cam_id}] pipeline reload requested")
    return {"updated": True, "pipeline": pipeline}


@router.post("/api/cameras/{cam_id}/reload")
async def reload_camera(cam_id: int, request: Request):
    tr = trackers_map.get(cam_id)
    if not tr:
        return JSONResponse({"error": "not_found"}, status_code=404)
    tr.restart_capture = True
    logger.info(f"[{cam_id}] manual stream reload triggered")
    return {"reloaded": True}


@router.get("/camera/{cam_id}")
async def get_camera_config(cam_id: int, request: Request):
    """Return camera URL, backend, pipeline, and ffmpeg flags."""
    async with cams_lock:
        cam = next((c for c in cams if c["id"] == cam_id), None)
    if not cam:
        return JSONResponse({"error": "not_found"}, status_code=404)
    val = redis.get(f"camera_pipeline:{cam_id}")
    pipeline = val.decode() if val else ""
    val = redis.get(f"camera_backend:{cam_id}")
    backend = val.decode() if val else cfg.get("stream_mode", "ffmpeg")
    val = redis.get(f"camera_ffmpeg_flags:{cam_id}")
    ffmpeg_flags = val.decode() if val else ""
    val = redis.get(f"camera_profile:{cam_id}")
    profile = val.decode() if val else ""
    return {
        "url": cam.get("url", ""),
        "backend": backend,
        "pipeline": pipeline,
        "ffmpeg_flags": ffmpeg_flags,
        "profile": profile,
    }


@router.post("/camera/{cam_id}")
async def update_camera_config(cam_id: int, request: Request):
    """Update camera URL, backend, pipeline and ffmpeg flags."""
    data = await request.json()
    url = (data.get("url") or "").strip()
    backend = (data.get("backend") or "").strip()
    pipeline = (data.get("pipeline") or "").strip()
    ffmpeg_flags = (data.get("ffmpeg_flags") or "").strip()
    profile = (data.get("profile") or "").strip()
    if not url:
        return JSONResponse({"error": "missing_url"}, status_code=400)
    if backend != "ffmpeg":
        return JSONResponse({"error": "invalid_backend"}, status_code=400)
    if len(pipeline) > 200:
        return JSONResponse({"error": "pipeline_too_long"}, status_code=400)
    if pipeline:
        try:
            shlex.split(pipeline)
        except ValueError:
            return JSONResponse({"error": "invalid_pipeline"}, status_code=400)
        redis.set(f"camera_pipeline:{cam_id}", pipeline)
    else:
        redis.delete(f"camera_pipeline:{cam_id}")
    if len(ffmpeg_flags) > 200:
        return JSONResponse({"error": "ffmpeg_flags_too_long"}, status_code=400)
    if ffmpeg_flags:
        if not _validate_ffmpeg_flags(ffmpeg_flags):
            return JSONResponse({"error": "invalid_ffmpeg_flags"}, status_code=400)
        redis.set(f"camera_ffmpeg_flags:{cam_id}", ffmpeg_flags)
    else:
        redis.delete(f"camera_ffmpeg_flags:{cam_id}")
    async with cams_lock:
        cam = next((c for c in cams if c["id"] == cam_id), None)
        if not cam:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if profile:
            if profile not in cfg.get("pipeline_profiles", {}):
                return JSONResponse({"error": "invalid_profile"}, status_code=400)
            redis.set(f"camera_profile:{cam_id}", profile)
            cam["profile"] = profile
        else:
            redis.delete(f"camera_profile:{cam_id}")
            cam.pop("profile", None)
        redis.set(f"camera_backend:{cam_id}", backend)
        cam["url"] = url
        if url.isdigit() or url.startswith("/dev/"):
            cam["type"] = "local"
        elif url.startswith("rtsp://"):
            cam["type"] = "rtsp"
        else:
            cam["type"] = "http"
        save_cameras(cams, redis)
    tr = trackers_map.get(cam_id)
    if tr:
        tr.restart_capture = True
    logger.info(f"[{cam_id}] camera config updated")
    return {"updated": True}


@router.patch("/api/cameras/{cam_id}")
async def patch_camera(cam_id: int, request: Request):
    """Update lightweight camera settings.

    Currently supports toggling person counting via the ``counting`` flag.
    """
    data = await request.json()
    changed = False
    cam = None
    if "counting" in data:
        counting = bool(data.get("counting"))
        async with cams_lock:
            cam = next((c for c in cams if c["id"] == cam_id), None)
            if cam is not None:
                tasks = set(cam.get("tasks", []))
                count_tasks = {"in_count", "out_count", "inout_count"}
                if counting:
                    tasks |= count_tasks
                else:
                    tasks -= count_tasks
                cam["tasks"] = list(tasks)
                save_cameras(cams, redis)
                changed = True
        if changed:
            tr = trackers_map.get(cam_id)
            if tr and cam is not None:
                tr.update_cfg({"tasks": cam["tasks"]})
    if changed:
        redis.publish("counter.config", f"cam:{cam_id}")
    return {"updated": changed}


@router.patch("/api/cameras/{cam_id}/line")
async def set_camera_line(cam_id: int, request: Request):
    """Store virtual line configuration for ``cam_id`` in Redis."""
    data = await request.json()
    try:
        cfg = LineConfig.model_validate(data)
    except ValidationError as exc:
        return _validation_response(exc)
    redis.hset(f"cam:{cam_id}:line", mapping=cfg.model_dump())
    redis.publish("counter.config", f"cam:{cam_id}")
    ratio = (cfg.x1 + cfg.x2) / 2 if cfg.orientation == "vertical" else (cfg.y1 + cfg.y2) / 2
    tr = trackers_map.get(cam_id)
    if tr:
        tr.line_orientation = cfg.orientation
        tr.line_ratio = ratio
        if getattr(tr, "cfg", None) is not None:
            tr.cfg["line_ratio"] = ratio
    return {"updated": True}


@router.patch("/api/cameras/{cam_id}/settings")
async def set_camera_settings(cam_id: int, request: Request):
    """Store per-camera settings and publish reload.

    This endpoint currently supports configuring vehicle class filters. Counting
    is managed via ``PATCH /api/cameras/{id}``.
    """

    data = await request.json()
    if "vehicle_classes" in data:
        key = f"cam:{cam_id}:vehicle_classes"
        redis.delete(key)
        classes = data.get("vehicle_classes") or []
        invalid = [c for c in classes if c not in VEHICLE_LABELS]
        if invalid:
            return JSONResponse(
                {"error": "invalid_vehicle_class", "invalid": invalid}, status_code=400
            )
        if classes:
            redis.sadd(key, *classes)
    redis.publish("counter.config", f"cam:{cam_id}")
    return {"updated": True}


@router.get("/api/cameras/{cam_id}/effective_config")
async def get_camera_effective_config(cam_id: int):
    """Return counter configuration stored in Redis for ``cam_id``."""
    raw = redis.hgetall(f"cam:{cam_id}:line")
    line_data: dict[str, float | str] = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        if key == "orientation":
            line_data[key] = v.decode() if isinstance(v, bytes) else v
        else:
            line_data[key] = float(v)
    async with cams_lock:
        cam = next((c for c in cams if c["id"] == cam_id), None)
    count_tasks = {"in_count", "out_count", "inout_count"}
    counting = bool(cam and any(t in cam.get("tasks", []) for t in count_tasks))
    classes = [c.decode() for c in redis.smembers(f"cam:{cam_id}:vehicle_classes")]
    return {
        "line": line_data,
        "counting": counting,
        "vehicle_classes": classes,
    }
