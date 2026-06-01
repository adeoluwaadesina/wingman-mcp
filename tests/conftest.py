from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path))
    # Reset any cached DB path between tests
    from wingman.storage import db as db_module
    db_module.set_db_path(None)
    yield tmp_path
