"""FreeCAD CAM adapter: runs ``freecad_runner.py`` under FreeCAD's interpreter and converts Path commands to IR."""

from __future__ import annotations

import json
import os
import resource
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.services.cam.base import CamEngineError, CamOperationResult, CamRunResult
from app.services.cam.ir import Move

RUNNER_DIR = Path(__file__).parent

# FreeCAD Path operations this adapter drives (see freecad_runner.build_and_run)
SUPPORTED = frozenset({"face", "pocket", "adaptive", "contour", "drill", "helix"})


def _limits(cpu_s: int, mem_mb: int):
    def fn():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setsid()
    return fn


class FreeCADEngine:
    name = "freecad-cam"
    supported_operations = SUPPORTED

    def __init__(self, python: str | None = None, lib_dir: str | None = None) -> None:
        s = get_settings()
        self.python = python or s.freecad_python
        self.lib_dir = lib_dir or s.freecad_lib_dir

    def _env(self) -> dict:
        return {"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "LANG": "C.UTF-8",
                "FREECAD_LIB_DIR": self.lib_dir, "QT_QPA_PLATFORM": "offscreen"}

    def available(self) -> tuple[bool, str]:
        return _probe(self.python, self.lib_dir)

    def run(self, spec: dict, timeout_s: int, mem_mb: int) -> CamRunResult:
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="cnc-cam-") as td:
            sp, rp = Path(td) / "spec.json", Path(td) / "result.json"
            sp.write_text(json.dumps(spec))
            cmd = [self.python, "-c",
                   f"import sys; sys.path.insert(0, {str(RUNNER_DIR)!r}); import freecad_runner; freecad_runner.main()",
                   str(sp), str(rp)]
            try:
                proc = subprocess.run(cmd, cwd=td, env=self._env(), capture_output=True, text=True,
                                      timeout=timeout_s, preexec_fn=_limits(timeout_s, mem_mb))
            except subprocess.TimeoutExpired:
                raise CamEngineError(f"CAM computation exceeded the {timeout_s}s time limit and was stopped")
            except FileNotFoundError:
                raise CamEngineError(f"FreeCAD interpreter not found at {self.python}")
            if not rp.exists():
                sig = f" (signal {-proc.returncode})" if proc.returncode < 0 else ""
                raise CamEngineError(f"CAM runner exited without a result{sig}: {(proc.stderr or '')[-1500:]}")
            out = json.loads(rp.read_text())
        if out["status"] != "ok":
            raise CamEngineError(out.get("error", "CAM runner failed"))
        r = out["result"]
        ops = [CamOperationResult(id=o["id"], status=o["status"], messages=o["messages"], commands=o["commands"],
                                  effective=o.get("effective", {})) for o in r["operations"]]
        return CamRunResult(engine=r["engine"], engine_version=r["engine_version"], operations=ops,
                            duration_s=time.perf_counter() - t0)


@lru_cache(maxsize=4)
def _probe(python: str, lib_dir: str) -> tuple[bool, str]:
    if not Path(python).exists():
        return False, f"FreeCAD interpreter not found at {python}"
    code = ("import sys; sys.path.append(%r)\n"
            "def m():\n import FreeCAD, Path.Op.Adaptive, Path.Op.Drilling, Path.Op.Profile, area\n"
            " print('.'.join(FreeCAD.Version()[:3]))\nm()" % lib_dir)
    try:
        p = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=60,
                           env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir()})
    except Exception as e:  # pragma: no cover
        return False, str(e)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout)[-500:]
    return True, f"FreeCAD {p.stdout.strip().splitlines()[-1]} with CAM workbench and libarea"


# ------------------------------------------------------------------------- Path commands -> IR

CYCLES = {"G81": "drill", "G82": "drill_dwell", "G83": "peck", "G73": "chip_break"}
IGNORED = {"M3", "M4", "M5", "M6", "M7", "M8", "M9", "G90", "G91", "G17", "G21", "G40", "G49", "G54", "G94", "G80"}


class ConversionError(ValueError):
    pass


def commands_to_moves(commands: list, clearance_z: float) -> list[Move]:
    """Convert FreeCAD ``[name, params, annotations]`` commands into absolute IR moves.

    * Feeds: FreeCAD stores mm/s; IR uses mm/min (x60).
    * Moves before X/Y are known only set Z. The first fully known position is reached by an explicit rapid at
      ``max(clearance_z, current Z)`` so every operation starts with a well-defined positioning move.
    * G98/G99 retract mode is taken from the command or its ``RetractMode`` annotation (FreeCAD default G98).
    * Spindle/coolant/modal commands are dropped: the postprocessor emits them from the operation definition.
    """
    x = y = z = None
    feed = None
    retract_mode = "initial"
    moves: list[Move] = []
    for name, params, ann in commands:
        if name.startswith("("):
            continue
        name = name.upper()
        if name in ("G98", "G99"):
            retract_mode = "initial" if name == "G98" else "r_plane"
            continue
        if name in IGNORED:
            continue
        motion = name in ("G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03") or name in CYCLES
        if not motion and name not in ("G4", "G04"):
            raise ConversionError(f"unsupported command {name} from CAM engine")
        if "F" in params and name not in ("G0", "G00") and params["F"] > 0:
            feed = params["F"] * 60.0
        nx, ny, nz = params.get("X", x), params.get("Y", y), params.get("Z", z)
        if name in ("G4", "G04"):
            if x is None:
                raise ConversionError("dwell before any positioning move")
            moves.append(Move(k="dwell", x=x, y=y, z=z, p=params.get("P", 0.0)))
            continue
        if nx is None or ny is None:
            if name not in ("G0", "G00"):
                raise ConversionError(f"{name} before the XY position is known")
            z = nz
            continue
        if not moves:
            start_z = max(clearance_z, z if z is not None else clearance_z)
            moves.append(Move(k="rapid", x=nx, y=ny, z=start_z))
            x, y, z = nx, ny, start_z
            if nz is None:
                nz = start_z
        if name in ("G0", "G00"):
            if (nx, ny, nz) != (x, y, z):
                moves.append(Move(k="rapid", x=nx, y=ny, z=nz))
        elif name in ("G1", "G01"):
            if feed is None:
                raise ConversionError("feed move without a feed rate")
            moves.append(Move(k="linear", x=nx, y=ny, z=nz, f=feed))
        elif name in ("G2", "G02", "G3", "G03"):
            if feed is None:
                raise ConversionError("arc without a feed rate")
            moves.append(Move(k="arc_cw" if name in ("G2", "G02") else "arc_ccw", x=nx, y=ny, z=nz,
                              i=params.get("I", 0.0), j=params.get("J", 0.0), f=feed))
        else:  # canned cycle
            rm = params.get("RetractMode") or (ann or {}).get("RetractMode")
            mode = {"G98": "initial", "G99": "r_plane"}.get(rm, retract_mode)
            if "Z" not in params or "R" not in params:
                raise ConversionError(f"{name} without Z/R")
            if feed is None:
                raise ConversionError(f"{name} without a feed rate")
            moves.append(Move(k="drill", cycle=CYCLES[name], x=nx, y=ny, z=params["Z"], r=params["R"],
                              q=params.get("Q"), p=params.get("P"), f=feed, retract=mode))
            nz = z if mode == "initial" else params["R"]  # tool position after the cycle
        x, y, z = nx, ny, nz
    return moves
