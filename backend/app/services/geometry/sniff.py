"""Content-based file-type detection. The extension must agree with the detected content."""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

EXTENSIONS = {
    ".step": "step",
    ".stp": "step",
    ".iges": "iges",
    ".igs": "iges",
    ".stl": "stl",
    ".dxf": "dxf",
    ".fcstd": "fcstd",
}


class FileRejected(ValueError):
    pass


@dataclass
class SniffResult:
    file_format: str
    detail: str


def _head(path: Path, n: int = 4096) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _is_step(head: bytes) -> bool:
    return head.lstrip(b"\xef\xbb\xbf \r\n\t").upper().startswith(b"ISO-10303-21")


def _is_iges(head: bytes) -> bool:
    lines = head.replace(b"\r\n", b"\n").split(b"\n")
    good = [ln for ln in lines[:3] if len(ln) >= 73]
    return bool(good) and all(ln[72:73] in (b"S", b"G", b"F") for ln in good) and good[0][72:73] in (b"S", b"F")


def _stl_kind(path: Path) -> str | None:
    size = path.stat().st_size
    head = _head(path, 512)
    if size >= 84:
        (n,) = struct.unpack("<I", head[80:84])
        if 84 + 50 * n == size:
            return "binary"
    txt = head.lstrip().lower()
    if txt.startswith(b"solid") and (b"facet" in txt or size < 512):
        return "ascii"
    return None


def _is_dxf(head: bytes) -> bool:
    txt = head.decode("latin-1", errors="ignore").replace("\r\n", "\n")
    lines = [ln.strip() for ln in txt.split("\n")[:8]]
    if head.startswith(b"AutoCAD Binary DXF"):
        return True
    return len(lines) >= 2 and lines[0] == "0" and lines[1] in ("SECTION",)


def _is_fcstd(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return "Document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def sniff(path: Path, original_filename: str) -> SniffResult:
    ext = Path(original_filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise FileRejected(
            f"Unsupported file extension '{ext}'. Supported: {', '.join(sorted(EXTENSIONS))}"
        )
    expected = EXTENSIONS[ext]
    if path.stat().st_size == 0:
        raise FileRejected("The file is empty")
    head = _head(path)
    if expected == "step" and _is_step(head):
        return SniffResult("step", "ISO 10303-21 header found")
    if expected == "iges" and _is_iges(head):
        return SniffResult("iges", "IGES fixed-format start/global section found")
    if expected == "stl":
        kind = _stl_kind(path)
        if kind:
            return SniffResult("stl", f"{kind} STL")
    if expected == "dxf" and _is_dxf(head):
        return SniffResult("dxf", "DXF section structure found")
    if expected == "fcstd" and _is_fcstd(path):
        return SniffResult("fcstd", "FreeCAD zip archive with Document.xml")
    raise FileRejected(
        f"File content does not match the '{ext}' extension (expected a valid {expected.upper()} file)"
    )
