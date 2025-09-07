"""String constants for Redis keys used across the application."""

CFG_VERSION = "cfg:version"
CAM_STATE = "cam:{id}:state"
EVENTS_STREAM = "events"

__all__ = [
    "CFG_VERSION",
    "CAM_STATE",
    "EVENTS_STREAM",
]
