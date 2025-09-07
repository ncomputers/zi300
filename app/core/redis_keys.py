"""String constants for Redis keys used across the application."""

CFG_VERSION = "app:cfg:version"
CAM_STATE = "app:cam:{id}:state"
EVENTS_STREAM = "app:events"


__all__ = [
    "CFG_VERSION",
    "CAM_STATE",
    "EVENTS_STREAM",
]
