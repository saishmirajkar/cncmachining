# Database schema

SQLAlchemy 2 models in `backend/app/models/`, migrations in `backend/alembic/versions/`. UUID primary keys;
JSON columns use JSONB on PostgreSQL. All organisation-owned tables carry `organization_id` and every query is
filtered by the caller's membership.

## Entities

| Area | Table | Purpose / key columns |
| --- | --- | --- |
| Identity | `users` | email, bcrypt hash, `is_platform_admin` |
| | `organizations`, `organization_memberships` | role per user and organisation |
| | `audit_events` | action, entity type/id, details, user, org, IP, time |
| Projects | `projects` | name, part number, customer reference |
| Designs | `uploaded_designs` | immutable file: format, size, SHA-256, storage key, detected/confirmed units, revision chain (`parent_design_id`), normalized STEP key |
| | `geometry_analyses` | per design and orientation: engine/version, unit scale, summary, features, warnings, limitations, preview mesh key |
| Catalogue | `machine_manufacturers`, `machine_models` | physical machine: category, axes, travels, spindle, feeds, tool changer, options, source, verification status, `is_example` |
| | `controller_manufacturers`, `controller_models`, `controller_versions` | control family/model and exact software version |
| | `postprocessors`, `postprocessor_versions` | immutable configuration JSON + SHA-256, status, documentation, limitations, compatible controller models |
| | `postprocessor_verifications` | level, method (documentation_review / fixture_tests / machine_prove_out), result, evidence, reviewer |
| | `machine_configurations` | machine model + controller version + postprocessor version + options, overrides, owner-supplied machine blocks, supported operations, source type, verification; `organization_id` NULL = global |
| Materials & tools | `materials`, `material_grades` | ISO group, designation, standards/equivalents, kc1.1/mc, generic cutting data with source and confidence |
| | `tool_libraries`, `tools`, `tool_assemblies` | tool geometry, material, offsets, manufacturer cutting data (with source); holder diameter/length and stick-out |
| Setup | `stock_definitions`, `workholding_definitions` | stock block + part offset + material grade; vise/fixture parameters and keep-out boxes |
| | `machining_setups` | design revision, analysis, machine configuration, stock, workholding, tool library, orientation, WCS, datum, heights, tolerance, coolant, preferences, plan report, approval |
| | `machining_operations` | ordered operations with tool, strategy, feeds/speeds, depths, heights, parameters, **parameter provenance**, issues, unresolved decisions, `user_modified` |
| CAM | `cam_jobs` | immutable `input_snapshot` + SHA-256, idempotency key (unique per org), status, engine/version, progress, log, per-operation results, attempts |
| | `toolpath_artifacts` | IR (`cnc-ir/1`) storage key, SHA-256, statistics |
| Programs | `generated_programs` | CAM job, artifact, postprocessor version, machine configuration, released revision |
| | `program_revisions` | immutable content + SHA-256, revision number, parent, source (generated/manual_edit), operation map, post warnings, released flag |
| | `validation_reports` | checks, summary, verification levels, engine versions — one per validation run of a revision |
| | `simulation_results` | Z-map statistics, height map and backplot storage keys, limitations |
| | `engineering_reviews` | reviewer, decision, acknowledgements, comments, report reviewed |
| | `prove_out_records` | physical first-article result recorded by the shop |

## Required relationships (as implemented)

* One machine model → many machine configurations (different controllers/options); one controller model →
  many configurations and many compatible postprocessor versions.
* Each machine configuration references the postprocessor version it requires.
* A project contains many designs (revisions) and setups.
* A CAM job references the exact design revision (ID + SHA-256), geometry analysis, stock, tools, machine
  configuration, postprocessor version (ID + config SHA-256) and operations through its frozen snapshot.
* A generated program references its CAM job, toolpath artifact and postprocessor version; validation reports,
  simulations, reviews and prove-outs reference an exact program revision.

## Lifecycle values

`uploaded → geometry_analyzed → setup_incomplete → ready_for_cam → cam_processing → toolpaths_generated →
postprocessing → validation_required → (validation_failed | engineering_review_required) →
ready_for_controlled_export`, plus `cam_failed` and `cancelled`. Transitions are enforced in
`app/services/lifecycle.py`.
