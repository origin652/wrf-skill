from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

SECTION_START_RE = re.compile(r"^\s*&([A-Za-z0-9_]+)\s*$")
SECTION_END_RE = re.compile(r"^\s*/\s*$")


def _strip_comment(line: str) -> str:
    in_quote = False
    result: list[str] = []
    for char in line:
        if char == "'":
            in_quote = not in_quote
        if char == "!" and not in_quote:
            break
        result.append(char)
    return "".join(result).rstrip()


def _split_values(raw_value: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_quote = False
    for char in raw_value:
        if char == "'":
            in_quote = not in_quote
        if char == "," and not in_quote:
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
            continue
        current.append(char)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def _parse_token(token: str) -> Any:
    lowered = token.lower()
    if lowered == ".true.":
        return True
    if lowered == ".false.":
        return False
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return ".true." if value else ".false."
    if isinstance(value, str):
        return f"'{value}'"
    return str(value)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value]


def read_namelist_text(text: str) -> dict[str, dict[str, Any]]:
    current_section: str | None = None
    parsed: dict[str, dict[str, Any]] = {}

    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue

        section_match = SECTION_START_RE.match(line)
        if section_match:
            current_section = section_match.group(1)
            parsed[current_section] = {}
            continue

        if SECTION_END_RE.match(line):
            current_section = None
            continue

        if current_section is None or "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.rstrip(",").strip()
        tokens = [_parse_token(token) for token in _split_values(raw_value)]
        parsed[current_section][key] = tokens[0] if len(tokens) == 1 else tokens

    return parsed


def read_namelist(path: Path | str) -> dict[str, dict[str, Any]]:
    return read_namelist_text(Path(path).read_text(encoding="utf-8"))


def write_namelist(config: dict[str, dict[str, Any]], path: Path | str) -> None:
    lines: list[str] = []
    for section, values in config.items():
        lines.append(f"&{section}")
        for key, value in values.items():
            tokens = _as_list(value)
            rendered = ", ".join(_render_value(item) for item in tokens)
            lines.append(f" {key} = {rendered},")
        lines.append("/")
        lines.append("")
    Path(path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def validate_namelist(config: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    domains = config.get("domains", {})
    share = config.get("share", {})
    geogrid = config.get("geogrid", {})

    max_dom = domains.get("max_dom", share.get("max_dom", geogrid.get("max_dom")))
    if isinstance(max_dom, list):
        max_dom = max_dom[0]

    if max_dom is not None:
        for key in ("e_we", "e_sn", "parent_id", "parent_grid_ratio"):
            container = domains if key in domains else geogrid
            if key in container:
                values = _as_list(container[key])
                if len(values) != int(max_dom):
                    errors.append(f"{key} length must match max_dom")

    for key in ("dx", "dy"):
        if key in domains and float(domains[key]) <= 0:
            errors.append(f"{key} must be positive in domains")
        if key in geogrid and float(geogrid[key]) <= 0:
            errors.append(f"{key} must be positive in geogrid")

    if "time_step" in domains and int(domains["time_step"]) <= 0:
        errors.append("time_step must be positive")

    return errors


def merge_namelist(
    base: dict[str, dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = deepcopy(base)
    for section, values in overrides.items():
        merged.setdefault(section, {})
        merged[section].update(values)
    return merged


def to_json(config: dict[str, dict[str, Any]]) -> str:
    return json.dumps(config, indent=2, sort_keys=True)
