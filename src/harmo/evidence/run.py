"""Run-level manifest capture for ingest and analysis orchestration."""

from __future__ import annotations

import os
import hashlib
import importlib.metadata
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from harmo import __version__

from .io import sha256_file, write_json_atomic
from .models import ArtifactReference, EvidenceManifest, TransformationStep


_SOURCE_MEDIA_TYPES = {
    ".c3d": "application/octet-stream",
    ".csv": "text/csv",
    ".json": "application/json",
    ".mat": "application/x-matlab-data",
    ".mot": "text/plain",
    ".sto": "text/plain",
    ".trc": "text/plain",
    ".tsv": "text/tab-separated-values",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def _repository_root(cwd: Optional[str] = None) -> Optional[Path]:
    starts = [Path(cwd or os.getcwd()).resolve(), Path(__file__).resolve().parents[3]]
    candidates = []
    for start in starts:
        candidates.extend([start] + list(start.parents))
    repository = next((path for path in candidates if (path / ".git").exists()), None)
    return repository


def current_commit(cwd: Optional[str] = None) -> str:
    repository = _repository_root(cwd)
    if repository is None:
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repository),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def current_repository_state(cwd: Optional[str] = None) -> Dict[str, object]:
    """Return commit plus a fingerprint that exposes unreleased local code."""
    repository = _repository_root(cwd)
    if repository is None:
        return {"commit": "unknown", "dirty": None, "working_tree_sha256": None}
    try:
        commit = current_commit(str(repository))
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=str(repository),
            capture_output=True,
            check=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=str(repository),
            capture_output=True,
            check=False,
        )
        if diff.returncode != 0 or untracked.returncode != 0:
            return {"commit": commit, "dirty": None, "working_tree_sha256": None}
        digest = hashlib.sha256()
        digest.update(diff.stdout)
        paths = sorted(path for path in untracked.stdout.split(b"\0") if path)
        for raw_path in paths:
            digest.update(b"\0untracked\0")
            digest.update(raw_path)
            path = repository / os.fsdecode(raw_path)
            if path.is_file():
                digest.update(bytes.fromhex(sha256_file(str(path))))
        dirty = bool(diff.stdout or paths)
        return {
            "commit": commit,
            "dirty": dirty,
            "working_tree_sha256": digest.hexdigest() if dirty else None,
        }
    except OSError:
        return {"commit": "unknown", "dirty": None, "working_tree_sha256": None}


def runtime_environment() -> Dict[str, str]:
    result = {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
    for distribution in ("harmo", "pandas", "duckdb", "pyspark", "scipy", "PyYAML"):
        try:
            result[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return result


def capture_run_manifest(
    config_path: str,
    warehouse_uri: str,
    operation: str,
    status: str,
    error: Optional[str] = None,
    run_id: Optional[str] = None,
    source_digests: Optional[Dict[str, str]] = None,
) -> str:
    config = str(Path(config_path).resolve())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = run_id or "{}-{}-{}".format(operation, timestamp, uuid.uuid4().hex[:8])
    repository_state = current_repository_state()
    inputs = [
        ArtifactReference(
            "config",
            Path(config).as_uri(),
            sha256_file(config),
            "application/yaml",
        )
    ]
    for index, (source_path, digest) in enumerate(
        sorted((source_digests or {}).items()), start=1
    ):
        resolved_source = Path(source_path).resolve()
        if resolved_source == Path(config):
            continue
        inputs.append(
            ArtifactReference(
                "source-{:06d}".format(index),
                resolved_source.as_uri(),
                digest,
                _SOURCE_MEDIA_TYPES.get(
                    resolved_source.suffix.lower(), "application/octet-stream"
                ),
            )
        )

    step = TransformationStep(
        step_id=operation,
        name="harmo {}".format(operation),
        software="harmo",
        software_version=__version__,
        parameters={"status": status, "error": error},
        inputs=[artifact.artifact_id for artifact in inputs],
        outputs=["warehouse"] if status == "completed" else [],
        completed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    manifest = EvidenceManifest(
        run_id=run_id,
        inputs=inputs,
        outputs=(
            [ArtifactReference("warehouse", warehouse_uri)]
            if status == "completed"
            else []
        ),
        steps=[step],
        code_commit=str(repository_state["commit"]),
        config_sha256=sha256_file(config),
        container_digest=os.environ.get("HARMO_CONTAINER_DIGEST"),
        code_dirty=repository_state["dirty"],
        working_tree_sha256=repository_state["working_tree_sha256"],
        environment=runtime_environment(),
    )
    if warehouse_uri.startswith(("s3://", "s3a://", "gs://")):
        manifest_dir = Path(os.environ.get("HARMO_EVIDENCE_DIR", ".harmo-evidence"))
    else:
        raw_path = (
            warehouse_uri[7:] if warehouse_uri.startswith("file://") else warehouse_uri
        )
        manifest_dir = Path(raw_path) / "_harmo_evidence"
    return write_json_atomic(str(manifest_dir / (run_id + ".manifest.json")), manifest)
