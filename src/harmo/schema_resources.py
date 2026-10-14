"""Locate schema data in a source checkout or a normal installed distribution."""
from pathlib import Path
import sysconfig
import sys


def schema_directory():
    package = Path(__file__).resolve().parent
    # Only the explicit src layout is a checkout; never search the working
    # directory or unrelated ancestors of an installed package.
    if getattr(sys, 'frozen', False):
        directory = package / 'schemas'
    elif package.parent.name == "src":
        directory = package.parent.parent / "schemas"
    else:
        directory = Path(sysconfig.get_path("data")) / "share/harmo/schemas"
    if not directory.is_dir() or not any(directory.glob("*.json")):
        raise RuntimeError("Harmo schema data is missing: " + str(directory))
    return directory


def schema_path(name):
    if not isinstance(name,str) or Path(name).name != name or "\\" in name or not name.endswith(".json"):
        raise ValueError("A schema JSON basename is required")
    path = schema_directory() / name
    if not path.is_file():
        raise RuntimeError("Harmo schema is missing: " + name)
    return path
