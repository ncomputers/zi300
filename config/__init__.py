"""Unified configuration package."""

from .constants import (
    ANOMALY_ITEMS,
    AVAILABLE_CLASSES,
    BRANDING_DEFAULTS,
    CAMERA_TASKS,
    CONFIG_DEFAULTS,
    COUNT_GROUPS,
    DEFAULT_CONFIG,
    DEFAULT_MODULES,
    MODEL_CLASSES,
    OTHER_CLASSES,
    PPE_ITEMS,
    PPE_PAIRS,
    PPE_TASKS,
    UI_CAMERA_TASKS,
    VEHICLE_LABELS,
)
from .storage import (
    _sanitize_track_ppe,
    load_branding,
    load_config,
    save_branding,
    save_config,
    sync_detection_classes,
)
from .versioning import bump_version, watch_config

config = DEFAULT_CONFIG.copy()


def set_config(cfg: dict) -> None:
    """Replace the global configuration with ``cfg`` and sync thresholds."""

    config.clear()
    config.update(DEFAULT_CONFIG)
    config.update(cfg)


__all__ = [
    "load_config",
    "save_config",
    "load_branding",
    "save_branding",
    "sync_detection_classes",
    "_sanitize_track_ppe",
    "set_config",
    "config",
    "watch_config",
    "bump_version",
    # re-exported constants
    "ANOMALY_ITEMS",
    "AVAILABLE_CLASSES",
    "BRANDING_DEFAULTS",
    "CAMERA_TASKS",
    "CONFIG_DEFAULTS",
    "COUNT_GROUPS",
    "DEFAULT_CONFIG",
    "DEFAULT_MODULES",
    "MODEL_CLASSES",
    "OTHER_CLASSES",
    "PPE_ITEMS",
    "PPE_PAIRS",
    "PPE_TASKS",
    "VEHICLE_LABELS",
    "UI_CAMERA_TASKS",
]
