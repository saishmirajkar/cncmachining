"""Z-map (height-field) material-removal simulation for 3-axis milling and drilling.

The stock is a grid of column heights. Each feed segment lowers the columns covered by the tool profile
(flat, ball, bull-nose, or drill cone). Before material is removed, the simulator checks:

* rapids: any material inside the tool envelope -> rapid into material,
* feeds: material above the flute length within the tool radius -> shank engagement,
         material above the stick-out within the holder radius -> holder collision.

Final heights are compared with the part's top envelope (height field rasterised from its tessellation) to
find gouges and remaining material.

Limitations (reported with every result): a height field cannot represent undercuts or side features; the
grid resolution limits accuracy near walls (cells are classified conservatively); machine components other than
the tool assembly and user/vise keep-out boxes are not modelled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.services.validation.gcode import Finding, Segment

LIMITATIONS = [
    "Z-map (height-field) simulation: undercuts and side-access features are not represented.",
    "Accuracy is limited by the grid resolution; results near vertical walls carry +/- one cell uncertainty.",
    "Only the tool, shank, holder and the defined keep-out boxes are checked; spindle head, table, vise body and "
    "other machine components are not modelled.",
    "Machining time uses programmed feeds and the configured rapid rate without acceleration limits.",
]


@dataclass
class ToolShape:
    number: int
    kind: str  # flat_endmill | ball_endmill | bull_endmill | drill | spot_drill | face_mill
    diameter: float
    corner_radius: float
    tip_angle: float | None
    flute_length: float
    stick_out: float | None
    holder_diameter: float | None
    holder_length: float | None
    shank_diameter: float | None

    def profile(self, d: np.ndarray) -> np.ndarray:
        """Height of the cutting edge above the tip at radial distance d (d <= R)."""
        r = self.diameter / 2
        if self.kind == "ball_endmill":
            return r - np.sqrt(np.maximum(r * r - d * d, 0))
        if self.kind in ("drill", "spot_drill"):
            half = math.radians((self.tip_angle or 118) / 2)
            return d / math.tan(half)
        if self.kind == "bull_endmill" and self.corner_radius > 0:
            rc = self.corner_radius
            out = np.zeros_like(d)
            m = d > r - rc
            dd = d[m] - (r - rc)
            out[m] = rc - np.sqrt(np.maximum(rc * rc - dd * dd, 0))
            return out
        return np.zeros_like(d)


@dataclass
class Grid:
    x0: float
    y0: float
    res: float
    nx: int
    ny: int

    def idx(self, x, y):
        return np.floor((x - self.x0) / self.res).astype(int), np.floor((y - self.y0) / self.res).astype(int)

    def centers(self):
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.res
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.res
        return xs, ys


def make_grid(stock_min, stock_max, max_tool_r: float, max_cells: int) -> Grid:
    margin = max_tool_r + 2.0
    w = stock_max[0] - stock_min[0] + 2 * margin
    h = stock_max[1] - stock_min[1] + 2 * margin
    res = max(0.1, math.sqrt(w * h / max_cells))
    res = math.ceil(res * 20) / 20  # 0.05 mm steps
    return Grid(stock_min[0] - margin, stock_min[1] - margin, res, int(math.ceil(w / res)), int(math.ceil(h / res)))


def rasterize_top(positions: np.ndarray, indices: np.ndarray, grid: Grid) -> np.ndarray:
    """Max Z of the mesh over each cell centre (-inf where no triangle covers the centre)."""
    H = np.full((grid.ny, grid.nx), -np.inf)
    tri = positions[indices]
    xs, ys = grid.centers()
    for t in tri:
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = t
        det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(det) < 1e-12:
            continue
        i0 = max(0, int((min(x1, x2, x3) - grid.x0) / grid.res))
        i1 = min(grid.nx - 1, int((max(x1, x2, x3) - grid.x0) / grid.res))
        j0 = max(0, int((min(y1, y2, y3) - grid.y0) / grid.res))
        j1 = min(grid.ny - 1, int((max(y1, y2, y3) - grid.y0) / grid.res))
        if i1 < i0 or j1 < j0:
            continue
        X, Y = np.meshgrid(xs[i0:i1 + 1], ys[j0:j1 + 1])
        a = ((y2 - y3) * (X - x3) + (x3 - x2) * (Y - y3)) / det
        b = ((y3 - y1) * (X - x3) + (x1 - x3) * (Y - y3)) / det
        c = 1 - a - b
        m = (a >= -1e-9) & (b >= -1e-9) & (c >= -1e-9)
        if not m.any():
            continue
        Z = a * z1 + b * z2 + c * z3
        sub = H[j0:j1 + 1, i0:i1 + 1]
        np.maximum(sub, np.where(m, Z, -np.inf), out=sub)
    return H


def _filter3(a: np.ndarray, op) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    out = a.copy()
    for dj in (0, 1, 2):
        for di in (0, 1, 2):
            op(out, p[dj:dj + a.shape[0], di:di + a.shape[1]], out=out)
    return out


@dataclass
class SimResult:
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    heightmap: dict | None = None


def _footprint(grid: Grid, a: np.ndarray, b: np.ndarray, radius: float, horizontal: bool):
    """Cells swept by a disc of ``radius`` moving from a to b: (linear cell index, XY distance, tip Z).

    Horizontal moves use the exact swept area (distance from each cell centre to the segment), which is the same
    result as dense sampling at a fraction of the cost. Other moves (plunges, ramps, helix chords) are sampled.
    """
    if horizontal:
        i0 = max(0, int(math.floor((min(a[0], b[0]) - radius - grid.x0) / grid.res)))
        i1 = min(grid.nx - 1, int(math.floor((max(a[0], b[0]) + radius - grid.x0) / grid.res)))
        j0 = max(0, int(math.floor((min(a[1], b[1]) - radius - grid.y0) / grid.res)))
        j1 = min(grid.ny - 1, int(math.floor((max(a[1], b[1]) + radius - grid.y0) / grid.res)))
        if i1 < i0 or j1 < j0:
            return np.empty(0, int), np.empty(0), np.empty(0)
        I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        X = grid.x0 + (I + 0.5) * grid.res
        Y = grid.y0 + (J + 0.5) * grid.res
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        tt = np.clip(((X - a[0]) * dx + (Y - a[1]) * dy) / l2, 0.0, 1.0) if l2 > 0 else 0.0
        d = np.hypot(X - (a[0] + tt * dx), Y - (a[1] + tt * dy))
        m = d <= radius
        return (J * grid.nx + I)[m], d[m], np.full(int(m.sum()), a[2])
    L = float(np.linalg.norm(b - a))
    n = max(2, int(math.ceil(L / grid.res)) + 1)
    P = a[None, :] + (b - a)[None, :] * np.linspace(0, 1, n)[:, None]
    di, dj, _ = _kernel(grid, radius + grid.res)
    ci, cj = grid.idx(P[:, 0], P[:, 1])
    I = ci[:, None] + di[None, :]
    J = cj[:, None] + dj[None, :]
    d = np.hypot(grid.x0 + (I + 0.5) * grid.res - P[:, 0][:, None], grid.y0 + (J + 0.5) * grid.res - P[:, 1][:, None])
    m = (I >= 0) & (I < grid.nx) & (J >= 0) & (J < grid.ny) & (d <= radius)
    z = np.broadcast_to(P[:, 2][:, None], I.shape)
    return (J * grid.nx + I)[m], d[m], z[m]


_KERNELS: dict[tuple[float, float], tuple] = {}


def _kernel(grid: Grid, radius: float):
    key = (round(grid.res, 6), round(radius, 6))
    if key in _KERNELS:
        return _KERNELS[key]
    _KERNELS[key] = _kernel_build(grid, radius)
    return _KERNELS[key]


def _kernel_build(grid: Grid, radius: float):
    n = int(math.ceil(radius / grid.res)) + 1
    o = np.arange(-n, n + 1)
    DI, DJ = np.meshgrid(o, o)
    d = np.hypot(DI * grid.res, DJ * grid.res)
    m = d <= radius
    return DI[m], DJ[m], d[m]


def simulate(segments: list[Segment], tools: dict[int, ToolShape], stock_min, stock_max, part_top: np.ndarray | None,
             grid: Grid, tolerance: float, keep_out: list[dict], breakthrough_allowance: float,
             rapid_rate: float | None, tool_change_s: float | None, tool_changes: int, dwell_s: float) -> SimResult:
    res = SimResult()
    H = np.full((grid.ny, grid.nx), -np.inf)
    i0, j0 = grid.idx(np.array([stock_min[0]]), np.array([stock_min[1]]))
    xs, ys = grid.centers()
    inside_x = (xs >= stock_min[0]) & (xs <= stock_max[0])
    inside_y = (ys >= stock_min[1]) & (ys <= stock_max[1])
    stock_mask = np.outer(inside_y, inside_x)
    H[stock_mask] = stock_max[2]
    H0 = H.copy()
    flat = H.reshape(-1)
    eps = max(0.02, tolerance)
    hits: dict[str, list[int]] = {"rapid": [], "shank": [], "holder": [], "below_stock": [], "keep_out": [],
                                  "plunge": []}
    worst = {"rapid": 0.0, "shank": 0.0, "holder": 0.0}
    keep_hits: dict[str, list[int]] = {}
    feed_len = rapid_len = feed_time = rapid_time = 0.0
    max_depth_by_tool: dict[int, float] = {}
    for s in segments:
        a = np.array(s.start, dtype=float)
        b = np.array(s.end, dtype=float)
        L = float(np.linalg.norm(b - a))
        if s.kind == "feed":
            feed_len += L
            if s.feed:
                feed_time += L / s.feed * 60
        else:
            rapid_len += L
            if rapid_rate:
                rapid_time += float(np.max(np.abs(b - a))) / rapid_rate * 60
        if L < 1e-9 or s.tool is None or s.tool not in tools:
            continue
        t = tools[s.tool]
        R = t.diameter / 2
        horizontal = abs(a[2] - b[2]) < 1e-9
        lin, dist, tipz = _footprint(grid, a, b, R, horizontal)
        # keep-out boxes: tool and holder cylinders vs AABB, sampled every 1 mm along the segment
        if keep_out:
            P = a[None, :] + (b - a)[None, :] * np.linspace(0, 1, max(2, int(math.ceil(L)) + 1))[:, None]
            reach = t.stick_out or t.flute_length
            for box in keep_out:
                bmin, bmax = np.array(box["min"]), np.array(box["max"])
                dx = np.maximum(np.maximum(bmin[0] - P[:, 0], 0), P[:, 0] - bmax[0])
                dy = np.maximum(np.maximum(bmin[1] - P[:, 1], 0), P[:, 1] - bmax[1])
                dxy = np.hypot(dx, dy)
                tool_hit = (dxy < R) & (P[:, 2] < bmax[2]) & (P[:, 2] + reach > bmin[2])
                holder_hit = np.zeros_like(tool_hit)
                if t.holder_diameter:
                    holder_hit = ((dxy < t.holder_diameter / 2) & (P[:, 2] + reach < bmax[2])
                                  & (P[:, 2] + reach + (t.holder_length or 0) > bmin[2]))
                if np.any(tool_hit | holder_hit):
                    keep_hits.setdefault(box.get("name", "keep-out"), []).append(s.line)
        if lin.size == 0:
            continue
        cur = flat[lin]
        prof = t.profile(np.minimum(dist, R))
        cut_z = tipz + prof
        core = dist <= max(0.0, R - grid.res)  # conservative core for collision tests
        if s.kind == "rapid":
            if core.any():
                mx = float((cur[core] - cut_z[core]).max())
                if mx > eps:
                    hits["rapid"].append(s.line)
                    worst["rapid"] = max(worst["rapid"], mx)
            continue
        # vertical plunge of a milling tool into material (requires a centre-cutting tool; ramp/helix preferred)
        if (t.kind not in ("drill", "spot_drill") and abs(b[0] - a[0]) < 1e-6 and abs(b[1] - a[1]) < 1e-6
                and b[2] < a[2] and core.any() and np.any(cur[core] > b[2] + eps)):
            hits["plunge"].append(s.line)
        # shank engagement: material above the flute length inside the tool radius
        if t.flute_length and core.any():
            mx = float((cur[core] - (tipz[core] + t.flute_length)).max())
            if mx > eps:
                hits["shank"].append(s.line)
                worst["shank"] = max(worst["shank"], mx)
        if t.holder_diameter and t.stick_out:
            hl, hd, hz = _footprint(grid, a, b, t.holder_diameter / 2, horizontal)
            if hl.size:
                mx = float((flat[hl] - (hz + t.stick_out)).max())
                if mx > eps:
                    hits["holder"].append(s.line)
                    worst["holder"] = max(worst["holder"], mx)
        zmin = min(a[2], b[2])
        if zmin < stock_min[2] - breakthrough_allowance - 1e-6:
            hits["below_stock"].append(s.line)
        np.minimum.at(flat, lin, cut_z)
        max_depth_by_tool[s.tool] = max(max_depth_by_tool.get(s.tool, 0.0), float(stock_max[2] - zmin))
    H = flat.reshape(H.shape)

    def lines_str(v):
        return sorted(set(v))

    if hits["rapid"]:
        res.findings.append(Finding("simulation", "SIM_RAPID_INTO_MATERIAL", "error",
                                    f"Rapid move passes through material (up to {worst['rapid']:.2f} mm)",
                                    lines_str(hits["rapid"])))
    if hits["shank"]:
        res.findings.append(Finding("simulation", "SIM_SHANK_ENGAGEMENT", "error",
                                    f"Material above the flute length contacts the shank (up to {worst['shank']:.2f} mm)",
                                    lines_str(hits["shank"])))
    if hits["holder"]:
        res.findings.append(Finding("simulation", "SIM_HOLDER_COLLISION", "error",
                                    f"Tool holder contacts material (up to {worst['holder']:.2f} mm)",
                                    lines_str(hits["holder"])))
    if hits["below_stock"]:
        res.findings.append(Finding("simulation", "SIM_BELOW_STOCK", "error",
                                    "Tool goes below the stock bottom beyond the allowed breakthrough; check "
                                    "parallels/fixture clearance", lines_str(hits["below_stock"])))
    if hits["plunge"]:
        res.findings.append(Finding("simulation", "SIM_PLUNGE_INTO_MATERIAL", "warning",
                                    "Milling tool plunges vertically into material: the tool must be centre-cutting "
                                    "(consider a ramp or helical entry)", lines_str(hits["plunge"])))
    for name, ls in keep_hits.items():
        res.findings.append(Finding("simulation", "SIM_KEEP_OUT_COLLISION", "error",
                                    f"Tool assembly enters keep-out zone '{name}'", lines_str(ls)))
    stats = {"resolution_mm": grid.res, "grid": [grid.nx, grid.ny], "feed_length_mm": round(feed_len, 1),
             "rapid_length_mm": round(rapid_len, 1), "max_depth_below_stock_top_by_tool": max_depth_by_tool}
    removed = np.where(stock_mask, H0 - np.maximum(H, stock_min[2]), 0)
    stats["removed_volume_mm3"] = round(float(np.clip(removed, 0, None).sum() * grid.res ** 2), 1)
    if part_top is not None:
        has_part = np.isfinite(part_top) & stock_mask
        pt = np.where(np.isfinite(part_top), part_top, -1e9)
        # +/- one cell around walls is ambiguous at grid resolution: erode the part envelope for gouge checks and
        # dilate it for remaining-material checks so that wall discretisation is not reported as either
        pmin = _filter3(pt, np.minimum)
        pmax = _filter3(pt, np.maximum)
        gouge = has_part & (H < pmin - eps)
        g_depth = float((pmin - H)[gouge].max()) if gouge.any() else 0.0
        env = np.where(pmax > -1e8, pmax, stock_min[2])
        with np.errstate(invalid="ignore"):
            remaining = np.where(stock_mask, H - np.maximum(env, stock_min[2]), 0)
        remaining = np.where(np.isfinite(remaining), remaining, 0)
        over = remaining > max(eps, tolerance)
        stats.update({
            "gouge_cells": int(gouge.sum()), "max_gouge_mm": round(g_depth, 3),
            "remaining_cells": int((over & stock_mask).sum()),
            "max_remaining_mm": round(float(remaining[stock_mask].max()) if stock_mask.any() else 0.0, 3),
            "remaining_volume_mm3": round(float(np.clip(remaining, 0, None)[over].sum() * grid.res ** 2), 1),
            "part_cells": int(has_part.sum()),
            "part_surface_within_tolerance_pct": round(
                100.0 * float((has_part & ~over & ~gouge).sum()) / max(1, int(has_part.sum())), 2),
        })
        if gouge.any():
            res.findings.append(Finding("simulation", "SIM_GOUGE", "error",
                                        f"Material removed below the part surface in {int(gouge.sum())} cells "
                                        f"(max {g_depth:.3f} mm at {grid.res} mm resolution)", []))
        if (over & stock_mask).any():
            res.findings.append(Finding("simulation", "SIM_REMAINING_MATERIAL", "warning",
                                        f"Material remains above the part/stock-bottom envelope: max "
                                        f"{stats['max_remaining_mm']:.2f} mm, ~{stats['remaining_volume_mm3']:.0f} mm3. "
                                        "Check for unplanned features or limited contour depth.", []))
    total = feed_time + rapid_time + dwell_s + (tool_change_s or 0) * tool_changes
    stats["time"] = {"feed_s": round(feed_time, 1), "rapid_s": round(rapid_time, 1), "dwell_s": round(dwell_s, 2),
                     "tool_changes": tool_changes, "tool_change_s": tool_change_s,
                     "total_s": round(total, 1),
                     "assumptions": ["programmed feed rates held constant (no acceleration)",
                                     f"rapid traverse {rapid_rate} mm/min per axis" if rapid_rate else
                                     "rapid rate unknown: rapid time not included",
                                     f"{tool_change_s} s per tool change" if tool_change_s else
                                     "tool change time unknown: not included"]}
    # downsampled height map for the viewer
    step = max(1, int(math.ceil(max(grid.nx, grid.ny) / 220)))
    Hs = H[::step, ::step]
    res.heightmap = {"x0": grid.x0 + grid.res * step / 2, "y0": grid.y0 + grid.res * step / 2,
                     "dx": grid.res * step, "nx": Hs.shape[1], "ny": Hs.shape[0],
                     "z": [None if not np.isfinite(v) else round(float(v), 3) for v in Hs.ravel()],
                     "stock_bottom": float(stock_min[2])}
    res.stats = stats
    return res
