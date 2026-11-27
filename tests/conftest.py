from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _run_from_repo_root(monkeypatch):
    """Tests refer to bots by repo-relative paths such as bots/examplebot."""
    monkeypatch.chdir(ROOT)
