"""Plain, consistent number and date wording shared by messages and the UI."""

from __future__ import annotations

from datetime import date

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def money(value: float, *, decimals: int = 2, sign: bool = False) -> str:
    """$1,234.50 · −$12.30 · +$5.00 (with ``sign``). Uses a true minus sign."""
    rounded = round(value, decimals)
    body = f"${abs(rounded):,.{decimals}f}"
    if rounded < 0:
        return "−" + body
    return ("+" + body) if sign and rounded > 0 else body


def quantity(value: float) -> str:
    """Share counts without noise: 18 · 2.5 · 0.125."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def display_date(value: date, date_format: str) -> str:
    return value.strftime(date_format)


def period_label(start: date, end: date) -> str:
    """Jan–Dec 2025 · Mar 2024–Feb 2025."""
    if start.year == end.year:
        return f"{MONTHS[start.month - 1]}–{MONTHS[end.month - 1]} {end.year}"
    return f"{MONTHS[start.month - 1]} {start.year}–{MONTHS[end.month - 1]} {end.year}"


def plural(count: int, noun: str, many: str | None = None) -> str:
    return f"{count:,} {noun if count == 1 else (many or noun + 's')}"


def percent(value: float | None, *, decimals: int = 0, sign: bool = False) -> str:
    """22% · +12.3% · −4% (true minus sign). None becomes an em dash."""
    if value is None:
        return "—"
    rounded = round(value * 100, decimals)
    body = f"{abs(rounded):.{decimals}f}%"
    if rounded < 0:
        return "−" + body
    return ("+" + body) if sign and rounded > 0 else body


def compact_money(value: float | None) -> str:
    """$3.4T · $80B · $950M · $12,000 for big company sizes; None becomes an em dash."""
    if value is None:
        return "—"
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= size:
            shown = value / size
            return f"${shown:.1f}{suffix}" if abs(shown) < 100 else f"${shown:.0f}{suffix}"
    return f"${value:,.0f}"
