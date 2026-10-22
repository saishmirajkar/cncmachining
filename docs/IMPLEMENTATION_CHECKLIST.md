# Implementation checklist

Legend: ✅ done and tested · 🟡 partial / software only · ⛔ not implemented (reason given)

## Phase 1 – Foundation
- ✅ Environment inspection (see DEPENDENCY_REPORT.md)
- ✅ FastAPI backend, React/Vite frontend scaffold
- ✅ PostgreSQL schema (SQLAlchemy 2, 33 tables) with Alembic migrations; SQLite supported for tests
- ✅ Authentication (bcrypt + JWT), organisations, memberships, roles (owner/admin/engineer/programmer/operator/viewer)
- ✅ Projects, organisation isolation (cross-org IDs return 404; header spoofing returns 403) — tested
- ✅ Upload UI: drag & drop, file picker, progress, cancel, size limit, error messages
- 🟡 E-mail invitations: members can only be added if already registered

## Phase 2 – CAD processing
- ✅ Content sniffing per format; extension/content mismatch rejected
- ✅ STEP/IGES via OCCT 8.0 (OCP); FCStd read as zip (BREP payloads only, no FreeCAD execution)
- ✅ Unit detection (STEP length unit, IGES global section, DXF $INSUNITS); user confirmation/correction with rescale
- ✅ Bounding box, volume, area, solids/shells/faces, validity, free edges, surface-type histogram
- ✅ Features: holes (through/blind/internal, counterbore, entry chamfer, drill-point bottom, tap/clearance size hints), closed pockets, open pockets/steps, slots, corner radii, sharp corners, max tool diameter, islands, plateaus, external contour (projected silhouette), chamfers, fillets, freeform surfaces, down-facing faces, cylindrical bosses, linear/rectangular/circular patterns, mirror symmetry, other-orientation access
- ✅ Orientation re-analysis (6 directions × 90° about Z)
- ✅ STL inspection with explicit mesh limitations; DXF 2D inspection with explicit limitations
- ✅ 3D preview (per-analysis tessellation with per-face ranges for feature highlighting)
- 🟡 Thread recognition: only size-based hints (thread geometry is not in STEP AP214 models)
- ⛔ CAM from STL or DXF: not supported (mesh/2D sources need different strategies)

## Phase 3 – Machine and tool libraries
- ✅ Separate MachineManufacturer / MachineModel / ControllerManufacturer / ControllerModel / ControllerVersion / Postprocessor / PostprocessorVersion / MachineConfiguration
- ✅ Verification states (draft, documented, tested, machine_verified, deprecated) with recorded verifications
- ✅ Global catalogue protected (platform admin only); organisation-private configurations start as draft and reset to draft on edit
- ✅ Machine-specific blocks (parking, tool change, coolant, declared M-codes) supplied by the machine owner, validated against the postprocessor's declared codes
- ✅ Catalogue export/import (`cnc-catalogue/1`)
- ✅ Seed: Indian-market and global manufacturers as research targets, controller families, example machines clearly labelled as fictional
- ✅ Materials and grades (ISO 513 groups) with generic cutting data labelled as estimates; tool libraries, tools, holder assemblies; example tool library
- ✅ Cutting calculator (n = 1000·Vc/(π·D), F = n·z·fz, chip thinning, Kienzle power estimate, machine clamping, provenance)
- 🟡 No real machine models with manufacturer specifications are seeded (requires verified documentation)

## Phase 4 – CAM integration
- ✅ `CamEngine` interface; FreeCAD 26.3 adapter running in a separate interpreter with timeout, CPU and memory limits
- ✅ Operations: facing (MillFace, entry off-stock), adaptive clearing (helical entry + finishing profile), offset pocket, outside contour (multi-pass), drilling (G81/G82/G83/G73), helical hole/counterbore milling
- ✅ Face re-identification by geometric signature across OCCT builds (fails loudly if not found)
- ✅ Machine-neutral IR (`cnc-ir/1`), feeds converted from FreeCAD mm/s
- ✅ Background processing (RQ), idempotent job creation, cancel, retry with identical snapshot, per-operation errors surfaced
- ⛔ Finishing with a separate tool, rest machining, 3D surfacing, chamfering, thread milling, tapping, reaming, boring (FreeCAD supports several of these; not wired and not validated yet)
- ⛔ Turning, mill-turn and multi-axis (out of MVP scope)

## Phase 5 – Postprocessing
- ✅ Declarative, schema-validated, hashed, immutable postprocessor versions
- ✅ LinuxCNC 2.9 3-axis mill: syntax from the public LinuxCNC 2.9 documentation, status *tested* (fixture tests)
- 🟡 FANUC 0i-MF ISO and SINUMERIK 828D basic: *draft* — not reviewed against vendor manuals for a specific software version; releases are blocked
- ✅ Program header with traceability, operation map, line numbers, comment sanitising, decimal-point rules, modal suppression, arc splitting/linearisation, helical control, canned cycles or expansion
- ✅ G-code viewer with highlighting, search, annotations, operation navigation, copy, download, export

## Phase 6 – Validation
- ✅ Independent G-code interpreter (modal state, canned cycles, arcs) driven by the postprocessor declaration
- ✅ Geometry, toolpath, postprocessor, simulation and machine-status checks with severities and line references
- ✅ Z-map simulation (exact swept area for horizontal moves), collisions with material/shank/holder/keep-out boxes, gouge and remaining-material comparison against the part height field, plunge detection, time estimate with assumptions
- ✅ Release gates, engineering review with acknowledgements, four-eyes rule, immutable released revisions, prove-out records
- 🟡 Absolute travel-limit checks only when the work-offset position in machine coordinates is entered
- ⛔ Full machine-kinematic collision simulation (spindle head, table, enclosure) — needs machine 3D models

## Phase 7 – Production hardening
- ✅ Audit events for all state changes; structured JSON logs with request IDs
- ✅ Rate limiting (Redis or in-memory), upload limits, untrusted-file isolation, no macro execution
- ✅ Dockerfiles, compose stack, nginx config (not build-tested here: no Docker daemon)
- 🟡 Error tracking: structured logs only (no Sentry integration)
- 🟡 Object storage: local filesystem implementation behind an interface (S3 backend not implemented)
- 🟡 Backups: documented procedure (DEPLOYMENT.md), no automation
- ⛔ SSO, password reset, MFA

## Definition of Done (MVP) — status
1. Upload STEP ✅ 2. See geometry and dimensions ✅ 3. Select configured 3-axis machine with controller/post ✅ (example machine, LinuxCNC post)
4. Specify stock, tools, material, WCS ✅ 5. Real toolpaths via FreeCAD CAM ✅ 6. Program via selected post ✅ 7. Inspect G-code ✅
8. Run software validation ✅ 9. Review warnings ✅ 10. Download program + setup sheet + tool list + report ✅ 11. Retrieve job and exact configuration ✅

The only blocker for production use is outside software: a real machine configuration must be entered from the
owner's documentation and verified by physical prove-out, and FANUC/SINUMERIK postprocessors must be reviewed
against the vendor documentation for the installed software version (see DEPENDENCY_REPORT.md §6).
