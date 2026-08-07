from __future__ import annotations

import os
from pathlib import Path


def default_db_path() -> Path:
    root = Path(os.environ.get("LUNCHTAG_DATA_DIR", "data"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "lunchtag.sqlite"
