from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger("http")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(json=s.env == "production")
    app = FastAPI(title="CNC Programming & CAM Platform API", version="0.1.0")
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error", path=request.url.path)
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        response.headers["x-request-id"] = rid
        log.info("request", method=request.method, path=request.url.path, status=response.status_code,
                 ms=round((time.perf_counter() - t0) * 1000, 1))
        structlog.contextvars.clear_contextvars()
        return response

    from app.api import auth, catalogue, designs, jobs, programs, projects, setups, tooling, dashboard

    for r in (auth.router, auth.orgs, projects.router, projects.audit_router, designs.router,
              catalogue.router, catalogue.admin, tooling.router, setups.router, jobs.router, programs.router,
              dashboard.router):
        app.include_router(r)

    @app.get("/api/health")
    def health() -> dict:
        from app.worker.heartbeat import engine_status_for_api

        return {"status": "ok", "env": s.env, "queue_backend": s.queue_backend,
                "cam_engines": engine_status_for_api()}

    return app


app = create_app()
