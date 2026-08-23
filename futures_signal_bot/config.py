"""Configuration owned only by the Future Signal • TG feature."""

from __future__ import annotations

DEFAULT_TIMEFRAME_MINUTES = 1
DEFAULT_UTC_OFFSET = 6  # Bangladesh, matching the upstream project's default.
MAX_SELECTED_ASSETS = 5
MAX_SIGNALS_PER_ASSET = 15

MARKETS = {
    "real": "🌍 Real Market",
    "quotex": "📈 Quotex OTC",
    "po": "💼 Pocket Option OTC",
    "iq": "📊 IQ Option OTC",
    "olymp": "🏦 Olymp Trade OTC",
}

# Kept intentionally local to this feature.  The OTC labels mirror the source
# project's user flow while the engine converts them to the main bot's feed keys.
MARKET_ASSETS = {
    "real": (
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD",
        "EUR/JPY", "GBP/JPY", "XAU/USD",
    ),
    "quotex": (
        "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "AUD/USD (OTC)",
        "USD/CAD (OTC)", "EUR/JPY (OTC)", "GBP/JPY (OTC)", "Gold (OTC)",
    ),
    "po": (
        "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "AUD/USD (OTC)",
        "USD/CAD (OTC)", "EUR/JPY (OTC)", "GBP/JPY (OTC)", "Gold (OTC)",
    ),
    "iq": (
        "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "AUD/USD (OTC)",
        "USD/CAD (OTC)", "EUR/JPY (OTC)", "GBP/JPY (OTC)", "Gold (OTC)",
    ),
    "olymp": (
        "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "AUD/USD (OTC)",
        "USD/CAD (OTC)", "EUR/JPY (OTC)", "GBP/JPY (OTC)", "Gold (OTC)",
    ),
}