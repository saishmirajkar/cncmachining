"""Cutting-parameter calculation with explicit provenance.

Formulas (metric):
  spindle speed      n  = 1000 * Vc / (pi * D)        [rpm]     Vc in m/min, D in mm
  milling feed       vf = n * z * fz                   [mm/min]  z teeth, fz mm/tooth
  drilling feed      vf = n * f                        [mm/min]  f mm/rev
  chip thinning (ae < D/2): fz_adj = fz * D / (2 * sqrt(ae * (D - ae)))  (radial chip thinning)
  power (approx.)    Pc = ap * ae * vf * kc / 60e6     [kW]      kc = kc1.1 * hm^-mc (Kienzle)

Source precedence for Vc / fz:  user override > tool manufacturer data on the tool > generic material table.
Each returned value carries ``source`` and ``detail`` so the UI can show *why* a number was chosen, and a
generic-table value is always labelled as an estimate, never as a manufacturer recommendation.
Machine limits are enforced last; any clamp is recorded as an assumption.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Param:
    value: float
    unit: str
    source: str  # user | tool_manufacturer | material_table | calculated | machine_limit
    detail: str = ""

    def as_dict(self) -> dict:
        return {"value": round(self.value, 4), "unit": self.unit, "source": self.source, "detail": self.detail}


@dataclass
class CuttingResult:
    params: dict[str, Param] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    def value(self, k: str) -> float | None:
        p = self.params.get(k)
        return p.value if p else None

    def provenance(self) -> dict:
        return {k: v.as_dict() for k, v in self.params.items()}


class CuttingDataUnavailable(ValueError):
    pass


def spindle_speed(vc_m_min: float, d_mm: float) -> float:
    if d_mm <= 0:
        raise ValueError("diameter must be positive")
    return 1000.0 * vc_m_min / (math.pi * d_mm)


def milling_feed(n_rpm: float, teeth: int, fz_mm: float) -> float:
    return n_rpm * teeth * fz_mm


def drilling_feed(n_rpm: float, f_mm_rev: float) -> float:
    return n_rpm * f_mm_rev


def chip_thinning_factor(ae: float, d: float) -> float:
    """Factor (>=1) applied to fz when radial engagement is below half the diameter."""
    if ae <= 0 or ae >= d / 2:
        return 1.0
    return d / (2.0 * math.sqrt(ae * (d - ae)))


def interp(table: list[list[float]], x: float) -> float:
    pts = sorted(table)
    if x <= pts[0][0]:
        return pts[0][1] * x / pts[0][0]  # proportional below the smallest tabulated diameter
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def power_kw(ap: float, ae: float, vf: float, kc11: float, mc: float, fz: float, d: float) -> float:
    # mean chip thickness for side milling hm ~ fz * sqrt(ae/D) (approximation)
    hm = max(1e-4, fz * math.sqrt(min(1.0, ae / d)))
    kc = kc11 * hm ** (-mc)
    return ap * ae * vf * kc / 60e6


TOOL_MATERIAL_TABLE_KEY = {"carbide": "carbide", "hss": "hss", "hss_co": "hss", "insert": "carbide"}


def compute(
    *,
    operation: str,  # milling | drilling
    tool: dict,  # diameter_mm, flute_count, tool_material, cutting_data, tool_type
    grade: dict | None,  # designation, cutting_data, iso_group, kc11_n_mm2, mc
    machine: dict,  # spindle_min_rpm, spindle_max_rpm, max_feed_mm_min, spindle_power_kw
    preference: str = "balanced",  # conservative | balanced | productive
    ae_mm: float | None = None,
    ap_mm: float | None = None,
    overrides: dict | None = None,
) -> CuttingResult:
    res = CuttingResult()
    overrides = overrides or {}
    d = float(tool["diameter_mm"])
    z = int(tool["flute_count"])
    iso = (grade or {}).get("iso_group")
    tm = TOOL_MATERIAL_TABLE_KEY.get(tool.get("tool_material", ""), None)
    frac = {"conservative": 0.0, "balanced": 0.5, "productive": 1.0}.get(preference, 0.5)

    # --- cutting speed and feed per tooth / per rev
    tool_cd = (tool.get("cutting_data") or {}).get("by_material", {})
    tool_src = (tool.get("cutting_data") or {}).get("source", "")
    manu = tool_cd.get(iso) if iso else None
    vc = fz = None
    if "vc_m_min" in overrides:
        vc = Param(overrides["vc_m_min"], "m/min", "user", "entered by user")
    elif manu and "vc_m_min" in manu:
        vc = Param(manu["vc_m_min"], "m/min", "tool_manufacturer", tool_src or "tool cutting data")
    feed_key = "fz_mm" if operation == "milling" else "f_mm_rev"
    if feed_key in overrides:
        fz = Param(overrides[feed_key], "mm", "user", "entered by user")
    elif manu and feed_key in manu:
        fz = Param(manu[feed_key], "mm", "tool_manufacturer", tool_src or "tool cutting data")
    if vc is None or fz is None:
        if not grade or not grade.get("cutting_data") or not tm or tm not in grade["cutting_data"]:
            raise CuttingDataUnavailable(
                f"No cutting data for tool material '{tool.get('tool_material')}' in "
                f"'{(grade or {}).get('designation', 'unknown material')}'. Enter manufacturer data on the tool "
                "or values on the operation.")
        table = grade["cutting_data"][tm]
        sub = table if operation == "milling" else table.get("drill")
        if not sub:
            raise CuttingDataUnavailable("No drilling data for this material/tool material")
        lo, hi = sub["vc_m_min"]
        if vc is None:
            vc = Param(lo + frac * (hi - lo), "m/min", "material_table",
                       f"generic estimate for {grade['designation']} ({tm}), range {lo}-{hi} m/min, "
                       f"'{preference}' preference; not a manufacturer recommendation")
        if fz is None:
            tab = sub["fz_by_d"] if operation == "milling" else sub["f_by_d"]
            base = interp(tab, d)
            fz = Param(base, "mm", "material_table",
                       f"generic estimate interpolated for D{d:g} from {grade['designation']} table; "
                       "not a manufacturer recommendation")
    res.params["vc"] = vc
    res.params["fz" if operation == "milling" else "f_rev"] = fz

    # --- spindle speed
    n = spindle_speed(vc.value, d)
    res.params["spindle_rpm"] = Param(n, "rpm", "calculated", f"n = 1000*Vc/(pi*D) = 1000*{vc.value:.1f}/(pi*{d:g})")
    smax, smin = machine.get("spindle_max_rpm"), machine.get("spindle_min_rpm")
    if smax is None:
        res.issues.append({"severity": "error", "code": "MACHINE_SPINDLE_LIMIT_UNKNOWN",
                           "message": "Machine maximum spindle speed is not configured"})
    elif n > smax:
        res.assumptions.append(f"Spindle speed clamped from {n:.0f} to machine maximum {smax:.0f} rpm; "
                               "cutting speed reduced accordingly.")
        n = float(smax)
        res.params["spindle_rpm"] = Param(n, "rpm", "machine_limit", "clamped to machine maximum")
    if smin and n < smin:
        res.issues.append({"severity": "error", "code": "SPINDLE_BELOW_MIN",
                           "message": f"Required {n:.0f} rpm is below the machine minimum {smin:.0f} rpm"})
    n = round(n)
    res.params["spindle_rpm"].value = n

    # --- feed
    if operation == "milling":
        fz_eff = fz.value
        if ae_mm is not None and ae_mm < d / 2:
            k = chip_thinning_factor(ae_mm, d)
            k = min(k, 2.0)
            fz_eff = fz.value * k
            res.assumptions.append(f"Radial chip thinning factor {k:.2f} applied (ae={ae_mm:.2f} mm, D={d:g} mm), "
                                   "capped at 2.0.")
        vf = milling_feed(n, z, fz_eff)
        res.params["feed_mm_min"] = Param(vf, "mm/min", "calculated", f"F = n*z*fz = {n:.0f}*{z}*{fz_eff:.4f}")
        res.params["plunge_feed_mm_min"] = Param(vf * 0.3, "mm/min", "calculated",
                                                 "30% of cutting feed (rule of thumb for ramp/plunge)")
        res.assumptions.append("Plunge/ramp feed is 30% of the cutting feed (generic rule, not tool data).")
    else:
        vf = drilling_feed(n, fz.value)
        res.params["feed_mm_min"] = Param(vf, "mm/min", "calculated", f"F = n*f = {n:.0f}*{fz.value:.4f}")
    fmax = machine.get("max_feed_mm_min")
    if fmax and res.params["feed_mm_min"].value > fmax:
        res.assumptions.append(f"Feed clamped to machine maximum {fmax:.0f} mm/min")
        res.params["feed_mm_min"] = Param(float(fmax), "mm/min", "machine_limit", "clamped to machine maximum")
    for k in ("feed_mm_min", "plunge_feed_mm_min"):
        if k in res.params:
            res.params[k].value = round(res.params[k].value, 1)

    # --- power estimate
    if operation == "milling" and grade and grade.get("kc11_n_mm2") and ap_mm and ae_mm:
        pc = power_kw(ap_mm, ae_mm, res.params["feed_mm_min"].value, grade["kc11_n_mm2"], grade.get("mc") or 0.25,
                      fz.value, d)
        res.params["power_kw"] = Param(pc, "kW", "calculated",
                                       "approximate (Kienzle kc1.1 with mean chip thickness); efficiency not included")
        pmax = machine.get("spindle_power_kw")
        if pmax and pc > 0.8 * pmax:
            res.issues.append({"severity": "warning", "code": "POWER_HIGH",
                               "message": f"Estimated cutting power {pc:.2f} kW exceeds 80% of spindle power {pmax} kW"})
    return res
