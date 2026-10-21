"""Generate the known test parts used by the test-suite (run: ``python tests/fixtures/make_fixtures.py``).

All dimensions are in millimetres unless the file name says otherwise. The expected features of each part are
documented in ``EXPECTED`` and asserted by ``tests/test_geometry.py``.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

HERE = Path(__file__).parent


def box(x, y, z, dx, dy, dz):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def zcyl(x, y, z, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r, h).Shape()


def cut(a, b):
    op = BRepAlgoAPI_Cut(a, b)
    op.Build()
    assert op.IsDone()
    return op.Shape()


def fuse(a, b):
    op = BRepAlgoAPI_Fuse(a, b)
    op.Build()
    assert op.IsDone()
    return op.Shape()


def drill_point(x, y, z_bottom, r):
    """118 deg drill point cone with its apex below z_bottom, base at z_bottom."""
    h = r / math.tan(math.radians(59))
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, z_bottom - h), gp_Dir(0, 0, 1)), 0.0, r, h).Shape()


def write_step(shape, path: Path, unit: str = "MM") -> None:
    Interface_Static.SetCVal_s("write.step.unit", unit)
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    w = STEPControl_Writer()
    # OCCT 8 ignores "write.step.unit"; the model's write length unit (in mm) controls the declared unit
    w.Model().SetWriteLengthUnit({"MM": 1.0, "IN": 25.4}[unit])
    w.Transfer(shape, STEPControl_AsIs)
    assert w.Write(str(path)) == IFSelect_RetDone
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    if unit == "IN":
        # OCCT 8.0 writes inch values but still declares a millimetre length unit; declare INCH explicitly
        # (ISO 10303-41 conversion-based unit) so the file is self-consistent.
        text = path.read_text()
        mm = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )"
        assert mm in text
        text = text.replace(
            mm,
            "( CONVERSION_BASED_UNIT('INCH',#990001) LENGTH_UNIT() NAMED_UNIT(#990002) )",
        ).replace(
            "ENDSEC;\nEND-ISO",
            "#990001 = LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#990003);\n"
            "#990002 = DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);\n"
            "#990003 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\nENDSEC;\nEND-ISO",
        )
        path.write_text(text)


def make_block():
    return box(0, 0, 0, 100, 60, 25)


def make_drilled_plate():
    s = box(0, 0, 0, 120, 80, 12)
    for x, y in [(10, 10), (110, 10), (10, 70), (110, 70)]:  # 4x clearance M6, through
        s = cut(s, zcyl(x, y, -1, 3.3, 14))
    for x in (40, 60, 80):  # linear pattern of 3 tap-drill M8 blind holes, 8 deep, drill point
        s = cut(s, zcyl(x, 40, 4, 3.4, 9))
        s = cut(s, drill_point(x, 40, 4, 3.4))
    # counterbored M6 SHCS hole: 11 dia x 6.5 deep + 6.6 through
    s = cut(s, zcyl(60, 15, 5.5, 5.5, 7))
    s = cut(s, zcyl(60, 15, -1, 3.3, 14))
    return s


def make_pocketed_part():
    s = box(0, 0, 0, 100, 80, 30)
    # closed pocket 50 x 30 x 10 deep with R5 vertical corner fillets
    p = box(25, 25, 20, 50, 30, 11)
    mk = BRepFilletAPI_MakeFillet(p)
    ex = TopExp_Explorer(p, TopAbs_EDGE)
    while ex.More():
        e = TopoDS.Edge(ex.Current())
        from OCP.BRepAdaptor import BRepAdaptor_Curve

        c = BRepAdaptor_Curve(e)
        a, b = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        if abs(a.X() - b.X()) < 1e-9 and abs(a.Y() - b.Y()) < 1e-9:  # vertical edge
            mk.Add(5.0, e)
        ex.Next()
    s = cut(s, mk.Shape())
    # open slot 10 wide x 8 deep running out of the +X face
    s = cut(s, box(85, 35, 22, 16, 10, 9))
    # shoulder step along the -Y side: 100 long x 10 wide x 5 deep
    s = cut(s, box(-1, -1, 25, 102, 11, 6))
    # two through holes 8 dia
    s = cut(s, zcyl(12, 70, -1, 4, 32))
    s = cut(s, zcyl(88, 70, -1, 4, 32))
    # side hole (axis along X) 6 dia, 20 deep from the -X face -> needs another orientation
    side = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(-1, 40, 10), gp_Dir(1, 0, 0)), 3, 21).Shape()
    s = cut(s, side)
    return s


def make_stepped_shaft():
    s = zcyl(0, 0, 0, 20, 30)
    s = fuse(s, zcyl(0, 0, 30, 12.5, 40))
    s = fuse(s, zcyl(0, 0, 70, 8, 20))
    return s


def write_stl(shape, path: Path) -> None:
    BRepMesh_IncrementalMesh(shape, 0.1, False, 0.3, True)
    w = StlAPI_Writer()
    w.ASCIIMode = False
    assert w.Write(shape, str(path))


def write_dxf_plate(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010", setup=False)
    doc.header["$INSUNITS"] = 4  # millimetres
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (120, 0), (120, 80), (0, 80)], close=True, dxfattribs={"layer": "OUTLINE"})
    for x, y in [(10, 10), (110, 10), (10, 70), (110, 70)]:
        msp.add_circle((x, y), 3.3, dxfattribs={"layer": "HOLES"})
    doc.saveas(path)


EXPECTED = {
    "rect_block.step": {"bbox": (100, 60, 25), "holes": 0, "pockets": 0},
    "drilled_plate.step": {"bbox": (120, 80, 12), "through_holes": 5, "blind_holes": 3, "counterbores": 1},
    "pocketed_part.step": {"bbox": (100, 80, 30), "closed_pockets": 1, "pocket_corner_radius": 5.0},
    "stepped_shaft.step": {"bbox": (40, 40, 90)},
    "rect_block_inch.step": {"bbox": (100, 60, 25), "declared_unit": "inch"},
}


def main() -> None:
    write_step(make_block(), HERE / "rect_block.step")
    write_step(make_drilled_plate(), HERE / "drilled_plate.step")
    write_step(make_pocketed_part(), HERE / "pocketed_part.step")
    write_step(make_stepped_shaft(), HERE / "stepped_shaft.step")
    write_step(make_block(), HERE / "rect_block_inch.step", unit="IN")
    Interface_Static.SetCVal_s("write.iges.unit", "MM")
    iw = IGESControl_Writer("MM", 1)
    iw.AddShape(make_drilled_plate())
    iw.ComputeModel()
    assert iw.Write(str(HERE / "drilled_plate.igs"))
    write_stl(make_block(), HERE / "rect_block.stl")
    write_dxf_plate(HERE / "plate_profile.dxf")
    (HERE / "corrupt.step").write_text("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('broken'),'2;1');\nENDSEC;\nDATA;\n#1=GARBAGE(;\n")
    (HERE / "empty.stl").write_bytes(b"\0" * 80 + struct.pack("<I", 0))
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
