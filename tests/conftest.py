"""Shared fixtures. Every test runs with a clean environment and no config file unless it provides one."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from folioex.config import Settings, load_settings
from folioex.errors import MessageCatalog


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden", action="store_true", help="rewrite tests/golden/*.json from the current results"
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("FOLIOEX_"):
            monkeypatch.delenv(key)
    yield


@pytest.fixture
def settings() -> Settings:
    return load_settings()


@pytest.fixture
def catalog() -> MessageCatalog:
    return MessageCatalog.load()
