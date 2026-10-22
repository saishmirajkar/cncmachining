# CNC Programming & CAM Automation Platform

A web application that turns an engineering model into a **controller-specific, validated, traceable CNC
program**: STEP upload → deterministic geometry analysis → machine/controller selection → stock, material,
workholding and tooling setup → rule-based machining plan → toolpaths from a real CAM engine (FreeCAD CAM) →
versioned postprocessor → independent G-code validation and Z-map simulation → four-eyes engineering review →
controlled export.

No language model is involved in geometry, toolpaths, cutting parameters, postprocessing or validation.

> **Safety statement.** Every check in this platform is a software-level check. Programs are never transmitted
> to a machine. A released program is "released for controlled prove-out", not "production proven". The only
> postprocessor promoted beyond *draft* is LinuxCNC 2.9 (documented + fixture-tested at software level); no
> configuration in this repository is machine-verified.

## What works today (verified in this repository)

| Step | Status |
| --- | --- |
| Upload STEP/IGES/STL/DXF/FCStd with content sniffing, size limit, progress, cancel | ✅ |
| B-rep analysis (OCCT 8.0 via OCP): bbox, volume, validity, holes (through/blind/counterbore/drill point/tap-drill hints), pockets, slots, steps, contour, chamfers, fillets, freeform, patterns, symmetry, orientation needs | ✅ |
| STL (trimesh) and DXF (ezdxf) inspection, explicitly *not* usable for CAM | ✅ |
| Unit detection (STEP/IGES/DXF headers) and user confirmation/correction | ✅ |
| Machine catalogue separating machine model, controller version and postprocessor version, with verification states, org-private configurations and admin APIs | ✅ |
| Setup: orientation, stock, datum/WCS, vise keep-out zones, tolerances, coolant, heights | ✅ |
| Rule-based planner (face, adaptive/pocket, contour, spot, drill/peck, helix) using only the user's tools; parameter provenance; unresolved decisions | ✅ |
| Toolpaths from **FreeCAD 26.3 CAM** in an isolated subprocess (timeouts, rlimits) | ✅ |
| Postprocessors: LinuxCNC 2.9 (*tested*), FANUC 0i-MF ISO (*draft*), SINUMERIK 828D basic (*draft*) | ✅ |
| Independent G-code parser + layered checks + Z-map simulation (rapids into material, shank/holder/keep-out collisions, gouges, remaining material, time estimate) | ✅ |
| Lifecycle, release gates, four-eyes review, immutable revisions, diff, prove-out record | ✅ |
| Export bundle: NC file, setup sheet, tool list, operation plan, validation report, configuration snapshot, manifest with hashes | ✅ |
| Dashboard, projects, libraries, job history, reports, settings, audit trail | ✅ |

Tests: 92 backend tests (SQLite and PostgreSQL) and a Playwright end-to-end test that drives the whole workflow
through the browser against the real stack (API, RQ worker, FreeCAD CAM). See
[`docs/IMPLEMENTATION_CHECKLIST.md`](docs/IMPLEMENTATION_CHECKLIST.md) for open items and
[`docs/DEPENDENCY_REPORT.md`](docs/DEPENDENCY_REPORT.md) for blockers.

## Screenshots

Captured by the Playwright end-to-end test against the running stack:

| Geometry review | Setup (WCS, stock, vise keep-out zones) |
| --- | --- |
| ![Geometry review](docs/screenshots/geometry-review.png) | ![Setup](docs/screenshots/setup.png) |
| **Machining plan with parameter provenance** | **Simulation (backplot, Z-map, limitations)** |
| ![Plan](docs/screenshots/machining-plan.png) | ![Simulation](docs/screenshots/simulation.png) |

![Program](docs/screenshots/program.png)

## Repository layout

```
backend/            FastAPI app, services, worker, Alembic migrations, tests, fixtures
  app/services/geometry/   OCCT loaders, feature recognition, tessellation, units
  app/services/cam/        CAM engine interface, FreeCAD adapter + runner, toolpath IR, pipeline
  app/services/post/       declarative postprocessor schema, engine, bundled configs, expected fixtures
  app/services/validation/ G-code parser, checks, Z-map simulator
  app/services/planning/   setup geometry (stock/WCS/workholding) and planner
frontend/           React 19 + Vite + Tailwind + R3F + CodeMirror; Playwright e2e in frontend/e2e
deploy/             Dockerfiles, docker-compose, nginx
docs/               architecture, schema, postprocessors, catalogue format, dependencies, deployment
scripts/dev.sh      local development stack
```

## Local development

Prerequisites: Python 3.12+, Node 22, PostgreSQL 16, Redis 7 and a FreeCAD ≥ 1.0 (tested 26.3.0) environment
whose Python can `import FreeCAD` — see [`docs/DEPENDENCY_REPORT.md`](docs/DEPENDENCY_REPORT.md) for the exact
installation used here (micromamba + conda-forge into `/opt/freecad-env`).

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt
cp .env.example .env                      # adjust database URL, FreeCAD paths
createdb cncplatform                      # or use the compose stack's postgres
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed.seed --admin-email admin@example.com --admin-password '<strong password>'

# frontend
cd ../frontend && npm ci

# run everything (API :8000, RQ worker, UI :5173)
cd .. && scripts/dev.sh          # scripts/dev.sh stop
```

Open http://127.0.0.1:5173, create an organisation, and start a **New CNC job** with
`backend/tests/fixtures/pocketed_part.step`.

A complete headless run that writes every artifact (analysis, plan, NC program, validation report, export zip):

```bash
cd backend && .venv/bin/python scripts/run_demo.py --out var/demo
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest                       # SQLite
TEST_DATABASE_URL=postgresql+psycopg://cnc:cnc@localhost/cncplatform_test .venv/bin/python -m pytest
cd frontend && npx tsc -b && npx vite build
# browser end-to-end (stack must be running; CHROMIUM_PATH optional)
cd frontend && CHROMIUM_PATH=/opt/pw-browsers/chromium npx playwright test
```

FreeCAD-dependent tests are skipped automatically when the CAM runtime is not available.

## Documentation

* [Architecture](docs/ARCHITECTURE.md) – components, data flow, IR, validation layers, security
* [Implementation checklist](docs/IMPLEMENTATION_CHECKLIST.md) – phases, status, open items
* [Dependency & compatibility report](docs/DEPENDENCY_REPORT.md) – environment inspection, versions, blockers
* [Database schema](docs/DATABASE_SCHEMA.md)
* [Postprocessors](docs/POSTPROCESSORS.md) – configuration schema, verification, adding a controller
* [Catalogue format](docs/CATALOGUE_FORMAT.md) – machine/controller import/export
* [Deployment](docs/DEPLOYMENT.md) – containers, operations, backups
* [Read-only preview](docs/PREVIEW.md) – static build that replays a recorded run (no backend)
