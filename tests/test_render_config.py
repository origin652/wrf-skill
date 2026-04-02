import shutil
import unittest
from pathlib import Path

from scripts.namelist_parser import read_namelist
from scripts.render_config import render_from_spec, validate_spec, write_rendered_files

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def sample_spec() -> dict:
    return {
        "project_name": "demo",
        "data_source": "gfs",
        "start_time": "2024-07-20_00:00:00",
        "end_time": "2024-07-20_06:00:00",
        "run_mode": "local",
        "domains": [
            {
                "name": "d01",
                "parent_id": 1,
                "parent_grid_ratio": 1,
                "dx_km": 27,
                "dy_km": 27,
                "e_we": 100,
                "e_sn": 100,
                "i_parent_start": 1,
                "j_parent_start": 1,
                "ref_lat": 31.2,
                "ref_lon": 121.5,
            },
            {
                "name": "d02",
                "parent_id": 1,
                "parent_grid_ratio": 3,
                "dx_km": 9,
                "dy_km": 9,
                "e_we": 151,
                "e_sn": 151,
                "i_parent_start": 30,
                "j_parent_start": 30,
                "ref_lat": 31.2,
                "ref_lon": 121.5,
            },
        ],
        "physics": {
            "mp_physics": 6,
            "cu_physics": 1,
            "ra_lw_physics": 4,
            "ra_sw_physics": 4,
            "bl_pbl_physics": 1,
        },
    }


class RenderConfigTests(unittest.TestCase):
    def test_validate_spec_accepts_sample(self) -> None:
        self.assertEqual(validate_spec(sample_spec()), [])

    def test_write_rendered_files_creates_both_namelists(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")
        tmp_dir = make_test_dir("_test_render_config")
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        written = write_rendered_files(rendered, tmp_dir)
        self.assertEqual(len(written), 2)
        namelist = read_namelist(tmp_dir / "namelist.wps")
        self.assertEqual(namelist["share"]["max_dom"], 2)
        namelist_input = read_namelist(tmp_dir / "namelist.input")
        self.assertEqual(namelist_input["namelist_quilt"]["nio_tasks_per_group"], 0)

    def test_render_from_spec_accepts_lowres_geog_data_res(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG", "lowres")
        self.assertEqual(rendered["namelist.wps"]["geogrid"]["geog_data_res"], ["lowres", "lowres"])

    def test_render_from_spec_defaults_e_vert_to_50(self) -> None:
        rendered = render_from_spec(sample_spec(), "/data/WPS_GEOG")
        self.assertEqual(rendered["namelist.input"]["domains"]["e_vert"], [50, 50])


if __name__ == "__main__":
    unittest.main()
