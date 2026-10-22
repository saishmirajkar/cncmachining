"""CAM engine status as seen by the process that actually runs CAM.

With the ``rq`` backend the API and the worker are separate processes, usually separate containers, and only the
worker image contains FreeCAD. Probing FreeCAD from the API would therefore always report it missing. Instead each
worker probes its own engine and publishes the result to Redis with a short expiry; the API reports what workers
published, or that no worker has reported recently.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
KEY_PREFIX = "cnc:worker-engines:"
PUBLISH_EVERY_S = 30
REPORT_TTL_S = 90
REPROBE_EVERY_S = 600
_local_cache: tuple[float, list[dict]] | None = None


def probe() -> list[dict]:
    from app.services.cam.registry import engine_status

    return engine_status()


def local_status() -> list[dict]:
    """Probe in this process (thread/inline backends), cached because a probe starts a FreeCAD interpreter."""
    global _local_cache
    if _local_cache is None or time.monotonic() - _local_cache[0] > REPROBE_EVERY_S:
        _local_cache = (time.monotonic(), probe())
    return _local_cache[1]


def _redis():
    from redis import Redis

    return Redis.from_url(get_settings().redis_url, socket_timeout=3)


def start_publisher() -> threading.Thread:
    """Publish this worker's engine status every PUBLISH_EVERY_S seconds (daemon thread)."""
    host = socket.gethostname()

    def loop():
        status, probed_at = None, 0.0
        while True:
            try:
                if status is None or time.monotonic() - probed_at > REPROBE_EVERY_S:
                    status, probed_at = probe(), time.monotonic()
                    log.info("cam_engine_probe", engines=[(e["name"], e["available"], e["detail"]) for e in status])
                report = {"host": host, "reported_at": datetime.now(timezone.utc).isoformat(), "engines": status}
                _redis().set(KEY_PREFIX + host, json.dumps(report), ex=REPORT_TTL_S)
            except Exception:  # keep publishing after transient Redis/probe failures
                log.exception("cam_engine_heartbeat_failed")
            time.sleep(PUBLISH_EVERY_S)

    t = threading.Thread(target=loop, name="cam-engine-heartbeat", daemon=True)
    t.start()
    return t


def reported_status() -> list[dict]:
    """Merge the reports of all live workers: an engine is available if any worker has it."""
    from app.services.cam.registry import _ENGINES

    try:
        r = _redis()
        reports = [json.loads(v) for v in (r.get(k) for k in r.scan_iter(KEY_PREFIX + "*")) if v]
    except Exception as e:
        return [{"name": n, "available": False, "detail": f"Worker status unavailable: Redis error ({e})",
                 "supported_operations": sorted(c.supported_operations)} for n, c in _ENGINES.items()]
    out = []
    for name, cls in _ENGINES.items():
        seen = [(rep["host"], e) for rep in reports for e in rep["engines"] if e["name"] == name]
        ok = [(h, e) for h, e in seen if e["available"]]
        if ok:
            detail = f"{ok[0][1]['detail']} — {len(ok)} worker(s) ready"
        elif seen:
            detail = f"Worker {seen[0][0]}: {seen[0][1]['detail']}"
        else:
            detail = (f"No CAM worker has reported in the last {REPORT_TTL_S} s. Start a worker on a host with FreeCAD "
                      "(python -m app.worker.run) connected to the same Redis.")
        out.append({"name": name, "available": bool(ok), "detail": detail,
                    "supported_operations": sorted(cls.supported_operations), "workers": len(ok)})
    return out


def engine_status_for_api() -> list[dict]:
    return reported_status() if get_settings().queue_backend == "rq" else local_status()
