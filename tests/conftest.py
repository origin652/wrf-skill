from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

root_text = str(ROOT)
pythonpath_entries = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
if root_text not in pythonpath_entries:
    os.environ["PYTHONPATH"] = os.pathsep.join([root_text, *pythonpath_entries])
