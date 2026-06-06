"""GoCharting chart-link module — SUPREME PRO AI BOT.

Generates clickable chart URLs for any pair + timeframe.

Primary  : GoCharting  (gocharting.com)
Fallback : TradingView (tradingview.com) — silent swap, no error shown

Supported timeframes: 10s, 30s, 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
                      (10s / 30s auto-upgraded to 1m on TradingView fallback
                       because TV public URLs don't carry sub-minute bars)

Public API
----------
chart_url(pair, tf="1h")  → str  (always returns a URL, never raises)
chart_button_text(pair, tf)  → str  (label for inline button)
"""
from __future__ import annotations

import time
import urllib.request
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
# Timeframe maps
# ═══════════════════════════════════════════════════════════════════════════

# GoCharting resolution strings
_GC_RESOLUTION: dict[str, str] = {
    "10s": "10S",
    "30s": "30S",
    "1m":  "1",
    "3m":  "3",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "4h":  "240",
    "1d":  "D",
    "1w":  "W",
}

# TradingView interval strings (sub-minute TFs → 1m fallback)
_TV_INTERVAL: dict[str, str] = {
    "10s": "1",
    "30s": "1",
    "1m":  "1",
    "3m":  "3",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "4h":  "240",
    "1d":  "D",
    "1w":  "W",
}

# Human-readable TF labels
_TF_LABEL: dict[str, str] = {
    "10s": "10 Sec",
    "30s": "30 Sec",
    "1m":  "1 Min",
    "3m":  "3 Min",
    "5m":  "5 Min",
    "15m": "15 Min",
    "30m": "30 Min",
    "1h":  "1 Hour",
    "4h":  "4 Hour",
    "1d":  "1 Day",
    "1w":  "1 Week",
}

# ═══════════════════════════════════════════════════════════════════════════
# Symbol maps  →  (gc_exchange:gc_symbol, tv_exchange:tv_symbol)
# ═══════════════════════════════════════════════════════════════════════════

# Normalised pair → (GoCharting ticker, TradingView ticker)
_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    # ── Major forex ───────────────────────────────────────────────────────
    "EUR/USD":  ("FX_IDC:EURUSD",  "FX:EURUSD"),
    "GBP/USD":  ("FX_IDC:GBPUSD",  "FX:GBPUSD"),
    "USD/JPY":  ("FX_IDC:USDJPY",  "FX:USDJPY"),
    "AUD/USD":  ("FX_IDC:AUDUSD",  "FX:AUDUSD"),
    "USD/CAD":  ("FX_IDC:USDCAD",  "FX:USDCAD"),
    "NZD/USD":  ("FX_IDC:NZDUSD",  "FX:NZDUSD"),
    "USD/CHF":  ("FX_IDC:USDCHF",  "FX:USDCHF"),
    "USD/MXN":  ("FX_IDC:USDMXN",  "FX:USDMXN"),
    "USD/ZAR":  ("FX_IDC:USDZAR",  "FX:USDZAR"),
    "USD/INR":  ("FX_IDC:USDINR",  "FX:USDINR"),
    "USD/PKR":  ("FX_IDC:USDPKR",  "FX:USDPKR"),
    "USD/BDT":  ("FX_IDC:USDBDT",  "FX:USDBDT"),
    "USD/NGN":  ("FX_IDC:USDNGN",  "FX:USDNGN"),
    "USD/BRL":  ("FX_IDC:USDBRL",  "FX:USDBRL"),
    "USD/IDR":  ("FX_IDC:USDIDR",  "FX:USDIDR"),
    "USD/PHP":  ("FX_IDC:USDPHP",  "FX:USDPHP"),
    "USD/EGP":  ("FX_IDC:USDEGP",  "FX:USDEGP"),
    "USD/DZD":  ("FX_IDC:USDDZD",  "FX:USDDZD"),
    "USD/ARS":  ("FX_IDC:USDARS",  "FX:USDARS"),
    "USD/COP":  ("FX_IDC:USDCOP",  "FX:USDCOP"),
    # ── Cross pairs ───────────────────────────────────────────────────────
    "EUR/GBP":  ("FX_IDC:EURGBP",  "FX:EURGBP"),
    "EUR/JPY":  ("FX_IDC:EURJPY",  "FX:EURJPY"),
    "EUR/AUD":  ("FX_IDC:EURAUD",  "FX:EURAUD"),
    "EUR/CAD":  ("FX_IDC:EURCAD",  "FX:EURCAD"),
    "EUR/CHF":  ("FX_IDC:EURCHF",  "FX:EURCHF"),
    "EUR/NZD":  ("FX_IDC:EURNZD",  "FX:EURNZD"),
    "GBP/JPY":  ("FX_IDC:GBPJPY",  "FX:GBPJPY"),
    "GBP/AUD":  ("FX_IDC:GBPAUD",  "FX:GBPAUD"),
    "GBP/CAD":  ("FX_IDC:GBPCAD",  "FX:GBPCAD"),
    "GBP/CHF":  ("FX_IDC:GBPCHF",  "FX:GBPCHF"),
    "GBP/NZD":  ("FX_IDC:GBPNZD",  "FX:GBPNZD"),
    "AUD/JPY":  ("FX_IDC:AUDJPY",  "FX:AUDJPY"),
    "AUD/CAD":  ("FX_IDC:AUDCAD",  "FX:AUDCAD"),
    "AUD/CHF":  ("FX_IDC:AUDCHF",  "FX:AUDCHF"),
    "AUD/NZD":  ("FX_IDC:AUDNZD",  "FX:AUDNZD"),
    "NZD/JPY":  ("FX_IDC:NZDJPY",  "FX:NZDJPY"),
    "NZD/CAD":  ("FX_IDC:NZDCAD",  "FX:NZDCAD"),
    "NZD/CHF":  ("FX_IDC:NZDCHF",  "FX:NZDCHF"),
    "CAD/JPY":  ("FX_IDC:CADJPY",  "FX:CADJPY"),
    "CAD/CHF":  ("FX_IDC:CADCHF",  "FX:CADCHF"),
    "CHF/JPY":  ("FX_IDC:CHFJPY",  "FX:CHFJPY"),
    # ── Metals ────────────────────────────────────────────────────────────
    "XAU/USD":  ("TVC:GOLD",       "TVC:GOLD"),
    "XAG/USD":  ("TVC:SILVER",     "TVC:SILVER"),
    "GOLD":     ("TVC:GOLD",       "TVC:GOLD"),
    "SILVER":   ("TVC:SILVER",     "TVC:SILVER"),
    # ── Energy / Commodities ──────────────────────────────────────────────
    "USOIL":    ("TVC:USOIL",      "TVC:USOIL"),
    "UKOIL":    ("TVC:UKOIL",      "TVC:UKOIL"),
    "UKBRENT":  ("TVC:UKOIL",      "TVC:UKOIL"),
    "USCRUDE":  ("TVC:USOIL",      "TVC:USOIL"),
    "DXY":      ("TVC:DXY",        "TVC:DXY"),
    # ── Crypto ────────────────────────────────────────────────────────────
    "BTC/USD":  ("BINANCE:BTCUSDT", "BINANCE:BTCUSDT"),
    "ETH/USD":  ("BINANCE:ETHUSDT", "BINANCE:ETHUSDT"),
    "BNB/USD":  ("BINANCE:BNBUSDT", "BINANCE:BNBUSDT"),
    "SOL/USD":  ("BINANCE:SOLUSDT", "BINANCE:SOLUSDT"),
    "XRP/USD":  ("BINANCE:XRPUSDT", "BINANCE:XRPUSDT"),
    "BTC":      ("BINANCE:BTCUSDT", "BINANCE:BTCUSDT"),
    "BTCUSD":   ("BINANCE:BTCUSDT", "BINANCE:BTCUSDT"),
    "BTCUSDT":  ("BINANCE:BTCUSDT", "BINANCE:BTCUSDT"),
    "ETHUSD":   ("BINANCE:ETHUSDT", "BINANCE:ETHUSDT"),
    "ETHUSDT":  ("BINANCE:ETHUSDT", "BINANCE:ETHUSDT"),
    "SOLUSDT":  ("BINANCE:SOLUSDT", "BINANCE:SOLUSDT"),
    "BITCOIN":  ("BINANCE:BTCUSDT", "BINANCE:BTCUSDT"),
    "ETHEREUM": ("BINANCE:ETHUSDT", "BINANCE:ETHUSDT"),
    "SOLANA":   ("BINANCE:SOLUSDT", "BINANCE:SOLUSDT"),
    "LITECOIN": ("BINANCE:LTCUSDT", "BINANCE:LTCUSDT"),
    "DASH":     ("BINANCE:DASHUSDT","BINANCE:DASHUSDT"),
    "POLKADOT": ("BINANCE:DOTUSDT", "BINANCE:DOTUSDT"),
    "CHAINLINK":("BINANCE:LINKUSDT","BINANCE:LINKUSDT"),
    "AVALANCHE":("BINANCE:AVAXUSDT","BINANCE:AVAXUSDT"),
    "TONCOIN":  ("BINANCE:TONUSDT", "BINANCE:TONUSDT"),
    "TRUMP":    ("BINANCE:TRUMPUSDT","BINANCE:TRUMPUSDT"),
    "BITCOIN CASH": ("BINANCE:BCHUSDT","BINANCE:BCHUSDT"),
    "BINANCE COIN": ("BINANCE:BNBUSDT","BINANCE:BNBUSDT"),
    "AXIE INFINITY": ("BINANCE:AXSUSDT","BINANCE:AXSUSDT"),
    "ETHEREUM CLASSIC": ("BINANCE:ETCUSDT","BINANCE:ETCUSDT"),
    # ── Indices ───────────────────────────────────────────────────────────
    "NAS100":   ("FOREXCOM:NAS100", "NASDAQ:NDX"),
    "US100":    ("FOREXCOM:NAS100", "NASDAQ:NDX"),
    "DJ30":     ("FOREXCOM:US30",   "DJ:DJI"),
    "SP500":    ("FOREXCOM:SPX500", "SP:SPX"),
    # ── Stocks (OTC) ──────────────────────────────────────────────────────
    "AMERICAN EXPRESS":  ("NYSE:AXP",   "NYSE:AXP"),
    "BOEING COMPANY":    ("NYSE:BA",    "NYSE:BA"),
    "FACEBOOK INC":      ("NASDAQ:META","NASDAQ:META"),
    "INTEL":             ("NASDAQ:INTC","NASDAQ:INTC"),
    "JOHNSON JOHNSON":   ("NYSE:JNJ",   "NYSE:JNJ"),
    "MCDONALD'S":        ("NYSE:MCD",   "NYSE:MCD"),
    "PFIZER INC":        ("NYSE:PFE",   "NYSE:PFE"),
}

# ═══════════════════════════════════════════════════════════════════════════
# GoCharting reachability cache (so we only probe once per 5 minutes)
# ═══════════════════════════════════════════════════════════════════════════

_GC_REACHABLE: Optional[bool] = None
_GC_CHECKED_AT: float = 0.0
_GC_CHECK_TTL: float = 300.0   # 5 minutes
_GC_BASE = "https://gocharting.com"
_TV_BASE = "https://www.tradingview.com"


def _gc_reachable() -> bool:
    """Return True if gocharting.com responds within 3 seconds."""
    global _GC_REACHABLE, _GC_CHECKED_AT
    now = time.time()
    if _GC_REACHABLE is not None and (now - _GC_CHECKED_AT) < _GC_CHECK_TTL:
        return _GC_REACHABLE
    try:
        req = urllib.request.Request(
            _GC_BASE,
            headers={"User-Agent": "Mozilla/5.0"},
            method="HEAD",
        )
        urllib.request.urlopen(req, timeout=3)
        _GC_REACHABLE = True
    except Exception:
        _GC_REACHABLE = False
    _GC_CHECKED_AT = now
    return _GC_REACHABLE


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _normalise_pair(pair: str) -> str:
    """Strip OTC suffixes and normalise to upper-case key."""
    p = pair.upper()
    for sfx in (" 〔OTC〕", "(OTC)", " (OTC)", " OTC", "〔OTC〕"):
        p = p.replace(sfx, "")
    return p.strip()


def _lookup(pair: str) -> Optional[tuple[str, str]]:
    """Return (gc_ticker, tv_ticker) or None."""
    key = _normalise_pair(pair)
    if key in _SYMBOL_MAP:
        return _SYMBOL_MAP[key]
    # Try stripping spaces
    key2 = key.replace(" ", "")
    if key2 in _SYMBOL_MAP:
        return _SYMBOL_MAP[key2]
    # Generic forex fallback — if it looks like CCY/CCY build a ticker
    clean = key.replace("/", "").replace("-", "")
    if len(clean) == 6 and clean.isalpha():
        gc = f"FX_IDC:{clean}"
        tv = f"FX:{clean}"
        return gc, tv
    return None


def _gc_url(gc_ticker: str, tf: str) -> str:
    res = _GC_RESOLUTION.get(tf, "60")
    return (
        f"https://gocharting.com/terminal"
        f"?ticker={gc_ticker}"
        f"&type=CANDLESTICK"
        f"&resolution={res}"
        f"&theme=dark"
    )


def _tv_url(tv_ticker: str, tf: str) -> str:
    interval = _TV_INTERVAL.get(tf, "60")
    return (
        f"https://www.tradingview.com/chart/"
        f"?symbol={tv_ticker}"
        f"&interval={interval}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def chart_url(pair: str, tf: str = "1h") -> str:
    """Return the best available chart URL for pair + tf.

    Tries GoCharting first. Falls back silently to TradingView if
    GoCharting is unreachable or the pair is unmapped.
    Always returns a usable URL — never raises.
    """
    tf = tf.lower().strip()
    tickers = _lookup(pair)

    if tickers is None:
        # Unknown pair — TradingView generic search fallback
        symbol = _normalise_pair(pair).replace("/", "")
        return f"https://www.tradingview.com/chart/?symbol={symbol}"

    gc_ticker, tv_ticker = tickers

    if _gc_reachable():
        return _gc_url(gc_ticker, tf)
    return _tv_url(tv_ticker, tf)


def tv_url(pair: str, tf: str = "1h") -> str:
    """Always return a TradingView chart URL (skip GoCharting check)."""
    tf = tf.lower().strip()
    tickers = _lookup(pair)
    if tickers is None:
        symbol = _normalise_pair(pair).replace("/", "")
        return f"https://www.tradingview.com/chart/?symbol={symbol}"
    _, tv_ticker = tickers
    return _tv_url(tv_ticker, tf)


def chart_button_text(pair: str, tf: str = "1h") -> str:
    """Short label for an inline keyboard button, e.g. '📊 EUR/USD · 1 Hour'."""
    label = _TF_LABEL.get(tf.lower().strip(), tf.upper())
    clean = _normalise_pair(pair)
    return f"📊 {clean} · {label}"


def both_urls(pair: str, tf: str = "1h") -> dict[str, str]:
    """Return both URLs regardless of reachability — for debug / admin use."""
    tf = tf.lower().strip()
    tickers = _lookup(pair)
    if tickers is None:
        symbol = _normalise_pair(pair).replace("/", "")
        tv = f"https://www.tradingview.com/chart/?symbol={symbol}"
        return {"gocharting": tv, "tradingview": tv, "active": "tradingview"}
    gc_ticker, tv_ticker = tickers
    return {
        "gocharting":   _gc_url(gc_ticker, tf),
        "tradingview":  _tv_url(tv_ticker, tf),
        "active":       "gocharting" if _gc_reachable() else "tradingview",
    }
