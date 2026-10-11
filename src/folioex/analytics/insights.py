"""Rule-based insights: small functions that look at the numbers and return a plain-English note (or nothing).

Wording lives in messages.toml under [insights]; thresholds come from [insights] in config.toml.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from folioex.analytics.allocation import Slice
from folioex.analytics.holdings import FUNDS, UNCLASSIFIED, HoldingsView
from folioex.config import InsightsCfg
from folioex.errors import Issue
from folioex.errors import Severity as IssueSeverity
from folioex.formatting import money, percent, plural

if TYPE_CHECKING:
    from folioex.analytics.performance import PerformanceView

Severity = Literal["warning", "positive", "info"]


@dataclass(frozen=True, slots=True)
class Insight:
    key: str
    severity: Severity
    context: dict[str, Any] = field(default_factory=dict)


Rule = Callable[[HoldingsView, Sequence[Slice], InsightsCfg], Insight | None]


def _position_concentration(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> Insight | None:
    top = max((r for r in view.rows if r.sector != FUNDS), key=lambda r: r.weight, default=None)
    if top is None or top.weight <= cfg.max_position_weight:
        return None
    return Insight(
        "position_concentration",
        "warning",
        {
            "symbol": top.symbol,
            "weight": percent(top.weight),
            "limit": percent(cfg.max_position_weight),
        },
    )


def _sector_concentration(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> Insight | None:
    real = [s for s in sectors if s.name not in (FUNDS, UNCLASSIFIED)]
    top = max(real, key=lambda s: s.weight, default=None)
    if top is None or top.weight <= cfg.max_sector_weight:
        return None
    return Insight(
        "sector_concentration",
        "warning",
        {
            "sector": top.name,
            "weight": percent(top.weight),
            "limit": percent(cfg.max_sector_weight),
        },
    )


def _best_sector(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> Insight | None:
    ranked = [s for s in sectors if s.gain_pct is not None and s.name != UNCLASSIFIED]
    if len(ranked) < 2:
        return None
    best = max(ranked, key=lambda s: s.gain_pct or 0.0)
    if (best.gain_pct or 0.0) <= 0:
        return None
    return Insight(
        "best_sector", "positive", {"sector": best.name, "gain": percent(best.gain_pct, sign=True)}
    )


def _big_losers(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> Insight | None:
    losers = [r.symbol for r in view.rows if r.gain_pct is not None and r.gain_pct <= cfg.big_loser_pct]
    if not losers:
        return None
    return Insight(
        "big_losers", "warning", {"symbols": ", ".join(losers), "threshold": percent(-cfg.big_loser_pct)}
    )


def _big_winners(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> Insight | None:
    winners = [r.symbol for r in view.rows if r.gain_pct is not None and r.gain_pct >= cfg.big_winner_pct]
    if not winners:
        return None
    return Insight(
        "big_winners", "positive", {"symbols": ", ".join(winners), "threshold": percent(cfg.big_winner_pct)}
    )


def _idle_cash(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> Insight | None:
    if view.account_value <= 0 or view.cash <= 0:
        return None
    share = view.cash / view.account_value
    if share <= cfg.idle_cash_pct:
        return None
    return Insight("idle_cash", "info", {"weight": percent(share), "amount": money(view.cash, decimals=0)})


HOLDINGS_RULES: dict[str, Rule] = {
    "position_concentration": _position_concentration,
    "sector_concentration": _sector_concentration,
    "big_losers": _big_losers,
    "best_sector": _best_sector,
    "big_winners": _big_winners,
    "idle_cash": _idle_cash,
}
_ORDER: dict[Severity, int] = {"warning": 0, "positive": 1, "info": 2}


def holdings_insights(view: HoldingsView, sectors: Sequence[Slice], cfg: InsightsCfg) -> list[Insight]:
    found = [i for rule in HOLDINGS_RULES.values() if (i := rule(view, sectors, cfg)) is not None]
    return sorted(found, key=lambda i: _ORDER[i.severity])


# ---------- performance ----------

PERFORMANCE_RULES = (
    "beat_benchmark",
    "lag_benchmark",
    "best_month",
    "worst_month",
    "dividend_share",
    "dividend_growth",
    "dividend_drop",
    "fee_drag",
)
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def performance_insights(
    view: PerformanceView, bench_name: str, period_text: str, cfg: InsightsCfg | None = None
) -> list[Insight]:
    cfg = cfg or InsightsCfg()
    found: list[Insight] = []
    if view.excess is not None:
        pts = f"{abs(view.excess) * 100:.1f} pts"
        key, severity = ("beat_benchmark", "positive") if view.excess >= 0 else ("lag_benchmark", "info")
        found.append(Insight(key, severity, {"bench": bench_name, "pts": pts, "period": period_text}))  # type: ignore[arg-type]
    if len(view.months) >= 2:
        best = max(view.months, key=lambda m: m.ret)
        worst = min(view.months, key=lambda m: m.ret)
        if best.ret > 0:
            found.append(
                Insight(
                    "best_month",
                    "positive",
                    {
                        "month": f"{_MONTH_NAMES[best.month - 1]} {best.year}",
                        "ret": percent(best.ret, decimals=1, sign=True),
                    },
                )
            )
        if worst.ret < 0:
            found.append(
                Insight(
                    "worst_month",
                    "info",
                    {
                        "month": f"{_MONTH_NAMES[worst.month - 1]} {worst.year}",
                        "ret": percent(worst.ret, decimals=1, sign=True),
                    },
                )
            )
    gain = view.breakdown.end_value - view.breakdown.start_value - view.breakdown.added
    if gain > 0 and view.income.net > 0:
        found.append(
            Insight(
                "dividend_share", "info", {"share": percent(view.income.net / gain), "period": period_text}
            )
        )
    costs = -(view.breakdown.withholding + view.breakdown.fees)
    earned = max(view.breakdown.price_gains, 0.0) + max(view.breakdown.dividends, 0.0)
    if costs > 0 and earned > 0 and costs / earned >= cfg.fee_drag_pct - 1e-9:
        found.append(
            Insight(
                "fee_drag",
                "info",
                {"share": percent(costs / earned), "amount": money(costs, decimals=0), "period": period_text},
            )
        )
    if view.dividend_growth is not None:
        year, change = view.dividend_growth
        key, severity = ("dividend_growth", "positive") if change >= 0 else ("dividend_drop", "info")
        found.append(Insight(key, severity, {"year": year, "change": percent(abs(change))}))  # type: ignore[arg-type]
    return sorted(found, key=lambda i: _ORDER[i.severity])


def data_quality_insight(issues: Sequence[Issue]) -> Insight | None:
    """A nudge toward the Data Hub when some file problems could affect the numbers."""
    worrying = [i for i in issues if i.severity in (IssueSeverity.WARNING, IssueSeverity.BLOCKING)]
    if not worrying:
        return None
    return Insight("data_quality", "warning", {"things": plural(len(worrying), "thing")})
