"""App factory and the ``folioex`` command."""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from dash import Dash

from folioex.config import ConfigError, Settings, load_settings
from folioex.errors import MessageCatalog
from folioex.logging_setup import setup_logging
from folioex.market_data.cache import CachedMarketData
from folioex.market_data.universe import HttpUniverseSource, Universe
from folioex.market_data.yfinance_provider import YFinanceProvider
from folioex.ui.callbacks import register_callbacks
from folioex.ui.layout import build_layout

ASSETS = Path(__file__).parent / "assets"
FONTS = "https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&family=Sora:wght@500;600;700&display=swap"


def create_app(
    settings: Settings | None = None,
    catalog: MessageCatalog | None = None,
    market: CachedMarketData | None = None,
    universe: Universe | None = None,
) -> Dash:
    """Build the app. ``market`` defaults to Yahoo Finance behind the on-disk cache, ``universe`` to the
    NYSE and Nasdaq stock list."""
    settings = settings or load_settings()
    market = market or CachedMarketData(
        YFinanceProvider(), settings.cache, date_format=settings.format.date_format
    )
    universe = universe or Universe(HttpUniverseSource(), settings.cache)
    app = Dash(
        __name__,
        assets_folder=str(ASSETS),
        title=settings.ui.app_name,
        update_title="",  # empty = never flash "Updating..." in the browser tab
        external_stylesheets=[FONTS],
    )
    app.layout = build_layout(settings, catalog)
    register_callbacks(app, settings, market, universe)
    return app


def run_options(settings: Settings) -> dict[str, Any]:
    """Server options. Dash's debug overlay and dev tools are always off: no technical errors on screen."""
    return {
        "host": settings.ui.host,
        "port": settings.ui.port,
        "debug": False,
        "dev_tools_ui": False,
        "dev_tools_props_check": False,
        "dev_tools_hot_reload": False,
    }


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc.user_message, file=sys.stderr)
        raise SystemExit(2) from None
    log_path = setup_logging(settings.logging)
    app = create_app(settings)
    url = f"http://{settings.ui.host}:{settings.ui.port}"
    print(f"{settings.ui.app_name} is running at {url}  (press Ctrl+C to stop)")
    print(f"Technical log: {log_path}")
    if settings.ui.open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(**run_options(settings))
