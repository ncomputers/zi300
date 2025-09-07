import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from types import SimpleNamespace

import routers.cameras as cameras


def test_auto_resolution_on_add_and_update(client, monkeypatch):
    monkeypatch.setattr(cameras, "cams", [])
    monkeypatch.setattr(cameras, "start_tracker", lambda *a, **k: None)
    monkeypatch.setattr(cameras, "save_cameras", lambda *a, **k: None)

    async def fake_res(
        url,
        *,
        cache_seconds=300,
        invalidate=False,
        timeout=5,
        fallback_ttl=None,
    ):
        return 111, 222

    monkeypatch.setattr(cameras, "async_get_stream_resolution", fake_res)

    resp = client.post("/cameras", json={"url": "rtsp://test", "resolution": "auto"})
    assert resp.status_code == 200
    cam = resp.json()["camera"]
    assert cam["resolution"] == "111x222"
    cam_id = cam["id"]

    async def fake_res2(
        url,
        *,
        cache_seconds=300,
        invalidate=False,
        timeout=5,
        fallback_ttl=None,
    ):
        return 333, 444

    monkeypatch.setattr(cameras, "async_get_stream_resolution", fake_res2)
    resp = client.put(f"/cameras/{cam_id}", json={"resolution": "auto"})
    assert resp.status_code == 200
    assert cameras.cams[0]["resolution"] == "333x444"


def test_resolution_update_restarts_capture(client, monkeypatch):
    monkeypatch.setattr(cameras, "save_cameras", lambda *a, **k: None)
    monkeypatch.setattr(cameras, "cfg", {})
    cameras.cams = [
        {
            "id": 1,
            "url": "rtsp://test",
            "type": "rtsp",
            "tasks": [],
            "ppe": False,
            "enabled": True,
            "show": True,
            "reverse": False,
            "line_orientation": "vertical",
            "resolution": "1080p",
        }
    ]
    tracker = SimpleNamespace(restart_capture=False, update_cfg=lambda cfg: None)
    cameras.trackers_map = {1: tracker}

    resp = client.put("/cameras/1", json={"resolution": "480p"})
    assert resp.status_code == 200
    assert cameras.cams[0]["resolution"] == "480p"
    assert tracker.restart_capture is True
