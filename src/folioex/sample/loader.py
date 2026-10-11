"""The bundled sample files behind the "Load sample data" button."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from folioex.errors import FolioExError, IssueKind
from folioex.ingestion.pipeline import Upload


def sample_dir() -> Path:
    return Path(str(resources.files("folioex").joinpath("sample", "files")))


def sample_uploads() -> list[Upload]:
    files = sorted(sample_dir().glob("TxnHistory_*.csv")) if sample_dir().is_dir() else []
    if not files:
        raise FolioExError(IssueKind.SAMPLE_DATA_MISSING)
    return [Upload(path.name, path.read_bytes()) for path in files]
