"""Application settings: built-in defaults < ``config.toml`` < ``FOLIOEX_*`` environment variables.

Lookup order for the settings file: explicit path → ``FOLIOEX_CONFIG`` → ``./config.toml`` → none (defaults).
Nested env overrides use a double underscore, e.g. ``FOLIOEX_UI__PORT=9000``.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from folioex.domain.models import TxnType

CONFIG_ENV_VAR = "FOLIOEX_CONFIG"

DEFAULT_TYPES: dict[str, TxnType] = {
    "Buy": TxnType.BUY,
    "Sell": TxnType.SELL,
    "Dividend": TxnType.DIVIDEND,
    "Payment in Lieu of Dividend": TxnType.DIVIDEND,
    "Foreign Tax Withholding": TxnType.WITHHOLDING,
    "Withholding Tax": TxnType.WITHHOLDING,
    "Other Fee": TxnType.FEE,
    "Commission Adjustment": TxnType.FEE,
    "Deposit": TxnType.DEPOSIT,
    "Withdrawal": TxnType.WITHDRAWAL,
    "Credit Interest": TxnType.INTEREST,
    "Debit Interest": TxnType.INTEREST,
    "Transfer In": TxnType.TRANSFER_IN,
    "Transfer Out": TxnType.TRANSFER_OUT,
    "Stock Split": TxnType.SPLIT,
}

DEFAULT_BENCHMARKS: dict[str, str] = {
    "SPY": "S&P 500 (SPY)",
    "QQQ": "Nasdaq-100 (QQQ)",
    "DIA": "Dow Jones 30 (DIA)",
}

DEFAULT_SAMPLE_TICKERS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "JNJ",
    "PG",
    "KO",
    "V",
    "HD",
    "XOM",
    "COST",
    "PEP",
    "JPM",
    "UNH",
    "VOO",
    "SCHD",
)


class ConfigError(Exception):
    """Settings could not be loaded. ``user_message`` is plain English for the terminal."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _expand(path: Path) -> Path:
    return Path(os.path.expanduser(str(path)))


class UploadCfg(_Section):
    max_files: int = Field(20, ge=1, le=100)
    max_file_mb: float = Field(5, gt=0)
    max_total_mb: float = Field(25, gt=0)


class BenchmarkCfg(_Section):
    default: str = "SPY"
    choices: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_BENCHMARKS))

    @model_validator(mode="after")
    def _default_is_a_choice(self) -> Self:
        if self.default not in self.choices:
            raise ValueError(f"benchmark.default must be one of: {', '.join(self.choices)}")
        return self


class AnalyticsCfg(_Section):
    lot_method: Literal["FIFO"] = "FIFO"
    risk_free_rate: float = Field(0.04, ge=0, le=0.2)
    base_currency: Literal["USD"] = "USD"


class InsightsCfg(_Section):
    max_position_weight: float = Field(0.20, gt=0, le=1)
    max_sector_weight: float = Field(0.35, gt=0, le=1)
    big_loser_pct: float = Field(-0.20, gt=-1, lt=0)
    big_winner_pct: float = Field(0.50, gt=0)
    idle_cash_pct: float = Field(0.05, ge=0, le=1)
    fee_drag_pct: float = Field(0.10, gt=0, le=1)


class CacheCfg(_Section):
    dir: Path = Field(default_factory=lambda: Path.home() / ".folioex" / "cache")
    price_ttl_hours: float = Field(12, gt=0)
    profile_ttl_days: float = Field(7, gt=0)
    universe_ttl_days: float = Field(1, gt=0)
    max_mb: float = Field(500, gt=0)

    _expand_dir = field_validator("dir")(_expand)


class CompareCfg(_Section):
    # Eight is the colour-safe series limit; the whole-portfolio line does not count toward it.
    max_tickers: int = Field(8, ge=1, le=8)
    default_period: Literal["1M", "3M", "6M", "YTD", "1Y", "3Y", "All"] = "1Y"
    show_portfolio: bool = True


class ProgressCfg(_Section):
    poll_ms: int = Field(300, ge=50, le=5000)
    min_stage_ms: int = Field(80, ge=0, le=2000)
    green_ratio: float = Field(0.65, ge=0, le=1)


class QuizCfg(_Section):
    enabled: bool = True
    bucket_width_pct: float = Field(10, gt=0, le=100)


class PerformersCfg(_Section):
    count: int = Field(3, ge=1, le=10)
    min_days: int = Field(30, ge=0)
    min_avg_capital: float = Field(100, ge=0)


class ColumnsCfg(_Section):
    date: str = "Date"
    account: str = "Account"
    type: str = "Transaction Type"
    symbol: str = "Symbol"
    quantity: str = "Quantity"
    price: str = "Price"
    currency: str = "Price Currency"
    gross: str = "Gross Amount"
    commission: str = "Commission"
    net: str = "Net Amount"


class FormatCfg(_Section):
    date_format: str = "%d/%m/%Y"
    null_token: str = "-"
    section: str = "Transaction History"
    summary_section: str = "Summary"
    statement_section: str = "Statement"
    columns: ColumnsCfg = Field(default_factory=ColumnsCfg)
    types: dict[str, TxnType] = Field(default_factory=lambda: dict(DEFAULT_TYPES))
    symbols: dict[str, str] = Field(default_factory=dict)

    @field_validator("types", mode="before")
    @classmethod
    def _merge_types(cls, value: Any) -> Any:
        # A user's [format.types] extends the defaults rather than replacing them.
        return {**DEFAULT_TYPES, **value} if isinstance(value, dict) else value


class SampleCfg(_Section):
    years: tuple[int, int] = (2021, 2026)
    tickers: tuple[str, ...] = DEFAULT_SAMPLE_TICKERS
    monthly_deposit: float = Field(1000, gt=0)
    withholding_rate: float = Field(0.0, ge=0, le=1)
    seed: int = 42


class UiCfg(_Section):
    app_name: str = "FolioEx"
    tagline: str = "Your portfolio, explained."
    default_theme: Literal["light", "dark"] = "light"
    host: str = "127.0.0.1"
    port: int = Field(8050, ge=1, le=65535)
    open_browser: bool = True


class LoggingCfg(_Section):
    dir: Path = Field(default_factory=lambda: Path.home() / ".folioex" / "logs")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    _expand_dir = field_validator("dir")(_expand)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FOLIOEX_", env_nested_delimiter="__", extra="forbid", frozen=True
    )

    upload: UploadCfg = Field(default_factory=UploadCfg)
    benchmark: BenchmarkCfg = Field(default_factory=BenchmarkCfg)
    analytics: AnalyticsCfg = Field(default_factory=AnalyticsCfg)
    insights: InsightsCfg = Field(default_factory=InsightsCfg)
    cache: CacheCfg = Field(default_factory=CacheCfg)
    compare: CompareCfg = Field(default_factory=CompareCfg)
    progress: ProgressCfg = Field(default_factory=ProgressCfg)
    quiz: QuizCfg = Field(default_factory=QuizCfg)
    performers: PerformersCfg = Field(default_factory=PerformersCfg)
    format: FormatCfg = Field(default_factory=FormatCfg)
    sample: SampleCfg = Field(default_factory=SampleCfg)
    ui: UiCfg = Field(default_factory=UiCfg)
    logging: LoggingCfg = Field(default_factory=LoggingCfg)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment wins over the file contents (passed as init kwargs); no .env or secrets files.
        return (env_settings, init_settings)


def _resolve_path(path: Path | str | None) -> Path | None:
    if path is not None:
        return Path(path)
    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        return Path(from_env)
    local = Path.cwd() / "config.toml"
    return local if local.is_file() else None


def _describe(exc: ValidationError, source: Path | None) -> str:
    where = source.name if source else "your settings"
    lines = [f"Some settings in {where} can't be used:"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "settings"
        msg = str(err["msg"]).removeprefix("Value error, ")
        if err["type"] == "extra_forbidden":
            msg = "unknown setting (check the spelling)"
        lines.append(f"  - {loc}: {msg}")
    lines.append("Fix these values, or delete them to use the defaults.")
    return "\n".join(lines)


def load_settings(path: Path | str | None = None) -> Settings:
    """Load settings, raising ``ConfigError`` with a plain-English message on any problem."""
    source = _resolve_path(path)
    data: dict[str, Any] = {}
    if source is not None:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            raise ConfigError(
                f"FolioEx couldn't open the settings file {source}. "
                "Check the path, or remove it to use the defaults."
            ) from None
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"{source.name} isn't written correctly ({exc}). "
                "Fix that line, or delete the file to use the defaults."
            ) from None
    try:
        return Settings(**data)
    except ValidationError as exc:
        raise ConfigError(_describe(exc, source)) from None
