"""Abstract base class for file readers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import pandas as pd


class BaseReader(ABC):
    """Base class that every format-specific reader should inherit from.

    Subclasses must implement the ``read`` method which accepts a filesystem
    path and an optional configuration dictionary and returns a
    :class:`pandas.DataFrame`.
    """

    @abstractmethod
    def read(self, file_path: str, config: Dict[str, Any]) -> pd.DataFrame:
        """Read a single file and return a DataFrame.

        Parameters
        ----------
        file_path : str
            Path to the file on disk.
        config : dict
            Reader-specific options (keys depend on the format).

        Returns
        -------
        pd.DataFrame
        """
        ...
