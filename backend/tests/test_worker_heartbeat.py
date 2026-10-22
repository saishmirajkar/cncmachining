"""The API reports CAM availability from worker heartbeats, not from its own (FreeCAD-less) environment."""

import json

from app.worker import heartbeat


class FakeRedis:
    def __init__(self, data: dict[str, str]):
        self.data = data

    def scan_iter(self, pattern: str):
        return [k for k in self.data if k.startswith(pattern.rstrip("*"))]

    def get(self, key: str):
        return self.data.get(key)


def _report(host: str, available: bool, detail: str) -> str:
    return json.dumps({"host": host, "reported_at": "2026-10-22T00:00:00+00:00",
                       "engines": [{"name": "freecad-cam", "available": available, "detail": detail,
                                    "supported_operations": []}]})


def test_no_worker_reported(monkeypatch):
    monkeypatch.setattr(heartbeat, "_redis", lambda: FakeRedis({}))
    [e] = heartbeat.reported_status()
    assert e["available"] is False and "No CAM worker" in e["detail"]


def test_any_ready_worker_makes_engine_available(monkeypatch):
    data = {heartbeat.KEY_PREFIX + "w1": _report("w1", False, "FreeCAD interpreter not found"),
            heartbeat.KEY_PREFIX + "w2": _report("w2", True, "FreeCAD 26.3.0")}
    monkeypatch.setattr(heartbeat, "_redis", lambda: FakeRedis(data))
    [e] = heartbeat.reported_status()
    assert e["available"] is True and e["workers"] == 1 and "FreeCAD 26.3.0" in e["detail"]


def test_worker_without_freecad_is_reported(monkeypatch):
    data = {heartbeat.KEY_PREFIX + "w1": _report("w1", False, "FreeCAD interpreter not found at /opt/x")}
    monkeypatch.setattr(heartbeat, "_redis", lambda: FakeRedis(data))
    [e] = heartbeat.reported_status()
    assert e["available"] is False and e["detail"].startswith("Worker w1: FreeCAD interpreter not found")


def test_redis_down_is_reported(monkeypatch):
    def boom():
        raise ConnectionError("refused")

    monkeypatch.setattr(heartbeat, "_redis", boom)
    [e] = heartbeat.reported_status()
    assert e["available"] is False and "Redis error" in e["detail"]
