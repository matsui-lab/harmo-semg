"""Base interface for explicit ingest plugins."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

import pandas as pd


class BaseIngestPlugin(ABC):
    """Base class for deterministic, explicitly selected ingest plugins."""

    name: str = "base"
    version: str = "0.0.0"

    @abstractmethod
    def extract(self, file_path: str, options: Dict[str, Any]) -> pd.DataFrame:
        """Return tidy long DataFrame with required columns: time, channel, value."""
        ...
