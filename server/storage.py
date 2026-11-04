"""Saving JSON so that closing at any moment can't corrupt it."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json(path: Path, value) -> None:
    """Whole-file atomic write, so closing at any moment can't corrupt progress."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(handle, "w") as out:
        json.dump(value, out, indent=2)
    os.replace(temporary, path)


def read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path) as source:
        return json.load(source)
