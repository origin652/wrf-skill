import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.utils import load_json
from scripts.wrf_bootstrap import bootstrap_to_output, build_config, host_kind

ROOT = Path(__file__).resolve().parents[1]
CHECK_ENV = ROOT / "scripts" / "check_env.sh"



def write_executable(path: Path, content: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)



def build_fake_wps_root(root: Path) -> None:
    bin_dir = root / "bin"
    write_executable(bin_dir / "geogrid")
    write_executable(bin_dir / "ungrib")
    write_executable(bin_dir / "metgrid")
    write_executable(bin_dir / "link_grib.csh")
    (root / "geogrid" / "GEOGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "geogrid" / "GEOGRID.TBL.ARW").write_text("geogrid\n", encoding="utf-8")
    (root / "metgrid" / "METGRID.TBL.ARW").parent.mkdir(parents=True, exist_ok=True)
    (root / "metgrid" / "METGRID.TBL.ARW").write_text("metgrid\n", encoding="utf-8")
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").parent.mkdir(parents=True, exist_ok=True)
    (root / "ungrib" / "Variable_Tables" / "Vtable.GFS").write_text("gfs\n", encoding="utf-8")
    (root / "ungrib" / "Variable_Tables" / "Vtable.ECMWF").write_text("era5\n", encoding="utf-8")



def build_fake_wrf_root(root: Path) -> None:
    write_executable(root / "bin" / "real")
    write_executable(root / "bin" / "wrf")
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "LANDUSE.TBL").write_text("landuse\n", encoding="utf-8")



def build_fake_geog_root(root: Path) -> None:
    category = root / "topo_gmted2010_5m"
    category.mkdir(parents=True, exist_ok=True)
    (category / "index").write_text("index\n", encoding="utf-8")



def build_fake_support_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "GEOGRID.TBL.ARW").write_text("geogrid\n", encoding="utf-8")
    (root / "METGRID.TBL.ARW").write_text("metgrid\n", encoding="utf-8")
    (root / "Vtable.GFS").write_text("gfs\n", encoding="utf-8")
    (root / "Vtable.ECMWF").write_text("era5\n", encoding="utf-8")



class WrfBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="wrf-bootstrap-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))
        self.wrf_root = self.tmp_dir / "wrf"
        self.wps_root = self.tmp_dir / "wps"
        self.geog_root = self.tmp_dir / "WPS_GEOG"
        self.support_root = self.tmp_dir / "wps-support"
        build_fake_wrf_root(self.wrf_root)
        build_fake_wps_root(self.wps_root)
        build_fake_geog_root(self.geog_root)
        build_fake_support_root(self.support_root)

    def test_check_env_json_reports_valid_fake_runtime(self) -> None:
        config_path = self.tmp_dir / "wrf_env.json"
        config_path.write_text(
            json.dumps(
                {
                    "platform": host_kind(),
                    "shell": "bash",
                    "wrf_dir": self.wrf_root.as_posix(),
                    "wps_dir": self.wps_root.as_posix(),
                    "geog_data_path": self.geog_root.as_posix(),
                    "run_mode": "local",
                    "local": {"default_np": 1},
                    "wrf_run_dir": (self.wrf_root / "run").as_posix(),
                    "wps_bin_dir": (self.wps_root / "bin").as_posix(),
                    "wps_tables": {
                        "geogrid": (self.support_root / "GEOGRID.TBL.ARW").as_posix(),
                        "metgrid": (self.support_root / "METGRID.TBL.ARW").as_posix(),
                        "vtable": (self.support_root / "Vtable.GFS").as_posix(),
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            ["bash", str(CHECK_ENV), "--json", str(config_path)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["paths"]["wrf_dir"]["path"], self.wrf_root.as_posix())
        self.assertTrue(payload["executables"]["wps"]["geogrid"]["found"])
        self.assertTrue(payload["executables"]["wrf"]["real"]["found"])
        self.assertFalse(payload["required_external_commands"])

    def test_bootstrap_writes_valid_config_from_explicit_paths(self) -> None:
        output_path = self.tmp_dir / "generated" / "wrf_env.json"
        result = bootstrap_to_output(
            output_path,
            request={
                "profile": "linux_prebuilt",
                "prefer_repo_local": False,
                "local": {"default_np": 1},
                "paths": {
                    "wrf_dir": self.wrf_root.as_posix(),
                    "wps_dir": self.wps_root.as_posix(),
                    "geog_data_path": self.geog_root.as_posix(),
                    "wps_support_dir": self.support_root.as_posix(),
                },
            },
        )

        self.assertTrue(result["valid"])
        self.assertTrue(result["written"])
        payload = load_json(output_path)
        self.assertEqual(payload["wrf_dir"], self.wrf_root.as_posix())
        self.assertEqual(payload["wps_dir"], self.wps_root.as_posix())
        self.assertEqual(payload["geog_data_path"], self.geog_root.as_posix())
        self.assertEqual(payload["wrf_run_dir"], (self.wrf_root / "run").as_posix())
        self.assertEqual(payload["wps_bin_dir"], (self.wps_root / "bin").as_posix())
        self.assertEqual(payload["wps_support_dir"], self.support_root.as_posix())
        self.assertEqual(payload["local"]["default_np"], 1)
        if "mpi_cmd" in payload["local"]:
            self.assertIsInstance(payload["local"]["mpi_cmd"], str)
        self.assertEqual(
            payload["wps_tables"]["vtable_by_source"]["era5"],
            (self.support_root / "Vtable.ECMWF").as_posix(),
        )

    def test_bootstrap_refuses_invalid_config_without_allow_invalid(self) -> None:
        output_path = self.tmp_dir / "invalid" / "wrf_env.json"
        missing_geog = self.tmp_dir / "missing-geog"
        result = bootstrap_to_output(
            output_path,
            request={
                "profile": "linux_prebuilt",
                "prefer_repo_local": False,
                "local": {"default_np": 1},
                "paths": {
                    "wrf_dir": self.wrf_root.as_posix(),
                    "wps_dir": self.wps_root.as_posix(),
                    "geog_data_path": missing_geog.as_posix(),
                    "wps_support_dir": self.support_root.as_posix(),
                },
            },
            allow_invalid=False,
        )

        self.assertFalse(result["valid"])
        self.assertFalse(result["written"])
        self.assertFalse(output_path.exists())
        self.assertTrue(any("Missing path" in item for item in result["doctor"]["errors"]))

    def test_build_config_with_hpc_template_merges_hpc_block(self) -> None:
        result = build_config(
            {
                "profile": "hpc_template",
                "prefer_repo_local": False,
                "local": {"default_np": 1},
                "paths": {
                    "wrf_dir": self.wrf_root.as_posix(),
                    "wps_dir": self.wps_root.as_posix(),
                    "geog_data_path": self.geog_root.as_posix(),
                    "wps_support_dir": self.support_root.as_posix(),
                },
                "hpc": {
                    "remote_host": "cluster.example",
                    "account": "science",
                },
            }
        )

        self.assertTrue(result["valid"])
        self.assertIn("hpc", result["config"])
        self.assertEqual(result["config"]["hpc"]["remote_host"], "cluster.example")
        self.assertEqual(result["config"]["hpc"]["account"], "science")
        self.assertEqual(result["config"]["wrf_dir"], self.wrf_root.as_posix())


if __name__ == "__main__":
    unittest.main()
