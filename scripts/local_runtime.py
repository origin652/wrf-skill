from __future__ import annotations

import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

SAFE_LOCAL_MODE = "custom_safe"
PROJECT_LOCAL_MODE = "project"
ALLOWED_LOCAL_MODES = {PROJECT_LOCAL_MODE, SAFE_LOCAL_MODE}
ALLOWED_LAUNCHERS = {"mpirun", "mpiexec", "srun"}
DISALLOWED_EXECUTABLE_NAMES = {
    "bash",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "pwsh",
    "sh",
    "zsh",
}
DISALLOWED_ENV_KEYS = {
    "BASH_ENV",
    "CDPATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "ENV",
    "GCONV_PATH",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PATH",
    "PROMPT_COMMAND",
    "PYTHONHOME",
    "PYTHONPATH",
    "SHELLOPTS",
}
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z0-9_]+)\}")


class LocalRuntimeConfigError(ValueError):
    pass


def _local_block(config: dict[str, Any]) -> dict[str, Any]:
    local = config.get("local", {})
    if not isinstance(local, dict):
        raise LocalRuntimeConfigError("config.local must be an object")
    return local


def local_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    runtime = deepcopy(_local_block(config).get("runtime", {}))
    runtime["mode"] = str(runtime.get("mode") or PROJECT_LOCAL_MODE).strip().lower()
    return runtime


def local_wps_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    runtime = deepcopy(_local_block(config).get("wps_runtime", {}))
    runtime["mode"] = str(runtime.get("mode") or PROJECT_LOCAL_MODE).strip().lower()
    return runtime


def _validate_mode(runtime: dict[str, Any], field_name: str) -> None:
    mode = runtime["mode"]
    if mode not in ALLOWED_LOCAL_MODES:
        raise LocalRuntimeConfigError(
            f"{field_name}.mode must be one of {sorted(ALLOWED_LOCAL_MODES)}, received: {mode}"
        )


def _validate_env_map(value: Any, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise LocalRuntimeConfigError(f"{field_name} must be an object of string pairs")
    for key, item in value.items():
        normalized_key = str(key)
        if not ENV_KEY_PATTERN.fullmatch(normalized_key):
            raise LocalRuntimeConfigError(f"Invalid environment variable name: {key}")
        if normalized_key.upper() in DISALLOWED_ENV_KEYS:
            raise LocalRuntimeConfigError(f"{field_name}.{normalized_key} is not allowed in custom_safe mode")
        if not isinstance(item, str):
            raise LocalRuntimeConfigError(f"{field_name}.{key} must be a string")


def _validate_string_list(
    value: Any,
    field_name: str,
    *,
    absolute: bool = False,
    reject_root: bool = False,
) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise LocalRuntimeConfigError(f"{field_name} must be a list of strings")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise LocalRuntimeConfigError(f"{field_name}[{index}] must be a non-empty string")
        if absolute:
            path = Path(item).expanduser()
            if not path.is_absolute():
                raise LocalRuntimeConfigError(f"{field_name}[{index}] must be an absolute path")
            if reject_root and path.resolve() == Path(path.anchor):
                raise LocalRuntimeConfigError(f"{field_name}[{index}] must not be a filesystem root")


def _validate_command_template(
    value: Any,
    field_name: str,
    *,
    required: bool,
) -> None:
    if value is None:
        if required:
            raise LocalRuntimeConfigError(f"{field_name} is required when mode=custom_safe")
        return
    if not isinstance(value, list) or not value:
        raise LocalRuntimeConfigError(f"{field_name} must be a non-empty list of strings")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise LocalRuntimeConfigError(f"{field_name}[{index}] must be a non-empty string")


def _validate_runtime_common(runtime: dict[str, Any], field_name: str) -> None:
    _validate_mode(runtime, field_name)
    if runtime["mode"] != SAFE_LOCAL_MODE:
        return
    _validate_env_map(runtime.get("env"), f"{field_name}.env")
    _validate_string_list(
        runtime.get("prepend_path"),
        f"{field_name}.prepend_path",
        absolute=True,
        reject_root=True,
    )
    _validate_string_list(
        runtime.get("trusted_exec_roots"),
        f"{field_name}.trusted_exec_roots",
        absolute=True,
        reject_root=True,
    )


def validate_local_runtime_sections(config: dict[str, Any]) -> None:
    runtime = local_runtime_config(config)
    _validate_runtime_common(runtime, "local.runtime")
    if runtime["mode"] == SAFE_LOCAL_MODE:
        _validate_command_template(runtime.get("real_cmd"), "local.runtime.real_cmd", required=False)
        _validate_command_template(runtime.get("wrf_cmd"), "local.runtime.wrf_cmd", required=True)

    wps_runtime = local_wps_runtime_config(config)
    _validate_runtime_common(wps_runtime, "local.wps_runtime")
    if wps_runtime["mode"] == SAFE_LOCAL_MODE:
        for key in ("geogrid_cmd", "link_grib_cmd", "ungrib_cmd", "metgrid_cmd"):
            _validate_command_template(
                wps_runtime.get(key),
                f"local.wps_runtime.{key}",
                required=True,
            )


def _launcher_from_template(template: Any) -> str | None:
    if not isinstance(template, list) or not template:
        return None
    first = str(template[0]).strip()
    if first in ALLOWED_LAUNCHERS:
        return first
    return None


def required_local_external_commands(config: dict[str, Any]) -> list[str]:
    validate_local_runtime_sections(config)
    commands: set[str] = set()
    local = _local_block(config)

    runtime = local_runtime_config(config)
    if runtime["mode"] == SAFE_LOCAL_MODE:
        for key in ("real_cmd", "wrf_cmd"):
            launcher = _launcher_from_template(runtime.get(key))
            if launcher:
                commands.add(launcher)
    else:
        np = max(1, int(local.get("default_np") or 1))
        mpi_cmd = str(local.get("mpi_cmd") or "").strip()
        if np > 1 and mpi_cmd:
            commands.add(mpi_cmd)

    wps_runtime = local_wps_runtime_config(config)
    if wps_runtime["mode"] == SAFE_LOCAL_MODE:
        for key in ("geogrid_cmd", "link_grib_cmd", "ungrib_cmd", "metgrid_cmd"):
            launcher = _launcher_from_template(wps_runtime.get(key))
            if launcher:
                commands.add(launcher)

    return sorted(commands)


def trusted_exec_roots(
    config: dict[str, Any],
    runtime: dict[str, Any],
    *,
    project_root: Path,
) -> list[Path]:
    roots: list[Path] = [project_root.resolve()]
    for key in ("wrf_dir", "wrf_run_dir", "wps_dir", "wps_bin_dir"):
        value = config.get(key)
        if value:
            roots.append(Path(str(value)).expanduser().resolve())
    for raw_path in runtime.get("trusted_exec_roots") or []:
        roots.append(Path(str(raw_path)).expanduser().resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        token = root.as_posix()
        if token in seen:
            continue
        seen.add(token)
        unique.append(root)
    return unique


def _format_token(template: str, context: dict[str, Any], allowed_placeholders: set[str]) -> list[str]:
    placeholders = PLACEHOLDER_PATTERN.findall(template)
    for key in placeholders:
        if key not in allowed_placeholders:
            raise LocalRuntimeConfigError(f"Unsupported placeholder {{{key}}} in command template")

    if placeholders and template == "{" + placeholders[0] + "}" and len(placeholders) == 1:
        value = context[placeholders[0]]
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    rendered = template
    for key in placeholders:
        value = context[key]
        if isinstance(value, list):
            raise LocalRuntimeConfigError(
                f"List placeholder {{{key}}} must occupy the entire command token"
            )
        rendered = rendered.replace("{" + key + "}", str(value))

    if PLACEHOLDER_PATTERN.search(rendered):
        raise LocalRuntimeConfigError(f"Unresolved placeholder in command token: {rendered}")
    if not rendered.strip():
        raise LocalRuntimeConfigError("Rendered command token must not be empty")
    return [rendered]


def render_command_template(
    template: list[str],
    *,
    context: dict[str, Any],
    allowed_placeholders: set[str],
) -> list[str]:
    if not isinstance(template, list) or not template:
        raise LocalRuntimeConfigError("Command template must be a non-empty list of strings")

    rendered: list[str] = []
    for token in template:
        if not isinstance(token, str) or not token.strip():
            raise LocalRuntimeConfigError("Command template tokens must be non-empty strings")
        rendered.extend(_format_token(token, context, allowed_placeholders))
    if not rendered:
        raise LocalRuntimeConfigError("Rendered command must not be empty")
    return rendered


def _resolve_path_token(token: str, *, cwd: Path) -> Path | None:
    if not token:
        return None
    if "/" not in token and "\\" not in token and not Path(token).is_absolute():
        return None
    path = Path(token)
    if not path.is_absolute():
        path = cwd / path
    return path.expanduser().resolve()


def _is_within_any_root(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_internal_exec(token: str, internal_execs: set[str]) -> bool:
    return token in internal_execs


def _build_search_path(prepend_path: list[str] | None = None) -> str | None:
    path_items: list[str] = []
    for raw_path in prepend_path or []:
        path_items.append(str(Path(str(raw_path)).expanduser()))

    existing_path = os.environ.get("PATH")
    if existing_path:
        path_items.extend(part for part in existing_path.split(os.pathsep) if part)

    if not path_items:
        return None
    return os.pathsep.join(path_items)


def _resolve_launcher_path(name: str, *, prepend_path: list[str] | None = None) -> Path | None:
    resolved = shutil.which(name, path=_build_search_path(prepend_path))
    if resolved is None:
        return None
    return Path(resolved).expanduser().resolve()


def _validate_target_token(
    token: str,
    *,
    cwd: Path,
    trusted_roots: list[Path],
    internal_execs: set[str],
    require_exists: bool,
    require_internal_exists: bool,
) -> bool:
    if _is_internal_exec(token, internal_execs):
        if not require_internal_exists:
            return True
        candidate = Path(token)
        return candidate.exists() and os.access(candidate, os.X_OK)

    candidate = _resolve_path_token(token, cwd=cwd)
    if candidate is None:
        return False
    if candidate.name.lower() in DISALLOWED_EXECUTABLE_NAMES:
        return False
    if not _is_within_any_root(candidate, trusted_roots):
        return False
    if require_exists and (not candidate.exists() or not os.access(candidate, os.X_OK)):
        return False
    return True


def validate_rendered_command(
    command: list[str],
    *,
    cwd: Path,
    trusted_roots: list[Path],
    internal_execs: list[Path],
    require_target_exists: bool = True,
    require_internal_execs: bool = False,
    prepend_path: list[str] | None = None,
) -> None:
    if not command:
        raise LocalRuntimeConfigError("Rendered command must not be empty")

    internal_exec_tokens = {path.resolve().as_posix() for path in internal_execs}
    first = str(command[0]).strip()
    if not first:
        raise LocalRuntimeConfigError("Rendered command must start with a non-empty token")

    if first in ALLOWED_LAUNCHERS:
        runtime_launcher_path = _resolve_launcher_path(first, prepend_path=prepend_path)
        if runtime_launcher_path is None:
            raise LocalRuntimeConfigError(f"Missing launcher command: {first}")
        system_launcher_path = _resolve_launcher_path(first)
        if system_launcher_path is None:
            if not _is_within_any_root(runtime_launcher_path, trusted_roots):
                raise LocalRuntimeConfigError(
                    f"Launcher command {first} must resolve from a trusted root"
                )
        elif runtime_launcher_path != system_launcher_path and not _is_within_any_root(
            runtime_launcher_path,
            trusted_roots,
        ):
            raise LocalRuntimeConfigError(
                f"Launcher command {first} must resolve from the current PATH or a trusted root"
            )
        for token in command[1:]:
            if _validate_target_token(
                str(token),
                cwd=cwd,
                trusted_roots=trusted_roots,
                internal_execs=internal_exec_tokens,
                require_exists=require_target_exists,
                require_internal_exists=require_internal_execs,
            ):
                return
        raise LocalRuntimeConfigError(
            f"Launcher command {first} must reference a trusted executable target"
        )

    first_path = _resolve_path_token(first, cwd=cwd)
    if first_path is not None and first_path.name.lower() in DISALLOWED_EXECUTABLE_NAMES:
        raise LocalRuntimeConfigError("Direct shell executables are not allowed in custom_safe mode")

    if _validate_target_token(
        first,
        cwd=cwd,
        trusted_roots=trusted_roots,
        internal_execs=internal_exec_tokens,
        require_exists=require_target_exists,
        require_internal_exists=require_internal_execs,
    ):
        return

    raise LocalRuntimeConfigError(
        "The first command token must be a trusted executable path or an allowed launcher"
    )


def build_process_env(
    command: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    prepend_path: list[str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    if prepend_path:
        path_items = [str(Path(str(item)).expanduser()) for item in prepend_path if str(item)]
        existing_path = env.get("PATH")
        if existing_path:
            path_items.append(existing_path)
        env["PATH"] = os.pathsep.join(path_items)
    if env_overrides:
        env.update(env_overrides)

    launcher = Path(command[0]).name if command else ""
    try:
        is_root = os.geteuid() == 0
    except AttributeError:  # pragma: no cover
        is_root = False
    if is_root and launcher in {"mpirun", "mpiexec"}:
        env.setdefault("OMPI_ALLOW_RUN_AS_ROOT", "1")
        env.setdefault("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1")
    return env
