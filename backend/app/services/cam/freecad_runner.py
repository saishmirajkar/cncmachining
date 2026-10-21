"""FreeCAD CAM runner. Executed by FreeCAD's own Python interpreter in a child process:

    <freecad-python> freecad_runner.py <spec.json> <result.json>

It must not import the platform package (different interpreter and environment). Input: a job specification
(normalized STEP path, 4x4 transform into the WCS, stock box, tools, operations with target face signatures).
Output: per-operation ``Path.Commands`` (name, parameters, annotations) or an explicit error.

FreeCAD internal units: lengths mm, feeds mm/s. Feeds are set from mm/min quantities and returned in mm/s; the
platform adapter converts back to mm/min.
"""

import json
import math
import os
import sys
import time
import traceback

FC_LIB = os.environ.get("FREECAD_LIB_DIR")
if FC_LIB and FC_LIB not in sys.path:
    sys.path.append(FC_LIB)

SURFACE_MAP = {"Plane": "plane", "Cylinder": "cylinder", "Cone": "cone", "Sphere": "sphere", "Toroid": "torus",
               "BSplineSurface": "bspline", "BezierSurface": "bezier", "SurfaceOfRevolution": "revolution",
               "SurfaceOfExtrusion": "extrusion", "OffsetSurface": "offset"}


def _surface_name(face):
    return SURFACE_MAP.get(type(face.Surface).__name__, "other")


def match_faces(shape, signatures, scale):
    """Return 1-based face indices matching each signature (centroid, area, surface type)."""
    tol_c = max(1e-4, 2e-5 * scale)
    out = []
    faces = shape.Faces
    props = [(i + 1, _surface_name(f), f.Area, f.CenterOfMass) for i, f in enumerate(faces)]
    for sig in signatures:
        best, best_d = None, None
        cx, cy, cz = sig["centroid"]
        for idx, st, area, c in props:
            if st != sig["surface"]:
                continue
            if abs(area - sig["area"]) > max(1e-6, 1e-4 * sig["area"]):
                continue
            d = math.sqrt((c.x - cx) ** 2 + (c.y - cy) ** 2 + (c.z - cz) ** 2)
            if d <= tol_c and (best_d is None or d < best_d):
                best, best_d = idx, d
        if best is None:
            raise LookupError("target face could not be re-identified in the CAM kernel: %s" % json.dumps(sig))
        out.append(best)
    return out


def _q(value, unit):
    import FreeCAD

    return FreeCAD.Units.Quantity("%r %s" % (float(value), unit))


def _clear_expr(obj, prop):
    try:
        obj.setExpression(prop, None)
    except Exception:
        pass


def _set(obj, prop, value):
    _clear_expr(obj, prop)
    setattr(obj, prop, value)


def build_and_run(spec):
    import FreeCAD
    import Part
    import Path

    import Path.Main.Job as PathJob
    import Path.Main.Stock as PathStock
    import Path.Op.Adaptive as PathAdaptive
    import Path.Op.Drilling as PathDrilling
    import Path.Op.Helix as PathHelix
    import Path.Op.MillFace as PathMillFace
    import Path.Op.PocketShape as PathPocketShape
    import Path.Op.Profile as PathProfile
    import Path.Tool.Controller as PathToolController
    from Path.Tool.toolbit import ToolBit

    try:
        import logging

        logging.getLogger("Path").setLevel(logging.ERROR)
    except Exception:
        pass

    doc = FreeCAD.newDocument("cnc_job")
    shape = Part.read(spec["step_path"])
    m = spec["transform"]
    rot = FreeCAD.Matrix(*[m[i][j] for i in range(4) for j in range(4)])
    # face matching happens in the oriented (rotated, untranslated) frame used by the analyzer
    rot_only = FreeCAD.Matrix(*[(m[i][j] if j < 3 else 0.0) if i < 3 else (1.0 if j == 3 else 0.0)
                                for i in range(4) for j in range(4)])
    oriented = shape.copy()
    oriented.transformShape(rot_only)
    bb = oriented.BoundBox
    scale = max(bb.XLength, bb.YLength, bb.ZLength)

    base = doc.addObject("Part::Feature", "Part")
    wcs_shape = shape.copy()
    wcs_shape.transformShape(rot)
    base.Shape = wcs_shape
    doc.recompute()

    job = PathJob.Create("Job", [base], None)
    job.GeometryTolerance = _q(spec.get("geometry_tolerance_mm", 0.01), "mm")
    model = job.Model.Group[0]
    smin, smax = spec["stock"]["min"], spec["stock"]["max"]
    # replace the default stock with the user's stock box (WCS)
    old_stock = job.Stock
    ext = FreeCAD.Vector(smax[0] - smin[0], smax[1] - smin[1], smax[2] - smin[2])
    new_stock = PathStock.CreateBox(job, ext, FreeCAD.Placement(FreeCAD.Vector(*smin), FreeCAD.Rotation()))
    job.Stock = new_stock
    if old_stock is not None and old_stock is not new_stock:
        doc.removeObject(old_stock.Name)
    doc.recompute()

    controllers = {}
    for t in spec["tools"]:
        shape_id = {"flat_endmill": "endmill.fcstd", "ball_endmill": "ballend.fcstd", "bull_endmill": "bullnose.fcstd",
                    "face_mill": "endmill.fcstd", "drill": "drill.fcstd", "spot_drill": "drill.fcstd",
                    "chamfer_mill": "chamfer.fcstd"}.get(t["tool_type"])
        if shape_id is None:
            raise ValueError("tool type %s is not supported by the FreeCAD adapter" % t["tool_type"])
        bit = ToolBit.from_shape_id(shape_id, t["name"])
        tobj = bit.attach_to_doc(doc=doc)
        tobj.Diameter = _q(t["diameter_mm"], "mm")
        if hasattr(tobj, "CuttingEdgeHeight"):
            tobj.CuttingEdgeHeight = _q(t["cutting_length_mm"], "mm")
        if hasattr(tobj, "Length"):
            tobj.Length = _q(t["overall_length_mm"], "mm")
        if hasattr(tobj, "Flutes"):
            tobj.Flutes = int(t["flute_count"])
        if hasattr(tobj, "TipAngle") and t.get("tip_angle_deg"):
            tobj.TipAngle = _q(t["tip_angle_deg"], "deg")
        if hasattr(tobj, "CornerRadius") and t.get("corner_radius_mm"):
            tobj.CornerRadius = _q(t["corner_radius_mm"], "mm")
        if hasattr(tobj, "ShankDiameter") and t.get("shank_diameter_mm"):
            tobj.ShankDiameter = _q(t["shank_diameter_mm"], "mm")
        controllers[t["tool_number"]] = tobj

    created = []
    tc_cache = {}
    for op in spec["operations"]:
        key = (op["tool_number"], op["spindle_rpm"], op["feed_mm_min"], op["plunge_feed_mm_min"])
        if key not in tc_cache:
            tc = PathToolController.Create("TC%d_%d" % (op["tool_number"], len(tc_cache)), controllers[op["tool_number"]],
                                           op["tool_number"])
            tc.SpindleSpeed = float(op["spindle_rpm"])
            tc.SpindleDir = "Forward"
            tc.HorizFeed = _q(op["feed_mm_min"], "mm/min")
            tc.VertFeed = _q(op["plunge_feed_mm_min"], "mm/min")
            job.Proxy.addToolController(tc)
            tc_cache[key] = tc
        tc = tc_cache[key]
        kind = op["op_type"]
        label = "op_%s" % op["id"].replace("-", "")[:20]
        if kind == "face":
            o = PathMillFace.Create(label, parentJob=job)
            o.BoundaryShape = "Stock"
            o.ClearEdges = True
            # extend the facing region beyond the stock so that entry happens off the material
            bo = op.get("parameters", {}).get("boundary_offset_mm")
            if bo:
                _set(o, "ExtraOffset", _q(bo, "mm"))
            o.StepOver = int(round(op["stepover_percent"]))
            o.CutMode = "Climb" if op["cut_direction"] == "climb" else "Conventional"
        elif kind == "pocket":
            o = PathPocketShape.Create(label, parentJob=job)
            o.StepOver = int(round(op["stepover_percent"]))
            o.CutMode = "Climb" if op["cut_direction"] == "climb" else "Conventional"
        elif kind == "adaptive":
            o = PathAdaptive.Create(label, parentJob=job)
            o.Side = "Inside"
            o.OperationType = "Clearing"
            o.StepOverPercent = int(round(op["stepover_percent"]))
            o.Tolerance = 0.1
            o.FinishingProfile = True
        elif kind == "contour":
            o = PathProfile.Create(label, parentJob=job)
            o.Side = "Outside"
            o.Direction = "CW" if op["cut_direction"] == "climb" else "CCW"
            o.UseComp = True
            passes = int(op.get("parameters", {}).get("passes", 1))
            o.NumPasses = max(1, passes)
            if passes > 1:
                o.Stepover = _q(op["parameters"].get("pass_stepover_mm", 0.5 * op["tool_diameter"]), "mm")
            o.processHoles = False
            o.processPerimeter = True
            o.processCircles = False
        elif kind == "drill":
            o = PathDrilling.Create(label, parentJob=job)
            p = op.get("parameters", {})
            cyc = p.get("cycle", "drill")
            o.PeckEnabled = cyc in ("peck", "chip_break")
            if hasattr(o, "ChipBreakEnabled"):
                o.ChipBreakEnabled = cyc == "chip_break"
            if o.PeckEnabled:
                _set(o, "PeckDepth", _q(p["peck_mm"], "mm"))
            o.DwellEnabled = cyc == "drill_dwell"
            if o.DwellEnabled:
                o.DwellTime = float(p.get("dwell_s", 0.5))
            o.ExtraOffset = "None"
            if hasattr(o, "KeepToolDown"):
                o.KeepToolDown = False
        elif kind == "helix":
            o = PathHelix.Create(label, parentJob=job)
            o.Side = "Inside"
            o.Direction = "CCW" if op["cut_direction"] == "climb" else "CW"
            o.StepOver = int(round(op["stepover_percent"]))
        else:
            raise ValueError("operation type %s is not supported by the FreeCAD adapter" % kind)
        o.ToolController = tc
        if op.get("face_signatures"):
            idx = match_faces(oriented, op["face_signatures"], scale)
            o.Base = [(model, ["Face%d" % i for i in idx])]
        elif kind in ("pocket", "adaptive", "drill", "helix"):
            raise ValueError("operation %s requires target faces" % op["name"])
        for prop, val in (("StartDepth", op["start_depth_mm"]), ("FinalDepth", op["final_depth_mm"]),
                          ("SafeHeight", op["safe_height_mm"]), ("ClearanceHeight", op["clearance_height_mm"])):
            if hasattr(o, prop):
                _set(o, prop, _q(val, "mm"))
        if hasattr(o, "StepDown") and op.get("stepdown_mm"):
            _set(o, "StepDown", _q(op["stepdown_mm"], "mm"))
        if kind == "drill" and hasattr(o, "RetractHeight"):
            _set(o, "RetractHeight", _q(op.get("parameters", {}).get("r_plane_mm", op["safe_height_mm"]), "mm"))
        if kind == "drill" and hasattr(o, "PeckRetract"):
            _set(o, "PeckRetract", _q(op.get("parameters", {}).get("r_plane_mm", op["safe_height_mm"]), "mm"))
        if hasattr(o, "FinishDepth") and op.get("finish_allowance_mm"):
            _set(o, "FinishDepth", _q(op["finish_allowance_mm"], "mm"))
        if kind == "adaptive" and op.get("finish_allowance_mm"):
            o.StockToLeave = _q(op["finish_allowance_mm"], "mm")
        o.CoolantMode = {"off": "None", "flood": "Flood", "mist": "Mist"}.get(op.get("coolant", "off"), "None")
        created.append((op, o))

    t0 = time.time()
    doc.recompute()
    results = []
    for op, o in created:
        r = {"id": op["id"], "status": "ok", "commands": [], "messages": []}
        try:
            state = o.State if hasattr(o, "State") else []
            if "Invalid" in state:
                r["status"] = "error"
                r["messages"].append("FreeCAD marked the operation invalid")
            cmds = o.Path.Commands
            for c in cmds:
                ann = {}
                try:
                    ann = dict(c.Annotations)
                except Exception:
                    pass
                r["commands"].append([c.Name, dict(c.Parameters), ann])
            if not cmds or all(c.Name.startswith("(") for c in cmds):
                r["status"] = "error"
                r["messages"].append("The CAM engine produced no motion for this operation")
            try:
                r["cycle_time"] = str(o.CycleTime)
            except Exception:
                pass
            r["effective"] = {
                "start_depth": float(o.StartDepth.Value) if hasattr(o, "StartDepth") else None,
                "final_depth": float(o.FinalDepth.Value) if hasattr(o, "FinalDepth") else None,
                "step_down": float(o.StepDown.Value) if hasattr(o, "StepDown") else None,
            }
        except Exception as e:  # pragma: no cover - engine failure path
            r["status"] = "error"
            r["messages"].append("%s: %s" % (type(e).__name__, e))
        results.append(r)
    version = ".".join(FreeCAD.Version()[:3])
    FreeCAD.closeDocument(doc.Name)
    return {"engine": "freecad-cam", "engine_version": version, "recompute_s": round(time.time() - t0, 3),
            "operations": results}


def main():
    spec_path, result_path = sys.argv[1], sys.argv[2]
    with open(spec_path) as f:
        spec = json.load(f)
    try:
        out = {"status": "ok", "result": build_and_run(spec)}
    except Exception as e:
        out = {"status": "error", "error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()[-4000:]}
    with open(result_path, "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()
