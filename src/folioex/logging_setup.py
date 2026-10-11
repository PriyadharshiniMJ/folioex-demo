"""Technical logging: a rotating file under ~/.folioex/logs. Nothing technical is ever shown on screen.

Privacy: log issue kinds, file names and row numbers, never amounts or other transaction values.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from folioex.config import LoggingCfg

_MARK = "_folioex"
_LOGGERS = ("folioex", "py.warnings", "werkzeug")


def setup_logging(cfg: LoggingCfg) -> Path:
    """Attach FolioEx's file handler (idempotent) and route Python warnings into it. Returns the log path."""
    teardown_logging()
    cfg.dir.mkdir(parents=True, exist_ok=True)
    path = cfg.dir / "folioex.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    setattr(handler, _MARK, True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    for name in _LOGGERS:
        logging.getLogger(name).addHandler(handler)
    logging.getLogger("folioex").setLevel(cfg.level)
    # Per-request lines from the local web server are noise; keep only its warnings.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.captureWarnings(True)
    return path


def teardown_logging() -> None:
    """Remove and close FolioEx's handlers (used by tests and on re-setup)."""
    seen: set[int] = set()
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if getattr(handler, _MARK, False):
                logger.removeHandler(handler)
                if id(handler) not in seen:
                    seen.add(id(handler))
                    handler.close()
    logging.captureWarnings(False)
