"""B-rep loading through Open CASCADE (OCP bindings).

STEP/IGES are converted to millimetres by OCCT using the unit declared in the file. FreeCAD documents are
read *as zip archives*: only the BREP payloads are parsed; FreeCAD itself never opens user documents, so
embedded Python features or macros are never executed.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Trsf
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader
from OCP.Message import Message
from OCP.STEPControl import STEPControl_Reader
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape

from app.services.geometry.units import detect_iges_units, detect_step_units

_silenced = False


def silence_occt() -> None:
    """OCCT prints transfer statistics to stdout; remove the default printers once per process."""
    global _silenced
    if _silenced:
        return
    try:
        messenger = Message.DefaultMessenger_s()
        printers = messenger.Printers()
        for i in range(printers.Length(), 0, -1):
            messenger.RemovePrinter(printers.Value(i))
    except Exception:  # pragma: no cover - binding differences
        pass
    _silenced = True


class GeometryLoadError(ValueError):
    pass


@dataclass
class LoadedBrep:
    shape: TopoDS_Shape
    declared_units: str | None
    notes: list[str] = field(default_factory=list)


def _read_text_head(path: Path, n: int = 2_000_000) -> str:
    with open(path, "rb") as f:
        return f.read(n).decode("latin-1", errors="ignore")


def load_step(path: Path) -> LoadedBrep:
    silence_occt()
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise GeometryLoadError(f"STEP file could not be parsed (reader status {status.name})")
    n = reader.TransferRoots()
    shape = reader.OneShape()
    if n == 0 or shape.IsNull():
        raise GeometryLoadError("STEP file contains no transferable geometry")
    return LoadedBrep(shape, detect_step_units(_read_text_head(path)))


def load_iges(path: Path) -> LoadedBrep:
    silence_occt()
    reader = IGESControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise GeometryLoadError(f"IGES file could not be parsed (reader status {status.name})")
    n = reader.TransferRoots()
    shape = reader.OneShape()
    if n == 0 or shape.IsNull():
        raise GeometryLoadError("IGES file contains no transferable geometry")
    notes = ["IGES often stores trimmed surfaces rather than solids; solid reconstruction is not attempted."]
    return LoadedBrep(shape, detect_iges_units(_read_text_head(path, 200_000)), notes)


def _read_brep_bytes(data: bytes) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    builder = BRep_Builder()
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".brp") as tmp:
        tmp.write(data)
        tmp.flush()
        if not BRepTools.Read_s(shape, tmp.name, builder):
            raise GeometryLoadError("BREP payload could not be read")
    return shape


MAX_FCSTD_MEMBER = 200 * 1024 * 1024


def load_fcstd(path: Path) -> LoadedBrep:
    """Read the visible solid shapes of a FreeCAD document without executing it.

    Selection: objects whose ``Shape`` property references a BREP file and which are marked visible in
    ``GuiDocument.xml``. Without GUI data, the largest shape payload is used and a note is recorded.
    """
    silence_occt()
    notes = ["FreeCAD document read as archive; parametric history and expressions are ignored."]
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise GeometryLoadError("FCStd archive is corrupt") from e
    with z:
        names = set(z.namelist())
        for info in z.infolist():
            if info.file_size > MAX_FCSTD_MEMBER:
                raise GeometryLoadError("FCStd archive member exceeds size limit")
        doc = ET.fromstring(z.read("Document.xml"))
        shape_files: dict[str, str] = {}
        for obj in doc.iter("Object"):
            name = obj.get("name")
            for prop in obj.iter("Property"):
                if prop.get("name") == "Shape":
                    part = prop.find("Part")
                    if part is not None and part.get("file") in names:
                        shape_files[name] = part.get("file")
        if not shape_files:
            raise GeometryLoadError("FCStd document contains no shape data")
        visible: set[str] = set()
        if "GuiDocument.xml" in names:
            gui = ET.fromstring(z.read("GuiDocument.xml"))
            for vp in gui.iter("ViewProvider"):
                for prop in vp.iter("Property"):
                    if prop.get("name") == "Visibility":
                        b = prop.find("Bool")
                        if b is not None and b.get("value") == "true":
                            visible.add(vp.get("name"))
        chosen = [n for n in shape_files if n in visible]
        if not chosen:
            largest = max(shape_files, key=lambda n: z.getinfo(shape_files[n]).file_size)
            chosen = [largest]
            notes.append(f"No visibility data; used the largest shape payload ('{largest}').")
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for n in chosen:
            builder.Add(compound, _read_brep_bytes(z.read(shape_files[n])))
        notes.append(f"Objects used: {', '.join(chosen)}")
    return LoadedBrep(compound, "mm", notes)


def scale_shape(shape: TopoDS_Shape, factor: float) -> TopoDS_Shape:
    if abs(factor - 1.0) < 1e-12:
        return shape
    t = gp_Trsf()
    from OCP.gp import gp_Pnt

    t.SetScale(gp_Pnt(0, 0, 0), factor)
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def load_brep_file(path: Path, file_format: str) -> LoadedBrep:
    if file_format == "step":
        return load_step(path)
    if file_format == "iges":
        return load_iges(path)
    if file_format == "fcstd":
        return load_fcstd(path)
    raise GeometryLoadError(f"{file_format} is not a B-rep format")


def write_step_mm(shape: TopoDS_Shape, path: Path) -> None:
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    silence_occt()
    w = STEPControl_Writer()
    w.Model().SetWriteLengthUnit(1.0)
    w.Transfer(shape, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise GeometryLoadError("could not write normalized STEP")


def read_step_bytes(data: bytes) -> TopoDS_Shape:  # pragma: no cover - convenience
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".step") as tmp:
        tmp.write(data)
        tmp.flush()
        return load_step(Path(tmp.name)).shape

