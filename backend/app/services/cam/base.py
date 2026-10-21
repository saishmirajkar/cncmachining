"""CAM engine adapter interface. The platform talks to CAM engines only through this interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CamOperationResult:
    id: str
    status: str  # ok | error
    messages: list[str] = field(default_factory=list)
    commands: list = field(default_factory=list)  # engine-native commands, converted by the adapter
    effective: dict = field(default_factory=dict)


@dataclass
class CamRunResult:
    engine: str
    engine_version: str
    operations: list[CamOperationResult]
    duration_s: float


class CamEngineError(RuntimeError):
    pass


class CamEngine(Protocol):
    name: str
    supported_operations: frozenset[str]

    def available(self) -> tuple[bool, str]:
        """(is_available, detail) – verifies the runtime can actually be started."""

    def run(self, spec: dict, timeout_s: int, mem_mb: int) -> CamRunResult:
        """Compute toolpaths for ``spec`` (see freecad_runner.py for the spec format)."""
