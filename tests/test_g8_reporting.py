from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_g8_local_reporting_audit_passes_without_inventing_external_metadata():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/paper/audit_g8_reporting.py",
            "--root",
            ".",
            "--check-only",
        ],
        cwd=str(ROOT),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"local_reporting_ready": true' in completed.stdout
    assert '"external_submission_metadata_ready": false' in completed.stdout

