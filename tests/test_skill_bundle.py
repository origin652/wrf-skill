import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.skill_bundle import (
    BUNDLE_INSTALL_NOTES,
    BUNDLE_MANIFEST_NAME,
    build_bundle_manifest,
    create_bundle_archive,
    install_bundle,
    stage_bundle,
)

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class SkillBundleTests(unittest.TestCase):
    def create_source_tree(self, root: Path) -> None:
        (root / ".claude" / "skills" / "wrf").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "skills" / "wrf" / "SKILL.md").write_text("wrf\n", encoding="utf-8")
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "wrf_task.py").write_text("print('task')\n", encoding="utf-8")
        (root / "scripts" / "install_skill_bundle.py").write_text("print('install')\n", encoding="utf-8")
        (root / "scripts" / "__pycache__").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "__pycache__" / "skip.pyc").write_text("x\n", encoding="utf-8")
        (root / "templates").mkdir(parents=True, exist_ok=True)
        (root / "templates" / "namelist.input.template").write_text("&time_control\n", encoding="utf-8")
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "config" / "domains_presets.json").write_text("{}\n", encoding="utf-8")
        (root / "config" / "physics_schemes.json").write_text("{}\n", encoding="utf-8")
        (root / "config" / "simulation_schema.json").write_text("{}\n", encoding="utf-8")
        (root / "config" / "wrf_env.hpc.example.json").write_text("{}\n", encoding="utf-8")
        (root / "config" / "wrf_env.json").write_text('{"private": true}\n', encoding="utf-8")
        (root / "config" / "wrf_env.lowres.json").write_text('{"lowres": true}\n', encoding="utf-8")
        (root / "third_party" / "wps-support").mkdir(parents=True, exist_ok=True)
        (root / "third_party" / "wps-support" / "Vtable.GFS").write_text("vtable\n", encoding="utf-8")
        (root / "third_party" / "WPS_GEOG").mkdir(parents=True, exist_ok=True)
        (root / "third_party" / "WPS_GEOG" / "big.bin").write_text("skip\n", encoding="utf-8")
        (root / "runs").mkdir(parents=True, exist_ok=True)
        (root / "runs" / ".gitkeep").write_text("", encoding="utf-8")
        (root / "runs" / "demo").mkdir(parents=True, exist_ok=True)
        (root / "runs" / "demo" / "project.json").write_text("{}\n", encoding="utf-8")
        (root / ".gitignore").write_text("runs/*\n", encoding="utf-8")

    def test_manifest_includes_only_whitelisted_files(self) -> None:
        source_root = make_test_dir("_test_skill_bundle_manifest")
        self.addCleanup(lambda: shutil.rmtree(source_root, ignore_errors=True))
        self.create_source_tree(source_root)

        manifest = build_bundle_manifest(source_root)
        files = set(manifest["files"])

        self.assertIn(".claude/skills/wrf/SKILL.md", files)
        self.assertIn("scripts/wrf_task.py", files)
        self.assertIn("config/wrf_env.hpc.example.json", files)
        self.assertIn("third_party/wps-support/Vtable.GFS", files)
        self.assertNotIn("config/wrf_env.json", files)
        self.assertNotIn("config/wrf_env.lowres.json", files)
        self.assertNotIn("runs/demo/project.json", files)
        self.assertNotIn("third_party/WPS_GEOG/big.bin", files)
        self.assertFalse(any("__pycache__" in path for path in files))

    def test_stage_and_install_bundle(self) -> None:
        source_root = make_test_dir("_test_skill_bundle_install_source")
        target_root = make_test_dir("_test_skill_bundle_install_target")
        staged_parent = make_test_dir("_test_skill_bundle_staged")
        shutil.rmtree(target_root, ignore_errors=True)
        self.addCleanup(lambda: shutil.rmtree(source_root, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(target_root, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(staged_parent, ignore_errors=True))
        self.create_source_tree(source_root)

        bundle_root = staged_parent / "wrf-skill-bundle"
        manifest = stage_bundle(source_root, bundle_root)
        install_payload = install_bundle(bundle_root, target_root)

        self.assertEqual(install_payload["file_count"], manifest["file_count"])
        self.assertTrue((target_root / BUNDLE_MANIFEST_NAME).exists())
        self.assertTrue((target_root / BUNDLE_INSTALL_NOTES).exists())
        self.assertTrue((target_root / ".claude" / "skills" / "wrf" / "SKILL.md").exists())
        self.assertTrue((target_root / "scripts" / "wrf_task.py").exists())
        self.assertFalse((target_root / "config" / "wrf_env.json").exists())
        self.assertFalse((target_root / "runs" / "demo" / "project.json").exists())

        with self.assertRaises(FileExistsError):
            install_bundle(bundle_root, target_root)

    def test_create_bundle_archive_writes_tarball(self) -> None:
        source_root = make_test_dir("_test_skill_bundle_archive_source")
        archive_root = make_test_dir("_test_skill_bundle_archive_out")
        self.addCleanup(lambda: shutil.rmtree(source_root, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(archive_root, ignore_errors=True))
        self.create_source_tree(source_root)

        archive_path = archive_root / "wrf-skill-bundle.tar.gz"
        payload = create_bundle_archive(source_root, archive_path)

        self.assertEqual(payload["archive"], archive_path.resolve().as_posix())
        self.assertTrue(archive_path.exists())
        with tarfile.open(archive_path, "r:gz") as archive:
            names = set(archive.getnames())
        self.assertIn("wrf-skill-bundle/bundle_manifest.json", names)
        self.assertIn("wrf-skill-bundle/INSTALL.txt", names)
        self.assertIn("wrf-skill-bundle/.claude/skills/wrf/SKILL.md", names)
        self.assertNotIn("wrf-skill-bundle/config/wrf_env.json", names)

        with tempfile.TemporaryDirectory(prefix="wrf-skill-bundle-test-") as tmp_dir:
            with tarfile.open(archive_path, "r:gz") as archive:
                archive.extractall(tmp_dir)
            manifest = json.loads(
                (Path(tmp_dir) / "wrf-skill-bundle" / BUNDLE_MANIFEST_NAME).read_text(encoding="utf-8")
            )
        self.assertGreater(manifest["file_count"], 0)


if __name__ == "__main__":
    unittest.main()
