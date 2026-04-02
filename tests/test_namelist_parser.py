import shutil
import unittest
from pathlib import Path

from scripts.namelist_parser import read_namelist, validate_namelist, write_namelist

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class NamelistParserTests(unittest.TestCase):
    def test_roundtrip_preserves_basic_values(self) -> None:
        config = {
            "share": {
                "wrf_core": "ARW",
                "max_dom": 2,
                "start_date": ["2024-07-20_00:00:00", "2024-07-20_00:00:00"],
            },
            "geogrid": {
                "parent_id": [1, 1],
                "parent_grid_ratio": [1, 3],
                "e_we": [100, 151],
                "e_sn": [100, 151],
                "dx": 27000,
                "dy": 27000,
            },
        }

        tmp_dir = make_test_dir("_test_namelist_parser")
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        namelist_path = tmp_dir / "namelist.wps"
        write_namelist(config, namelist_path)
        parsed = read_namelist(namelist_path)

        self.assertEqual(parsed["share"]["max_dom"], 2)
        self.assertEqual(parsed["geogrid"]["parent_grid_ratio"], [1, 3])
        self.assertEqual(parsed["geogrid"]["dx"], 27000)

    def test_validate_namelist_detects_length_mismatch(self) -> None:
        config = {
            "domains": {
                "max_dom": 2,
                "e_we": [100],
                "e_sn": [100, 151],
                "parent_id": [1, 1],
                "parent_grid_ratio": [1, 3],
                "dx": 27000,
                "dy": 27000,
                "time_step": 162,
            }
        }
        errors = validate_namelist(config)
        self.assertIn("e_we length must match max_dom", errors)


if __name__ == "__main__":
    unittest.main()
