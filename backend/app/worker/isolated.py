"""Run untrusted-input processing in a child process with resource limits.

Usage (internal): ``python -m app.worker.isolated <kind> <request.json> <result.json>``. The parent
(``run_isolated``) enforces a wall-clock timeout; the child applies CPU-time and address-space limits.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from app.core.config import BACKEND_DIR


class IsolatedTaskError(RuntimeError):
    pass


def _limit(cpu_s: int, mem_mb: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
    if mem_mb > 0:
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_isolated(kind: str, request: dict, timeout_s: int, mem_mb: int = 4096) -> dict:
    with tempfile.TemporaryDirectory(prefix="cnc-iso-") as td:
        req = Path(td) / "request.json"
        res = Path(td) / "result.json"
        req.write_text(json.dumps(request))
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PYTHONPATH") or k.startswith("CNC_")}
        env["PYTHONPATH"] = str(BACKEND_DIR)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "app.worker.isolated", kind, str(req), str(res)],
                cwd=td, env=env, capture_output=True, text=True, timeout=timeout_s,
                preexec_fn=lambda: _limit(timeout_s, mem_mb),
            )
        except subprocess.TimeoutExpired:
            raise IsolatedTaskError(f"{kind} exceeded the {timeout_s}s time limit")
        if not res.exists():
            tail = (proc.stderr or "")[-2000:]
            if proc.returncode < 0:
                raise IsolatedTaskError(f"{kind} process was killed (signal {-proc.returncode}); "
                                        "resource limit exceeded or crash in native code")
            raise IsolatedTaskError(f"{kind} failed without result (exit {proc.returncode}): {tail}")
        out = json.loads(res.read_text())
        if out.get("status") != "ok":
            raise IsolatedTaskError(out.get("error", "unknown error"))
        return out["result"]


def _geometry(req: dict) -> dict:
    import base64

    from app.services.geometry.service import analyze_file

    r = analyze_file(Path(req["path"]), req["file_format"], req.get("confirmed_units"),
                     req.get("orientation", "+Z"), req.get("rot_z_deg", 0.0))
    return {
        "representation": r.representation, "declared_units": r.declared_units,
        "unit_scale_to_mm": r.unit_scale_to_mm, "summary": r.summary, "features": r.features,
        "warnings": r.warnings, "limitations": r.limitations, "mesh": r.mesh,
        "normalized_step_b64": base64.b64encode(r.normalized_step).decode() if r.normalized_step else None,
    }


KINDS = {"geometry": _geometry}


def main() -> None:  # pragma: no cover - executed in the child process
    kind, req_path, res_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    try:
        result = KINDS[kind](json.loads(req_path.read_text()))
        res_path.write_text(json.dumps({"status": "ok", "result": result}))
    except MemoryError:
        res_path.write_text(json.dumps({"status": "error", "error": f"{kind}: memory limit exceeded"}))
    except Exception as e:
        res_path.write_text(json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}",
                                        "trace": traceback.format_exc()[-4000:]}))


if __name__ == "__main__":  # pragma: no cover
    main()
