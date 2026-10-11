"""Rule-based ticker normalization. No per-ticker logic: rules plus the config override map."""

from __future__ import annotations

import re
from collections.abc import Mapping

_SHARE_CLASS = re.compile(r"[ .]+")


def normalize_symbol(raw: str | None, overrides: Mapping[str, str], null_token: str) -> str | None:
    """Upper-case; share-class separators become "-" (BRK B, BRK.B → BRK-B); overrides win."""
    if raw is None:
        return None
    text = " ".join(raw.split()).upper()
    if not text or text == null_token:
        return None
    table: dict[str, str] = {}
    for key, value in overrides.items():
        clean = " ".join(key.split()).upper()
        table[clean] = table[_SHARE_CLASS.sub("-", clean)] = value.strip().upper()
    if text in table:
        return table[text]
    hyphenated = _SHARE_CLASS.sub("-", text)
    return table.get(hyphenated, hyphenated)
