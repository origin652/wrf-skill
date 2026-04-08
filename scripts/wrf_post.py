from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any

try:
    from plot_wrfout import (
        enumerate_wrfout_frames,
        infer_domain_from_path,
        run_product_request,
        select_wrfout_frames,
    )
    from post_spec import default_post_spec, load_json, normalize_post_spec, validate_post_spec
    from project_state import (
        assert_mutation_allowed,
        clear_error,
        load_project,
        posix_path,
        record_error,
        register_artifact,
        save_project,
    )
except ImportError:  # pragma: no cover
    from .plot_wrfout import (
        enumerate_wrfout_frames,
        infer_domain_from_path,
        run_product_request,
        select_wrfout_frames,
    )
    from .post_spec import (
        default_post_spec,
        load_json,
        normalize_post_spec,
        validate_post_spec,
    )
    from .project_state import (
        assert_mutation_allowed,
        clear_error,
        load_project,
        posix_path,
        record_error,
        register_artifact,
        save_project,
    )


def write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip())
        handle.write("\n")


def project_root(runs_dir: Path | str, project_name: str) -> Path:
    return Path(runs_dir) / project_name


def project_json_path(runs_dir: Path | str, project_name: str) -> Path:
    return project_root(runs_dir, project_name) / "project.json"


def _resolve_paths_from_artifact(state: dict[str, Any], artifact_name: str) -> list[Path]:
    raw_value = state.get("artifacts", {}).get(artifact_name)
    if raw_value is None:
        return []

    items = raw_value if isinstance(raw_value, list) else [raw_value]
    resolved: list[Path] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        path = Path(str(item)).resolve()
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def resolve_input_paths(
    product_spec: dict[str, Any],
    state: dict[str, Any],
    *,
    project_dir: Path,
) -> list[Path]:
    inputs = product_spec.get("inputs", {})
    selectors = product_spec.get("selectors", {})
    mode = str(inputs.get("mode") or "project_artifacts")

    if mode == "project_artifacts":
        artifact_name = str(inputs.get("artifact") or "wrfout_files")
        candidates = _resolve_paths_from_artifact(state, artifact_name)
    elif mode == "explicit_paths":
        candidates = []
        for raw_path in inputs.get("paths", []):
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = project_dir / path
            candidates.append(path.resolve())
    elif mode == "glob":
        pattern = str(inputs.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("glob mode requires a non-empty pattern")
        search_pattern = pattern if Path(pattern).is_absolute() else (project_dir / pattern).as_posix()
        candidates = [Path(match).resolve() for match in glob.glob(search_pattern)]
    else:
        raise ValueError(f"Unsupported input mode: {mode}")

    domain = selectors.get("domain") if isinstance(selectors, dict) else None
    seen: set[str] = set()
    resolved: list[Path] = []
    for candidate in sorted(candidates, key=lambda item: item.as_posix()):
        key = candidate.as_posix()
        if key in seen:
            continue
        if not candidate.exists() or not candidate.is_file():
            continue
        if domain and infer_domain_from_path(candidate) != domain:
            continue
        seen.add(key)
        resolved.append(candidate)

    max_files = selectors.get("max_files") if isinstance(selectors, dict) else None
    if max_files is not None:
        resolved = resolved[: int(max_files)]
    return resolved


def load_post_spec(
    project_name: str,
    project_dir: Path,
    post_spec_path: Path | str | None,
) -> tuple[dict[str, Any], Path | None]:
    if post_spec_path is not None:
        path = Path(post_spec_path)
        if not path.is_absolute():
            path = project_dir / path
        if not path.exists():
            raise FileNotFoundError(f"Missing post spec: {path}")
        return load_json(path), path

    default_path = project_dir / "post_spec.json"
    if default_path.exists():
        return load_json(default_path), default_path
    return default_post_spec(project_name), None


def run_postprocess(
    project_name: str,
    *,
    runs_dir: Path | str = "runs",
    post_spec_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    project_json = project_json_path(runs_dir, project_name)
    state = load_project(project_json)
    assert_mutation_allowed(state, "wrf-post")

    project_dir = Path(state["paths"]["project_root"])
    output_dir = Path(state["paths"]["output_dir"])
    log_path = Path(state["paths"]["log_dir"]) / "wrf-post.log"

    raw_spec, resolved_spec_path = load_post_spec(project_name, project_dir, post_spec_path)
    spec = normalize_post_spec(raw_spec, project_name_fallback=project_name)
    errors = validate_post_spec(spec)
    if errors:
        raise ValueError("Invalid post spec: " + "; ".join(errors))

    lines = [
        f"wrf-post project={project_name}",
        f"project_root={posix_path(project_dir)}",
        f"post_spec={posix_path(resolved_spec_path) if resolved_spec_path else '(generated-default)'}",
        f"dry_run={dry_run}",
    ]
    generated: list[dict[str, Any]] = []

    try:
        for index, product_spec in enumerate(spec["products"], start=1):
            input_paths = resolve_input_paths(product_spec, state, project_dir=project_dir)
            if not input_paths:
                raise FileNotFoundError(
                    f"No input files resolved for product {product_spec['product']}"
                )

            frames = enumerate_wrfout_frames(input_paths)
            selected_frames = select_wrfout_frames(frames, product_spec.get("selectors"))
            if not selected_frames:
                raise ValueError(
                    f"No frames selected for product {product_spec['product']} after applying selectors"
                )

            lines.append(
                f"[product {index}] name={product_spec['product']} inputs={len(input_paths)} frames={len(selected_frames)}"
            )
            artifacts = run_product_request(
                product_spec,
                selected_frames,
                output_dir,
                dry_run=dry_run,
            )
            for artifact in artifacts:
                generated.append(artifact)
                lines.append(f"output={artifact['path']}")
                if not dry_run:
                    register_artifact(state, "plots", artifact["path"])

        if dry_run:
            return {
                "dry_run": True,
                "project": state,
                "post_spec": spec,
                "artifacts": generated,
            }

        clear_error(state)
        save_project(state, project_json)
        write_log(log_path, lines)
        return {
            "dry_run": False,
            "project": state,
            "post_spec": spec,
            "artifacts": generated,
            "log_path": posix_path(log_path),
        }
    except Exception as exc:
        if not dry_run:
            record_error(
                state,
                "wrf-post",
                "WRF_POST_FAILED",
                str(exc),
                posix_path(log_path),
            )
            save_project(state, project_json)
            lines.extend(["[error]", str(exc)])
            write_log(log_path, lines)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run post-processing for a WRF project.")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument(
        "--post-spec",
        help="Optional post-processing spec path. Defaults to runs/<project>/post_spec.json when present.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_postprocess(
        args.project_name,
        runs_dir=args.runs_dir,
        post_spec_path=args.post_spec,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
