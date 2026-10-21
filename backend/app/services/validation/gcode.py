"""Independent G-code interpreter used for validation.

It deliberately does not reuse the postprocessor emitter: the program *text* is re-parsed with a modal state
machine configured from the postprocessor declaration (declared words, dialect details) and turned into motion
segments. Parsing success does not prove the physical controller will execute the program identically.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.services.post.schema import PostConfig, _norm_word

WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
ALLOWED_ADDRESSES = set("NGMTSFXYZIJKRQPHDO")

MOTION = {"G0": "rapid", "G1": "linear", "G2": "cw", "G3": "ccw"}
CYCLE_NAMES = {"drill", "drill_dwell", "peck", "chip_break"}


@dataclass
class Segment:
    line: int
    kind: str  # rapid | feed
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    tool: int | None
    feed: float | None
    spindle_rpm: float
    spindle_on: bool
    coolant: str | None
    op_index: int
    arc: dict | None = None  # {"cx","cy","dir"} for arcs (already discretised into chords for checks)


@dataclass
class Finding:
    layer: str
    code: str
    severity: str  # error | warning | info
    message: str
    lines: list[int] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"layer": self.layer, "code": self.code, "severity": self.severity, "message": self.message,
                "lines": self.lines[:50], "detail": self.detail}


@dataclass
class ParseResult:
    segments: list[Segment]
    findings: list[Finding]
    tool_changes: list[tuple[int, int]]  # (line, tool)
    max_rpm: float
    max_feed: float
    dwell_s: float
    flags: dict


class _State:
    def __init__(self):
        self.pos: list[float | None] = [None, None, None]
        self.motion: str | None = None
        self.units: str | None = None
        self.absolute: bool | None = None
        self.plane: str | None = None
        self.wcs: str | None = None
        self.feed: float | None = None
        self.rpm = 0.0
        self.spindle_on = False
        self.tool: int | None = None
        self.pending_tool: int | None = None
        self.length_comp = False
        self.cutter_comp = False
        self.coolant: str | None = None
        self.retract_initial = True
        self.cycle: dict | None = None
        self.cycle_initial_z: float | None = None


def _cycle_map(cfg: PostConfig) -> dict[str, str]:
    cc = cfg.canned_cycles
    m = {}
    for name, w in (("drill", cc.drill), ("drill_dwell", cc.drill_dwell), ("peck", cc.peck), ("chip_break", cc.chip_break)):
        if w and cc.enabled:
            m[_norm_word(w)] = name
    return m


def parse_program(text: str, cfg: PostConfig, machine_blocks: dict | None = None,
                  op_line_starts: list[int] | None = None) -> ParseResult:
    mb = machine_blocks or {}
    declared = {_norm_word(w) for w in cfg.supported_gcodes + cfg.supported_mcodes}
    declared |= {_norm_word(w) for w in mb.get("declared_mcodes", [])}
    cycles = _cycle_map(cfg)
    coolant_codes = {}
    src = mb.get("coolant") or {"flood": cfg.coolant.flood, "mist": cfg.coolant.mist, "air": cfg.coolant.air,
                                "through_spindle": cfg.coolant.through_spindle, "off": cfg.coolant.stop}
    for k, v in src.items():
        if v:
            coolant_codes[_norm_word(v)] = k
    unit_word = _norm_word(cfg.unit_words[cfg.output_units])
    other_units = {_norm_word(v) for k, v in cfg.unit_words.items() if k != cfg.output_units}
    end_word = _norm_word(cfg.program.end_word)
    tc_m = {_norm_word(m) for tpl in (mb.get("tool_change") or cfg.tool_change.sequence)
            for m in re.findall(r"M\d+", tpl)}
    lc_word = _norm_word(re.match(r"\s*(G\d+(?:\.\d+)?)", cfg.tool_change.length_comp).group(1)) \
        if cfg.tool_change.length_comp else None
    lc_cancel = _norm_word(cfg.length_comp_cancel_word) if cfg.length_comp_cancel_word else None
    dwell_word = _norm_word(cfg.dwell_word)
    st = _State()
    segs: list[Segment] = []
    fnd: list[Finding] = []
    tool_changes: list[tuple[int, int]] = []
    flags = {"ended": False, "end_line": None, "start_delim": False, "end_delim": False, "program_number": None,
             "z_without_length_comp": [], "motion_before_units": [], "motion_before_wcs": [],
             "cut_spindle_off": [], "cut_no_feed": [], "cut_no_tool": [], "after_end": []}
    max_rpm = max_feed = dwell_total = 0.0
    lines = text.splitlines()
    op_idx = 0
    op_starts = sorted(op_line_starts or [])

    def add_seg(lineno, kind, a, b, arc=None):
        segs.append(Segment(lineno, kind, tuple(a), tuple(b), st.tool, st.feed, st.rpm, st.spindle_on, st.coolant,
                            op_idx, arc))
        if kind == "feed":
            if not st.spindle_on:
                flags["cut_spindle_off"].append(lineno)
            if st.feed is None:
                flags["cut_no_feed"].append(lineno)
            if st.tool is None:
                flags["cut_no_tool"].append(lineno)

    for lineno, raw in enumerate(lines, 1):
        while op_starts and lineno >= op_starts[0]:
            op_starts.pop(0)
            op_idx += 1
        line = raw.strip()
        if not line:
            continue
        if line == "%":
            if lineno == 1 or not any(lines[i].strip() for i in range(lineno - 1)):
                flags["start_delim"] = True
            else:
                flags["end_delim"] = True
            continue
        if flags["ended"]:
            flags["after_end"].append(lineno)
        # comments
        if line.count("(") != line.count(")"):
            fnd.append(Finding("postprocessor", "PP_SYNTAX_COMMENT", "error", "Unbalanced comment parentheses", [lineno]))
        code = re.sub(r"\([^)]*\)", " ", line)
        if cfg.comments.style == "semicolon" or ";" in code:
            code = code.split(";", 1)[0]
        if cfg.max_line_length and len(raw) > cfg.max_line_length:
            fnd.append(Finding("postprocessor", "PP_LINE_LENGTH", "error",
                               f"Line exceeds {cfg.max_line_length} characters", [lineno]))
        code = code.strip()
        if not code:
            continue
        words = WORD.findall(code)
        leftover = WORD.sub("", code).strip()
        if leftover:
            fnd.append(Finding("postprocessor", "PP_SYNTAX", "error", f"Unparseable text '{leftover[:30]}'", [lineno]))
        vals: dict[str, float] = {}
        gs: list[str] = []
        ms: list[str] = []
        for letter, num in words:
            L = letter.upper()
            if L not in ALLOWED_ADDRESSES:
                fnd.append(Finding("postprocessor", "PP_UNKNOWN_ADDRESS", "error", f"Unknown address '{L}'", [lineno]))
                continue
            if L == "G":
                gs.append(_norm_word(f"G{num}"))
            elif L == "M":
                ms.append(_norm_word(f"M{num}"))
            elif L == "N":
                continue
            elif L == "O" and lineno <= 3:
                flags["program_number"] = int(float(num))
            else:
                if L in vals:
                    fnd.append(Finding("postprocessor", "PP_DUPLICATE_WORD", "error", f"Word {L} repeated", [lineno]))
                vals[L] = float(num)
        for w in gs + ms:
            if w not in declared:
                fnd.append(Finding("postprocessor", "PP_UNDECLARED_CODE", "error",
                                   f"{w} is not declared as supported by {cfg.key} {cfg.version}", [lineno]))
        motion_words = [g for g in gs if g in MOTION or g in cycles or g == _norm_word(cfg.canned_cycles.cancel)]
        if len(motion_words) > 1:
            fnd.append(Finding("postprocessor", "PP_MODAL_CONFLICT", "error",
                               f"Multiple motion words in one block: {' '.join(motion_words)}", [lineno]))
        # ---- modal G words
        cycle_word = None
        is_dwell = False
        for g in gs:
            if g == unit_word:
                st.units = cfg.output_units
            elif g in other_units:
                st.units = "other"
                fnd.append(Finding("postprocessor", "PP_UNITS", "error", f"{g} selects a unit other than "
                                   f"{cfg.output_units}", [lineno]))
            elif g == "G90":
                st.absolute = True
            elif g == "G91":
                st.absolute = False
                fnd.append(Finding("postprocessor", "PP_INCREMENTAL", "warning",
                                   "Incremental positioning (G91) used; checks assume absolute coordinates", [lineno]))
            elif g == _norm_word(cfg.plane_xy_word):
                st.plane = "XY"
            elif g in {_norm_word(w) for w in cfg.work_offsets}:
                st.wcs = g
            elif lc_word and g == lc_word:
                st.length_comp = True
            elif lc_cancel and g == lc_cancel:
                st.length_comp = False
            elif g == "G40":
                st.cutter_comp = False
            elif g in ("G41", "G42"):
                st.cutter_comp = True
                fnd.append(Finding("postprocessor", "PP_CUTTER_COMP", "error",
                                   "Cutter radius compensation is not expected in tool-centre programs", [lineno]))
            elif g == _norm_word(cfg.canned_cycles.retract_initial or "G98"):
                st.retract_initial = True
            elif g == _norm_word(cfg.canned_cycles.retract_r_plane or "G99"):
                st.retract_initial = False
            elif g in MOTION:
                st.motion = g
                st.cycle = None
            elif g in cycles:
                cycle_word = g
            elif cfg.canned_cycles.enabled and g == _norm_word(cfg.canned_cycles.cancel):
                st.cycle = None
                st.motion = None
            elif g == dwell_word:
                is_dwell = True
            elif g in ("G53", "G28", "G30"):
                fnd.append(Finding("postprocessor", "PP_MACHINE_COORD_MOVE", "info",
                                   f"{g} machine-coordinate/reference move: position is machine specific and not "
                                   "simulated", [lineno]))
                st.pos = [None, None, None] if g != "G53" else [st.pos[0], st.pos[1], None]
                vals.pop("X", None), vals.pop("Y", None), vals.pop("Z", None)
        if "T" in vals:
            st.pending_tool = int(vals["T"])
            if not tc_m:  # controls where the T word itself changes tools
                st.tool = st.pending_tool
        # ---- M words
        for m in ms:
            if m == _norm_word(cfg.spindle.cw) or m == _norm_word(cfg.spindle.ccw):
                st.spindle_on = True
            elif m == _norm_word(cfg.spindle.stop):
                st.spindle_on = False
            elif m in coolant_codes:
                st.coolant = None if coolant_codes[m] == "off" else coolant_codes[m]
            elif m in tc_m:
                if st.pending_tool is None:
                    fnd.append(Finding("postprocessor", "PP_TOOL_CHANGE_NO_T", "error", "Tool change without T word",
                                       [lineno]))
                st.tool = st.pending_tool
                st.length_comp = False if lc_word else True  # Siemens-style: offset active with T/D call
                tool_changes.append((lineno, st.tool))
                st.pos = [None, None, None]
            elif m == end_word or m in ("M2", "M30"):
                st.spindle_on = False
                if not flags["ended"]:
                    flags["ended"] = True
                    flags["end_line"] = lineno
        if "S" in vals:
            st.rpm = vals["S"]
            max_rpm = max(max_rpm, st.rpm)
        if "F" in vals and not is_dwell:
            st.feed = vals["F"]
            max_feed = max(max_feed, st.feed)
        if is_dwell:
            v = vals.get(cfg.dwell_value_word.upper(), vals.get("P", 0.0))
            dwell_total += v / 1000 if cfg.canned_cycles.dwell_unit == "ms" and cfg.dwell_value_word == "P" else v
            continue
        has_xyz = any(k in vals for k in "XYZ")
        target = [vals.get("X", st.pos[0]), vals.get("Y", st.pos[1]), vals.get("Z", st.pos[2])]
        if cycle_word or (st.cycle is not None and has_xyz and not any(g in MOTION for g in gs)):
            # canned cycle block
            if cycle_word:
                if st.pos[2] is None:
                    fnd.append(Finding("postprocessor", "PP_CYCLE_NO_Z", "error", "Canned cycle with unknown initial Z", [lineno]))
                    continue
                st.cycle = {"type": cycles[cycle_word], "z": vals.get("Z"), "r": vals.get("R"), "q": vals.get("Q"),
                            "p": vals.get(cfg.canned_cycles.dwell_word)}
                st.cycle_initial_z = st.pos[2]
                if st.cycle["z"] is None or st.cycle["r"] is None:
                    fnd.append(Finding("postprocessor", "PP_CYCLE_ARGS", "error", "Canned cycle without Z or R", [lineno]))
                    continue
                if st.cycle["type"] in ("peck", "chip_break") and not st.cycle["q"]:
                    fnd.append(Finding("postprocessor", "PP_CYCLE_ARGS", "error", "Peck cycle without Q", [lineno]))
                    continue
            else:
                for k, key in (("Z", "z"), ("R", "r"), ("Q", "q")):
                    if k in vals:
                        st.cycle[key] = vals[k]
            cx, cy = vals.get("X", st.pos[0]), vals.get("Y", st.pos[1])
            if cx is None or cy is None:
                fnd.append(Finding("postprocessor", "PP_CYCLE_NO_XY", "error", "Canned cycle with unknown XY", [lineno]))
                continue
            c = st.cycle
            init_z = st.cycle_initial_z
            here = list(st.pos)
            p1 = [cx, cy, here[2]]
            add_seg(lineno, "rapid", here, p1)
            pr = [cx, cy, c["r"]]
            add_seg(lineno, "rapid", p1, pr)
            cur = pr
            if c["type"] in ("peck", "chip_break"):
                depth = c["r"]
                while depth - c["q"] > c["z"] + 1e-9:
                    nd = depth - c["q"]
                    add_seg(lineno, "feed", cur, [cx, cy, nd])
                    back = c["r"] if c["type"] == "peck" else nd + 0.5
                    add_seg(lineno, "rapid", [cx, cy, nd], [cx, cy, back])
                    cur = [cx, cy, back]
                    if c["type"] == "peck":  # rapid back down to just above the previous depth
                        add_seg(lineno, "rapid", cur, [cx, cy, nd + 0.5])
                        cur = [cx, cy, nd + 0.5]
                    depth = nd
            add_seg(lineno, "feed", cur, [cx, cy, c["z"]])
            if c["type"] == "drill_dwell" and c.get("p"):
                dwell_total += c["p"] / 1000 if cfg.canned_cycles.dwell_unit == "ms" else c["p"]
            back = init_z if st.retract_initial else c["r"]
            add_seg(lineno, "rapid", [cx, cy, c["z"]], [cx, cy, back])
            st.pos = [cx, cy, back]
            continue
        if not has_xyz:
            continue
        if st.motion is None:
            fnd.append(Finding("postprocessor", "PP_NO_MOTION_MODE", "error", "Coordinates without an active motion mode",
                               [lineno]))
            continue
        if st.units is None:
            flags["motion_before_units"].append(lineno)
        if st.wcs is None:
            flags["motion_before_wcs"].append(lineno)
        if "Z" in vals and not st.length_comp and st.tool is not None:
            flags["z_without_length_comp"].append(lineno)
        if None in target:
            # position partially unknown (e.g. XY move right after a tool change): record known axes only
            st.pos = target
            continue
        start = st.pos
        if st.motion in ("G0",):
            if None not in start:
                add_seg(lineno, "rapid", start, target)
        elif st.motion == "G1":
            if None in start:
                fnd.append(Finding("toolpath", "TP_FEED_FROM_UNKNOWN", "error", "Feed move from an unknown position",
                                   [lineno]))
            else:
                add_seg(lineno, "feed", start, target)
        else:  # arcs
            if None in start:
                fnd.append(Finding("toolpath", "TP_ARC_FROM_UNKNOWN", "error", "Arc from an unknown position", [lineno]))
            else:
                if "R" in vals:
                    fnd.append(Finding("postprocessor", "PP_ARC_R", "warning", "R-format arc: centre ambiguity not "
                                       "simulated precisely", [lineno]))
                    cx, cy = _r_center(start, target, vals["R"], st.motion == "G2")
                else:
                    cx, cy = start[0] + vals.get("I", 0.0), start[1] + vals.get("J", 0.0)
                r0 = math.hypot(start[0] - cx, start[1] - cy)
                r1 = math.hypot(target[0] - cx, target[1] - cy)
                if abs(r0 - r1) > max(0.002, 1e-4 * r0):
                    fnd.append(Finding("postprocessor", "PP_ARC_RADIUS", "error",
                                       f"Arc start/end radii differ ({r0:.4f} vs {r1:.4f} mm)", [lineno]))
                for a, b in _arc_chords(start, target, cx, cy, st.motion == "G2"):
                    add_seg(lineno, "feed", a, b, arc={"cx": cx, "cy": cy, "dir": "cw" if st.motion == "G2" else "ccw"})
        st.pos = target
    return ParseResult(segs, fnd, tool_changes, max_rpm, max_feed, dwell_total, flags)


def _r_center(a, b, r, cw):
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    q = math.hypot(dx, dy)
    h = math.sqrt(max(0.0, r * r - (q / 2) ** 2))
    ux, uy = -dy / q, dx / q
    s = -1 if (cw ^ (r < 0)) else 1
    return mx + s * h * ux, my + s * h * uy


def _arc_chords(a, b, cx, cy, cw, tol: float = 0.01):
    r = math.hypot(a[0] - cx, a[1] - cy)
    a0 = math.atan2(a[1] - cy, a[0] - cx)
    a1 = math.atan2(b[1] - cy, b[0] - cx)
    sweep = (a0 - a1) % (2 * math.pi) if cw else (a1 - a0) % (2 * math.pi)
    if sweep < 1e-9:
        sweep = 2 * math.pi
    step = 2 * math.acos(max(-1, min(1, 1 - tol / r))) if r > tol else sweep
    n = max(1, math.ceil(sweep / max(step, 1e-3)))
    pts = [a]
    for k in range(1, n + 1):
        ang = a0 + (-1 if cw else 1) * sweep * k / n
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang), a[2] + (b[2] - a[2]) * k / n))
    pts[-1] = tuple(b)
    return list(zip(pts, pts[1:]))
