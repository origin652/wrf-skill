"""Shared utility functions for WRF workflow scripts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return current UTC time as ISO format string."""
    return datetime.now(timezone.utc).isoformat()


def posix_path(path: Path | str) -> str:
    """Convert path to POSIX format string."""
    return Path(path).as_posix()


def load_json(path: Path | str) -> dict[str, Any]:
    """Load JSON file and return as dictionary."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path | str, payload: dict[str, Any]) -> None:
    """Write dictionary to JSON file with consistent formatting."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def coerce_value(raw_value: str) -> Any:
    """Coerce string value to appropriate Python type.

    Converts:
    - "true"/"false" to bool
    - numeric strings to int/float
    - everything else remains string
    """
    lowered = raw_value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    # Try numeric conversion
    try:
        if "." in raw_value:
            return float(raw_value)
        return int(raw_value)
    except ValueError:
        return raw_value


def ensure_dir(path: Path | str) -> Path:
    """Ensure directory exists, create if needed."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def repo_root() -> Path:
    """Return repository root directory."""
    return Path(__file__).resolve().parents[1]


def scripts_dir() -> Path:
    """Return scripts directory."""
    return repo_root() / "scripts"


def template_dir() -> Path:
    """Return templates directory."""
    return repo_root() / "templates"


def config_dir() -> Path:
    """Return config directory."""
    return repo_root() / "config"
