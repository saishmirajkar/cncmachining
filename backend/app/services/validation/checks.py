"""Layered program validation: geometry, toolpath, postprocessor, simulation, machine-specific status.

Every report states explicitly which verification levels were and were not achieved. A passing report means
"no software-level check failed", never "safe to run".
"""

from __future__ import annotations

import numpy as np

from app.models.base import VERIFICATION_RANK, VerificationStatus
from app.services.post.schema import PostConfig
from app.services.validation.gcode import Finding, parse_program
from app.services.validation.zmap import LIMITATIONS, ToolShape, make_grid, rasterize_top, simulate

ENGINE = "cnc-validator/1 (z-map)"


def _tool_shapes(snapshot: dict) -> dict[int, ToolShape]:
    out = {}
    for t in snapshot["tools"]:
        a = t.get("assembly") or {}
        out[t["tool_number"]] = ToolShape(
            number=t["tool_number"], kind=t["tool_type"], diameter=t["diameter_mm"],
            corner_radius=t.get("corner_radius_mm") or 0.0, tip_angle=t.get("tip_angle_deg"),
            flute_length=t["cutting_length_mm"], stick_out=a.get("stick_out_mm"),
            holder_diameter=a.get("holder_diameter_mm"), holder_length=a.get("holder_length_mm"),
            shank_diameter=t.get("shank_diameter_mm"))
    return out


def validate(text: str, cfg: PostConfig, snapshot: dict, part_mesh_wcs: dict | None, post_warnings: list[dict],
             op_line_starts: list[int], max_cells: int = 600_000) -> dict:
    mc = snapshot["machine_configuration"]
    limits = mc["effective_limits"]
    mb = mc.get("machine_blocks") or {}
    setup = snapshot["setup"]
    stock_min = np.array(snapshot["stock_wcs"]["min"])
    stock_max = np.array(snapshot["stock_wcs"]["max"])
    findings: list[Finding] = []

    # ------------------------------------------------------------------ A. geometry
    g = snapshot["geometry"]
    if g.get("representation") != "brep":
        findings.append(Finding("geometry", "GEOM_NOT_BREP", "error", "Programs require a B-rep (STEP/IGES) model"))
    if not g.get("units_known"):
        findings.append(Finding("geometry", "GEOM_UNITS", "error", "Model units are not declared or confirmed"))
    for w in g.get("analysis_warnings", []):
        if w["code"] in ("GEOM_INVALID", "GEOM_NO_SOLID", "GEOM_OPEN_EDGES"):
            findings.append(Finding("geometry", w["code"], "error", w["message"]))
    for msg in snapshot.get("part_in_stock_issues", []):
        findings.append(Finding("geometry", "GEOM_PART_OUTSIDE_STOCK", "error", msg))
    size = stock_max - stock_min
    for k, ax in enumerate("xyz"):
        travel = limits.get(f"travel_{ax}_mm")
        if travel is None:
            findings.append(Finding("geometry", "MACHINE_TRAVEL_UNKNOWN", "warning",
                                    f"{ax.upper()} travel of the machine is not configured"))
        elif size[k] > travel:
            findings.append(Finding("geometry", "GEOM_STOCK_EXCEEDS_ENVELOPE", "error",
                                    f"Stock {ax.upper()} {size[k]:.1f} mm exceeds machine travel {travel:.1f} mm"))

    # ------------------------------------------------------------------ C. postprocessor (parse)
    pr = parse_program(text, cfg, mb, op_line_starts)
    findings += pr.findings
    fl = pr.flags
    if cfg.program.start_delimiter and not fl["start_delim"]:
        findings.append(Finding("postprocessor", "PP_MISSING_START", "error", "Program start delimiter missing"))
    if cfg.program.end_delimiter and not fl["end_delim"]:
        findings.append(Finding("postprocessor", "PP_MISSING_END_DELIM", "error", "Program end delimiter missing"))
    if cfg.program.number_template and fl["program_number"] is None:
        findings.append(Finding("postprocessor", "PP_MISSING_PROGRAM_NUMBER", "error", "Program number missing"))
    if not fl["ended"]:
        findings.append(Finding("postprocessor", "PP_MISSING_PROGRAM_END", "error",
                                f"Program end ({cfg.program.end_word}) missing"))
    if fl["after_end"]:
        findings.append(Finding("postprocessor", "PP_BLOCKS_AFTER_END", "warning", "Blocks after program end",
                                fl["after_end"]))
    for key, code, msg in (("motion_before_units", "PP_UNITS_NOT_SET", "Motion before the unit mode is set"),
                           ("motion_before_wcs", "PP_WCS_NOT_SET", "Motion before a work offset is selected"),
                           ("cut_spindle_off", "PP_CUT_SPINDLE_OFF", "Feed move with the spindle stopped"),
                           ("cut_no_feed", "PP_CUT_NO_FEED", "Feed move without a feed rate"),
                           ("cut_no_tool", "PP_CUT_NO_TOOL", "Feed move before any tool change"),
                           ("z_without_length_comp", "PP_NO_LENGTH_COMP", "Z motion without tool length compensation")):
        if fl[key]:
            findings.append(Finding("postprocessor", code, "error", msg, fl[key]))
    known_tools = {t["tool_number"]: t for t in snapshot["tools"]}
    for line, tn in pr.tool_changes:
        if tn not in known_tools:
            findings.append(Finding("postprocessor", "PP_UNKNOWN_TOOL", "error",
                                    f"T{tn} is not in the setup's tool list", [line]))
    smax, smin_rpm, fmax = limits.get("spindle_max_rpm"), limits.get("spindle_min_rpm"), limits.get("max_feed_mm_min")
    if smax and pr.max_rpm > smax:
        findings.append(Finding("postprocessor", "PP_RPM_LIMIT", "error",
                                f"Spindle {pr.max_rpm:.0f} rpm exceeds machine maximum {smax:.0f}"))
    if fmax and pr.max_feed > fmax:
        findings.append(Finding("postprocessor", "PP_FEED_LIMIT", "error",
                                f"Feed {pr.max_feed:.0f} mm/min exceeds machine maximum {fmax:.0f}"))
    for w in post_warnings:
        findings.append(Finding("postprocessor", w["code"], "warning", w["message"], [], w.get("detail", {})))

    # ------------------------------------------------------------------ B. toolpath rules
    tools = _tool_shapes(snapshot)
    clearance = setup["clearance_z_wcs"]
    feeds = [s for s in pr.segments if s.kind == "feed"]
    if feeds:
        pts = np.array([s.end for s in feeds] + [s.start for s in feeds])
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        rmax = max((tools[s.tool].diameter / 2 for s in feeds if s.tool in tools), default=0)
        # entry/exit moves (e.g. facing over-travel) may legitimately run up to one tool diameter past the stock
        out_lo = stock_min - (2 * rmax + 3.0)
        out_hi = stock_max + np.array([2 * rmax + 3.0, 2 * rmax + 3.0, clearance - stock_max[2] + 1.0])
        bad = [s.line for s in feeds if np.any(np.array(s.end) < out_lo) or np.any(np.array(s.end) > out_hi)]
        if bad:
            findings.append(Finding("toolpath", "TP_CUT_OUTSIDE_STOCK", "warning",
                                    "Cutting moves extend beyond the stock plus tool radius", sorted(set(bad))))
        wop = setup.get("work_offset_machine_position")
        axes = {a["name"]: a for a in snapshot["machine_configuration"]["machine_model"].get("axes", [])}
        allp = np.array([s.end for s in pr.segments] + [s.start for s in pr.segments])
        alo, ahi = allp.min(axis=0), allp.max(axis=0)
        if wop and all(k in axes and "min" in axes[k] and "max" in axes[k] for k in "XYZ"):
            for k, ax in enumerate("XYZ"):
                lo_m, hi_m = alo[k] + wop[ax.lower()], ahi[k] + wop[ax.lower()]
                if lo_m < axes[ax]["min"] - 1e-6 or hi_m > axes[ax]["max"] + 1e-6:
                    findings.append(Finding("toolpath", "TP_TRAVEL_LIMIT", "error",
                                            f"{ax} travel {lo_m:.2f}..{hi_m:.2f} outside machine limits "
                                            f"{axes[ax]['min']}..{axes[ax]['max']} (tool-tip position; tool length "
                                            "offsets not included)"))
        else:
            span = ahi - alo
            for k, ax in enumerate("xyz"):
                tr = limits.get(f"travel_{ax}_mm")
                if tr and span[k] > tr:
                    findings.append(Finding("toolpath", "TP_TRAVEL_SPAN", "error",
                                            f"{ax.upper()} motion span {span[k]:.1f} mm exceeds travel {tr:.1f} mm"))
            findings.append(Finding("toolpath", "TP_TRAVEL_NOT_CHECKED", "warning",
                                    "Absolute travel limits not checked: work-offset position in machine coordinates "
                                    "or axis limits are not configured"))
    for op in snapshot["operations"]:
        t = known_tools.get(op["tool_number"])
        if not t or op.get("stepdown_mm") is None:
            continue
        if op["stepdown_mm"] > t["cutting_length_mm"] + 1e-6:
            findings.append(Finding("toolpath", "TP_STEPDOWN_EXCEEDS_FLUTE", "error",
                                    f"{op['name']}: stepdown {op['stepdown_mm']} mm exceeds flute length "
                                    f"{t['cutting_length_mm']} mm"))
        lim = setup.get("max_stepdown_mm")
        if lim and op["stepdown_mm"] > lim + 1e-6:
            findings.append(Finding("toolpath", "TP_STEPDOWN_LIMIT", "error",
                                    f"{op['name']}: stepdown {op['stepdown_mm']} mm exceeds the setup maximum {lim} mm"))
    for t in snapshot["tools"]:
        if not t.get("assembly"):
            findings.append(Finding("toolpath", "TP_NO_HOLDER_DATA", "warning",
                                    f"T{t['tool_number']} has no holder/stick-out data: holder collisions not checked"))
    for d in snapshot.get("plan_unresolved", []):
        findings.append(Finding("toolpath", "TP_UNRESOLVED_PLAN", "warning", d["message"], [], {"code": d["code"]}))

    # ------------------------------------------------------------------ D. simulation
    sim_stats: dict = {}
    heightmap = None
    part_top = None
    max_r = max((t.diameter / 2 for t in tools.values()), default=5.0)
    grid = make_grid(stock_min, stock_max, max(max_r, max((t.holder_diameter or 0) / 2 for t in tools.values())),
                     max_cells)
    if part_mesh_wcs:
        pos = np.asarray(part_mesh_wcs["positions"], float).reshape(-1, 3)
        idx = np.asarray(part_mesh_wcs["indices"], int).reshape(-1, 3)
        part_top = rasterize_top(pos, idx, grid)
    sim = simulate(pr.segments, tools, stock_min, stock_max, part_top, grid, setup.get("tolerance_mm") or 0.05,
                   snapshot.get("keep_out_boxes", []), snapshot.get("allowed_breakthrough_mm", 0.0),
                   limits.get("rapid_rate_mm_min"), mc.get("tool_change_time_s"), len(pr.tool_changes), pr.dwell_s)
    findings += sim.findings
    sim_stats, heightmap = sim.stats, sim.heightmap

    # ------------------------------------------------------------------ E. machine-specific status
    ppv = snapshot["postprocessor"]
    pp_rank = VERIFICATION_RANK[VerificationStatus(ppv["status"])]
    if pp_rank < VERIFICATION_RANK[VerificationStatus.tested]:
        findings.append(Finding("machine", "MACHINE_POST_NOT_TESTED", "error",
                                f"Postprocessor {ppv['key']} {ppv['version']} is '{ppv['status']}': it must be at least "
                                "'tested' before programs can be released"))
    if mc["verification_status"] != VerificationStatus.machine_verified.value:
        findings.append(Finding("machine", "MACHINE_CONFIG_NOT_VERIFIED", "warning",
                                f"Machine configuration is '{mc['verification_status']}', not machine-verified: "
                                "first-article prove-out on the machine is required"))
    if "unspecified" in mc["controller"]["version"].lower():
        findings.append(Finding("machine", "MACHINE_CONTROLLER_VERSION_UNKNOWN", "error",
                                "Controller software version is not recorded for this machine configuration"))

    out = [f.as_dict() for f in findings]
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    blocking_layers = {"geometry", "toolpath", "postprocessor", "simulation"}
    sw_errors = sum(1 for f in findings if f.severity == "error" and f.layer in blocking_layers)
    status = "failed" if errors else ("passed_with_warnings" if warnings else "passed")
    levels = {
        "syntactic_validation": "passed" if not any(f.layer == "postprocessor" and f.severity == "error" for f in findings) else "failed",
        "geometry_and_toolpath_checks": "passed" if not any(f.layer in ("geometry", "toolpath") and f.severity == "error" for f in findings) else "failed",
        "controller_compatibility": f"postprocessor status '{ppv['status']}' (software-level declaration check only)",
        "machine_specific_verification": mc["verification_status"],
        "simulation": ("passed" if not any(f.layer == "simulation" and f.severity == "error" for f in findings)
                       else "failed") + " (Z-map, see limitations)",
        "physical_prove_out": "not performed",
    }
    return {
        "status": status, "checks": out,
        "summary": {"errors": errors, "warnings": warnings, "software_errors": sw_errors,
                    "segments": len(pr.segments), "tool_changes": len(pr.tool_changes),
                    "estimated_time": sim_stats.get("time"), "max_rpm": pr.max_rpm, "max_feed": pr.max_feed},
        "verification_levels": levels,
        "engine_versions": {"validator": ENGINE},
        "simulation": {"engine": ENGINE, "resolution_mm": grid.res, "stats": sim_stats, "limitations": LIMITATIONS},
        "heightmap": heightmap,
        "backplot": _backplot(pr.segments),
    }


def _backplot(segments) -> dict:
    """Compact polyline data for the viewer: per segment start/end with kind and source line."""
    pts, kinds, lines, tools, ops = [], [], [], [], []
    for s in segments:
        pts += [round(v, 3) for v in (*s.start, *s.end)]
        kinds.append(0 if s.kind == "rapid" else 1)
        lines.append(s.line)
        tools.append(s.tool or 0)
        ops.append(s.op_index)
    return {"positions": pts, "kinds": kinds, "lines": lines, "tools": tools, "ops": ops}
