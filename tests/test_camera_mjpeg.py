from fastapi import FastAPI
from starlette.testclient import TestClient

from routers import cameras


def setup_function():
    cameras.cams = []
    cameras.preview_publisher = cameras.preview_publisher.__class__()
    cameras.rtsp_connectors = {}


def _build_app():
    app = FastAPI()
    app.include_router(cameras.router)
    app.include_router(cameras.preview_router)
    return app


def test_mjpeg_stream_viewer_role(monkeypatch):
    cameras.cams = [{"id": 1, "url": "rtsp://example/stream"}]

    calls = []

    def fake_require_roles(request, roles):
        calls.append(roles)
        return {"role": "viewer"}

    monkeypatch.setattr(cameras, "require_roles", fake_require_roles)

    class FakePub:
        def __init__(self):
            self.shown = set()

        def start_show(self, cid):
            self.shown.add(cid)

        def stop_show(self, cid):
            self.shown.discard(cid)

        def is_showing(self, cid):
            return cid in self.shown

        async def stream(self, cid):
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nA\r\n"
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nB\r\n"

    cameras.preview_publisher = FakePub()

    app = _build_app()
    with TestClient(app) as client:
        resp = client.get("/api/cameras/1/mjpeg")
        assert resp.status_code == 200
        body = resp.content
        assert b"A" in body and b"B" in body

    assert calls == [["viewer", "admin"]]


def test_viewer_can_toggle_preview(monkeypatch):
    cameras.cams = [{"id": 1, "url": "rtsp://example/stream"}]

    calls = []

    def fake_require_roles(request, roles):
        calls.append(roles)
        return {"role": "viewer"}

    monkeypatch.setattr(cameras, "require_roles", fake_require_roles)

    class FakePub:
        def __init__(self):
            self.shown = set()

        def start_show(self, cid):
            self.shown.add(cid)

        def stop_show(self, cid):
            self.shown.discard(cid)

        def is_showing(self, cid):
            return cid in self.shown

        async def stream(self, cid):
            yield b""

    cameras.preview_publisher = FakePub()

    app = _build_app()
    with TestClient(app) as client:
        resp = client.post("/api/cameras/1/show")
        assert resp.status_code == 200
        assert resp.json()["showing"] is True
        resp2 = client.post("/api/cameras/1/hide")
        assert resp2.status_code == 200
        assert resp2.json()["showing"] is False

    assert calls == [["viewer", "admin"], ["viewer", "admin"]]
