# Technical Architecture

This document describes how the CNC programming and CAM automation platform is built. It reflects what has
actually been implemented and verified in this repository. See `DEPENDENCY_REPORT.md` for exact versions and
`IMPLEMENTATION_CHECKLIST.md` for phase status.

## 1. Design principles

1. **Deterministic engineering core.** Geometry analysis, cutting-parameter calculation, toolpath generation,
   postprocessing, and validation are deterministic code paths with unit tests. No LLM is involved in any of them.
2. **Real engines, not templates.** Toolpaths are computed by FreeCAD's CAM workbench (libarea/Clipper based
   2.5D operations). G-code is derived from those toolpaths by a configurable postprocessor. Nothing is emitted
   from a static G-code template.
3. **Separation of concerns.** The CAD kernel, CAM engine, machine catalogue, and postprocessor registry are
   independent modules with explicit interfaces. Machine-specific behaviour lives in data (catalogue and
   postprocessor configuration), never in generic application code.
4. **Traceability.** Every CAM job stores an immutable snapshot (with SHA-256) of the exact geometry revision,
   stock, tools, machine configuration, postprocessor version and operations it was built from. Every program
   revision, validation report, review and release is linked to that snapshot and recorded as an audit event.
5. **Explicit limits.** Unsupported formats, operations, and controller features are reported as unsupported.
   Missing critical data blocks generation rather than being guessed.

## 2. Runtime topology

```
          ┌──────────────┐  HTTPS/JSON  ┌────────────────────────┐
 Browser  │ React SPA    │ ───────────▶ │ FastAPI (api process)  │──── PostgreSQL (state, audit)
          │ (Vite build) │ ◀─────────── │  auth, CRUD, planning, │──── Object storage (files)
          └──────────────┘              │  postprocessing,       │──── Redis (queue, rate limit)
                                         │  validation (sync/fast)│
                                         └──────────┬─────────────┘
                                                    │ enqueue (RQ)
                                         ┌──────────▼─────────────┐
                                         │ worker process (RQ)    │
                                         │  geometry analysis     │
                                         │  (OCCT via OCP)        │
                                         │  CAM orchestration     │
                                         └──────────┬─────────────┘
                                                    │ subprocess, JSON in/out,
                                                    │ timeout + rlimits, temp dir
                                         ┌──────────▼─────────────┐
                                         │ FreeCAD CAM runner     │
                                         │ (separate Python 3.11  │
                                         │  env, FreeCAD 26.x)    │
                                         └────────────────────────┘
```

* **api** – FastAPI on Python 3.13. Owns HTTP, auth, org isolation, request validation and persistence.
* **worker** – the same code base started as an RQ worker. Runs geometry analysis, CAM jobs, and
  simulation. In development and tests the queue can run `inline` (synchronous) or in a thread pool.
* **FreeCAD CAM runner** – `backend/app/services/cam/freecad_runner.py`, executed by FreeCAD's own Python
  interpreter in a child process. The web process never imports FreeCAD. The runner receives a JSON job
  specification, writes a JSON toolpath result, and is killed on timeout. It is given an address-space limit and
  CPU-time limit (`resource.setrlimit`) and a private temporary directory.

## 3. Back-end module map (`backend/app`)

| Module | Responsibility |
| --- | --- |
| `core/` | settings (env vars), security (bcrypt, JWT), structured logging, rate limiting, error types |
| `db/` | SQLAlchemy engine/session, declarative base |
| `models/` | normalized ORM schema (see `DATABASE_SCHEMA.md`) |
| `schemas/` | Pydantic request/response models |
| `api/` | routers: auth, orgs, projects, designs, catalogue, tools, materials, setups, jobs, programs, admin |
| `services/storage.py` | object-storage abstraction (local filesystem implementation; S3-compatible interface) |
| `services/audit.py` | audit-event writer |
| `services/geometry/` | file sniffing, loaders (STEP/IGES/BREP/FCStd via OCP, STL via trimesh, DXF via ezdxf), unit handling, topology analysis, feature recognition, tessellation |
| `services/cutting/` | cutting-speed / feed calculation with provenance, machine-limit clamping, power estimate |
| `services/planning/` | rule-based machining planner producing an inspectable operation list |
| `services/cam/` | `CamEngine` adapter interface, machine-neutral toolpath IR, FreeCAD engine adapter + runner |
| `services/post/` | postprocessor registry, declarative postprocessor engine, bundled configurations |
| `services/validation/` | G-code parser (independent of the emitter), layered checks, Z-map material-removal simulation, time estimate |
| `services/lifecycle.py` | job status state machine and release gates |
| `services/export.py` | setup sheet, tool list, validation report, export bundle with manifest hashes |
| `worker/` | queue abstraction (RQ / inline / thread) and task functions |
| `seed/` | catalogue seed data (YAML) and loader |

## 4. Data flow of one job

1. **Upload** (`POST /api/designs`): streamed to a temp file with a hard size limit, SHA-256 computed, content
   sniffed (STEP `ISO-10303-21`, IGES fixed-column `S`/`G` records, binary/ASCII STL structure, DXF group codes,
   FCStd zip with `Document.xml`). Extension and content must agree. The original is stored immutably.
2. **Geometry analysis** (worker): the file is parsed by the matching loader. B-rep models are checked with
   `BRepCheck_Analyzer`, converted to millimetres, and analysed: bounding box, volume, area, solids/shells/faces,
   surface-type histogram, and features (holes, pockets, slots, steps, external contour, chamfers, fillets,
   patterns, orientation needs). A normalized STEP (mm) and a preview mesh are stored.
3. **Setup**: the user selects a machine configuration (machine model + controller version + postprocessor
   version), stock, material grade, workholding, tool library, orientation, datum and WCS. The API reports exactly
   which required fields are missing.
4. **Plan**: the planner maps recognised features to candidate operations using only the user's tools, computes
   parameters through the cutting service (each value carries a source tag: `tool_manufacturer`, `material_table`,
   `user`, or `calculated`), and lists unresolved decisions. The user edits and approves the plan.
5. **CAM**: a `CAMJob` snapshot is created (idempotency key prevents duplicates). The worker transforms the
   part into the work coordinate system, the FreeCAD runner builds a FreeCAD Job/Stock/ToolControllers/Ops,
   re-identifies target faces by geometric signature, recomputes and returns `Path.Commands` per operation. These
   are stored as a machine-neutral IR artifact (`cnc-ir/1`).
6. **Postprocess**: the selected postprocessor version renders the IR into controller G-code using only codes
   declared in its configuration. Machine-specific blocks (parking, tool-change macros) come exclusively from the
   machine configuration and are omitted with a warning if not supplied.
7. **Validate & simulate**: the emitted text is re-parsed by an independent parser; geometry, toolpath, and
   postprocessor checks run; a Z-map (height-field) material-removal simulation detects rapid-into-material,
   holder/shank engagement, fixture collisions, gouges against the part height-field, and remaining material.
8. **Review & release**: lifecycle gates (no validation errors, postprocessor status ≥ *tested*, an engineering
   review by an engineer-role user with explicit acknowledgements) must pass before *Ready for controlled
   export*. Released revisions are immutable; edits create a new revision and require re-validation.
9. **Export**: a zip bundle with the NC file, setup sheet, tool list, operation plan, validation report and a
   manifest with hashes and the full configuration snapshot.

## 5. Machine-neutral toolpath IR (`cnc-ir/1`)

```json
{
  "format": "cnc-ir/1", "units": "mm", "wcs": "G54",
  "engine": {"name": "freecad-cam", "version": "26.3.0"},
  "operations": [
    {"id": "...", "name": "Pocket 1", "type": "pocket", "tool_number": 1, "spindle_rpm": 7958,
     "spindle_dir": "cw", "coolant": "flood",
     "moves": [
        {"k": "rapid", "x": 60, "y": 45, "z": 5},
        {"k": "linear", "x": 60, "y": 45, "z": -1, "f": 300},
        {"k": "arc_cw", "x": 70, "y": 45, "z": -1, "i": 5, "j": 0, "f": 900},
        {"k": "drill", "cycle": "peck", "x": 10, "y": 10, "z": -25, "r": 2, "q": 3, "f": 120, "retract": "initial"}
     ]}
  ]
}
```

Arc centres are incremental (IJK) relative to the start point, XY plane. Canned cycles are retained as cycles so
the postprocessor decides whether the controller supports them or whether they must be expanded.

## 6. Postprocessor model

A postprocessor *version* is an immutable, hashed YAML/JSON document (schema in `services/post/schema.py`):
dialect, file extension, units/positioning words, program-number format, comment style, line numbering, decimal
precision per address, supported G/M codes, canned-cycle support, arc format, tool-change sequence, compensation
words, coolant words, start/end block skeletons composed only of codes the postprocessor declares. Machine-specific
sequences (safe parking, tool-change macros, spindle warm-up) are **not** in the postprocessor; they are fields of
the machine configuration and must be entered and verified by the machine owner.

Verification states (both postprocessors and machine configurations): `draft`, `documented`, `tested`,
`machine_verified`, `deprecated`. Only `PostprocessorVerification` records move a version forward, and the API
refuses `machine_verified` without a reviewer and machine-configuration reference.

## 7. Validation layers and what they prove

| Layer | Implemented checks | What it does **not** prove |
| --- | --- | --- |
| Geometry | empty/invalid shapes, units confirmed, stock present and containing the part, part vs. work envelope | that the model matches the drawing intent |
| Toolpath | cut moves outside stock + tool radius, rapids into material (Z-map), holder/shank engagement, tool reach, stepdown vs. limits, fixture-box collisions, gouge and remaining material vs. part height-field, travel limits (when work offset known) | full machine-kinematic collision; undercuts (Z-map is 2.5D) |
| Postprocessor | unknown words/codes, codes not declared by the postprocessor, missing sections, tool references, units/positioning modes, spindle-off cutting, missing feed, line-length/precision | that the physical controller executes it identically |
| Simulation | Z-map material removal, time estimate (feed + rapid + tool change, no acceleration model) | machine dynamics, chip load accuracy, thermal effects |
| Machine-specific | presence/verification status of machine configuration and postprocessor | — requires physical prove-out |
| Prove-out | recorded by a user after first-article run | — |

## 8. Security

* JWT bearer tokens (HS256, secret from env), bcrypt password hashes.
* Every org-scoped query is filtered by the caller's membership; cross-org IDs return 404.
* Roles: `owner`, `admin`, `engineer`, `programmer`, `operator`, `viewer`. Platform administrators manage the
  global catalogue; organisations may create private machine configurations that never alter global records.
* Uploads are untrusted: extension+content sniffing, size limits, parsing in worker with resource limits, no
  macro/script execution. FCStd files are read as zip archives (BREP payload only); FreeCAD never opens them.
* Rate limiting on auth and upload endpoints (Redis or in-memory token bucket).
* Secrets only via environment variables (`.env.example` documents them).

## 9. Front end (`frontend/`)

React 19 + TypeScript + Vite 8, Tailwind CSS 4 with shadcn/ui-style components (Radix primitives), React Router,
TanStack Query, React Three Fiber for the 3D workspace (part mesh, stock, axes, work origin, feature highlight,
toolpath lines, simulated stock height-field, playback), CodeMirror 6 for the G-code viewer (custom G-code
highlighting, search, line annotations, operation markers).

## 10. Extending the platform

* **New CAM engine** – implement `CamEngine` (`services/cam/base.py`) and register it.
* **New controller** – add a postprocessor YAML, fixture parts, and expected-output tests; register a
  `PostprocessorVersion` in `draft` and promote it through verification records.
* **New machine** – add through the Machine Library UI/API or import a catalogue JSON (`docs/CATALOGUE_FORMAT.md`).
