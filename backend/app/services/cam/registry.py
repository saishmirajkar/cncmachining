from __future__ import annotations

from app.services.cam.freecad_engine import FreeCADEngine

_ENGINES = {"freecad-cam": FreeCADEngine}


def get_engine(name: str = "freecad-cam"):
    try:
        return _ENGINES[name]()
    except KeyError:
        raise ValueError(f"Unknown CAM engine {name}")


def engine_status() -> list[dict]:
    out = []
    for name, cls in _ENGINES.items():
        eng = cls()
        ok, detail = eng.available()
        out.append({"name": name, "available": ok, "detail": detail,
                    "supported_operations": sorted(eng.supported_operations)})
    return out
