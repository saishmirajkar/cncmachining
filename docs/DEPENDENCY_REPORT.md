# Dependency and compatibility report

## 1. Environment inspection (start of work)

| Item | Finding |
| --- | --- |
| Repository | Empty (no commits) → new project scaffolded |
| OS / CPU / RAM | Ubuntu 24.04, 4 vCPU, 15 GB RAM |
| Python | 3.13.16 (system) |
| Node | 22.22, npm 10.9 |
| PostgreSQL / Redis | 16.15 / 7.0.15 installed (services not running initially) |
| Docker | client only, no daemon |
| FreeCAD | not installed; **not available in Ubuntu 24.04 apt** (no candidate); FreeCAD PPA and GitHub release downloads blocked by the network policy |
| Open CASCADE | not installed |
| PyPI / npm / conda-forge files | reachable |
| OpenCAMLib | no wheel for Python 3.13 on PyPI |

## 2. What was installed and how

**API/worker Python environment** (`backend/.venv`, Python 3.13): pinned in `backend/requirements.lock.txt`.
Key packages: `cadquery-ocp 8.0.1.1.0` (Open CASCADE 8.0 bindings), `numpy 2.5`, `scipy`, `shapely 2.2`,
`pyclipper 1.4`, `trimesh 5.1.1`, `ezdxf 1.4.5`, `fastapi 0.158`, `sqlalchemy 2.1`, `alembic 1.20`,
`psycopg 3.3`, `rq 2.12`, `redis 8.1`.

**FreeCAD CAM runtime** (separate interpreter, never imported by the web process):

```bash
curl -fsSL https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.3.0-0.tar.bz2 | tar -xj bin/micromamba
MAMBA_ROOT_PREFIX=/opt/mamba ./bin/micromamba create -y -p /opt/freecad-env -c conda-forge --override-channels \
    freecad python=3.11
```

Result: **FreeCAD 26.3.0** (Python 3.11.17, ~4.2 GB) with the CAM workbench (`Path.Op.*`), `libarea`, Adaptive
clearing, and the new tool-bit asset system. Verified headless: `Job`, `Stock.CreateBox`, `ToolController`,
`MillFace`, `PocketShape`, `Adaptive`, `Profile`, `Drilling`, `Helix`. OpenCAMLib (`ocl`) is not part of the
environment, so FreeCAD's 3D Surface/Waterline operations are not usable.

**Frontend**: React 19.3, Vite 8.3 (Rolldown), Tailwind 4.3, TanStack Query 5, React Router 7, three 0.186,
@react-three/fiber 9.8, drei 10.7, CodeMirror 6, Radix primitives, Playwright 1.64 (uses the preinstalled
Chromium via `CHROMIUM_PATH`).

## 3. Compatibility findings (verified during implementation)

| Finding | Handling |
| --- | --- |
| OCCT 8 moved `TopTools_IndexedMapOfShape` etc. to `OCP.collections`; `TopoDS.Edge_s` became `TopoDS.Edge`; `Bnd_Box.Get()` is not bound | compatibility shim in `geometry/topo.py`; `CornerMin/CornerMax` used |
| OCCT 8 STEP writer ignores `write.step.unit`; `SetWriteLengthUnit` scales values but still declares mm | inch fixture patched with an explicit ISO 10303-41 `CONVERSION_BASED_UNIT('INCH')`; unit detection parses STEP text |
| Face indices are not guaranteed identical between OCP 8.0 and FreeCAD's OCCT build | target faces are re-identified in FreeCAD by geometric signature (type, area, centroid) |
| FreeCAD's init scripts overwrite `__main__` globals | runner is imported as a module, not executed as `__main__` |
| FreeCAD Path feeds are mm/s internally | IR converts ×60 to mm/min; covered by tests |
| FreeCAD percent properties require `int` | runner casts |
| FreeCAD Drilling: R plane is `PeckRetract` for peck cycles and `SafeHeight` otherwise | planner records the matching R value |
| FreeCAD MillFace starts inside the stock unless the boundary is extended | `ExtraOffset = tool D + 2 mm` |
| Headless FreeCAD documents keep `Visibility` in `Document.xml` (no `GuiDocument.xml`) | FCStd loader reads either |
| YAML 1.1 parses an unquoted `off:` key as boolean | coolant-off field renamed `stop` |
| PostgreSQL enforces `VARCHAR` lengths SQLite ignores | test suite runs on both |
| Vite 8 (Rolldown) accepts only functional `manualChunks` | config updated |

## 4. Licensing

* FreeCAD (LGPL-2.1+) is used as an external program in a separate process; no FreeCAD postprocessor code is
  copied. The postprocessor configurations in this repository were written for this platform.
* OCP / Open CASCADE (LGPL-2.1 with exception), trimesh (MIT), ezdxf (MIT), shapely (BSD), FastAPI (MIT),
  React (MIT), three.js (MIT), CodeMirror (MIT).
* No proprietary controller manuals or vendor postprocessors are included.

## 5. Resource controls

| Process | Control |
| --- | --- |
| Geometry analysis | child process, `RLIMIT_CPU` = timeout (180 s), `RLIMIT_AS` 4 GB, wall-clock timeout |
| CAM runner | child process (FreeCAD Python), CPU/address-space limits, own session, wall-clock timeout (600 s), temp directory removed after the run |
| Uploads | streamed with a hard size limit (100 MB default), stored under server-generated keys |
| Simulation | grid size capped (`CNC_SIMULATION_MAX_CELLS`, default 300 000 cells) |

## 6. Blockers and how to resolve them

| Blocker | Resolution path |
| --- | --- |
| No real machine configuration is verified | Enter the shop's machine model (travels, spindle, tool changer) and its controller version from OEM documentation as an organisation configuration; add owner-supplied machine blocks (parking, tool change, coolant M-codes); run a first-article prove-out and record a `machine_verified` verification (requires platform admin) |
| FANUC and SINUMERIK postprocessors are drafts | For the exact control (e.g. FANUC 0i-MF Plus series/edition, SINUMERIK 828D SW version), review every declared word, decimal-point and dwell convention, canned-cycle parameters and program structure against the vendor's programming manual; create a new version, update expected fixtures, record `documented` then `tested`; then prove out on a machine for `machine_verified` |
| Native SINUMERIK cycles (CYCLE81/83) | implement cycle emission in the engine once the parameter conventions are verified for the target software version |
| Machine-kinematic collision checking | requires 3D models of spindle head, table and fixtures per machine; the Z-map simulator covers tool, holder and keep-out boxes only |
| 3D surfacing (Surface/Waterline) | install OpenCAMLib into the FreeCAD environment (conda-forge `opencamlib`) and add adapter + validation coverage |
| Docker images not build-tested | build in an environment with a Docker daemon; the commands mirror the verified installation above |
