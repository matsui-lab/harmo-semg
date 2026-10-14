"""Configuration loading and path resolution utilities."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from harmo.warehouse.connection import get_warehouse_path


def load_config(config_path):
    # type: (str) -> Dict[str, Any]
    """Load a study config.yaml, resolving relative paths against its parent dir.

    Returns a normalised configuration dictionary with:
    - ``warehouse_path`` resolved via the standard priority chain.
    - All ``definitions[*].path`` entries resolved relative to *config_path*.
    - ``input_dir_aliases`` values resolved relative to *config_path*.
    """
    config_dir = Path(config_path).resolve().parent

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Resolve warehouse path
    raw_wh = config.get("warehouse_path")
    config["warehouse_path"] = get_warehouse_path(raw_wh)

    # Resolve input_dir_aliases
    aliases = config.get("input_dir_aliases", {})
    for alias_key, alias_val in aliases.items():
        p = Path(alias_val)
        if not p.is_absolute():
            aliases[alias_key] = str(config_dir / p)
    config["input_dir_aliases"] = aliases

    # Resolve definition table paths
    definitions = config.get("definitions", {})
    for _key, defn in definitions.items():
        path_str = defn.get("path", "")
        p = Path(path_str)
        if not p.is_absolute():
            defn["path"] = str(config_dir / p)
    config["definitions"] = definitions

    return config


def get_primary_study_id(definitions):
    # type: (Dict[str, Any]) -> Optional[int]
    """Extract the first study_id from the study definition table.

    Searches definitions for the entry with ``yaml_key == 'studies'`` and
    returns the ``study_id`` of the first study listed.  Returns ``None``
    if no study_id can be found.

    This helper was originally in ``data_converter.py`` and is placed here
    to avoid circular imports between ingest and model layers.
    """
    for _key, def_config in definitions.items():
        if def_config.get("yaml_key") == "studies":
            path = def_config.get("path")
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    y_data = yaml.safe_load(f)
                    studies = y_data.get("studies", [])
                    if studies and isinstance(studies, list):
                        return studies[0].get("study_id")
    return None
