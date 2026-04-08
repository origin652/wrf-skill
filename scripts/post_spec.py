from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from spec_utils import TIME_FORMAT, deep_merge
except ImportError:  # pragma: no cover
    from .spec_utils import TIME_FORMAT, deep_merge

DEFAULT_POST_SPEC_VERSION = 1
ALLOWED_INPUT_MODES = {"project_artifacts", "explicit_paths", "glob"}
PRODUCT_SECTION_KEYS = ("inputs", "selectors", "render", "output", "options")
ROOT_RESERVED_KEYS = {"schema_version", "project_name", "defaults", "products"}
PRODUCT_RESERVED_KEYS = {"product", "label", *PRODUCT_SECTION_KEYS}


def load_json(path: Path | str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path | str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def default_post_defaults() -> dict[str, Any]:
    return {
        "inputs": {
            "mode": "project_artifacts",
            "artifact": "wrfout_files",
        },
        "selectors": {
            "domain": None,
            "time_indices": None,
            "time_range": {
                "start": None,
                "end": None,
            },
            "max_files": None,
        },
        "render": {
            "format": "png",
            "title": None,
            "colormap": None,
            "dpi": 150,
        },
        "output": {
            "subdir": "plots",
            "file_stem": None,
            "sidecar_json": True,
            "overwrite": False,
        },
        "options": {},
    }


def default_product_spec(product_name: str = "t2") -> dict[str, Any]:
    defaults = default_post_defaults()
    return {
        "product": product_name,
        "label": None,
        "inputs": deep_merge(defaults["inputs"], {"paths": [], "pattern": None}),
        "selectors": deepcopy(defaults["selectors"]),
        "render": deepcopy(defaults["render"]),
        "output": deepcopy(defaults["output"]),
        "options": deepcopy(defaults["options"]),
    }


def default_post_spec(project_name: str = "demo", *, product_name: str = "t2") -> dict[str, Any]:
    return {
        "schema_version": DEFAULT_POST_SPEC_VERSION,
        "project_name": project_name,
        "defaults": default_post_defaults(),
        "products": [default_product_spec(product_name)],
    }


def _coerce_root_shorthand(spec: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(spec)
    if "products" in payload or "product" not in payload:
        return payload

    root = {key: deepcopy(value) for key, value in payload.items() if key in ROOT_RESERVED_KEYS}
    product = {key: deepcopy(value) for key, value in payload.items() if key not in ROOT_RESERVED_KEYS}
    root["products"] = [product]
    return root


def _seed_project_name(spec: dict[str, Any], project_name_fallback: str | None) -> str:
    candidate = spec.get("project_name")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    if isinstance(project_name_fallback, str) and project_name_fallback.strip():
        return project_name_fallback.strip()
    return "demo"


def _normalize_product_spec(raw_product: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    seed_product = str(raw_product.get("product") or "").strip() or "t2"
    base_product = default_product_spec(seed_product)
    normalized = {
        key: deepcopy(value)
        for key, value in raw_product.items()
        if key not in PRODUCT_RESERVED_KEYS
    }
    normalized["product"] = raw_product.get("product", seed_product)
    normalized["label"] = raw_product.get("label")

    for section in PRODUCT_SECTION_KEYS:
        merged = deepcopy(base_product[section])
        default_overlay = defaults.get(section)
        if isinstance(default_overlay, dict):
            merged = deep_merge(merged, default_overlay)

        raw_value = raw_product.get(section)
        if isinstance(raw_value, dict):
            normalized[section] = deep_merge(merged, raw_value)
        elif raw_value is None:
            normalized[section] = merged
        else:
            normalized[section] = deepcopy(raw_value)

    return normalized


def normalize_post_spec(
    spec: dict[str, Any],
    *,
    project_name_fallback: str | None = None,
) -> dict[str, Any]:
    incoming = _coerce_root_shorthand(spec)
    seed_name = _seed_project_name(incoming, project_name_fallback)
    normalized = default_post_spec(seed_name)

    for key, value in incoming.items():
        if key not in ROOT_RESERVED_KEYS:
            normalized[key] = deepcopy(value)

    if "schema_version" in incoming:
        normalized["schema_version"] = deepcopy(incoming["schema_version"])

    if "project_name" in incoming:
        normalized["project_name"] = deepcopy(incoming["project_name"])

    raw_defaults = incoming.get("defaults")
    if isinstance(raw_defaults, dict):
        normalized["defaults"] = deep_merge(normalized["defaults"], raw_defaults)
    elif raw_defaults is not None:
        normalized["defaults"] = deepcopy(raw_defaults)

    raw_products = incoming.get("products")
    defaults = normalized["defaults"] if isinstance(normalized["defaults"], dict) else {}
    if isinstance(raw_products, list):
        normalized["products"] = []
        for item in raw_products:
            if isinstance(item, dict):
                normalized["products"].append(_normalize_product_spec(item, defaults))
            else:
                normalized["products"].append(
                    _normalize_product_spec({"product": str(item)}, defaults)
                )

    if (
        "project_name" not in incoming
        or not isinstance(normalized["project_name"], str)
        or not normalized["project_name"].strip()
    ):
        normalized["project_name"] = seed_name

    if "schema_version" not in incoming:
        normalized["schema_version"] = DEFAULT_POST_SPEC_VERSION

    return normalized


def _is_valid_time_token(value: str) -> bool:
    try:
        datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        return False
    return True


def _validate_defaults(defaults: Any, errors: list[str]) -> None:
    if defaults is None:
        return
    if not isinstance(defaults, dict):
        errors.append("defaults must be an object when provided")
        return
    for section in PRODUCT_SECTION_KEYS:
        value = defaults.get(section)
        if value is not None and not isinstance(value, dict):
            errors.append(f"defaults.{section} must be an object when provided")


def _validate_inputs(inputs: Any, product_index: int, errors: list[str]) -> None:
    prefix = f"products[{product_index}].inputs"
    if not isinstance(inputs, dict):
        errors.append(f"{prefix} must be an object")
        return

    mode = inputs.get("mode")
    if not isinstance(mode, str) or mode not in ALLOWED_INPUT_MODES:
        errors.append(
            f"{prefix}.mode must be one of {', '.join(sorted(ALLOWED_INPUT_MODES))}"
        )
        return

    if mode == "project_artifacts":
        artifact = inputs.get("artifact")
        if not isinstance(artifact, str) or not artifact.strip():
            errors.append(f"{prefix}.artifact must be a non-empty string in project_artifacts mode")
        return

    if mode == "explicit_paths":
        paths = inputs.get("paths")
        if not isinstance(paths, list) or not paths:
            errors.append(f"{prefix}.paths must be a non-empty list in explicit_paths mode")
            return
        for path_index, value in enumerate(paths):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.paths[{path_index}] must be a non-empty string")
        return

    pattern = inputs.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        errors.append(f"{prefix}.pattern must be a non-empty string in glob mode")


def _validate_selectors(selectors: Any, product_index: int, errors: list[str]) -> None:
    prefix = f"products[{product_index}].selectors"
    if not isinstance(selectors, dict):
        errors.append(f"{prefix} must be an object")
        return

    domain = selectors.get("domain")
    if domain is not None and (not isinstance(domain, str) or not domain.strip()):
        errors.append(f"{prefix}.domain must be a non-empty string or null")

    time_indices = selectors.get("time_indices")
    if time_indices is not None:
        if not isinstance(time_indices, list):
            errors.append(f"{prefix}.time_indices must be a list or null")
        else:
            for item_index, value in enumerate(time_indices):
                if not isinstance(value, int) or value < 0:
                    errors.append(
                        f"{prefix}.time_indices[{item_index}] must be a non-negative integer"
                    )

    time_range = selectors.get("time_range")
    if time_range is not None:
        if not isinstance(time_range, dict):
            errors.append(f"{prefix}.time_range must be an object or null")
        else:
            for key in ("start", "end"):
                value = time_range.get(key)
                if value is not None and (
                    not isinstance(value, str) or not _is_valid_time_token(value)
                ):
                    errors.append(
                        f"{prefix}.time_range.{key} must match {TIME_FORMAT} or be null"
                    )

    max_files = selectors.get("max_files")
    if max_files is not None and (not isinstance(max_files, int) or max_files < 1):
        errors.append(f"{prefix}.max_files must be a positive integer or null")


def _validate_render(render: Any, product_index: int, errors: list[str]) -> None:
    prefix = f"products[{product_index}].render"
    if not isinstance(render, dict):
        errors.append(f"{prefix} must be an object")
        return

    value = render.get("format")
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{prefix}.format must be a non-empty string")

    dpi = render.get("dpi")
    if not isinstance(dpi, int) or dpi < 1:
        errors.append(f"{prefix}.dpi must be a positive integer")

    for key in ("title", "colormap"):
        value = render.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{prefix}.{key} must be a non-empty string or null")


def _validate_output(output: Any, product_index: int, errors: list[str]) -> None:
    prefix = f"products[{product_index}].output"
    if not isinstance(output, dict):
        errors.append(f"{prefix} must be an object")
        return

    subdir = output.get("subdir")
    if not isinstance(subdir, str) or not subdir.strip():
        errors.append(f"{prefix}.subdir must be a non-empty string")

    file_stem = output.get("file_stem")
    if file_stem is not None and (not isinstance(file_stem, str) or not file_stem.strip()):
        errors.append(f"{prefix}.file_stem must be a non-empty string or null")

    for key in ("sidecar_json", "overwrite"):
        value = output.get(key)
        if not isinstance(value, bool):
            errors.append(f"{prefix}.{key} must be a boolean")


def validate_post_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    schema_version = spec.get("schema_version")
    if schema_version != DEFAULT_POST_SPEC_VERSION:
        errors.append(f"schema_version must equal {DEFAULT_POST_SPEC_VERSION}")

    project_name = spec.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        errors.append("project_name must be a non-empty string")

    _validate_defaults(spec.get("defaults"), errors)

    products = spec.get("products")
    if not isinstance(products, list) or not products:
        errors.append("products must be a non-empty list")
        return errors

    for product_index, product in enumerate(products):
        prefix = f"products[{product_index}]"
        if not isinstance(product, dict):
            errors.append(f"{prefix} must be an object")
            continue

        product_name = product.get("product")
        if not isinstance(product_name, str) or not product_name.strip():
            errors.append(f"{prefix}.product must be a non-empty string")

        label = product.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            errors.append(f"{prefix}.label must be a non-empty string or null")

        _validate_inputs(product.get("inputs"), product_index, errors)
        _validate_selectors(product.get("selectors"), product_index, errors)
        _validate_render(product.get("render"), product_index, errors)
        _validate_output(product.get("output"), product_index, errors)

        options = product.get("options")
        if not isinstance(options, dict):
            errors.append(f"{prefix}.options must be an object")

    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize and validate a WRF post-processing spec."
    )
    parser.add_argument(
        "--input",
        help="Path to an existing post-processing spec. Defaults to a generated starter spec.",
    )
    parser.add_argument(
        "--output",
        help="Write the normalized spec to this path. Defaults to stdout.",
    )
    parser.add_argument(
        "--project-name",
        default="demo",
        help="Project name fallback or starter-spec value.",
    )
    parser.add_argument(
        "--product",
        default="t2",
        help="Starter product name when --input is omitted.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only and do not emit normalized JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input:
        payload = load_json(args.input)
    else:
        payload = default_post_spec(args.project_name, product_name=args.product)

    normalized = normalize_post_spec(payload, project_name_fallback=args.project_name)
    errors = validate_post_spec(normalized)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    if args.check:
        return 0

    if args.output:
        dump_json(args.output, normalized)
    else:
        print(json.dumps(normalized, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
