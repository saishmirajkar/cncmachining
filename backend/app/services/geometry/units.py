from __future__ import annotations

import re

MM_PER_UNIT = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "inch": 25.4,
    "ft": 304.8,
    "um": 0.001,
}

SUPPORTED_UNITS = tuple(MM_PER_UNIT)


def to_mm(value: float, unit: str) -> float:
    return value * MM_PER_UNIT[unit]


def correction_scale(declared: str | None, confirmed: str) -> float:
    """Scale to apply to geometry the CAD kernel already converted using the *declared* unit.

    OCCT converts STEP/IGES to millimetres using the unit declared in the file. If the user states that the
    file's numbers are actually in another unit (mislabelled export), the raw numbers are value_mm /
    declared_scale and the corrected value is raw * confirmed_scale. Unit-less formats (STL) have
    ``declared=None`` and are loaded as raw numbers, so the scale is simply the confirmed unit.
    """
    if confirmed not in MM_PER_UNIT:
        raise ValueError(f"unsupported unit {confirmed!r}")
    if declared is None:
        return MM_PER_UNIT[confirmed]
    return MM_PER_UNIT[confirmed] / MM_PER_UNIT[declared]


_STEP_SI = re.compile(r"SI_UNIT\s*\(\s*(\.[A-Z]+\.|\$)\s*,\s*\.METRE\.\s*\)")
_STEP_CONV = re.compile(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", re.I)
_SI_PREFIX = {".MILLI.": "mm", ".CENTI.": "cm", "$": "m", ".MICRO.": "um"}
_CONV_NAMES = {"INCH": "inch", "INCHES": "inch", "FOOT": "ft", "FEET": "ft", "MILLIMETRE": "mm", "MILLIMETER": "mm"}


def detect_step_units(text: str) -> str | None:
    """Return the length unit of the first LENGTH_UNIT definition in a STEP Part 21 file."""
    for line in re.split(r";\s*", text):
        if "LENGTH_UNIT" not in line:
            continue
        m = _STEP_CONV.search(line)
        if m:
            return _CONV_NAMES.get(m.group(1).strip().upper())
        m = _STEP_SI.search(line)
        if m:
            return _SI_PREFIX.get(m.group(1))
    return None


# IGES global section parameter 14 (units flag), ISO/ANSI Y14.26M
_IGES_UNIT_FLAG = {1: "inch", 2: "mm", 4: "ft", 6: "m", 10: "cm", 11: "um"}


def detect_iges_units(text: str) -> str | None:
    g = "".join(line[:72] for line in text.splitlines() if len(line) >= 73 and line[72] == "G")
    if len(g) < 2:
        return None
    pdelim = g[2] if g[0:2] == "1H" else ","
    rdelim = g[6] if g[0:2] == "1H" and g[3:6] == ",1H" else ";"
    fields: list[str] = []
    i, cur = 0, ""
    while i < len(g):
        c = g[i]
        # Hollerith strings nH... may contain delimiters
        m = re.match(r"(\d+)H", g[i:])
        if m and cur.strip() == "":
            n = int(m.group(1))
            start = i + len(m.group(0))
            cur = g[start : start + n]
            i = start + n
            continue
        if c == pdelim or c == rdelim:
            fields.append(cur)
            cur = ""
            if c == rdelim:
                break
        else:
            cur += c
        i += 1
    try:
        flag = int(float(fields[13]))
    except (IndexError, ValueError):
        return None
    if flag == 3 and len(fields) > 14:
        name = fields[14].strip().upper()
        return {"IN": "inch", "INCH": "inch", "MM": "mm", "FT": "ft", "M": "m", "CM": "cm"}.get(name)
    return _IGES_UNIT_FLAG.get(flag)


# DXF $INSUNITS
DXF_INSUNITS = {1: "inch", 2: "ft", 4: "mm", 5: "cm", 6: "m", 13: "um"}
