"""Optional R analysis bridge for harmo.

This module provides integration with R-based analysis tools
(PhysioExperiment, NMF synergy analysis, ggplot2 visualization).

R is **not** required for the core harmo pipeline (ingest → OpenSim → warehouse).
Install the optional dependency group to enable R features::

    pip install harmo[r-analysis]

The R scripts are designed for use with Posit Workbench (RStudio Server)
or via the ``harmo r-analyze`` CLI subcommand.
"""
from __future__ import annotations

from harmo.r_bridge._compat import (
    R_AVAILABLE,
    check_r_available,
    get_rscript_path,
)

__all__ = [
    "R_AVAILABLE",
    "check_r_available",
    "get_rscript_path",
]
