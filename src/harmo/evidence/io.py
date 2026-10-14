"""Canonical hashing and atomic JSON I/O for evidence contracts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes(paths: Iterable[str]) -> Dict[str, str]:
    """Hash every input path, propagating missing or unreadable-file errors."""
    return {path: sha256_file(path) for path in paths}


def canonical_json_bytes(value: Any) -> bytes:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_contract(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def read_json(path: str) -> Dict[str, Any]:
    def reject_nonfinite(value: str) -> None:
        raise ValueError("non-finite JSON number is not allowed: {}".format(value))

    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle, parse_constant=reject_nonfinite)
    if not isinstance(value, dict):
        raise ValueError("contract JSON root must be an object")
    return value


def write_json_atomic(path: str, value: Any) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = value.to_dict() if hasattr(value, "to_dict") else value
    fd, tmp_path = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, str(target))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return str(target)
