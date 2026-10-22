# Postprocessors

A postprocessor version is a YAML document validated by `app/services/post/schema.py` (`PostConfig`), stored
immutably in `postprocessor_versions` with its SHA-256. The engine (`app/services/post/engine.py`) turns the
machine-neutral IR into program text using only words the configuration declares.

## Bundled configurations

| Key | Version | Status | Basis |
| --- | --- | --- | --- |
| `linuxcnc-mill-3axis` | 1.0.0 | **tested** (software) | Public LinuxCNC 2.9 G-code/M-code reference; output matches the reviewed expected fixture |
| `fanuc-0i-mf-iso` | 0.1.0-draft | draft | Common FANUC ISO conventions; **not** reviewed against a specific FANUC manual/edition |
| `sinumerik-828d-basic` | 0.1.0-draft | draft | Basic Siemens-mode words; native cycles not implemented (drilling expanded) |

Draft versions can generate programs for inspection, but the release gate `POSTPROCESSOR_TESTED` blocks
their release.

## Safety rules enforced by the engine

* Every G/M word in templates, safety blocks, start/end skeletons and machine-supplied blocks must be declared;
  otherwise the configuration (or the program) is rejected.
* Machine-specific behaviour is **not** in the postprocessor. `{machine:<key>}` placeholders (e.g.
  `program_start`, `pre_tool_change`, `end_parking`) are filled from the machine configuration or omitted with
  a `PP_MACHINE_BLOCK_MISSING` warning. Coolant and tool-change codes from the machine configuration take
  precedence; documented controller defaults produce `PP_COOLANT_UNCONFIRMED` / `PP_TOOL_CHANGE_UNCONFIRMED`.
* Rapids changing XY and Z are split (Z up first / Z down last).
* Positioning after a tool change: XY at the current height, then Z with length compensation.
* Arcs always state their end point; full circles and large sweeps are split when the control requires it;
  helical arcs are linearised when `helical_allowed` is false; arcs can be linearised entirely.
* Canned cycles are emitted only if declared; otherwise they are expanded with identical retract semantics
  (verified by comparing parsed motion of LinuxCNC vs. SINUMERIK output in the test suite).
* Spindle speed above the machine maximum, unsupported work offsets, missing program numbers and unconfigured
  coolant modes are errors.
* Axis words get a trailing decimal point when the control requires it (FANUC without calculator-type input
  reads `X10` as 10 µm).
* Cutter radius compensation is not used; programs are tool-centre paths, `G40` is asserted.

## Verification workflow

1. **draft** – created by a platform admin (`POST /api/admin/postprocessor-versions`).
2. **documented** – `documentation_review` verification recorded with the documents used.
3. **tested** – `python -m app.seed.verify_posts` renders `fixtures/reference_ir.json` and compares it byte-for-byte
   with `fixtures/expected/<key>@<version><ext>`. A documented version whose output matches is promoted;
   drafts are never promoted by this tool. This is software-level evidence only.
4. **machine_verified** – only with method `machine_prove_out` against a specific machine configuration.
5. **deprecated** – no longer offered for new configurations.

## Adding a controller

1. Copy a YAML in `backend/app/services/post/configs/`, set a new `key`/`version`, declare exactly the words the
   control supports, and fill `documentation_refs` and `known_limitations`.
2. `python -m app.seed.verify_posts --regenerate`, then **review the generated expected file line by line**
   against the controller documentation before committing it.
3. Add tests for controller-specific conventions in `tests/test_postprocessor.py`.
4. Register the version through the admin API (or seed), record verifications, and attach it to machine
   configurations whose controller model it declares compatible.
