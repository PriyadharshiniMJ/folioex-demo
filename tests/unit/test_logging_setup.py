"""Technical details go to a rotating log file, never to the screen."""

import logging
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from folioex.config import LoggingCfg
from folioex.logging_setup import setup_logging, teardown_logging


@pytest.fixture
def log_dir(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "logs"
    teardown_logging()


def read(path: Path) -> str:
    for handler in logging.getLogger("folioex").handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_creates_log_file_and_writes(log_dir: Path) -> None:
    path = setup_logging(LoggingCfg(dir=log_dir, level="INFO"))
    assert path == log_dir / "folioex.log"
    logging.getLogger("folioex.test").info("hello from a test")
    assert "hello from a test" in read(path)


def test_tracebacks_land_in_the_file(log_dir: Path) -> None:
    path = setup_logging(LoggingCfg(dir=log_dir))
    try:
        raise KeyError("boom")
    except KeyError:
        logging.getLogger("folioex.test").exception("callback failed")
    text = read(path)
    assert "Traceback" in text
    assert "KeyError" in text


def test_python_warnings_are_routed_to_the_log(log_dir: Path) -> None:
    path = setup_logging(LoggingCfg(dir=log_dir))
    with warnings.catch_warnings():
        warnings.simplefilter("default")
        warnings.warn("a library grumbled", UserWarning, stacklevel=1)
    for handler in logging.getLogger("py.warnings").handlers:
        handler.flush()
    assert "a library grumbled" in path.read_text(encoding="utf-8")


def test_setup_is_idempotent(log_dir: Path) -> None:
    setup_logging(LoggingCfg(dir=log_dir))
    setup_logging(LoggingCfg(dir=log_dir))
    ours = [h for h in logging.getLogger("folioex").handlers if getattr(h, "_folioex", False)]
    assert len(ours) == 1
