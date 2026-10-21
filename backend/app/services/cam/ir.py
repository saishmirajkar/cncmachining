"""Machine-neutral toolpath intermediate representation (``cnc-ir/1``).

Coordinates are absolute, millimetres, in the work coordinate system (WCS). Arcs are in the XY plane with
centre offsets I/J relative to the arc start point (helical arcs may change Z). Drill cycles keep their cycle
semantics so a postprocessor can emit a canned cycle or expand it if the controller lacks one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FORMAT = "cnc-ir/1"


class Move(BaseModel):
    k: Literal["rapid", "linear", "arc_cw", "arc_ccw", "drill", "dwell"]
    x: float
    y: float
    z: float
    f: float | None = None  # mm/min for feed moves
    i: float | None = None
    j: float | None = None
    # drill cycle parameters (k == "drill")
    cycle: Literal["drill", "drill_dwell", "peck", "chip_break"] | None = None
    r: float | None = None  # retract plane (WCS Z)
    q: float | None = None  # peck increment
    p: float | None = None  # dwell seconds
    retract: Literal["initial", "r_plane"] | None = None


class IROperation(BaseModel):
    id: str
    name: str
    op_type: str
    tool_number: int
    tool_type: str
    tool_diameter: float
    length_offset: int
    spindle_rpm: float
    spindle_dir: Literal["cw", "ccw"] = "cw"
    coolant: Literal["off", "flood", "mist", "air", "through_spindle"] = "off"
    clearance_z: float
    safe_z: float
    moves: list[Move]
    comment: str = ""


class ToolpathIR(BaseModel):
    format: str = FORMAT
    units: Literal["mm"] = "mm"
    wcs: str
    engine: dict
    stock: dict  # {"min": [x,y,z], "max": [x,y,z]} in WCS
    part_bbox: dict
    operations: list[IROperation] = Field(default_factory=list)

    def stats(self) -> dict:
        n = sum(len(o.moves) for o in self.operations)
        return {"operations": len(self.operations), "moves": n,
                "tools": sorted({o.tool_number for o in self.operations})}
