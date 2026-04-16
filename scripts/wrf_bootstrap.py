from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from utils import dump_json, load_json, repo_root
except ImportError:  # pragma: no cover
    from .utils import dump_json, load_json, repo_root

SUPPORTED_PROFILES = {"auto", "wsl_prebuilt", "linux_prebuilt", "hpc_template"}
PATH_KEYS = {
    "wrf_dir",
    "wps_dir",
    "geog_data_path",
    "wrf_run_dir",
    "wps_bin_dir",
    "wps_support_dir",
}
ENV_CANDIDATES = {
    "wrf_dir": ("WRF_DIR", "WRF_HOME"),
    "wps_dir": ("WPS_DIR", "WPS_HOME"),
    "geog_data_path": ("WPS_GEOG", "GEOG_DATA_PATH"),
    "wrf_run_dir": ("WRF_RUN_DIR",),
    "wps_bin_dir": ("WPS_BIN_DIR",),
    "wps_support_dir": ("WPS_SUPPORT_DIR",),
}
COMMON_CANDIDATES = {
    "wrf_dir": (
        "/opt/wrf",
        "/opt/WRF",
        "/usr/local/wrf",
        "~/wrf-install",
        "~/WRF",
    ),
    "wps_dir": (
        "/opt/wps",
        "/opt/WPS",
        "/usr/local/wps",
        "~/wps-install",
        "~/WPS",
    ),
    "geog_data_path": (
        "/data/WPS_GEOG",
        "/opt/WPS_GEOG",
        "/usr/local/share/WPS_GEOG",
        "~/WPS_GEOG",
    ),
    "wps_support_dir": (
        "/opt/wps-support",
        "/usr/local/share/wps-support",
        "~/wps-support",
    ),
}
VTABLE_BY_SOURCE = {
    "gfs": "Vtable.GFS",
    "fnl": "Vtable.GFS",
    "era5": "Vtable.ECMWF",
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged



def host_kind() -> str:
    if os.name != "posix":
        return "unsupported"
    proc_version = Path("/proc/version")
    try:
        version_text = proc_version.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        version_text = ""
    return "wsl" if "microsoft" in version_text.lower() else "linux"



def _normalize_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return str(Path(text).expanduser())



def _add_candidate(
    candidates: list[dict[str, str]],
    source: str,
    value: Any,
) -> None:
    normalized = _normalize_path(value)
    if not normalized:
        return
    entry = {"source": source, "path": Path(normalized).resolve().as_posix()}
    if entry not in candidates:
        candidates.append(entry)



def _first_existing(candidates: list[dict[str, str]]) -> tuple[str | None, str | None]:
    for item in candidates:
        if item["source"].startswith("request.") or item["source"].startswith("env."):
            return item["path"], item["source"]
    for item in candidates:
        if Path(item["path"]).exists():
            return item["path"], item["source"]
    if candidates:
        return candidates[0]["path"], candidates[0]["source"]
    return None, None



def _infer_wps_from_executable() -> tuple[str | None, str | None, str | None]:
    for name in ("geogrid.exe", "geogrid"):
        resolved = shutil.which(name)
        if not resolved:
            continue
        exec_path = Path(resolved).resolve()
        if exec_path.parent.name == "bin":
            return exec_path.parent.parent.as_posix(), exec_path.parent.as_posix(), name
        return exec_path.parent.as_posix(), exec_path.parent.as_posix(), name
    return None, None, None



def _infer_wrf_from_executable() -> tuple[str | None, str | None, str | None]:
    for name in ("real.exe", "real", "wrf.exe", "wrf"):
        resolved = shutil.which(name)
        if not resolved:
            continue
        exec_path = Path(resolved).resolve()
        parent = exec_path.parent
        if parent.name in {"bin", "run", "main"}:
            wrf_dir = parent.parent.as_posix()
        else:
            wrf_dir = parent.as_posix()
        if (Path(wrf_dir) / "run").exists():
            run_dir = (Path(wrf_dir) / "run").resolve().as_posix()
        else:
            run_dir = parent.as_posix()
        return wrf_dir, run_dir, name
    return None, None, None



def _profile_from_request(profile: str, detected_host: str) -> str:
    if profile == "auto":
        if detected_host == "wsl":
            return "wsl_prebuilt"
        return "linux_prebuilt"
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported bootstrap profile: {profile}")
    if profile == "hpc_template":
        if detected_host == "wsl":
            return "wsl_prebuilt"
        return "linux_prebuilt"
    return profile



def _repo_candidates(root: Path) -> dict[str, str]:
    return {
        "wrf_dir": (root / "third_party" / "wrf-install").as_posix(),
        "wps_dir": (root / "third_party" / "wps-install").as_posix(),
        "geog_data_path": (root / "third_party" / "WPS_GEOG").as_posix(),
        "wps_support_dir": (root / "third_party" / "wps-support").as_posix(),
    }



def discover_environment(request: dict[str, Any]) -> dict[str, Any]:
    root = repo_root()
    detected_host = host_kind()
    requested_profile = str(request.get("profile") or "auto").strip() or "auto"
    effective_profile = _profile_from_request(requested_profile, detected_host)
    prefer_repo_local = bool(request.get("prefer_repo_local", True))
    path_overrides = request.get("paths") or {}
    repo_defaults = _repo_candidates(root)

    inferred_wps_dir, inferred_wps_bin_dir, inferred_wps_exec = _infer_wps_from_executable()
    inferred_wrf_dir, inferred_wrf_run_dir, inferred_wrf_exec = _infer_wrf_from_executable()

    candidates: dict[str, list[dict[str, str]]] = {key: [] for key in PATH_KEYS}
    sources: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for key in PATH_KEYS:
        _add_candidate(candidates[key], f"request.paths.{key}", path_overrides.get(key))
        for env_key in ENV_CANDIDATES.get(key, ()):
            _add_candidate(candidates[key], f"env.{env_key}", os.environ.get(env_key))

    if inferred_wps_dir:
        _add_candidate(candidates["wps_dir"], f"path.{inferred_wps_exec}", inferred_wps_dir)
    if inferred_wps_bin_dir:
        _add_candidate(candidates["wps_bin_dir"], f"path.{inferred_wps_exec}", inferred_wps_bin_dir)
    if inferred_wrf_dir:
        _add_candidate(candidates["wrf_dir"], f"path.{inferred_wrf_exec}", inferred_wrf_dir)
    if inferred_wrf_run_dir:
        _add_candidate(candidates["wrf_run_dir"], f"path.{inferred_wrf_exec}", inferred_wrf_run_dir)

    candidate_groups: list[tuple[str, tuple[str, ...]]] = []
    if prefer_repo_local:
        candidate_groups.append(("repo.third_party", ("wrf_dir", "wps_dir", "geog_data_path", "wps_support_dir")))
        candidate_groups.append(("system.default", tuple(COMMON_CANDIDATES.keys())))
    else:
        candidate_groups.append(("system.default", tuple(COMMON_CANDIDATES.keys())))
        candidate_groups.append(("repo.third_party", ("wrf_dir", "wps_dir", "geog_data_path", "wps_support_dir")))

    for label, keys in candidate_groups:
        for key in keys:
            if label == "repo.third_party":
                _add_candidate(candidates[key], label, repo_defaults[key])
            else:
                for value in COMMON_CANDIDATES.get(key, ()):  # pragma: no branch - tiny lists
                    _add_candidate(candidates[key], label, value)

    resolved: dict[str, str | None] = {}
    for key in PATH_KEYS:
        resolved_path, source = _first_existing(candidates[key])
        resolved[key] = resolved_path
        if resolved_path:
            sources[key] = {"source": source, "value": resolved_path}

    wps_dir = resolved.get("wps_dir")
    wrf_dir = resolved.get("wrf_dir")

    if not resolved.get("wps_bin_dir") and wps_dir:
        candidate = Path(wps_dir) / "bin"
        if candidate.exists():
            resolved["wps_bin_dir"] = candidate.resolve().as_posix()
            sources["wps_bin_dir"] = {"source": "derived.wps_dir/bin", "value": resolved["wps_bin_dir"]}
        else:
            resolved["wps_bin_dir"] = Path(wps_dir).resolve().as_posix()
            sources["wps_bin_dir"] = {"source": "derived.wps_dir", "value": resolved["wps_bin_dir"]}
            warnings.append("WPS bin directory was inferred from wps_dir; verify executables live there.")

    if not resolved.get("wrf_run_dir") and wrf_dir:
        for relative, label in (("run", "derived.wrf_dir/run"), ("main", "derived.wrf_dir/main"), ("bin", "derived.wrf_dir/bin")):
            candidate = Path(wrf_dir) / relative
            if candidate.exists():
                resolved["wrf_run_dir"] = candidate.resolve().as_posix()
                sources["wrf_run_dir"] = {"source": label, "value": resolved["wrf_run_dir"]}
                if relative != "run":
                    warnings.append(
                        f"WRF runtime support directory was inferred from {candidate}; verify auxiliary run files are present."
                    )
                break

    if not resolved.get("geog_data_path") and wps_dir:
        sibling_geog = Path(wps_dir).resolve().parent / "WPS_GEOG"
        if sibling_geog.exists():
            resolved["geog_data_path"] = sibling_geog.as_posix()
            sources["geog_data_path"] = {"source": "derived.wps_dir_parent/WPS_GEOG", "value": resolved["geog_data_path"]}

    if not resolved.get("wps_support_dir") and wps_dir:
        candidate = Path(wps_dir).resolve().parent / "wps-support"
        if candidate.exists():
            resolved["wps_support_dir"] = candidate.as_posix()
            sources["wps_support_dir"] = {"source": "derived.wps_dir_parent/wps-support", "value": resolved["wps_support_dir"]}

    missing = [key for key in ("wrf_dir", "wps_dir", "geog_data_path") if not resolved.get(key)]
    if missing:
        warnings.append(
            "Bootstrap could not detect the following required paths automatically: "
            + ", ".join(missing)
        )

    return {
        "requested_profile": requested_profile,
        "effective_profile": effective_profile,
        "include_hpc_template": bool(request.get("include_hpc_template")) or requested_profile == "hpc_template",
        "host_kind": detected_host,
        "prefer_repo_local": prefer_repo_local,
        "resolved_paths": resolved,
        "sources": sources,
        "warnings": warnings,
    }



def _choose_mpi_command(request: dict[str, Any]) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    local_request = request.get("local") or {}
    explicit = local_request.get("mpi_cmd")
    if explicit:
        return str(explicit), warnings

    for candidate in ("mpirun", "mpiexec", "srun"):
        if shutil.which(candidate):
            return candidate, warnings

    warnings.append("No MPI launcher was detected in PATH; generated config will default to serial local execution.")
    return None, warnings



def _choose_default_np(request: dict[str, Any], mpi_cmd: str | None) -> int:
    local_request = request.get("local") or {}
    if local_request.get("default_np") is not None:
        return max(1, int(local_request["default_np"]))
    cpu_total = os.cpu_count() or 1
    if mpi_cmd:
        return max(1, min(cpu_total, 8))
    return 1



def _resolve_support_file(explicit: Any, *candidates: Path) -> str | None:
    explicit_path = _normalize_path(explicit)
    if explicit_path and Path(explicit_path).exists():
        return Path(explicit_path).resolve().as_posix()
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve().as_posix()
    return explicit_path



def build_config(request: dict[str, Any]) -> dict[str, Any]:
    discovery = discover_environment(request)
    resolved = discovery["resolved_paths"]
    warnings = list(discovery["warnings"])

    mpi_cmd, mpi_warnings = _choose_mpi_command(request)
    warnings.extend(mpi_warnings)
    default_np = _choose_default_np(request, mpi_cmd)

    python_env = request.get("python_env")
    if python_env is None:
        python_env = os.environ.get("CONDA_DEFAULT_ENV")
    if python_env is None:
        virtual_env = os.environ.get("VIRTUAL_ENV")
        if virtual_env:
            python_env = Path(virtual_env).name

    config: dict[str, Any] = {
        "platform": discovery["host_kind"],
        "shell": "bash",
        "wrf_dir": resolved.get("wrf_dir"),
        "wps_dir": resolved.get("wps_dir"),
        "geog_data_path": resolved.get("geog_data_path"),
        "run_mode": "local",
        "local": {
            "default_np": default_np,
        },
        "notifications": {
            "command": None,
        },
        "geog_data_res": str(request.get("geog_data_res") or "default"),
    }

    if mpi_cmd:
        config["local"]["mpi_cmd"] = mpi_cmd
    if python_env:
        config["python_env"] = python_env
    if resolved.get("wrf_run_dir"):
        config["wrf_run_dir"] = resolved["wrf_run_dir"]
    if resolved.get("wps_bin_dir"):
        config["wps_bin_dir"] = resolved["wps_bin_dir"]
    if resolved.get("wps_support_dir"):
        config["wps_support_dir"] = resolved["wps_support_dir"]

    wps_support_dir = Path(resolved["wps_support_dir"]) if resolved.get("wps_support_dir") else None
    wps_root = Path(resolved["wps_dir"]) if resolved.get("wps_dir") else None
    request_wps_tables = request.get("wps_tables") or {}

    geogrid_candidates = []
    metgrid_candidates = []
    vtable_gfs_candidates = []
    vtable_era5_candidates = []
    if wps_support_dir is not None:
        geogrid_candidates.append(wps_support_dir / "GEOGRID.TBL.ARW")
        metgrid_candidates.append(wps_support_dir / "METGRID.TBL.ARW")
        vtable_gfs_candidates.append(wps_support_dir / "Vtable.GFS")
        vtable_era5_candidates.append(wps_support_dir / "Vtable.ECMWF")
    if wps_root is not None:
        geogrid_candidates.append(wps_root / "geogrid" / "GEOGRID.TBL.ARW")
        metgrid_candidates.append(wps_root / "metgrid" / "METGRID.TBL.ARW")
        vtable_gfs_candidates.append(wps_root / "ungrib" / "Variable_Tables" / "Vtable.GFS")
        vtable_era5_candidates.append(wps_root / "ungrib" / "Variable_Tables" / "Vtable.ECMWF")

    geogrid_table = _resolve_support_file(request_wps_tables.get("geogrid"), *geogrid_candidates)
    metgrid_table = _resolve_support_file(request_wps_tables.get("metgrid"), *metgrid_candidates)
    vtable_gfs = _resolve_support_file(request_wps_tables.get("vtable"), *vtable_gfs_candidates)
    vtable_era5 = _resolve_support_file(
        (request_wps_tables.get("vtable_by_source") or {}).get("era5"),
        *vtable_era5_candidates,
    )

    wps_tables: dict[str, Any] = {}
    if geogrid_table:
        wps_tables["geogrid"] = geogrid_table
    if metgrid_table:
        wps_tables["metgrid"] = metgrid_table
    if vtable_gfs:
        wps_tables["vtable"] = vtable_gfs
        wps_tables["vtable_by_source"] = {
            "gfs": vtable_gfs,
            "fnl": vtable_gfs,
        }
        if vtable_era5:
            wps_tables["vtable_by_source"]["era5"] = vtable_era5
    elif vtable_era5:
        wps_tables["vtable_by_source"] = {"era5": vtable_era5}
    if wps_tables:
        config["wps_tables"] = wps_tables

    if discovery["include_hpc_template"]:
        hpc_example = load_json(repo_root() / "config" / "wrf_env.hpc.example.json")
        example_hpc = hpc_example.get("hpc", {}) if isinstance(hpc_example, dict) else {}
        config["hpc"] = deep_merge(example_hpc, request.get("hpc") or {})
    elif isinstance(request.get("hpc"), dict):
        config["hpc"] = deepcopy(request["hpc"])

    if isinstance(request.get("notifications"), dict):
        config["notifications"] = deep_merge(config["notifications"], request["notifications"])

    if isinstance(request.get("local"), dict):
        config["local"] = deep_merge(config["local"], request["local"])
        if not config["local"].get("mpi_cmd"):
            config["local"].pop("mpi_cmd", None)

    doctor = run_doctor(config)
    warnings.extend(doctor.get("warnings") or [])

    return {
        "request": request,
        "discovery": discovery,
        "config": config,
        "doctor": doctor,
        "warnings": _dedupe(warnings),
        "valid": bool(doctor.get("valid")),
    }



def _dedupe(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value)
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique



def run_doctor(config: dict[str, Any]) -> dict[str, Any]:
    doctor_script = repo_root() / "scripts" / "check_env.sh"
    with tempfile.TemporaryDirectory(prefix="wrf-bootstrap-") as tmp_dir:
        config_path = Path(tmp_dir) / "wrf_env.json"
        dump_json(config_path, config)
        completed = subprocess.run(
            ["bash", str(doctor_script), "--json", str(config_path)],
            capture_output=True,
            text=True,
            check=False,
        )

    stdout = completed.stdout.strip()
    if not stdout:
        return {
            "valid": False,
            "errors": ["Environment doctor produced no JSON output."],
            "warnings": [],
            "exit_code": completed.returncode,
        }

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {
            "valid": False,
            "errors": [f"Environment doctor returned invalid JSON: {exc}"],
            "warnings": [],
            "stdout": stdout,
            "stderr": completed.stderr.strip(),
            "exit_code": completed.returncode,
        }

    payload["exit_code"] = completed.returncode
    if completed.stderr.strip():
        payload["stderr"] = completed.stderr.strip()
    return payload



def load_bootstrap_request(path: Path | str | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("Bootstrap request JSON must be an object")
    return payload



def normalize_request(file_request: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    request = deepcopy(file_request)
    if args.profile is not None:
        request["profile"] = args.profile
    request.setdefault("profile", "auto")

    if args.prefer_system:
        request["prefer_repo_local"] = False
    elif args.prefer_repo_local:
        request["prefer_repo_local"] = True
    else:
        request.setdefault("prefer_repo_local", True)

    if args.include_hpc_template:
        request["include_hpc_template"] = True
    else:
        request.setdefault("include_hpc_template", False)

    if args.python_env is not None:
        request["python_env"] = args.python_env
    if args.geog_data_res is not None:
        request["geog_data_res"] = args.geog_data_res

    request.setdefault("paths", {})
    for key in PATH_KEYS:
        value = getattr(args, key)
        if value is not None:
            request["paths"][key] = value

    request.setdefault("local", {})
    if args.default_np is not None:
        request["local"]["default_np"] = args.default_np
    if args.mpi_cmd is not None:
        request["local"]["mpi_cmd"] = args.mpi_cmd

    return request



def bootstrap_to_output(
    output_path: Path | str,
    *,
    request: dict[str, Any] | None = None,
    allow_invalid: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    request_payload = request or {}
    result = build_config(request_payload)
    output = Path(output_path)
    written = False

    if not result["valid"] and not allow_invalid:
        result["output_path"] = output.resolve().as_posix()
        result["written"] = False
        return result

    if not dry_run:
        dump_json(output, result["config"])
        written = True

    result["output_path"] = output.resolve().as_posix()
    result["written"] = written
    return result



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect local WRF/WPS assets and generate wrf_env.json")
    parser.add_argument("--output", default="config/wrf_env.json")
    parser.add_argument("--bootstrap-config")
    parser.add_argument("--profile", choices=sorted(SUPPORTED_PROFILES))
    parser.add_argument("--wrf-dir")
    parser.add_argument("--wps-dir")
    parser.add_argument("--geog-data-path")
    parser.add_argument("--wrf-run-dir")
    parser.add_argument("--wps-bin-dir")
    parser.add_argument("--wps-support-dir")
    parser.add_argument("--python-env")
    parser.add_argument("--geog-data-res")
    parser.add_argument("--default-np", type=int)
    parser.add_argument("--mpi-cmd")
    parser.add_argument("--prefer-repo-local", action="store_true")
    parser.add_argument("--prefer-system", action="store_true")
    parser.add_argument("--include-hpc-template", action="store_true")
    parser.add_argument("--allow-invalid", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser



def main() -> int:
    args = build_parser().parse_args()
    file_request = load_bootstrap_request(args.bootstrap_config)
    request = normalize_request(file_request, args)
    result = bootstrap_to_output(
        args.output,
        request=request,
        allow_invalid=args.allow_invalid,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Bootstrap profile: {result['discovery']['requested_profile']} -> {result['discovery']['effective_profile']}")
        print(f"Detected host: {result['discovery']['host_kind']}")
        print(f"Output config: {result['output_path']}")
        for key in ("wrf_dir", "wps_dir", "geog_data_path", "wrf_run_dir", "wps_bin_dir"):
            source = result["discovery"]["sources"].get(key, {}).get("source")
            value = result["config"].get(key)
            print(f"{key}: {value} ({source or 'unresolved'})")
        print(f"Doctor valid: {result['valid']}")
        for warning in result["warnings"]:
            print(f"Warning: {warning}")
        if result["doctor"].get("errors"):
            for error in result["doctor"]["errors"]:
                print(f"Error: {error}")
        if result["written"]:
            print("Config written.")
        elif args.dry_run:
            print("Dry run only; config not written.")
        elif not result["valid"] and not args.allow_invalid:
            print("Config not written because validation failed.")

    return 0 if result["valid"] or args.allow_invalid else 1


if __name__ == "__main__":
    raise SystemExit(main())
