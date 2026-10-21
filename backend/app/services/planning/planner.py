"""Rule-based machining planner for 3-axis milling from +Z.

The planner proposes an inspectable operation list. It never invents tools: only the user's tool library is
used. Where no suitable tool or rule exists, it records an *unresolved decision* instead of substituting an
operation. All Z values in the output are WCS.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon, box as sbox

from app.services.cutting.calculator import CuttingDataUnavailable, compute

DRILL_SIZE_TOL = 0.02  # mm: a drill matches a hole when |D_drill - D_hole| <= this


@dataclass
class PlanContext:
    features: list[dict]
    origin: np.ndarray  # oriented-frame point that is the WCS origin
    part_min_w: np.ndarray
    part_max_w: np.ndarray
    stock_min_w: np.ndarray
    stock_max_w: np.ndarray
    tools: list[dict]
    grade: dict | None
    machine: dict
    machine_operations: list[str]
    clearance_z: float
    safe_z: float
    coolant: str
    tolerance_mm: float
    max_stepdown_mm: float | None
    preference: str
    cut_direction: str
    finish_pass: bool
    workholding_info: dict
    finish_allowance_mm: float = 0.0


@dataclass
class Plan:
    operations: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)


def _wz(ctx: PlanContext, z: float) -> float:
    return float(z - ctx.origin[2])


def _wxy(ctx: PlanContext, p) -> tuple[float, float]:
    return float(p[0] - ctx.origin[0]), float(p[1] - ctx.origin[1])


def _engagement(ctx: PlanContext) -> dict:
    e = ((ctx.grade or {}).get("cutting_data") or {}).get("engagement")
    return e or {}


def _reach(t: dict) -> float:
    """Usable depth along a wall: flute length, further limited by the holder's stick-out if known."""
    r = t["cutting_length_mm"]
    if t.get("assembly"):
        r = min(r, t["assembly"]["stick_out_mm"] - 2.0)
    return r


def _endmills(ctx: PlanContext) -> list[dict]:
    return sorted([t for t in ctx.tools if t["active"] and t["tool_type"] in ("flat_endmill", "face_mill")],
                  key=lambda t: -t["diameter_mm"])


def _issue(plan: Plan, severity: str, code: str, message: str, **detail) -> None:
    plan.issues.append({"severity": severity, "code": code, "message": message, "detail": detail})


def _decision(plan: Plan, code: str, message: str, feature_ids: list[str] | None = None) -> dict:
    d = {"code": code, "message": message, "feature_ids": feature_ids or []}
    plan.unresolved.append(d)
    return d


def _params(ctx: PlanContext, plan: Plan, tool: dict, operation: str, ae: float | None, ap: float | None,
            op_name: str) -> dict | None:
    try:
        r = compute(operation=operation, tool=tool, grade=ctx.grade, machine=ctx.machine,
                    preference=ctx.preference, ae_mm=ae, ap_mm=ap)
    except CuttingDataUnavailable as e:
        _decision(plan, "CUTTING_DATA_MISSING", f"{op_name}: {e}")
        return None
    for i in r.issues:
        _issue(plan, i["severity"], i["code"], f"{op_name}: {i['message']}")
    return {"spindle_rpm": r.value("spindle_rpm"), "feed_mm_min": r.value("feed_mm_min"),
            "plunge_feed_mm_min": r.value("plunge_feed_mm_min") or r.value("feed_mm_min"),
            "parameter_sources": r.provenance() | {"assumptions": r.assumptions}}


def _new_op(ctx: PlanContext, op_type: str, name: str, tool: dict, **kw) -> dict:
    op = {
        "id": str(uuid.uuid4()), "op_type": op_type, "name": name, "enabled": True, "tool_id": tool["id"],
        "tool_number": tool["tool_number"], "cut_direction": ctx.cut_direction, "coolant": ctx.coolant,
        "clearance_height_mm": ctx.clearance_z, "safe_height_mm": ctx.safe_z, "tolerance_mm": ctx.tolerance_mm,
        "finish_allowance_mm": 0.0, "parameters": {}, "unresolved_decisions": [], "issues": [],
        "target_feature_ids": [], "face_signatures": [], "entry_strategy": "", "exit_strategy": "",
        "strategy": "",
    }
    op.update(kw)
    return op


def _check_op_supported(ctx: PlanContext, plan: Plan, op_type: str, what: str) -> bool:
    if op_type not in ctx.machine_operations:
        _decision(plan, "OPERATION_NOT_ENABLED",
                  f"{what}: operation '{op_type}' is not enabled for the selected machine configuration")
        return False
    return True


def plan_operations(ctx: PlanContext) -> Plan:
    plan = Plan()
    eng = _engagement(ctx)
    if not eng:
        _decision(plan, "ENGAGEMENT_DATA_MISSING",
                  "No engagement (stepdown/stepover) data for the selected material; enter stepdown and stepover "
                  "for each operation manually.")
    mills = _endmills(ctx)
    stock_top = float(ctx.stock_max_w[2])
    part_top = float(ctx.part_max_w[2])
    part_bottom = float(ctx.part_min_w[2])
    seq_face, seq_rough, seq_contour, seq_spot, seq_drill, seq_helix = [], [], [], [], [], []
    feats = {f["id"]: f for f in ctx.features}
    handled: set[str] = set()

    def cap(ap: float, tool: dict) -> float:
        v = min(ap, _reach(tool))
        if ctx.max_stepdown_mm:
            v = min(v, ctx.max_stepdown_mm)
        return round(max(v, 0.05), 3)

    # ------------------------------------------------------------------ facing
    top_feats = [f for f in ctx.features if f["type"] == "top_face"]
    if stock_top > part_top + 1e-4:
        if not mills:
            _decision(plan, "NO_FACING_TOOL", "Stock is above the part top but no end mill is available for facing")
        elif _check_op_supported(ctx, plan, "face", "Facing"):
            t = mills[0]
            depth = stock_top - part_top
            ap = cap(eng.get("slot_ap_over_d", 0.25) * t["diameter_mm"], t) if eng else None
            p = _params(ctx, plan, t, "milling", 0.7 * t["diameter_mm"], ap, "Facing")
            if p and ap:
                seq_face.append(_new_op(
                    ctx, "face", f"Face top to Z{part_top:.3f}", t, strategy="zig-zag over stock boundary",
                    start_depth_mm=stock_top, final_depth_mm=part_top, stepdown_mm=min(ap, depth),
                    stepover_percent=70.0, entry_strategy="enter from outside the stock",
                    # facing region extended past the stock so the first plunge is clear of material
                    parameters={"boundary_offset_mm": round(t["diameter_mm"] + 2.0, 3)},
                    target_feature_ids=[f["id"] for f in top_feats],
                    face_signatures=[r for f in top_feats for r in f["face_refs"]], **p))
                if not top_feats:
                    _issue(plan, "warning", "FACE_NO_TOP_FEATURE", "No planar top face detected; facing uses stock boundary")
    else:
        _issue(plan, "info", "NO_FACING", "Stock top equals part top: no facing operation planned")
    for f in top_feats:
        handled.add(f["id"])

    # ------------------------------------------------------------------ pockets, slots, steps
    for f in ctx.features:
        if f["type"] not in ("pocket", "slot", "step"):
            continue
        handled.add(f["id"])
        m = f["measurements"]
        if "+Z" not in f.get("access_directions", []):
            _decision(plan, "FEATURE_NOT_ACCESSIBLE", f"{f['id']} {f['type']} is not accessible from +Z", [f["id"]])
            continue
        floor_w = _wz(ctx, f["geometry"]["floor_z"])
        top_w = min(_wz(ctx, f["geometry"]["top_z"]), part_top)
        depth = top_w - floor_w
        max_d = m.get("max_tool_diameter") or 0
        radii = m.get("internal_corner_radii") or []
        min_r = min(radii) if radii else None
        cand = [t for t in mills if t["diameter_mm"] <= 0.98 * max_d and _reach(t) >= min(depth, t["cutting_length_mm"])]
        reach_ok = [t for t in cand if _reach(t) >= depth]
        if not cand:
            _decision(plan, "NO_TOOL_FOR_FEATURE",
                      f"{f['id']} {f['type']}: no end mill fits (max tool diameter {max_d:.2f} mm)", [f["id"]])
            continue
        pool = reach_ok or cand
        corner_ok = [t for t in pool if min_r is None or t["diameter_mm"] / 2 <= min_r + 1e-6]
        t = (corner_ok or pool)[0]
        notes = []
        if not reach_ok:
            _decision(plan, "TOOL_REACH", f"{f['id']}: depth {depth:.2f} mm exceeds the reach of every fitting tool "
                      f"(best {_reach(t):.1f} mm)", [f["id"]])
            continue
        if min_r is not None and t["diameter_mm"] / 2 > min_r + 1e-6:
            notes.append(f"Corner radius R{min_r:g} needs tool <= D{2 * min_r:g}; T{t['tool_number']} leaves "
                         f"R{t['diameter_mm'] / 2:g} corners")
            _decision(plan, "CORNER_RADIUS", f"{f['id']}: " + notes[-1], [f["id"]])
        if m.get("sharp_internal_corners"):
            notes.append(f"{m['sharp_internal_corners']} sharp internal corner(s) will keep R{t['diameter_mm'] / 2:g}")
            _issue(plan, "warning", "SHARP_CORNERS", f"{f['id']}: " + notes[-1], feature_id=f["id"])
        op_type = "adaptive" if f["type"] == "pocket" or ctx.preference != "conservative" else "pocket"
        if not _check_op_supported(ctx, plan, op_type, f"{f['id']} {f['type']}"):
            continue
        if eng:
            if op_type == "adaptive":
                stepover = float(eng.get("adaptive_stepover_pct", 10))
                ap = cap(eng.get("side_ap_over_d", 1.0) * t["diameter_mm"], t)
            else:
                stepover = float(eng.get("pocket_stepover_pct", 40))
                ap = cap(eng.get("slot_ap_over_d", 0.25) * t["diameter_mm"], t)
        else:
            stepover, ap = None, None
        p = _params(ctx, plan, t, "milling", (stepover or 10) / 100 * t["diameter_mm"], ap, f"{f['id']}")
        if p is None or ap is None:
            continue
        seq_rough.append(_new_op(
            ctx, op_type, f"{'Adaptive clear' if op_type == 'adaptive' else 'Pocket'} {f['type']} {f['id']} "
                          f"to Z{floor_w:.3f}", t,
            strategy="adaptive clearing with finishing profile" if op_type == "adaptive" else "offset pocket",
            entry_strategy="helical ramp" if op_type == "adaptive" else "plunge (centre-cutting tool required)",
            start_depth_mm=top_w, final_depth_mm=floor_w, stepdown_mm=min(ap, depth), stepover_percent=stepover,
            target_feature_ids=[f["id"]], face_signatures=f["face_refs"], issues=notes, **p))

    # ------------------------------------------------------------------ holes
    drills = [t for t in ctx.tools if t["active"] and t["tool_type"] == "drill"]
    spots = [t for t in ctx.tools if t["active"] and t["tool_type"] == "spot_drill"]
    drill_groups: dict[tuple, list[dict]] = {}
    helix_jobs = []
    for f in ctx.features:
        if f["type"] != "hole":
            continue
        handled.add(f["id"])
        m = f["measurements"]
        d = m["diameter"]
        if f["subtype"] == "internal_cavity" or "+Z" not in f.get("access_directions", []):
            _decision(plan, "FEATURE_NOT_ACCESSIBLE",
                      f"{f['id']} hole D{d:g} is not accessible from +Z in this setup", [f["id"]])
            continue
        for h in f.get("thread_hints", []):
            if h["kind"] == "tap drill":
                _decision(plan, "THREAD_UNCONFIRMED", f"{f['id']} D{d:g} matches the {h['thread']} tap-drill size: "
                          "tapping is not planned; confirm thread requirements from the drawing", [f["id"]])
        if ctx.tolerance_mm < 0.05:
            _decision(plan, "HOLE_TOLERANCE", f"{f['id']}: tolerance {ctx.tolerance_mm} mm is tighter than typical "
                      "drilling capability; reaming/boring is not planned", [f["id"]])
        entry_w = _wz(ctx, m["entry_z"])
        cyl_refs = [r for r in f["face_refs"] if r.get("role") == "wall" and abs(r.get("radius", -1) * 2 - d) < 1e-3]
        cb = f.get("counterbore")
        match = [t for t in drills if abs(t["diameter_mm"] - d) <= DRILL_SIZE_TOL]
        if match:
            t = match[0]
            tip = (t["diameter_mm"] / 2) / math.tan(math.radians(t["tip_angle_deg"] / 2))
            if f["subtype"] == "through":
                bottom_w = _wz(ctx, m["bottom_z"])
                final = bottom_w - tip - 1.0  # 1 mm breakthrough beyond full diameter
                cycle_note = f"through: tip length {tip:.2f} mm + 1.0 mm breakthrough"
            else:
                bottom = f.get("bottom") or {}
                if bottom.get("type") == "conical":
                    final = _wz(ctx, m["bottom_z"])
                    if abs(bottom.get("included_angle_deg", 0) - t["tip_angle_deg"]) > 1.0:
                        _issue(plan, "warning", "DRILL_POINT_MISMATCH",
                               f"{f['id']}: model drill point {bottom.get('included_angle_deg')} deg vs tool "
                               f"{t['tip_angle_deg']} deg; cylindrical depth kept, tip geometry differs")
                        final = entry_w - m["depth"] - tip
                else:
                    final = entry_w - m["depth"] - tip
                    _decision(plan, "FLAT_BOTTOM_HOLE", f"{f['id']}: flat-bottom blind hole; the drill leaves a "
                              f"{t['tip_angle_deg']:g} deg cone. Flat bottoming is not planned.", [f["id"]])
                cycle_note = "blind"
            ld = (entry_w - final) / d
            cycle = "peck" if ld > 3.0 else "drill"
            depth_total = entry_w - final
            if depth_total > _reach(t):
                _decision(plan, "TOOL_REACH", f"{f['id']}: drill depth {depth_total:.1f} mm exceeds T{t['tool_number']} "
                          f"reach {_reach(t):.1f} mm", [f["id"]])
                continue
            if cb:
                pass  # counterbore handled below with helix milling
            key = (t["id"], round(entry_w, 4), round(final, 4), cycle)
            drill_groups.setdefault(key, []).append({"feature": f, "refs": cyl_refs, "note": cycle_note})
        else:
            # circular interpolation with an end mill
            fit = [t for t in mills if t["diameter_mm"] <= 0.8 * d and t["diameter_mm"] >= 0.3 * d]
            if fit and "helix" in ctx.machine_operations:
                t = fit[0]
                bottom_w = _wz(ctx, m["bottom_z"]) if f["subtype"] == "through" else entry_w - m["depth"]
                if f["subtype"] == "through":
                    bottom_w -= 0.5
                if entry_w - bottom_w > _reach(t):
                    _decision(plan, "TOOL_REACH", f"{f['id']}: helix depth exceeds T{t['tool_number']} reach", [f["id"]])
                    continue
                helix_jobs.append((f, t, entry_w, bottom_w, cyl_refs, f"D{d:g} circular milling"))
                _issue(plan, "info", "HOLE_BY_HELIX", f"{f['id']}: no D{d:g} drill in the library; planned as "
                       f"helical milling with T{t['tool_number']} D{t['diameter_mm']:g}")
            else:
                _decision(plan, "NO_TOOL_FOR_HOLE", f"{f['id']}: no D{d:g} drill and no end mill between "
                          f"D{0.3 * d:.1f} and D{0.8 * d:.1f} for circular milling", [f["id"]])
        if cb:
            cbd = cb["diameter"]
            fit = [t for t in mills if t["diameter_mm"] <= 0.8 * cbd]
            cb_refs = [r for r in f["face_refs"] if r.get("role") == "wall" and abs(r.get("radius", -1) * 2 - cbd) < 1e-3]
            if fit and "helix" in ctx.machine_operations:
                helix_jobs.append((f, fit[0], entry_w, entry_w - cb["depth"], cb_refs, f"counterbore D{cbd:g}"))
            else:
                _decision(plan, "NO_TOOL_FOR_COUNTERBORE", f"{f['id']}: no end mill for counterbore D{cbd:g}", [f["id"]])
        if f.get("chamfer"):
            _decision(plan, "CHAMFER_NOT_PLANNED", f"{f['id']}: entry chamfer/countersink is not machined in this "
                      "release", [f["id"]])

    for (tool_id, entry_w, final, cycle), members in drill_groups.items():
        t = next(x for x in drills if x["id"] == tool_id)
        if not _check_op_supported(ctx, plan, "drill", "Drilling"):
            break
        ids = [x["feature"]["id"] for x in members]
        refs = [r for x in members for r in x["refs"]]
        p = _params(ctx, plan, t, "drilling", None, None, f"Drill {','.join(ids)}")
        if p is None:
            continue
        # R plane: peck cycles retract to 2 mm above the hole entry (FreeCAD PeckRetract); single-pass cycles use
        # the operation safe height as R (FreeCAD Drilling behaviour), recorded here so the plan matches the output
        r_peck = round(min(entry_w + 2.0, ctx.safe_z) if ctx.safe_z > entry_w else entry_w + 1.0, 3)
        r_plane = r_peck if cycle == "peck" else ctx.safe_z
        params = {"cycle": cycle, "r_plane_mm": r_plane}
        if cycle == "peck":
            params["peck_mm"] = round(t["diameter_mm"], 3)
        if spots:
            s = spots[0]
            sd = min(members[0]["feature"]["measurements"]["diameter"], s["diameter_mm"]) * 0.9
            sdepth = (sd / 2) / math.tan(math.radians(s["tip_angle_deg"] / 2))
            ps = _params(ctx, plan, s, "drilling", None, None, "Spot drill")
            if ps and "drill" in ctx.machine_operations:
                seq_spot.append(_new_op(
                    ctx, "drill", f"Spot drill {','.join(ids)} (D{sd:.2f} spot)", s, strategy="spot (G81)",
                    start_depth_mm=entry_w, final_depth_mm=round(entry_w - sdepth, 3), stepdown_mm=None,
                    stepover_percent=None, target_feature_ids=ids, face_signatures=refs,
                    parameters={"cycle": "drill", "r_plane_mm": ctx.safe_z}, **ps))
        seq_drill.append(_new_op(
            ctx, "drill", f"Drill D{t['diameter_mm']:g} {','.join(ids)} to Z{final:.3f}", t,
            strategy=f"{'peck (G83)' if cycle == 'peck' else 'drill (G81)'}; {members[0]['note']}",
            start_depth_mm=entry_w, final_depth_mm=round(final, 3), stepdown_mm=None, stepover_percent=None,
            target_feature_ids=ids, face_signatures=refs, parameters=params, **p))

    for f, t, top_w, bottom_w, refs, what in helix_jobs:
        if not refs:
            _decision(plan, "HELIX_FACES", f"{f['id']}: cylindrical faces for {what} not identified", [f["id"]])
            continue
        stepover = float(eng.get("pocket_stepover_pct", 40)) if eng else 40.0
        p = _params(ctx, plan, t, "milling", stepover / 100 * t["diameter_mm"], None, f"{f['id']} {what}")
        if p is None:
            continue
        seq_helix.append(_new_op(
            ctx, "helix", f"Helix mill {what} {f['id']}", t, strategy="helical circular interpolation",
            entry_strategy="helix", start_depth_mm=round(top_w, 3), final_depth_mm=round(bottom_w, 3),
            stepdown_mm=None, stepover_percent=stepover, target_feature_ids=[f["id"]], face_signatures=refs, **p))

    # ------------------------------------------------------------------ external contour
    contour = next((f for f in ctx.features if f["type"] == "external_contour"), None)
    if contour:
        handled.add(contour["id"])
        outline = Polygon([_wxy(ctx, p) for p in contour["geometry"]["outline_xy"]])
        stock_rect = sbox(ctx.stock_min_w[0], ctx.stock_min_w[1], ctx.stock_max_w[0], ctx.stock_max_w[1])
        excess = stock_rect.difference(outline.buffer(1e-3))
        if excess.area > 1e-3 * stock_rect.area:
            gap = outline.hausdorff_distance(stock_rect)  # widest band of stock outside the part outline
            jaw_top = ctx.workholding_info.get("jaw_top_z")
            final = part_bottom
            if jaw_top is not None and final < jaw_top + 1.0:
                final = jaw_top + 1.0
                _decision(plan, "CONTOUR_LIMITED_BY_WORKHOLDING",
                          f"External contour limited to Z{final:.3f} (vise jaw top {jaw_top:.3f} + 1 mm); the part "
                          f"bottom at Z{part_bottom:.3f} needs a second setup or taller stock", [contour["id"]])
            depth = (stock_top if stock_top <= part_top + 1e-4 else part_top) - final
            cands = [t for t in mills if _reach(t) >= depth]
            if not cands:
                _decision(plan, "TOOL_REACH", f"External contour depth {depth:.2f} mm exceeds every end mill's reach",
                          [contour["id"]])
            elif depth > 0 and _check_op_supported(ctx, plan, "contour", "External contour"):
                t = cands[0]
                # pass 1 removes a band D wide next to the outline; each further pass adds the 0.5*D stepover
                d_t = t["diameter_mm"]
                passes = 1 + max(0, math.ceil((gap - d_t) / (0.5 * d_t) - 1e-9))
                ap = cap(eng.get("slot_ap_over_d", 0.25) * t["diameter_mm"], t) if eng else None
                p = _params(ctx, plan, t, "milling", 0.5 * t["diameter_mm"], ap, "External contour")
                if p and ap:
                    start = part_top
                    seq_contour.append(_new_op(
                        ctx, "contour", f"Contour outside to Z{final:.3f} ({passes} pass{'es' if passes > 1 else ''})",
                        t, strategy="outside profile, tool-centre compensated by CAM",
                        entry_strategy="plunge outside the part", start_depth_mm=start, final_depth_mm=final,
                        stepdown_mm=min(ap, depth), stepover_percent=None, target_feature_ids=[contour["id"]],
                        parameters={"passes": passes, "pass_stepover_mm": round(0.5 * t["diameter_mm"], 3),
                                    "max_stock_gap_mm": round(gap, 3)}, **p))
        else:
            _issue(plan, "info", "NO_CONTOUR", "Stock matches the part outline: no external contour planned")

    if part_bottom > float(ctx.stock_min_w[2]) + 1e-3:
        _decision(plan, "BOTTOM_STOCK_REMAINS",
                  f"{part_bottom - float(ctx.stock_min_w[2]):.2f} mm of stock remains below the part bottom "
                  f"(Z{part_bottom:.3f}); a second setup (flip / face-off) or a part-off operation is required")

    # ------------------------------------------------------------------ everything else
    for f in ctx.features:
        if f["id"] in handled or f["type"] in ("pattern",):
            continue
        if f["type"] in ("chamfer_group", "fillet_group", "freeform_surfaces", "downward_faces", "inaccessible_floor"):
            _decision(plan, "FEATURE_NOT_MACHINED", f"{f['id']} {f['type']}: not machined in this release/setup "
                      f"({'; '.join(f.get('limitations', [])) or 'no supported strategy'})", [f["id"]])
        elif f["type"] == "cylindrical_boss":
            _issue(plan, "info", "BOSS_BY_CONTOUR", f"{f['id']}: external diameters are machined only as part of "
                   "the external contour (2.5D)")
        elif f["type"] == "plateau":
            _issue(plan, "info", "PLATEAU", f"{f['id']}: plateau at Z{_wz(ctx, f['geometry']['floor_z']):.3f} is "
                   "shaped by the external contour depth levels only")

    seq = seq_face + seq_rough + seq_contour + seq_spot + seq_drill + seq_helix
    for k, op in enumerate(seq, 1):
        op["sequence"] = k
        op["parameters"]["face_signatures"] = op.pop("face_signatures", [])
        op["tool_diameter"] = next(t["diameter_mm"] for t in ctx.tools if t["id"] == op["tool_id"])
    plan.operations = seq
    if not seq:
        _issue(plan, "error", "PLAN_EMPTY", "No operation could be planned for this setup")
    return plan
