"""Execute R analysis scripts from Python.

This module provides a safe interface to run the bundled R scripts
(synergy analysis, EMG visualization, etc.) as subprocesses.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from harmo.r_bridge._compat import check_r_available, get_rscript_path

logger = logging.getLogger(__name__)

# Directory containing the bundled R scripts
SCRIPTS_DIR = Path(__file__).parent / "scripts"


def _resolve_script(name: str) -> Path:
    """Resolve an R script name to its full path.

    Accepts either a bare name (e.g. ``"analysis_quickstart"``) or
    a name ending in ``.R``.
    """
    if not name.endswith(".R"):
        name = name + ".R"
    path = SCRIPTS_DIR / name
    if not path.is_file():
        available = sorted(p.name for p in SCRIPTS_DIR.glob("*.R"))
        raise FileNotFoundError(
            f"R script not found: {name}\n"
            f"Available scripts: {', '.join(available)}"
        )
    return path


def run_r_script(
    script_name: str,
    *,
    args: Optional[List[str]] = None,
    env_vars: Optional[Dict[str, str]] = None,
    warehouse_path: Optional[str] = None,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a bundled R script via Rscript.

    Parameters
    ----------
    script_name:
        Name of the script (with or without ``.R`` extension).
    args:
        Additional command-line arguments passed to the R script.
    env_vars:
        Extra environment variables to set for the R process.
    warehouse_path:
        Path to the Iceberg warehouse directory.  Passed to R
        via the ``HARMO_WAREHOUSE_PATH`` environment variable.
    timeout:
        Maximum seconds to wait for the script to finish.

    Returns
    -------
    subprocess.CompletedProcess
        The result of the execution.

    Raises
    ------
    RuntimeError
        When R is not available or the script fails.
    """
    check_r_available("harmo r-analyze")
    rscript = get_rscript_path()
    script_path = _resolve_script(script_name)

    import os
    env = os.environ.copy()
    if warehouse_path:
        env["HARMO_WAREHOUSE_PATH"] = str(warehouse_path)
    if env_vars:
        env.update(env_vars)

    cmd = [rscript, "--vanilla", str(script_path)]
    if args:
        cmd.extend(args)

    logger.info("Running R script: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            cwd=str(SCRIPTS_DIR),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"R script timed out after {timeout}s: {script_name}"
        )
    except OSError as exc:
        raise RuntimeError(f"Failed to start Rscript: {exc}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"R script failed (exit {result.returncode}): {script_name}\n"
            f"--- stderr ---\n{stderr}"
        )

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            logger.info("[R] %s", line)
    return result


def run_analysis_pipeline(
    *,
    warehouse_path: str,
    study_id: int,
    subject_id: int,
    session_id: str,
    run_id: str,
    n_synergies: int = 4,
    emg_output_dir: Optional[str] = None,
    synergy_output_dir: Optional[str] = None,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run the full R analysis pipeline (EMG + synergy).

    This is a convenience wrapper around :func:`run_r_script` that
    calls ``analysis_quickstart.R`` with the appropriate parameters.
    """
    params = {
        "study_id": study_id,
        "subject_id": subject_id,
        "session_id": session_id,
        "run_id": run_id,
        "n_synergies": n_synergies,
    }
    if emg_output_dir:
        params["emg_output_dir"] = emg_output_dir
    if synergy_output_dir:
        params["synergy_output_dir"] = synergy_output_dir

    env_vars = {
        "HARMO_R_PARAMS": json.dumps(params),
    }

    return run_r_script(
        "analysis_quickstart",
        warehouse_path=warehouse_path,
        env_vars=env_vars,
        timeout=timeout,
    )


def list_scripts() -> List[Dict[str, Any]]:
    """List all available R scripts with descriptions.

    Returns
    -------
    list of dict
        Each dict has keys ``name``, ``path``, and ``description``.
    """
    scripts = []
    for path in sorted(SCRIPTS_DIR.glob("*.R")):
        desc = ""
        try:
            with open(path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("#") and not stripped.startswith("#!"):
                        # First real comment line as description
                        text = stripped.lstrip("#' ").strip()
                        # Skip separator lines (e.g. ====, ----)
                        if text and not all(c in "=-~" for c in text):
                            desc = text
                            break
        except OSError:
            pass
        scripts.append({
            "name": path.stem,
            "path": str(path),
            "description": desc,
        })
    return scripts
