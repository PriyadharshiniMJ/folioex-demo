"""Canonical domain types shared by every layer. Broker formats are mapped onto these in ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class TxnType(StrEnum):
    """Canonical transaction categories. File labels map onto these via config ``[format.types]``."""

    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    WITHHOLDING = "WITHHOLDING"
    FEE = "FEE"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    INTEREST = "INTEREST"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    SPLIT = "SPLIT"
    OTHER = "OTHER"


# Only these categories ever change share balances (spec rule 2). OTHER never does.
POSITION_TYPES: frozenset[TxnType] = frozenset(
    {TxnType.BUY, TxnType.SELL, TxnType.TRANSFER_IN, TxnType.TRANSFER_OUT, TxnType.SPLIT}
)

# Money crossing the account boundary; everything else is performance.
EXTERNAL_FLOW_TYPES: frozenset[TxnType] = frozenset({TxnType.DEPOSIT, TxnType.WITHDRAWAL})

# Within one day, apply rows in this order so cash and shares are available when needed.
DAY_ORDER: dict[TxnType, int] = {
    TxnType.DEPOSIT: 0,
    TxnType.TRANSFER_IN: 1,
    TxnType.BUY: 2,
    TxnType.SPLIT: 3,
    TxnType.SELL: 4,
    TxnType.TRANSFER_OUT: 5,
    TxnType.DIVIDEND: 6,
    TxnType.WITHHOLDING: 7,
    TxnType.FEE: 8,
    TxnType.INTEREST: 9,
    TxnType.OTHER: 10,
    TxnType.WITHDRAWAL: 11,
}


@dataclass(frozen=True, slots=True)
class Transaction:
    """One row of history in canonical form.

    ``quantity`` is the signed change in shares (+ bought or received, − sold or sent);
    it is 0 when shares don't move.
    Money columns are in USD: ``net`` is what actually moved through cash.
    """

    date: date
    type: TxnType
    raw_type: str
    symbol: str | None
    quantity: float
    price: float | None
    currency: str | None
    gross: float
    commission: float
    net: float
    account: str | None
    source_file: str
    line: int

    def key(self) -> tuple[object, ...]:
        """Identity used to spot the same row appearing in overlapping files."""
        price = None if self.price is None else round(self.price, 6)
        return (self.date, self.type, self.symbol, round(self.quantity, 6), price, round(self.net, 4))

    def sort_key(self) -> tuple[object, ...]:
        return (self.date, DAY_ORDER[self.type])
