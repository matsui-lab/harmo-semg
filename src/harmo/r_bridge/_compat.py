"""R environment detection and compatibility checks."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# R detection
# ---------------------------------------------------------------------------

_rscript_cache: Optional[str] = None


def get_rscript_path() -> Optional[str]:
    """Return the path to Rscript, or *None* if not found."""
    global _rscript_cache
    if _rscript_cache is not None:
        return _rscript_cache

    path = shutil.which("Rscript")
    if path is not None:
        _rscript_cache = path
        return path

    # Posit Workbench / RStudio Server typical locations
    for candidate in (
        "/usr/lib/rstudio-server/bin/Rscript",
        "/usr/local/bin/Rscript",
        "/opt/R/default/bin/Rscript",
    ):
        if Path(candidate).is_file():
            _rscript_cache = candidate
            return candidate

    return None


def _probe_r_version() -> Optional[str]:
    """Return the R version string, or *None* on failure."""
    rscript = get_rscript_path()
    if rscript is None:
        return None
    try:
        result = subprocess.run(
            [rscript, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Rscript --version writes to stderr
        output = (result.stderr or result.stdout or "").strip()
        for line in output.splitlines():
            if "version" in line.lower():
                return line.strip()
        return output.splitlines()[0] if output else None
    except (subprocess.TimeoutExpired, OSError):
        return None


R_AVAILABLE: bool = get_rscript_path() is not None
"""``True`` when an Rscript executable is found on the system."""


def check_r_available(feature: str = "R analysis") -> None:
    """Raise a clear error when R is not installed.

    Parameters
    ----------
    feature:
        Human-readable description of the feature that requires R,
        used in the error message.

    Raises
    ------
    RuntimeError
        When Rscript cannot be found.
    """
    if R_AVAILABLE:
        return

    msg = (
        f"{feature} requires R (Rscript) but it was not found on this system.\n"
        "\n"
        "Options to resolve this:\n"
        "  1. Install R: https://cran.r-project.org/\n"
        "  2. Use Posit Workbench (RStudio Server) where R is pre-installed\n"
        "  3. Skip R analysis — the core harmo pipeline works without R\n"
    )
    raise RuntimeError(msg)


def check_r_packages(packages: Optional[list] = None) -> Tuple[list, list]:
    """Check which R packages are installed.

    Returns
    -------
    (installed, missing)
        Two lists of package names.
    """
    check_r_available("R package check")
    rscript = get_rscript_path()

    if packages is None:
        packages = [
            "duckdb", "DBI", "ggplot2", "reshape2",
            "NMF", "dplyr", "tidyr", "jsonlite",
            "PhysioExperiment", "SummarizedExperiment", "S4Vectors",
        ]

    # Build one-liner check
    check_expr = (
        "pkgs <- c({pkgs}); "
        "cat(paste(pkgs, sapply(pkgs, requireNamespace, quietly=TRUE), sep='='), sep='\\n')"
    ).format(pkgs=", ".join(f'"{p}"' for p in packages))

    try:
        result = subprocess.run(
            [rscript, "-e", check_expr],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Failed to check R packages: %s", exc)
        return [], packages

    installed = []
    missing = []
    for line in result.stdout.strip().splitlines():
        if "=" in line:
            name, status = line.rsplit("=", 1)
            if status.strip().upper() == "TRUE":
                installed.append(name.strip())
            else:
                missing.append(name.strip())

    return installed, missing
