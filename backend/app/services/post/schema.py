"""Declarative postprocessor configuration schema.

A postprocessor version is pure data. The engine (``engine.py``) only emits words that the configuration
declares; anything else is a configuration error. Machine-specific sequences (parking, tool-change macros) are
*not* part of a postprocessor: they are referenced as ``{machine:<key>}`` placeholders and resolved from the
selected machine configuration, or omitted with a warning when the owner has not supplied them.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

WORD_RE = re.compile(r"\b([GM])(\d+(?:\.\d+)?)\b")


class ProgramFormat(BaseModel):
    start_delimiter: str | None = None  # e.g. "%"
    end_delimiter: str | None = None
    number_template: str | None = None  # e.g. "O{number:04d}"; None = no program number line
    number_min: int = 1
    number_max: int = 9999
    name_comment: bool = True
    end_word: str  # e.g. "M30" or "M2"


class CommentFormat(BaseModel):
    style: Literal["paren", "semicolon"] = "paren"
    uppercase: bool = False
    max_length: int = 120
    # characters outside this class are replaced by "_" (controllers reject e.g. nested parentheses)
    allowed_pattern: str = r"[A-Za-z0-9 .,:/_\-+=#*]"


class LineNumbers(BaseModel):
    enabled: bool = False
    prefix: str = "N"
    start: int = 10
    increment: int = 10
    maximum: int = 99999
    wrap: bool = False


class Precision(BaseModel):
    linear: int = 3
    arc_center: int = 3
    feed: int = 0
    spindle: int = 0
    dwell: int = 3
    leading_zero: bool = True  # "0.5" (True) vs ".5" (False)
    trailing_decimal_point: bool = True  # "X10." style when value is integral and decimals stripped
    strip_trailing_zeros: bool = True


class ToolChange(BaseModel):
    # templates may use {tool}, {tool:02d}, {h}, {d}; must only contain declared words
    sequence: list[str]  # e.g. ["T{tool} M6"]
    length_comp: str | None = None  # e.g. "G43 H{h}"; None when the control applies offsets with the T/D call
    length_comp_with_first_z: bool = True  # emit length comp together with the first Z move after the change
    tool_number_max: int = 99


class Spindle(BaseModel):
    cw: str = "M3"
    ccw: str = "M4"
    stop: str = "M5"
    template: str = "S{rpm} {dir}"


class Coolant(BaseModel):
    flood: str | None = "M8"
    mist: str | None = None
    air: str | None = None
    through_spindle: str | None = None
    # named "stop" (not "off"): YAML 1.1 parses an unquoted ``off`` key as boolean False
    stop: str | None = "M9"


class Arcs(BaseModel):
    enabled: bool = True
    format: Literal["IJK", "R"] = "IJK"
    full_circle_allowed: bool = True
    helical_allowed: bool = True  # arcs with simultaneous Z motion (helical interpolation)
    max_sweep_deg: float = 360.0
    linearize_tolerance_mm: float = 0.005  # only used when arcs are disabled or a sweep must be split


class CannedCycles(BaseModel):
    enabled: bool = True
    drill: str | None = "G81"
    drill_dwell: str | None = "G82"
    peck: str | None = "G83"
    chip_break: str | None = "G73"
    cancel: str = "G80"
    retract_initial: str | None = "G98"
    retract_r_plane: str | None = "G99"
    peck_word: str = "Q"
    dwell_word: str = "P"
    dwell_unit: Literal["s", "ms"] = "s"
    repeat_xy_only: bool = True  # subsequent holes in the same cycle only output X/Y


class PostConfig(BaseModel):
    key: str
    version: str
    name: str
    controller_family: str
    dialect: str
    description: str = ""
    file_extension: str
    output_units: Literal["mm", "inch"] = "mm"
    unit_words: dict[str, str] = {"mm": "G21", "inch": "G20"}
    absolute_word: str = "G90"
    plane_xy_word: str = "G17"
    feed_per_minute_word: str = "G94"
    rapid_word: str = "G0"
    linear_word: str = "G1"
    cw_arc_word: str = "G2"
    ccw_arc_word: str = "G3"
    cutter_comp_cancel_word: str | None = "G40"
    length_comp_cancel_word: str | None = "G49"
    dwell_word: str = "G4"
    dwell_value_word: str = "P"  # SINUMERIK uses "G4 F<seconds>"
    work_offsets: list[str] = ["G54", "G55", "G56", "G57", "G58", "G59"]
    program: ProgramFormat
    comments: CommentFormat = CommentFormat()
    line_numbers: LineNumbers = LineNumbers()
    precision: Precision = Precision()
    tool_change: ToolChange
    spindle: Spindle = Spindle()
    coolant: Coolant = Coolant()
    arcs: Arcs = Arcs()
    canned_cycles: CannedCycles = CannedCycles()
    # Ordered skeletons. Allowed entries: literal blocks (declared words only), "{safety}", "{work_offset}",
    # "{machine:<key>}" (resolved from the machine configuration), "{program_end}".
    header_blocks: list[str] = Field(default_factory=lambda: ["{safety}"])
    footer_blocks: list[str]
    safety_words: list[str]  # emitted by "{safety}" e.g. ["G17", "G21", "G40", "G49", "G80", "G90", "G94"]
    supported_gcodes: list[str]
    supported_mcodes: list[str]
    max_line_length: int | None = None
    suppress_modal_repeats: bool = True
    known_limitations: list[str] = []
    documentation_refs: list[str] = []

    @model_validator(mode="after")
    def _only_declared_words(self) -> "PostConfig":
        declared = set(self.supported_gcodes) | set(self.supported_mcodes)
        texts = [self.absolute_word, self.plane_xy_word, self.feed_per_minute_word, self.rapid_word,
                 self.linear_word, self.cw_arc_word, self.ccw_arc_word, self.dwell_word, self.program.end_word,
                 self.spindle.cw, self.spindle.ccw, self.spindle.stop,
                 self.canned_cycles.cancel, *self.unit_words.values(), *self.work_offsets, *self.safety_words,
                 *self.tool_change.sequence, *(b for b in self.header_blocks + self.footer_blocks if "{" not in b)]
        for opt in (self.cutter_comp_cancel_word, self.length_comp_cancel_word, self.tool_change.length_comp,
                    self.coolant.flood, self.coolant.stop, self.coolant.mist, self.coolant.air, self.coolant.through_spindle,
                    self.canned_cycles.drill, self.canned_cycles.drill_dwell, self.canned_cycles.peck,
                    self.canned_cycles.chip_break, self.canned_cycles.retract_initial,
                    self.canned_cycles.retract_r_plane):
            if opt:
                texts.append(opt)
        for t in texts:
            for letter, num in WORD_RE.findall(t):
                w = f"{letter}{_norm_num(num)}"
                if w not in {_norm_word(d) for d in declared}:
                    raise ValueError(f"word {w} used in configuration but not declared as supported")
        return self


def _norm_num(num: str) -> str:
    if "." in num:
        a, b = num.split(".", 1)
        return f"{int(a)}.{b}"
    return str(int(num))


def _norm_word(w: str) -> str:
    m = WORD_RE.match(w)
    if not m:
        return w
    return f"{m.group(1)}{_norm_num(m.group(2))}"
