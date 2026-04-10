import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import wrf


class WrfCliTests(unittest.TestCase):
    def test_init_forwards_to_legacy_script(self) -> None:
        with patch(
            "scripts.wrf.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ) as mocked_run:
            exit_code = wrf.main(["init", "--project-name", "demo"])

        self.assertEqual(exit_code, 0)
        mocked_run.assert_called_once()
        forwarded = mocked_run.call_args.args[0]
        self.assertEqual(
            forwarded,
            [
                sys.executable,
                str(Path(wrf.__file__).resolve().parent / "wrf_init.py"),
                "--project-name",
                "demo",
            ],
        )
        self.assertFalse(mocked_run.call_args.kwargs["check"])

    def test_data_forwards_to_task_start_with_fixed_step(self) -> None:
        with patch(
            "scripts.wrf.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=3),
        ) as mocked_run:
            exit_code = wrf.main(["data", "--project-name", "demo", "--wait"])

        self.assertEqual(exit_code, 3)
        forwarded = mocked_run.call_args.args[0]
        self.assertEqual(
            forwarded,
            [
                sys.executable,
                str(Path(wrf.__file__).resolve().parent / "wrf_task.py"),
                "start",
                "--step",
                "wrf-data",
                "--project-name",
                "demo",
                "--wait",
            ],
        )

    def test_help_for_task_step_forwards_help_to_underlying_parser(self) -> None:
        with patch(
            "scripts.wrf.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0),
        ) as mocked_run:
            exit_code = wrf.main(["help", "run"])

        self.assertEqual(exit_code, 0)
        forwarded = mocked_run.call_args.args[0]
        self.assertEqual(
            forwarded,
            [
                sys.executable,
                str(Path(wrf.__file__).resolve().parent / "wrf_task.py"),
                "start",
                "--step",
                "wrf-run",
                "--help",
            ],
        )

    def test_top_level_help_lists_preferred_entry(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = wrf.main([])

        self.assertEqual(exit_code, 0)
        help_text = buffer.getvalue()
        self.assertIn("Unified WRF workflow entry point.", help_text)
        self.assertIn("python3 scripts/wrf.py <command> [args...]", help_text)
        self.assertIn("Existing scripts/wrf_init.py", help_text)


if __name__ == "__main__":
    unittest.main()
