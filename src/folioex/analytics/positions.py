"""FIFO lots, realized gains, splits and per-symbol income, replayed from the transaction history.

Rules: cost basis includes buy commissions; realized gain = net sell proceeds − FIFO cost. A split (from the
file, or from market data when the file has none for that symbol nearby) rescales lot quantities
and keeps cost.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from folioex.domain.models import Transaction, TxnType
from folioex.errors import Issue, IssueKind, Severity
from folioex.formatting import display_date, quantity

_EPS = 1e-9
_SPLIT_MATCH = timedelta(days=7)


@dataclass(slots=True)
class Lot:
    date: date
    quantity: float
    cost: float


@dataclass(slots=True)
class Position:
    symbol: str
    lots: list[Lot] = field(default_factory=list)
    realized: float = 0.0
    dividends: float = 0.0
    withholding: float = 0.0
    fees: float = 0.0
    first_date: date | None = None

    @property
    def shares(self) -> float:
        return sum(lot.quantity for lot in self.lots)

    @property
    def cost(self) -> float:
        return sum(lot.cost for lot in self.lots)

    @property
    def dividends_net(self) -> float:
        return self.dividends + self.withholding + self.fees


@dataclass(frozen=True, slots=True)
class RealizedGain:
    date: date
    symbol: str
    quantity: float
    proceeds: float
    cost: float

    @property
    def gain(self) -> float:
        return self.proceeds - self.cost


@dataclass(slots=True)
class PositionBook:
    positions: dict[str, Position]
    realized: list[RealizedGain]
    issues: list[Issue]
    last_trade: dict[str, tuple[date, float]]  # price in today's share basis


def _ratio_text(ratio: float) -> str:
    return f"{quantity(ratio)}-for-1" if ratio >= 1 else f"1-for-{quantity(1 / ratio)}"


class _Replay:
    def __init__(self, price_on: Callable[[str, date], float | None] | None, date_format: str) -> None:
        self.positions: dict[str, Position] = {}
        self.realized: list[RealizedGain] = []
        self.issues: list[Issue] = []
        self.last_trade: dict[str, tuple[date, float]] = {}
        self.price_on = price_on
        self.date_format = date_format

    def position(self, symbol: str, day: date) -> Position:
        pos = self.positions.setdefault(symbol, Position(symbol))
        if pos.first_date is None:
            pos.first_date = day
        return pos

    def apply(self, t: Transaction) -> None:
        if t.symbol is None:
            return
        handler = {
            TxnType.BUY: self.buy,
            TxnType.TRANSFER_IN: self.transfer_in,
            TxnType.SELL: self.sell,
            TxnType.TRANSFER_OUT: self.transfer_out,
            TxnType.SPLIT: self.file_split,
            TxnType.DIVIDEND: self.income,
            TxnType.WITHHOLDING: self.income,
            TxnType.FEE: self.income,
        }.get(t.type)
        if handler is not None:
            handler(t)

    def buy(self, t: Transaction) -> None:
        assert t.symbol is not None
        self.position(t.symbol, t.date).lots.append(Lot(t.date, t.quantity, -t.net))
        if t.price is not None:
            self.last_trade[t.symbol] = (t.date, t.price)

    def transfer_in(self, t: Transaction) -> None:
        assert t.symbol is not None
        price = t.price
        if price is None and self.price_on is not None:
            price = self.price_on(t.symbol, t.date)
            self.issues.append(
                Issue(
                    IssueKind.TRANSFER_COST_ESTIMATED,
                    Severity.INFO,
                    {"symbol": t.symbol, "date": display_date(t.date, self.date_format)},
                )
            )
        self.position(t.symbol, t.date).lots.append(Lot(t.date, t.quantity, t.quantity * (price or 0.0)))

    def _consume(self, pos: Position, wanted: float) -> tuple[float, float]:
        """Take shares from the oldest lots first. Returns (shares taken, their cost)."""
        taken = cost = 0.0
        while pos.lots and taken < wanted - _EPS:
            lot = pos.lots[0]
            use = min(lot.quantity, wanted - taken)
            share_cost = lot.cost * use / lot.quantity
            lot.quantity -= use
            lot.cost -= share_cost
            taken += use
            cost += share_cost
            if lot.quantity <= _EPS:
                pos.lots.pop(0)
        return taken, cost

    def sell(self, t: Transaction) -> None:
        assert t.symbol is not None
        pos = self.position(t.symbol, t.date)
        wanted = -t.quantity
        taken, cost = self._consume(pos, wanted)
        if taken > _EPS:
            proceeds = t.net * taken / wanted  # shares we never saw bought are left out of the gain
            self.realized.append(RealizedGain(t.date, t.symbol, taken, proceeds, cost))
            pos.realized += proceeds - cost
        if t.price is not None:
            self.last_trade[t.symbol] = (t.date, t.price)

    def transfer_out(self, t: Transaction) -> None:
        assert t.symbol is not None
        self._consume(self.position(t.symbol, t.date), -t.quantity)

    def file_split(self, t: Transaction) -> None:
        assert t.symbol is not None
        pos = self.position(t.symbol, t.date)
        held = pos.shares
        if held > _EPS:
            self.rescale(t.symbol, (held + t.quantity) / held)

    def market_split(self, symbol: str, day: date, ratio: float) -> None:
        pos = self.positions.get(symbol)
        if pos is None or pos.shares <= _EPS:
            return
        self.rescale(symbol, ratio)
        self.issues.append(
            Issue(
                IssueKind.SPLIT_APPLIED,
                Severity.INFO,
                {
                    "symbol": symbol,
                    "ratio": _ratio_text(ratio),
                    "date": display_date(day, self.date_format),
                },
            )
        )

    def rescale(self, symbol: str, ratio: float) -> None:
        for lot in self.positions[symbol].lots:
            lot.quantity *= ratio
        if symbol in self.last_trade:
            day, price = self.last_trade[symbol]
            self.last_trade[symbol] = (day, price / ratio)

    def income(self, t: Transaction) -> None:
        assert t.symbol is not None
        pos = self.positions.setdefault(t.symbol, Position(t.symbol))
        if t.type is TxnType.DIVIDEND:
            pos.dividends += t.net
        elif t.type is TxnType.WITHHOLDING:
            pos.withholding += t.net
        else:
            pos.fees += t.net


def build_positions(
    transactions: Sequence[Transaction],
    *,
    market_splits: Mapping[str, Sequence[tuple[date, float]]] | None = None,
    price_on: Callable[[str, date], float | None] | None = None,
    date_format: str = "%d/%m/%Y",
) -> PositionBook:
    """Replay history in order. Market splits apply at the start of their day unless the file records them."""
    file_splits = {(t.symbol, t.date) for t in transactions if t.type is TxnType.SPLIT}

    def recorded(symbol: str, day: date) -> bool:
        return any(s == symbol and abs((d - day).days) <= _SPLIT_MATCH.days for s, d in file_splits)

    events: list[tuple[date, int, int, Transaction | tuple[str, float]]] = []
    for i, t in enumerate(transactions):
        events.append((t.date, 1, i, t))
    for symbol, splits in (market_splits or {}).items():
        for day, ratio in splits:
            if ratio > 0 and ratio != 1 and not recorded(symbol, day):
                events.append((day, 0, -1, (symbol, ratio)))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    replay = _Replay(price_on, date_format)
    for day, _, _, item in events:
        if isinstance(item, Transaction):
            replay.apply(item)
        else:
            replay.market_split(item[0], day, item[1])
    return PositionBook(replay.positions, replay.realized, replay.issues, replay.last_trade)
