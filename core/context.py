from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from fastapi import Request
from fastapi.templating import Jinja2Templates
from redis import Redis


# Default resources for tests or lightweight usage where the FastAPI app state
# is not fully populated.  Individual routers can populate these values by
# calling :func:`set_app_state_defaults`.
_default_config: dict = {}
_default_redis: Redis | None = None
_default_trackers: Dict[int, Any] = {}
_default_templates: Jinja2Templates = Jinja2Templates("templates")
_default_branding: dict = {}
_default_cameras: List[dict] = []
_default_redisfx: Any | None = None


def set_app_state_defaults(
    config: dict | None = None,
    redis: Redis | None = None,
    trackers: Dict[int, Any] | None = None,
    templates_path: str | None = None,
    cameras: List[dict] | None = None,
    redisfx: Any | None = None,
) -> None:
    """Populate module-level defaults used by :func:`get_app_context`.

    This helper allows tests to provide the objects normally stored on the
    FastAPI application's ``state`` without needing a full application
    instance.
    """

    global _default_config, _default_redis, _default_trackers
    global _default_templates, _default_branding, _default_cameras, _default_redisfx

    if config is not None:
        _default_config = config
        _default_branding = config.get("branding", {})
    if redis is not None:
        _default_redis = redis
    if trackers is not None:
        _default_trackers = trackers
    if templates_path is not None:
        _default_templates = Jinja2Templates(templates_path)
    if cameras is not None:
        _default_cameras = cameras
    if redisfx is not None:
        _default_redisfx = redisfx


@dataclass
class AppContext:
    """Container for shared application resources."""

    config: dict
    redis: Redis | None
    trackers: Dict[int, Any]
    templates: Jinja2Templates
    branding: dict
    cameras: List[dict]
    redisfx: Any | None = None


def get_app_context(request: Request) -> AppContext:
    """Return application context from the FastAPI app state."""
    ctx = getattr(request.state, "app_context", None)
    if ctx is None:
        app = request.app
        ctx = AppContext(
            config=getattr(app.state, "config", _default_config),
            redis=getattr(app.state, "redis_client", _default_redis),
            trackers=getattr(app.state, "trackers", _default_trackers),
            templates=getattr(app.state, "templates", _default_templates),
            branding=getattr(app.state, "config", _default_config).get("branding", _default_branding),
            cameras=getattr(app.state, "cameras", _default_cameras),
            redisfx=getattr(app.state, "redis_facade", _default_redisfx),
        )
        request.state.app_context = ctx
    return ctx
