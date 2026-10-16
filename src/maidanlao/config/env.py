"""Minimal .env loader (stdlib-only, no python-dotenv dependency).

Project settings are read via os.getenv() throughout the codebase. This module
ensures a root-level ``.env`` file is loaded into the process environment
before those reads happen, so operators have a single file to edit instead of
exporting variables in every shell.

The loader is idempotent and never overrides variables already present in the
environment (shell exports win), which keeps deployment via systemd
``EnvironmentFile`` working unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path


def _project_root() -> Path:
    # This module lives at <root>/src/maidanlao/config/env.py.
    return Path(__file__).resolve().parents[3]


def load_env_file(path: str | os.PathLike[str] | None = None) -> Path | None:
    """Parse KEY=VALUE lines from ``.env`` into ``os.environ``.

    Returns the path that was loaded, or None when no file existed. Existing
    environment values take precedence; comments and empty lines are skipped.
    Values may be quoted with single or double quotes; quotes are stripped.
    """
    env_path = Path(path) if path else _project_root() / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return env_path


def ensure_env_loaded() -> None:
    """Call once at import time so config reads see ``.env`` values."""
    if getattr(ensure_env_loaded, "_loaded", False):
        return
    load_env_file()
    ensure_env_loaded._loaded = True  # type: ignore[attr-defined]


ensure_env_loaded()
