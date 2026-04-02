import shutil
import subprocess
import unittest
from pathlib import Path

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class HpcTransferScriptTests(unittest.TestCase):
    def test_sync_hpc_local_mode_copies_project_tree(self) -> None:
        root = make_test_dir("_test_sync_hpc_local_mode")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        source_dir = root / "source"
        target_dir = root / "target"
        (source_dir / "config").mkdir(parents=True, exist_ok=True)
        (source_dir / "data").mkdir(parents=True, exist_ok=True)
        (source_dir / "output").mkdir(parents=True, exist_ok=True)
        (source_dir / "config" / "namelist.input").write_text("namelist\n", encoding="utf-8")
        (source_dir / "data" / "ignored.txt").write_text("data\n", encoding="utf-8")
        (source_dir / "output" / "ignored.txt").write_text("output\n", encoding="utf-8")

        subprocess.run(
            ["bash", str(SCRIPTS_DIR / "sync_hpc.sh"), "login", str(source_dir), "-", str(target_dir)],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertTrue((target_dir / "config" / "namelist.input").exists())
        self.assertFalse((target_dir / "data").exists())
        self.assertFalse((target_dir / "output").exists())

    def test_collect_hpc_local_mode_copies_expected_outputs(self) -> None:
        root = make_test_dir("_test_collect_hpc_local_mode")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        remote_dir = root / "remote_project"
        local_dir = root / "local_project"
        (remote_dir / "logs").mkdir(parents=True, exist_ok=True)
        (remote_dir / "output").mkdir(parents=True, exist_ok=True)
        (remote_dir / "wrf").mkdir(parents=True, exist_ok=True)
        (remote_dir / "logs" / "rsl.out.0000").write_text("log\n", encoding="utf-8")
        (remote_dir / "output" / "wrfout_d01_2024-07-20_00:00:00").write_text("out\n", encoding="utf-8")
        (remote_dir / "wrf" / "wrfinput_d01").write_text("input\n", encoding="utf-8")
        (remote_dir / "wrf" / "wrfbdy_d01").write_text("bdy\n", encoding="utf-8")
        (remote_dir / "wrf" / "namelist.output").write_text("namelist\n", encoding="utf-8")
        (remote_dir / "wrf" / "ignore.me").write_text("ignore\n", encoding="utf-8")

        subprocess.run(
            ["bash", str(SCRIPTS_DIR / "collect_hpc.sh"), "login", "-", str(remote_dir), str(local_dir)],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertTrue((local_dir / "logs" / "rsl.out.0000").exists())
        self.assertTrue((local_dir / "output" / "wrfout_d01_2024-07-20_00:00:00").exists())
        self.assertTrue((local_dir / "wrf" / "wrfinput_d01").exists())
        self.assertTrue((local_dir / "wrf" / "wrfbdy_d01").exists())
        self.assertTrue((local_dir / "wrf" / "namelist.output").exists())
        self.assertFalse((local_dir / "wrf" / "ignore.me").exists())


if __name__ == "__main__":
    unittest.main()
