"""Pytest fixtures for douyin-matrices.

Ensures ``src`` is importable and redirects pytest's ``tmp_path`` to a writable
workspace directory (the sandbox blocks the default ``%LOCALAPPDATA%\\Temp``).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Writable root for all temp dirs (avoids the blocked AppData\\Local\\Temp).
TMP_ROOT = ROOT / ".pytest_tmp"


@pytest.fixture
def tmp_path(request):
    """Redirect tmp_path to a workspace-local, writable directory."""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    raw = tempfile.mkdtemp(prefix=f"{request.node.name}-", dir=str(TMP_ROOT))
    return Path(raw)


@pytest.fixture(autouse=True)
def _matrix_tmp_data(tmp_path, monkeypatch):
    """Point DY_MATRICES_DATA at a fresh temp dir for each test."""
    data_dir = tmp_path / "matrix_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DY_MATRICES_DATA", str(data_dir))
    yield data_dir
    monkeypatch.delenv("DY_MATRICES_DATA", raising=False)
