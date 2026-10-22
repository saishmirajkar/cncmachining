# Deployment and operations

## Components

| Service | Image | Notes |
| --- | --- | --- |
| `postgres` | postgres:16 | durable state |
| `redis` | redis:7 (AOF) | job queue, rate limiting |
| `migrate` | api image | `alembic upgrade head` + idempotent catalogue seed + postprocessor fixture verification |
| `api` | `deploy/Dockerfile.api` | FastAPI/uvicorn; never imports FreeCAD |
| `worker` | `deploy/Dockerfile.worker` | `python -m app.worker.run`: RQ worker + FreeCAD 26.x environment; scale horizontally |
| `web` | `deploy/Dockerfile.web` | nginx serving the SPA and proxying `/api` |

```bash
cp deploy/.env.example deploy/.env   # set POSTGRES_PASSWORD, CNC_SECRET_KEY, PUBLIC_ORIGIN
docker compose -f deploy/docker-compose.yml build api
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml run --rm migrate \
  python -m app.seed.seed --admin-email admin@your.domain --admin-password '<strong password>'
```

The container files mirror the installation verified on bare metal (DEPENDENCY_REPORT.md) but were **not
built in the development environment** (no Docker daemon). Build and run the end-to-end tests against the
stack before first use.

## Configuration

All settings are `CNC_*` environment variables (`backend/.env.example`). Production refuses to start with the
development secret. Relevant limits: `CNC_MAX_UPLOAD_MB`, `CNC_GEOMETRY_TIMEOUT_S`, `CNC_CAM_TIMEOUT_S`,
`CNC_CAM_MEMORY_LIMIT_MB`, `CNC_SIMULATION_MAX_CELLS`, `CNC_RATE_LIMIT_*`.

## Job reliability

* CAM jobs are created with a client idempotency key (unique per organisation); repeated requests return the
  existing job.
* Tasks re-read their row and exit unless it is in the expected state, so a redelivered queue message cannot
  run a job twice. RQ retries once for infrastructure failures; CAM failures are terminal until a user retries
  (max 3 attempts, same snapshot).
* Native code runs in child processes with CPU, memory and wall-clock limits; a crash or timeout marks the job
  `cam_failed` with the reason.
* Temporary directories are per run and deleted afterwards; uploads are written to temp files and removed after
  storage.

## Monitoring

* JSON logs (`CNC_ENV=production`) with `request_id`, method, path, status, duration; audit events in
  `audit_events`.
* `GET /api/health` reports queue mode and CAM-engine availability. With the `rq` backend the API does not have
  FreeCAD; each worker probes FreeCAD itself and publishes the result to Redis every 30 s (expires after 90 s),
  and the API reports those heartbeats. "No CAM worker has reported" means no worker is running or it cannot
  reach the API's Redis; "Worker <host>: FreeCAD interpreter not found" means that worker's image or
  `CNC_FREECAD_PYTHON` is wrong. Start workers with `python -m app.worker.run` (plain `rq worker` runs jobs but
  sends no heartbeat).
* Watch the RQ failed registry (`rq info --url …`) and jobs in `cam_failed` / `validation_failed`.
* Error tracking (e.g. Sentry) is not integrated; add it at the ASGI middleware and RQ exception handler.

## Backups

* PostgreSQL: nightly `pg_dump -Fc cncplatform` with retention, plus WAL archiving for point-in-time recovery.
* Object storage (`/data/storage`): uploaded designs, normalized models, toolpath artifacts, simulation data.
  Back up together with the database dump (keys are referenced from the database). Files are write-once.
* Restore test: restore both into a staging stack, run `alembic current`, open a released program and verify
  that its SHA-256 matches the export manifest.

## Upgrades

* Apply migrations with the `migrate` service before starting new API/worker containers.
* Never edit a released postprocessor configuration: the seed refuses changed content for an existing version.
  Publish a new version and re-verify.
* After upgrading FreeCAD, run the backend test suite and the Playwright workflow; toolpath output may change
  and must be re-validated (existing programs keep their recorded engine version).
