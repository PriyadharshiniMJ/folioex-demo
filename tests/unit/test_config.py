"""Config loader: defaults, TOML file, env overrides, friendly validation errors."""

from pathlib import Path

import pytest

from folioex.config import ConfigError, load_settings
from folioex.domain.models import TxnType

REPO_ROOT = Path(__file__).resolve().parents[2]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_any_file() -> None:
    s = load_settings()
    assert (s.upload.max_files, s.upload.max_file_mb, s.upload.max_total_mb) == (20, 5, 25)
    assert s.compare.max_tickers == 8
    assert s.compare.show_portfolio is True
    assert s.ui.app_name == "FolioEx"
    assert s.ui.tagline == "Your portfolio, explained."
    assert s.benchmark.default == "SPY"
    assert list(s.benchmark.choices) == ["SPY", "QQQ", "DIA"]
    assert s.benchmark.choices["SPY"] == "S&P 500 (SPY)"
    assert s.format.date_format == "%d/%m/%Y"
    assert s.format.null_token == "-"
    assert s.format.types["Foreign Tax Withholding"] is TxnType.WITHHOLDING
    assert s.format.columns.net == "Net Amount"
    assert s.analytics.lot_method == "FIFO"
    assert s.quiz.enabled is True
    assert s.performers.count == 3


def test_file_overrides_and_type_map_is_merged_not_replaced(tmp_path: Path) -> None:
    path = write(tmp_path, '[ui]\nport = 9001\n\n[format.types]\n"Stock Award" = "OTHER"\n')
    s = load_settings(path)
    assert s.ui.port == 9001
    assert s.format.types["Stock Award"] is TxnType.OTHER
    assert s.format.types["Buy"] is TxnType.BUY  # defaults kept


def test_symbol_overrides_merge_with_defaults(tmp_path: Path) -> None:
    path = write(tmp_path, '[format.symbols]\n"FB" = "META"\n')
    assert load_settings(path).format.symbols["FB"] == "META"


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, "[ui]\nport = 9001\n")
    monkeypatch.setenv("FOLIOEX_UI__PORT", "9100")
    assert load_settings(path).ui.port == 9100


def test_config_location_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "my.toml").write_text('[ui]\ntagline = "Hello"\n', encoding="utf-8")
    monkeypatch.setenv("FOLIOEX_CONFIG", str(other / "my.toml"))
    assert load_settings().ui.tagline == "Hello"


def test_config_toml_in_working_directory_is_used(tmp_path: Path) -> None:
    write(tmp_path, "[upload]\nmax_files = 7\n")
    assert load_settings().upload.max_files == 7


@pytest.mark.parametrize(
    ("toml", "must_mention"),
    [
        ('[ui]\nport = "abc"\n', "ui.port"),
        ('[benchmark]\ndefault = "VTI"\n', "benchmark.default"),
        ("[compare]\nmax_tickers = 10\n", "compare.max_tickers"),
        ("[progress]\ngreen_ratio = 1.5\n", "progress.green_ratio"),
        ('[format.types]\n"Buy" = "PURCHASE"\n', "format.types"),
    ],
)
def test_invalid_values_give_plain_messages(tmp_path: Path, toml: str, must_mention: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(write(tmp_path, toml))
    message = info.value.user_message
    assert must_mention in message
    assert "pydantic" not in message.lower()
    assert "Traceback" not in message


def test_broken_toml_syntax_mentions_line(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(write(tmp_path, "[ui\nport = 1\n"))
    assert "line" in info.value.user_message


def test_missing_explicit_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(tmp_path / "nope.toml")
    assert "nope.toml" in info.value.user_message


def test_repository_config_file_is_valid_and_matches_defaults() -> None:
    from_file = load_settings(REPO_ROOT / "config.toml")
    defaults = load_settings()
    assert from_file.model_dump() == defaults.model_dump()
