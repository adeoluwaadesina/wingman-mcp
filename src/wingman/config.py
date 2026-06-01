"""Filesystem paths for Wingman."""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "wingman"
APP_AUTHOR = "wingman"


def data_dir() -> Path:
    """Return the per-user data directory, creating it if necessary.

    Overridable via the ``WINGMAN_DATA_DIR`` environment variable (used by tests).
    """
    override = os.environ.get("WINGMAN_DATA_DIR")
    base = Path(override) if override else Path(user_data_dir(APP_NAME, APP_AUTHOR))
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "plans.db"
