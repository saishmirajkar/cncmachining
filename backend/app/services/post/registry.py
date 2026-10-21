"""Postprocessor registry: bundled YAML configurations, validated and content-hashed.

Configurations are imported into ``PostprocessorVersion`` rows by the seed; at runtime the *database copy*
(immutable, hashed) is used, so a program always references the exact configuration that produced it.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import yaml

from app.services.post.schema import PostConfig

CONFIG_DIR = Path(__file__).parent / "configs"


def canonical_json(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"))


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(canonical_json(cfg).encode()).hexdigest()


def parse_config(data: dict) -> PostConfig:
    return PostConfig.model_validate(data)


@lru_cache
def bundled() -> dict[str, PostConfig]:
    out = {}
    for p in sorted(CONFIG_DIR.glob("*.yaml")):
        cfg = parse_config(yaml.safe_load(p.read_text()))
        out[f"{cfg.key}@{cfg.version}"] = cfg
    return out


def get(key: str, version: str) -> PostConfig:
    return bundled()[f"{key}@{version}"]
