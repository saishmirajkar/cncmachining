"""Object storage abstraction.

``LocalStorage`` stores objects under ``CNC_STORAGE_DIR``; keys are generated server-side (never from user
filenames) so path traversal is impossible. An S3-compatible backend can implement the same interface.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings


class ObjectStorage(Protocol):
    def put_file(self, src: Path, prefix: str, suffix: str = "") -> str: ...
    def put_bytes(self, data: bytes, prefix: str, suffix: str = "") -> str: ...
    def open(self, key: str) -> BinaryIO: ...
    def read_bytes(self, key: str) -> bytes: ...
    def local_path(self, key: str) -> Path: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _new_key(self, prefix: str, suffix: str) -> str:
        safe_prefix = "/".join(p for p in prefix.split("/") if p and p not in (".", ".."))
        safe_suffix = "".join(c for c in suffix if c.isalnum() or c == ".")[:12]
        return f"{safe_prefix}/{uuid.uuid4().hex}{safe_suffix}"

    def local_path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root) + os.sep):
            raise ValueError("invalid storage key")
        return p

    def put_file(self, src: Path, prefix: str, suffix: str = "") -> str:
        key = self._new_key(prefix, suffix)
        dst = self.local_path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o440)
        return key

    def put_bytes(self, data: bytes, prefix: str, suffix: str = "") -> str:
        key = self._new_key(prefix, suffix)
        dst = self.local_path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        os.chmod(dst, 0o440)
        return key

    def open(self, key: str) -> BinaryIO:
        return open(self.local_path(key), "rb")

    def read_bytes(self, key: str) -> bytes:
        return self.local_path(key).read_bytes()

    def delete(self, key: str) -> None:
        p = self.local_path(key)
        if p.exists():
            os.chmod(p, 0o640)
            p.unlink()


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    root = get_settings().storage_dir
    if _storage is None or _storage.root != Path(root).resolve():
        _storage = LocalStorage(Path(root))
    return _storage


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
