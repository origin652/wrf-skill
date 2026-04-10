import shutil
import unittest
from http import client as http_client
from pathlib import Path
from unittest.mock import patch

from scripts.download_gfs import _download_one

TMP_ROOT = Path(__file__).resolve().parents[1] / "runs"


def make_test_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


class _FakeResponse:
    def __init__(self, chunks, *, status: int = 200, headers: dict[str, str] | None = None):
        self._chunks = list(chunks)
        self.status = status
        self._headers = dict(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        item = self._chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def getcode(self) -> int:
        return self.status

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)


class DownloadGfsTests(unittest.TestCase):
    def test_download_one_retries_after_incomplete_read(self) -> None:
        runs_dir = make_test_dir("_test_download_gfs_incomplete_read")
        self.addCleanup(lambda: shutil.rmtree(runs_dir, ignore_errors=True))
        data_dir = runs_dir / "data"
        request_item = {
            "file_name": "sample.grib2",
            "url": "https://example.test/sample.grib2",
        }

        seen_ranges: list[str | None] = []

        def fake_urlopen(request, timeout=30):
            del timeout
            range_header = request.get_header("Range") if hasattr(request, "get_header") else None
            seen_ranges.append(range_header)
            if len(seen_ranges) == 1:
                return _FakeResponse(
                    [
                        b"partial-",
                        http_client.IncompleteRead(b"tail", 10),
                    ]
                )
            return _FakeResponse(
                [b"rest"],
                status=206,
                headers={"Content-Range": "bytes 8-11/12"},
            )

        with patch("scripts.download_gfs.urllib_request.urlopen", side_effect=fake_urlopen):
            record = _download_one(
                request_item,
                data_dir,
                timeout=30,
                retries=1,
                overwrite=False,
            )

        target_path = data_dir / "sample.grib2"
        self.assertEqual(record["status"], "downloaded")
        self.assertEqual(record["attempts"], 2)
        self.assertTrue(target_path.exists())
        self.assertEqual(target_path.read_bytes(), b"partial-rest")
        self.assertFalse((data_dir / "sample.grib2.part").exists())
        self.assertEqual(seen_ranges, [None, "bytes=8-"])


if __name__ == "__main__":
    unittest.main()
