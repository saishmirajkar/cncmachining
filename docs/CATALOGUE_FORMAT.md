# Catalogue import/export format (`cnc-catalogue/1`)

`GET /api/admin/catalogue/export` (platform admin) returns the global catalogue:

```json
{
  "format": "cnc-catalogue/1",
  "exported_at": "2026-10-22T12:00:00+00:00",
  "machine_models": [{ "manufacturer": {"name": "…"}, "model": "…", "category": "vmc", "axis_count": 3,
                       "axes": [{"name": "X", "type": "linear", "direction": "…", "min": 0, "max": 500}],
                       "travel_x_mm": 500, "spindle_max_rpm": 8000, "tool_changer": {"capacity": 20},
                       "source": "…", "verification_status": "draft", "is_example": false, "…": "…"}],
  "controllers": [{"manufacturer": "FANUC", "name": "Series 0i-MF Plus", "family": "FANUC 0i",
                   "dialect_family": "fanuc-iso", "versions": ["…"]}],
  "postprocessor_versions": [{"key": "…", "version": "…", "status": "…", "config": {}, "verifications": []}],
  "machine_configurations": [{"name": "…", "machine_model": {}, "controller": {}, "postprocessor": {},
                              "machine_blocks": {}, "verification_status": "…", "source": "…"}]
}
```

`POST /api/admin/catalogue/import` accepts:

```json
{
  "format": "cnc-catalogue/1",
  "machine_models": [{"manufacturer_name": "…", "model": "…", "category": "vmc", "axis_count": 3,
                      "travel_x_mm": 0, "source": "OEM brochure 2025, page 4 (to be verified)"}],
  "controllers": [{"manufacturer_name": "Siemens", "name": "SINUMERIK 828D", "version": "V4.95 SP2"}]
}
```

Rules:

* Imported records are always created as **draft**; existing records (same manufacturer + model, or same
  controller version) are skipped, never overwritten.
* `source` is mandatory for machine models. Record whether data came from documentation, the machine owner or a
  qualified engineer (`source_type` on machine configurations).
* Postprocessor versions and machine configurations are created through their own admin endpoints so that
  configuration validation and verification records apply.
* Do not import proprietary controller manuals or vendor postprocessors without confirming usage rights.
