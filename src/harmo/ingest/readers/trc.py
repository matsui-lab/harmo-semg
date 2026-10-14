"""TRC file reader (OpenSim marker trajectory format).

TRC files use a fixed 5-line header followed by tab-delimited marker
coordinate data.  The shared parser lives in
:mod:`harmo.ingest.readers.sto`; this module provides a TRC-specific
convenience wrapper.

Extracted from ``BIDS_LIKE/ingest_data_spark.py`` (lines 1313-1406).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from harmo.ingest.readers.sto import read_opensim_sto_trc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_trc_file(path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Read an OpenSim ``.trc`` marker-trajectory file.

    This is a thin wrapper around :func:`read_opensim_sto_trc` that
    delegates all parsing to the shared parser.

    Parameters
    ----------
    path : str
        File path.

    Returns
    -------
    (header_df, data_df)
        ``header_df`` is a single-row DataFrame with header metadata.
        ``data_df`` contains marker coordinates plus a ``time`` column.
    """
    return read_opensim_sto_trc(path)
