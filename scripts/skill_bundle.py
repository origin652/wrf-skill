from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from pathlib import Path

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_ROOT_NAME = "wrf-skill-bundle"
BUNDLE_MANIFEST_NAME = "bundle_manifest.json"
BUNDLE_INSTALL_NOTES = "INSTALL.txt"
BUNDLE_INCLUDE_PATHS = (
    ".claude/skills",
    ".gitignore",
    "config/domains_presets.json",
    "config/physics_schemes.json",
    "config/post_schema.json",
    "config/simulation_schema.json",
    "config/wrf_env.hpc.example.json",
    "runs/.gitkeep",
    "scripts",
    "templates",
    "third_party/wps-support",
)
SKIP_PART_NAMES = {"__pycache__", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

INSTALL_NOTES_TEMPLATE = """WRF skill bundle install
=======================

1. Extract this archive.
2. Install into a target workspace:

   python3 scripts/install_skill_bundle.py --target /path/to/workspace

3. For Codex, install the bundled skills with: `bash scripts/install_codex_skills.sh`
4. Then use `wrf-workspace-init` to create a clean workspace for the actual WRF work.
5. If you need HPC mode, copy config/wrf_env.hpc.example.json to config/wrf_env.json and fill in cluster-specific values.

The bundle intentionally excludes private configs, runs/, WPS_GEOG, and compiled artifacts.
"""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_PART_NAMES for part in path.parts) or path.suffix in SKIP_SUFFIXES


def collect_bundle_files(source_root: Path) -> list[str]:
    root = Path(source_root)
    files: set[str] = set()
    for rel_path in BUNDLE_INCLUDE_PATHS:
        candidate = root / rel_path
        if not candidate.exists():
            continue
        if candidate.is_file():
            rel = candidate.relative_to(root)
            if not _should_skip(rel):
                files.add(rel.as_posix())
            continue
        for child in candidate.rglob("*"):
            if not child.is_file():
                continue
            rel = child.relative_to(root)
            if _should_skip(rel):
                continue
            files.add(rel.as_posix())
    return sorted(files)


def build_bundle_manifest(source_root: Path, bundle_name: str = BUNDLE_ROOT_NAME) -> dict:
    files = collect_bundle_files(source_root)
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_name": bundle_name,
        "file_count": len(files),
        "files": files,
    }


def write_bundle_metadata(bundle_root: Path, manifest: dict) -> None:
    (bundle_root / BUNDLE_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (bundle_root / BUNDLE_INSTALL_NOTES).write_text(INSTALL_NOTES_TEMPLATE, encoding="utf-8")


def copy_bundle_files(source_root: Path, destination_root: Path, files: list[str]) -> None:
    for rel_path in files:
        source_path = source_root / rel_path
        destination_path = destination_root / rel_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        shutil.copymode(source_path, destination_path)


def stage_bundle(source_root: Path, bundle_root: Path, bundle_name: str = BUNDLE_ROOT_NAME) -> dict:
    source_root = Path(source_root).resolve()
    bundle_root = Path(bundle_root).resolve()
    manifest = build_bundle_manifest(source_root, bundle_name=bundle_name)
    bundle_root.mkdir(parents=True, exist_ok=True)
    copy_bundle_files(source_root, bundle_root, manifest["files"])
    write_bundle_metadata(bundle_root, manifest)
    return manifest


def load_bundle_manifest(source_root: Path, bundle_name: str = BUNDLE_ROOT_NAME) -> dict:
    source_root = Path(source_root).resolve()
    manifest_path = source_root / BUNDLE_MANIFEST_NAME
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return build_bundle_manifest(source_root, bundle_name=bundle_name)


def install_bundle(source_root: Path, target_root: Path, force: bool = False) -> dict:
    source_root = Path(source_root).resolve()
    target_root = Path(target_root).resolve()
    if source_root == target_root:
        raise ValueError("target_root must differ from source_root")

    manifest = load_bundle_manifest(source_root)
    files = manifest["files"]
    conflicts = [rel for rel in files if (target_root / rel).exists()]
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        raise FileExistsError(f"target already contains bundled files: {preview}")

    target_root.mkdir(parents=True, exist_ok=True)
    copy_bundle_files(source_root, target_root, files)
    write_bundle_metadata(target_root, manifest)
    return {
        "target_root": target_root.as_posix(),
        "file_count": len(files),
        "manifest_path": (target_root / BUNDLE_MANIFEST_NAME).as_posix(),
    }


def create_bundle_archive(
    source_root: Path,
    output_path: Path,
    bundle_name: str = BUNDLE_ROOT_NAME,
) -> dict:
    source_root = Path(source_root).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wrf-skill-bundle-") as tmp_dir:
        staged_root = Path(tmp_dir) / bundle_name
        manifest = stage_bundle(source_root, staged_root, bundle_name=bundle_name)
        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(staged_root, arcname=bundle_name)
    return {
        "archive": output_path.as_posix(),
        "bundle_name": bundle_name,
        "file_count": manifest["file_count"],
        "manifest": manifest,
    }
