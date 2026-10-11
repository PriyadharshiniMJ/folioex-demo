"""Split a sectioned CSV ("<Section>,Header,..." / "<Section>,Data,...") into named sections.

Unknown sections are kept but ignored by callers; section order doesn't matter;
a section may repeat its header.
A plain table (header row + data rows, no section columns) is accepted as one section when it has the
required columns.
"""

from __future__ import annotations

import codecs
import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass, field

from folioex.errors import FolioExError, IssueKind


def norm(name: str) -> str:
    """Loose header matching: case-insensitive, whitespace-insensitive."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True, slots=True)
class SectionRow:
    line: int
    values: dict[str, str]


@dataclass(slots=True)
class Section:
    name: str
    rows: list[SectionRow] = field(default_factory=list)
    columns: set[str] = field(default_factory=set)  # normalized names seen in any header

    def has_column(self, column: str) -> bool:
        return norm(column) in self.columns

    def get(self, row: SectionRow, column: str) -> str | None:
        wanted = norm(column)
        for key, value in row.values.items():
            if norm(key) == wanted:
                return value
        return None


def decode(raw: bytes, name: str) -> str:
    """Decode the encodings spreadsheets commonly save: UTF-8 (with or without BOM), UTF-16, Windows-1252."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FolioExError(IssueKind.FILE_UNREADABLE, file=name)


def split_sections(
    text: str, *, plain_section: str | None = None, plain_required: Sequence[str] = ()
) -> dict[str, Section]:
    records = list(enumerate(csv.reader(io.StringIO(text)), start=1))
    sections: dict[str, Section] = {}
    headers: dict[str, list[str]] = {}
    for line, cells in records:
        if len(cells) < 2:
            continue
        name, kind = cells[0].strip(), cells[1].strip()
        if kind == "Header":
            headers[name] = [c.strip() for c in cells[2:]]
            section = sections.setdefault(name, Section(name))
            section.columns.update(norm(c) for c in headers[name])
        elif kind == "Data" and name in headers:
            values = dict(zip(headers[name], (c.strip() for c in cells[2:]), strict=False))
            sections[name].rows.append(SectionRow(line, values))
    if not sections and plain_section:
        plain = _plain_table(records, plain_section, plain_required)
        if plain is not None:
            sections[plain_section] = plain
    return sections


def _plain_table(records: list[tuple[int, list[str]]], name: str, required: Sequence[str]) -> Section | None:
    filled = [(line, cells) for line, cells in records if any(c.strip() for c in cells)]
    if not filled:
        return None
    header = [c.strip() for c in filled[0][1]]
    section = Section(name, columns={norm(c) for c in header})
    if not all(section.has_column(r) for r in required):
        return None
    for line, cells in filled[1:]:
        section.rows.append(SectionRow(line, dict(zip(header, (c.strip() for c in cells), strict=False))))
    return section
