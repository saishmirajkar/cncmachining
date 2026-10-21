"""Configurable postprocessor engine: ``ToolpathIR`` + ``PostConfig`` + machine blocks -> NC program text.

Safety-relevant behaviour:

* Only words declared by the configuration are emitted. Machine-supplied blocks are checked against the same
  declaration and rejected if they contain undeclared G/M words.
* Rapids that change both XY and Z are split (Z up first, or XY first then Z down) so that the emitted motion
  never depends on the controller's non-linear rapid interpolation.
* Coolant and tool-change codes come from the machine configuration when it supplies them. Controller defaults
  are used only if the postprocessor declares them as documented controller behaviour, and a warning is
  recorded that the machine owner has not confirmed them.
* Cutter radius compensation is not used (tool-centre paths); G40 is asserted in the safety block.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field

from app.services.cam.ir import IROperation, Move, ToolpathIR
from app.services.post.schema import WORD_RE, PostConfig, _norm_word


class PostError(ValueError):
    pass


@dataclass
class PostContext:
    program_number: int | None = None
    program_name: str = "PROGRAM"
    machine_blocks: dict = field(default_factory=dict)
    machine_max_rpm: float | None = None
    header_info: list[str] = field(default_factory=list)  # setup lines rendered as comments
    tools: list[dict] = field(default_factory=list)  # [{"number": 1, "name": "...", "diameter": 10}]
    generated_at: dt.datetime | None = None


@dataclass
class PostResult:
    text: str
    lines: list[str]
    operation_map: list[dict]
    warnings: list[dict]
    file_extension: str


def _warn(ws: list, code: str, message: str, **detail) -> None:
    ws.append({"code": code, "severity": "warning", "message": message, "detail": detail})


class _Emitter:
    def __init__(self, cfg: PostConfig, extra_declared: list[str] | None = None) -> None:
        self.cfg = cfg
        self.lines: list[str] = []
        self.n = cfg.line_numbers.start
        self.motion: str | None = None
        self.pos: dict[str, float | None] = {"X": None, "Y": None, "Z": None}
        self.feed: float | None = None
        self.declared = {_norm_word(w) for w in cfg.supported_gcodes + cfg.supported_mcodes + (extra_declared or [])}

    # -- formatting
    def num(self, v: float, decimals: int, axis: bool = True) -> str:
        """Format a value. ``axis`` words (coordinates, I/J, R, Q) get a trailing decimal point when the
        controller needs one (FANUC reads ``X10`` as 10 least-input-increments without calculator input)."""
        p = self.cfg.precision
        v = round(v, decimals)
        if v == 0:
            v = 0.0  # avoid "-0"
        s = f"{v:.{decimals}f}"
        if p.strip_trailing_zeros and "." in s:
            s = s.rstrip("0").rstrip(".")
        if axis and "." not in s and p.trailing_decimal_point:
            s += "."
        if not p.leading_zero:
            if s.startswith("0."):
                s = s[1:]
            elif s.startswith("-0."):
                s = "-" + s[2:]
        return s

    def int_word(self, letter: str, v: float) -> str:
        return f"{letter}{int(round(v))}"

    def comment(self, text: str) -> str:
        c = self.cfg.comments
        text = text.upper() if c.uppercase else text
        safe = "".join(ch if re.fullmatch(c.allowed_pattern, ch) else "_" for ch in text)[: c.max_length]
        return f"({safe})" if c.style == "paren" else f"; {safe}"

    def check_words(self, block: str, where: str) -> None:
        for letter, num in WORD_RE.findall(block.split(";")[0].split("(")[0]):
            w = _norm_word(f"{letter}{num}")
            if w not in self.declared:
                raise PostError(f"{where}: word {w} is not declared as supported by postprocessor "
                                f"{self.cfg.key} {self.cfg.version}")

    def raw(self, text: str, numbered: bool = False) -> None:
        if numbered and self.cfg.line_numbers.enabled:
            text = f"{self.cfg.line_numbers.prefix}{self.n} {text}"
            self.n += self.cfg.line_numbers.increment
            if self.n > self.cfg.line_numbers.maximum:
                self.n = self.cfg.line_numbers.start if self.cfg.line_numbers.wrap else self.n
        self.lines.append(text)

    def block(self, *words: str, comment: str | None = None) -> None:
        text = " ".join(w for w in words if w)
        if comment:
            text = f"{text} {self.comment(comment)}" if text else self.comment(comment)
        if not text:
            return
        self.check_words(text, "generated block")
        self.raw(text, numbered=True)

    def comment_line(self, text: str) -> None:
        self.raw(self.comment(text), numbered=False)

    # -- motion
    def _coords(self, x: float | None, y: float | None, z: float | None, force: bool = False) -> list[str]:
        out = []
        prec = self.cfg.precision.linear
        for ax, v in (("X", x), ("Y", y), ("Z", z)):
            if v is None:
                continue
            cur = self.pos[ax]
            if force or not self.cfg.suppress_modal_repeats or cur is None or abs(cur - v) > 10 ** (-prec) / 2:
                out.append(f"{ax}{self.num(v, prec)}")
            self.pos[ax] = v
        return out

    def _motion_word(self, w: str) -> str:
        if self.cfg.suppress_modal_repeats and self.motion == w:
            return ""
        self.motion = w
        return w

    def _feed_word(self, f: float) -> str:
        if self.cfg.suppress_modal_repeats and self.feed is not None and abs(self.feed - f) < 1e-9:
            return ""
        self.feed = f
        return f"F{self.num(f, self.cfg.precision.feed, axis=False)}"

    def rapid(self, x=None, y=None, z=None, extra: list[str] | None = None) -> None:
        coords = self._coords(x, y, z)
        if not coords and not extra:
            return
        self.block(self._motion_word(self.cfg.rapid_word), *coords, *(extra or []))

    def linear(self, x, y, z, f) -> None:
        coords = self._coords(x, y, z)
        if not coords:
            return
        self.block(self._motion_word(self.cfg.linear_word), *coords, self._feed_word(f))

    def arc(self, cw: bool, x, y, z, i, j, f) -> None:
        word = self.cfg.cw_arc_word if cw else self.cfg.ccw_arc_word
        # arcs always state their end point explicitly (a bare "G2 I.. J.." is a full circle on most controls)
        coords = self._coords(x, y, None, force=True) + self._coords(None, None, z)
        if self.cfg.arcs.format == "IJK":
            centre = [f"I{self.num(i, self.cfg.precision.arc_center)}", f"J{self.num(j, self.cfg.precision.arc_center)}"]
        else:
            r = math.hypot(i, j)
            centre = [f"R{self.num(r, self.cfg.precision.arc_center)}"]
        self.block(self._motion_word(word), *coords, *centre, self._feed_word(f))

    def dwell_value(self, seconds: float) -> str:
        cc = self.cfg.canned_cycles
        if cc.dwell_unit == "ms":
            return self.num(seconds * 1000, 0, axis=False)
        return self.num(seconds, self.cfg.precision.dwell, axis=False)

    def invalidate_modal(self) -> None:
        self.motion = None


def _arc_sweep(sx, sy, m: Move) -> float:
    cx, cy = sx + m.i, sy + m.j
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(m.y - cy, m.x - cx)
    if m.k == "arc_ccw":
        sweep = (a1 - a0) % (2 * math.pi)
    else:
        sweep = (a0 - a1) % (2 * math.pi)
    if sweep < 1e-9:
        sweep = 2 * math.pi  # full circle (start == end)
    return sweep


def _arc_points(sx, sy, sz, m: Move, tol: float) -> list[tuple[float, float, float]]:
    cx, cy = sx + m.i, sy + m.j
    r = math.hypot(m.i, m.j)
    sweep = _arc_sweep(sx, sy, m)
    step = 2 * math.acos(max(-1.0, min(1.0, 1 - tol / r))) if r > tol else sweep
    n = max(2, math.ceil(sweep / max(step, 1e-3)))
    a0 = math.atan2(sy - cy, sx - cx)
    sign = 1 if m.k == "arc_ccw" else -1
    pts = []
    for k in range(1, n + 1):
        a = a0 + sign * sweep * k / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a), sz + (m.z - sz) * k / n))
    pts[-1] = (m.x, m.y, m.z)
    return pts


def _coolant_words(cfg: PostConfig, machine_blocks: dict, warnings: list) -> dict[str, str | None]:
    mc = machine_blocks.get("coolant")
    if mc:
        return {k: mc.get(k) for k in ("flood", "mist", "air", "through_spindle", "off")}
    return {"flood": cfg.coolant.flood, "mist": cfg.coolant.mist, "air": cfg.coolant.air,
            "through_spindle": cfg.coolant.through_spindle, "off": cfg.coolant.stop, "_default": True}


def render(ir: ToolpathIR, cfg: PostConfig, ctx: PostContext) -> PostResult:
    mb = ctx.machine_blocks or {}
    # M-codes the machine owner declares for this machine (e.g. builder-defined coolant codes)
    extra = [w for w in mb.get("declared_mcodes", []) if str(w).upper().startswith("M")]
    e = _Emitter(cfg, extra)
    warnings: list[dict] = []
    op_map: list[dict] = []

    if ir.units != "mm":
        raise PostError("IR must be in millimetres")
    if cfg.output_units != "mm":
        raise PostError("Inch output is not supported in this release")
    if ir.wcs not in cfg.work_offsets:
        raise PostError(f"Work offset {ir.wcs} is not supported by {cfg.key}")

    coolant = _coolant_words(cfg, mb, warnings)
    used_coolants = {o.coolant for o in ir.operations if o.coolant != "off"}
    if used_coolants and coolant.get("_default"):
        _warn(warnings, "PP_COOLANT_UNCONFIRMED",
              "Coolant M-codes are the controller's documented defaults; the machine owner has not confirmed them "
              "for this machine (coolant M-codes are machine-builder defined on many controls).")
    for c in used_coolants:
        if not coolant.get(c):
            raise PostError(f"Coolant mode '{c}' is not configured for this machine/postprocessor")
    tool_seq = mb.get("tool_change") or cfg.tool_change.sequence
    if not mb.get("tool_change"):
        _warn(warnings, "PP_TOOL_CHANGE_UNCONFIRMED",
              "Tool-change sequence uses the controller's documented syntax; the machine-specific tool-change "
              "behaviour (M6 macro, pre-change retract) has not been confirmed in the machine configuration.")
    for key, lines in mb.items():
        if key in ("coolant", "declared_mcodes") or not isinstance(lines, list):
            continue
        for ln in lines:
            e.check_words(str(ln), f"machine block '{key}'")

    # ---------------------------------------------------------------- program start
    if cfg.program.start_delimiter:
        e.raw(cfg.program.start_delimiter)
    if cfg.program.number_template:
        if ctx.program_number is None:
            raise PostError("This controller requires a program number")
        if not (cfg.program.number_min <= ctx.program_number <= cfg.program.number_max):
            raise PostError(f"Program number must be between {cfg.program.number_min} and {cfg.program.number_max}")
        first = cfg.program.number_template.format(number=ctx.program_number)
        e.raw(f"{first} {e.comment(ctx.program_name)}" if cfg.program.name_comment else first)
    elif cfg.program.name_comment:
        e.comment_line(ctx.program_name)
    stamp = (ctx.generated_at or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    e.comment_line(f"POST {cfg.key} {cfg.version}")
    e.comment_line(f"GENERATED {stamp}")
    for info in ctx.header_info:
        e.comment_line(info)
    for t in ctx.tools:
        e.comment_line(f"T{t['number']} D={t['diameter']:g} {t['name']}")

    def expand(blocks: list[str], where: str) -> None:
        for b in blocks:
            if b == "{safety}":
                e.block(*cfg.safety_words)
                e.invalidate_modal()
            elif b == "{work_offset}":
                e.block(ir.wcs)
            elif b.startswith("{machine:"):
                key = b[len("{machine:"):-1]
                lines = mb.get(key)
                if lines:
                    for ln in lines:
                        e.block(ln)
                    e.invalidate_modal()
                    e.pos = {"X": None, "Y": None, "Z": None}
                else:
                    _warn(warnings, "PP_MACHINE_BLOCK_MISSING",
                          f"Machine-specific block '{key}' is not configured for this machine and was omitted "
                          f"({where}). It must be supplied and verified by the machine owner.", block=key)
            elif b == "{coolant_off}":
                if coolant.get("off"):
                    e.block(coolant["off"])
                elif used_coolants:
                    raise PostError("Coolant was used but no coolant-off code is configured")
            elif b == "{spindle_stop}":
                e.block(cfg.spindle.stop)
            elif b == "{retract_clearance}":
                if ir.operations:
                    e.rapid(z=max(o.clearance_z for o in ir.operations))
            elif b == "{length_comp_cancel}":
                if cfg.length_comp_cancel_word:
                    e.block(cfg.length_comp_cancel_word)
            elif b == "{program_end}":
                e.block(cfg.program.end_word)
            elif "{" in b:
                raise PostError(f"Unknown placeholder {b} in {where}")
            else:
                e.block(b)

    expand(cfg.header_blocks, "program start")

    # ---------------------------------------------------------------- operations
    current_tool: int | None = None
    spindle_state: tuple[float, str] | None = None
    coolant_state = "off"
    cc = cfg.canned_cycles
    for op_index, op in enumerate(ir.operations, 1):
        start_line = len(e.lines) + 1
        if not op.moves:
            _warn(warnings, "PP_EMPTY_OPERATION", f"Operation '{op.name}' has no moves and was skipped", operation=op.id)
            continue
        if op.tool_number < 1 or op.tool_number > cfg.tool_change.tool_number_max:
            raise PostError(f"Tool number {op.tool_number} outside 1..{cfg.tool_change.tool_number_max}")
        if ctx.machine_max_rpm and op.spindle_rpm > ctx.machine_max_rpm + 1e-6:
            raise PostError(f"Operation '{op.name}': spindle {op.spindle_rpm:.0f} rpm exceeds machine maximum "
                            f"{ctx.machine_max_rpm:.0f} rpm")
        e.comment_line(f"OP{op_index} {op.name} - T{op.tool_number} {op.tool_type} D{op.tool_diameter:g}")
        need_length_comp = False
        if op.tool_number != current_tool:
            if coolant_state != "off" and coolant.get("off"):
                e.block(coolant["off"])
                coolant_state = "off"
            if spindle_state is not None:
                e.block(cfg.spindle.stop)
                spindle_state = None
            if current_tool is not None:
                pre = mb.get("pre_tool_change")
                if pre:
                    for ln in pre:
                        e.block(ln)
                    e.invalidate_modal()
                else:
                    e.rapid(z=op.clearance_z if e.pos["Z"] is None else max(e.pos["Z"], op.clearance_z))
            for tpl in tool_seq:
                e.block(tpl.format(tool=op.tool_number, h=op.length_offset, d=op.length_offset))
            e.invalidate_modal()
            e.pos = {"X": None, "Y": None, "Z": None}  # position after a tool change is machine-defined
            current_tool = op.tool_number
            need_length_comp = cfg.tool_change.length_comp is not None
        want_spindle = (op.spindle_rpm, op.spindle_dir)
        if spindle_state != want_spindle:
            dirw = cfg.spindle.cw if op.spindle_dir == "cw" else cfg.spindle.ccw
            e.block(cfg.spindle.template.format(rpm=e.num(op.spindle_rpm, cfg.precision.spindle, axis=False), dir=dirw))
            spindle_state = want_spindle
        # first positioning: XY at current height, then Z (with length compensation)
        first = op.moves[0]
        if first.k != "rapid":
            raise PostError(f"Operation '{op.name}' does not start with a rapid positioning move")
        e.rapid(x=first.x, y=first.y)
        if need_length_comp:
            lc = cfg.tool_change.length_comp.format(h=op.length_offset, tool=op.tool_number)
            e.invalidate_modal()
            coords = e._coords(None, None, first.z, force=True)
            e.block(e._motion_word(cfg.rapid_word), lc, *coords)
        else:
            e.rapid(z=first.z)
        if op.coolant != coolant_state:
            e.block(coolant["off"] if op.coolant == "off" else coolant[op.coolant])
            coolant_state = op.coolant

        in_cycle: tuple | None = None
        for m in op.moves[1:]:
            if m.k == "drill":
                cyc_word = {"drill": cc.drill, "drill_dwell": cc.drill_dwell, "peck": cc.peck,
                            "chip_break": cc.chip_break}[m.cycle]
                if cc.enabled and cyc_word:
                    sig = (m.cycle, m.z, m.r, m.q, m.p, m.f, m.retract)
                    if in_cycle == sig and cc.repeat_xy_only:
                        e.block(*e._coords(m.x, m.y, None))
                        continue
                    if in_cycle is not None:
                        e.block(cc.cancel)
                    # position over the hole at the current (initial) height before the cycle call
                    e.rapid(x=m.x, y=m.y)
                    words = []
                    rw = cc.retract_initial if m.retract == "initial" else cc.retract_r_plane
                    if rw:
                        words.append(rw)
                    words.append(cyc_word)
                    words += [f"X{e.num(m.x, cfg.precision.linear)}", f"Y{e.num(m.y, cfg.precision.linear)}",
                              f"Z{e.num(m.z, cfg.precision.linear)}", f"R{e.num(m.r, cfg.precision.linear)}"]
                    if m.cycle in ("peck", "chip_break"):
                        words.append(f"{cc.peck_word}{e.num(m.q, cfg.precision.linear)}")
                    if m.cycle == "drill_dwell":
                        words.append(f"{cc.dwell_word}{e.dwell_value(m.p)}")
                    e.feed = None
                    words.append(e._feed_word(m.f))
                    e.block(*words)
                    e.motion = cyc_word
                    initial_z = e.pos["Z"]
                    e.pos["X"], e.pos["Y"] = m.x, m.y
                    e.pos["Z"] = initial_z if m.retract == "initial" else m.r
                    in_cycle = sig
                    continue
                # expand the cycle into plain moves
                if in_cycle is not None:
                    e.block(cc.cancel)
                    in_cycle = None
                    e.invalidate_modal()
                _expand_drill(e, m, cfg)
                continue
            if in_cycle is not None:
                e.block(cc.cancel)
                in_cycle = None
                e.invalidate_modal()
            if m.k == "rapid":
                cz = e.pos["Z"]
                xy_change = (e.pos["X"] is None or abs(e.pos["X"] - m.x) > 1e-9 or
                             e.pos["Y"] is None or abs(e.pos["Y"] - m.y) > 1e-9)
                if cz is not None and xy_change and abs(cz - m.z) > 1e-9:
                    if m.z > cz:
                        e.rapid(z=m.z)
                        e.rapid(x=m.x, y=m.y)
                    else:
                        e.rapid(x=m.x, y=m.y)
                        e.rapid(z=m.z)
                else:
                    e.rapid(m.x, m.y, m.z)
            elif m.k == "linear":
                e.linear(m.x, m.y, m.z, m.f)
            elif m.k in ("arc_cw", "arc_ccw"):
                sx, sy, sz = e.pos["X"], e.pos["Y"], e.pos["Z"]
                sweep = _arc_sweep(sx, sy, m)
                helical = abs((sz if sz is not None else m.z) - m.z) > 1e-9
                if not cfg.arcs.enabled or (helical and not cfg.arcs.helical_allowed):
                    for px, py, pz in _arc_points(sx, sy, sz, m, cfg.arcs.linearize_tolerance_mm):
                        e.linear(px, py, pz, m.f)
                    continue
                limit = math.radians(min(cfg.arcs.max_sweep_deg, 360.0 if cfg.arcs.full_circle_allowed else 359.0))
                if sweep > limit + 1e-9:
                    # split into equal parts below the limit
                    parts = math.ceil(sweep / limit)
                    cx, cy = sx + m.i, sy + m.j
                    r = math.hypot(m.i, m.j)
                    a0 = math.atan2(sy - cy, sx - cx)
                    sign = 1 if m.k == "arc_ccw" else -1
                    px, py, pz = sx, sy, sz
                    for k in range(1, parts + 1):
                        a = a0 + sign * sweep * k / parts
                        nx, ny = (cx + r * math.cos(a), cy + r * math.sin(a)) if k < parts else (m.x, m.y)
                        nz = sz + (m.z - sz) * k / parts
                        e.arc(m.k == "arc_cw", nx, ny, nz, cx - px, cy - py, m.f)
                        px, py, pz = nx, ny, nz
                else:
                    e.arc(m.k == "arc_cw", m.x, m.y, m.z, m.i, m.j, m.f)
            elif m.k == "dwell":
                e.block(cfg.dwell_word, f"{cfg.dwell_value_word}{e.dwell_value(m.p)}")
        if in_cycle is not None:
            e.block(cc.cancel)
            e.invalidate_modal()
        op_map.append({"operation_id": op.id, "name": op.name, "tool_number": op.tool_number,
                       "start_line": start_line, "end_line": len(e.lines)})

    # ---------------------------------------------------------------- program end
    expand(cfg.footer_blocks, "program end")
    if cfg.program.end_delimiter:
        e.raw(cfg.program.end_delimiter)
    if cfg.max_line_length:
        for k, ln in enumerate(e.lines, 1):
            if len(ln) > cfg.max_line_length:
                raise PostError(f"Line {k} exceeds the controller's maximum length of {cfg.max_line_length}")
    text = "\n".join(e.lines) + "\n"
    return PostResult(text=text, lines=e.lines, operation_map=op_map, warnings=warnings,
                      file_extension=cfg.file_extension)


def _expand_drill(e: _Emitter, m: Move, cfg: PostConfig) -> None:
    """Expand a drill cycle into G0/G1 moves with the same retract semantics as the canned cycle."""
    initial = e.pos["Z"] if e.pos["Z"] is not None else m.r
    e.rapid(x=m.x, y=m.y)
    e.rapid(z=m.r)
    if m.cycle in ("peck", "chip_break") and m.q and m.q > 0:
        depth = m.r
        while depth - m.q > m.z + 1e-9:
            depth -= m.q
            e.linear(m.x, m.y, depth, m.f)
            if m.cycle == "peck":
                e.rapid(z=m.r)
                e.rapid(z=depth + 0.5)
            else:
                e.rapid(z=depth + 0.5)
        e.linear(m.x, m.y, m.z, m.f)
    else:
        e.linear(m.x, m.y, m.z, m.f)
    if m.cycle == "drill_dwell" and m.p:
        e.block(cfg.dwell_word, f"{cfg.dwell_value_word}{e.dwell_value(m.p)}")
    e.rapid(z=initial if m.retract == "initial" else m.r)
