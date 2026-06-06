"""Multi-timeframe candle & indicator feed — SUPREME PRO AI BOT.

Primary source  : TradingView public scanner (tradingview-ta)
                  → real-time prices, zero delay, 1m → 1wk coverage
Fallback source : yfinance OHLCV + manual RSI / EMA computation

NOTE: 5-second bars are NOT available from any free public API.
      Minimum supported timeframe is 1 minute.

Public API
----------
get_mtf_bias(pair)  →  dict  with keys:
    {
      "bias":     "BUY" | "SELL" | "NEUTRAL",
      "strength": float 0-1,
      "source":   "tradingview" | "yfinance" | "none",
      "tfs": {
          "1m":  {"bias": str, "rsi": float, "ok": bool},
          "5m":  {...},
          "15m": {...},
          "1h":  {...},
          "4h":  {...},
          "1d":  {...},
          "1W":  {...},
      }
    }

get_single_tf(pair, tf)  →  dict  same per-TF structure, or empty dict on failure.
"""
from __future__ import annotations

import time
from typing import Optional

# ── TradingView TA (primary) ───────────────────────────────────────────────
try:
    from tradingview_ta import TA_Handler, Interval
    _TV_OK = True
except Exception as _e:
    print(f"[candle_feed] tradingview-ta unavailable: {_e}")
    TA_Handler = None   # type: ignore
    Interval = None     # type: ignore
    _TV_OK = False

# ── yfinance (fallback) ────────────────────────────────────────────────────
try:
    import yfinance as _yf
    import pandas as _pd
    _YF_OK = True
except Exception:
    _yf = None          # type: ignore
    _pd = None          # type: ignore
    _YF_OK = False

# ═══════════════════════════════════════════════════════════════════════════
# Symbol maps
# ═══════════════════════════════════════════════════════════════════════════

# TradingView symbol  →  (symbol, screener, exchange)
_TV_MAP: dict[str, tuple[str, str, str]] = {
    "EUR/USD": ("EURUSD",  "forex",   "FX_IDC"),
    "GBP/USD": ("GBPUSD",  "forex",   "FX_IDC"),
    "USD/JPY": ("USDJPY",  "forex",   "FX_IDC"),
    "AUD/USD": ("AUDUSD",  "forex",   "FX_IDC"),
    "USD/CAD": ("USDCAD",  "forex",   "FX_IDC"),
    "NZD/USD": ("NZDUSD",  "forex",   "FX_IDC"),
    "EUR/GBP": ("EURGBP",  "forex",   "FX_IDC"),
    "EUR/JPY": ("EURJPY",  "forex",   "FX_IDC"),
    "GBP/JPY": ("GBPJPY",  "forex",   "FX_IDC"),
    "AUD/JPY": ("AUDJPY",  "forex",   "FX_IDC"),
    "USD/CHF": ("USDCHF",  "forex",   "FX_IDC"),
    "EUR/CHF": ("EURCHF",  "forex",   "FX_IDC"),
    "EUR/AUD": ("EURAUD",  "forex",   "FX_IDC"),
    "GBP/AUD": ("GBPAUD",  "forex",   "FX_IDC"),
    "EUR/CAD": ("EURCAD",  "forex",   "FX_IDC"),
    "GBP/CAD": ("GBPCAD",  "forex",   "FX_IDC"),
    "AUD/CAD": ("AUDCAD",  "forex",   "FX_IDC"),
    "AUD/NZD": ("AUDNZD",  "forex",   "FX_IDC"),
    "NZD/JPY": ("NZDJPY",  "forex",   "FX_IDC"),
    "GBP/CHF": ("GBPCHF",  "forex",   "FX_IDC"),
    "CAD/JPY": ("CADJPY",  "forex",   "FX_IDC"),
    "XAU/USD": ("XAUUSD",  "cfd",     "TVC"),
    "XAG/USD": ("XAGUSD",  "cfd",     "TVC"),
    "BTC/USD": ("BTCUSDT", "crypto",  "BINANCE"),
    "ETH/USD": ("ETHUSDT", "crypto",  "BINANCE"),
    "BNB/USD": ("BNBUSDT", "crypto",  "BINANCE"),
    "SOL/USD": ("SOLUSDT", "crypto",  "BINANCE"),
    "XRP/USD": ("XRPUSDT", "crypto",  "BINANCE"),
    "NAS100":  ("NDX",     "america", "NASDAQ"),
    "DJ30":    ("DJI",     "america", "DJ"),
    "SP500":   ("SPX",     "america", "SP"),
}

# yfinance tickers (fallback) — same as live_prices.py
_YF_MAP: dict[str, str] = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/CAD": "USDCAD=X", "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X", "USD/CHF": "USDCHF=X", "EUR/CHF": "EURCHF=X",
    "EUR/AUD": "EURAUD=X", "GBP/AUD": "GBPAUD=X", "EUR/CAD": "EURCAD=X",
    "GBP/CAD": "GBPCAD=X", "AUD/CAD": "AUDCAD=X", "AUD/NZD": "AUDNZD=X",
    "NZD/JPY": "NZDJPY=X", "GBP/CHF": "GBPCHF=X", "CAD/JPY": "CADJPY=X",
    "XAU/USD": "PAXG-USD", "XAG/USD": "SI=F",
    "BTC/USD": "BTC-USD",  "ETH/USD": "ETH-USD", "BNB/USD": "BNB-USD",
    "SOL/USD": "SOL-USD",  "XRP/USD": "XRP-USD",
    "NAS100":  "^NDX",     "DJ30": "^DJI", "SP500": "^GSPC",
}

# yfinance interval + period for each TF
_YF_TF: dict[str, tuple[str, str]] = {
    "1m":  ("1m",  "1d"),
    "5m":  ("5m",  "5d"),
    "15m": ("15m", "5d"),
    "1h":  ("1h",  "30d"),
    "4h":  ("1h",  "60d"),   # yfinance has no 4h; use 1h and we group 4 bars
    "1d":  ("1d",  "1y"),
    "1W":  ("1wk", "2y"),
}

# TradingView Interval objects
_TV_INTERVAL: dict[str, object] = {}
if _TV_OK:
    _TV_INTERVAL = {
        "1m":  Interval.INTERVAL_1_MINUTE,
        "5m":  Interval.INTERVAL_5_MINUTES,
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h":  Interval.INTERVAL_1_HOUR,
        "4h":  Interval.INTERVAL_4_HOURS,
        "1d":  Interval.INTERVAL_1_DAY,
        "1W":  Interval.INTERVAL_1_WEEK,
    }

# Cache TTL per TF (seconds)
_CACHE_TTL: dict[str, float] = {
    "1m": 30, "5m": 45, "15m": 60,
    "1h": 120, "4h": 180, "1d": 300, "1W": 600,
}

# ── per (pair, tf) cache: (timestamp, result_dict) ─────────────────────────
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}

# Overall MTF bias cache per pair
_MTF_CACHE: dict[str, tuple[float, dict]] = {}
_MTF_TTL = 45.0   # seconds


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _clean_pair(pair: str) -> str:
    """Strip OTC suffix and normalize."""
    p = pair.upper()
    for sfx in (" (OTC)", "(OTC)", " OTC", "/OTC"):
        p = p.replace(sfx, "")
    return p.strip()


def _tv_fetch(pair: str, tf: str) -> Optional[dict]:
    """Try TradingView scanner for one pair + TF. Returns indicator dict or None."""
    if not _TV_OK:
        return None
    info = _TV_MAP.get(pair)
    if not info:
        return None
    interval = _TV_INTERVAL.get(tf)
    if not interval:
        return None
    symbol, screener, exchange = info
    try:
        h = TA_Handler(
            symbol=symbol,
            screener=screener,
            exchange=exchange,
            interval=interval,
        )
        a = h.get_analysis()
        ind = a.indicators or {}
        summ = a.summary or {}
        rsi  = float(ind.get("RSI", 50) or 50)
        ema20 = float(ind.get("EMA20", 0) or 0)
        ema50 = float(ind.get("EMA50", 0) or 0)
        close = float(ind.get("close", 0) or 0)
        rec   = str(summ.get("RECOMMENDATION", "NEUTRAL")).upper()
        buy_v  = int(summ.get("BUY",  0) or 0)
        sell_v = int(summ.get("SELL", 0) or 0)
        neut_v = int(summ.get("NEUTRAL", 0) or 0)
        total  = max(1, buy_v + sell_v + neut_v)

        if "STRONG_BUY" in rec or rec == "BUY":
            bias = "BUY"
        elif "STRONG_SELL" in rec or rec == "SELL":
            bias = "SELL"
        else:
            bias = "NEUTRAL"

        strength = max(buy_v, sell_v) / total
        return {
            "bias":     bias,
            "rsi":      rsi,
            "ema20":    ema20,
            "ema50":    ema50,
            "close":    close,
            "strength": round(strength, 3),
            "buy_v":    buy_v,
            "sell_v":   sell_v,
            "ok":       True,
            "source":   "tradingview",
        }
    except Exception as e:
        print(f"[candle_feed] TV {pair} {tf}: {e}")
        return None


def _yf_indicators(series) -> tuple[float, float, float]:
    """Return (rsi14, ema20, ema50) from a close price Series."""
    if _pd is None or len(series) < 20:
        return 50.0, 0.0, 0.0
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs   = gain / loss.replace(0, 1e-10)
    rsi  = float((100 - (100 / (1 + rs))).iloc[-1])
    ema20 = float(series.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(series.ewm(span=50, adjust=False).mean().iloc[-1]) if len(series) >= 50 else ema20
    return round(rsi, 2), round(ema20, 6), round(ema50, 6)


def _yf_fetch(pair: str, tf: str) -> Optional[dict]:
    """yfinance fallback for one pair + TF."""
    if not _YF_OK:
        return None
    ticker = _YF_MAP.get(pair)
    if not ticker:
        return None
    interval, period = _YF_TF.get(tf, ("1h", "30d"))
    try:
        t = _yf.Ticker(ticker)
        df = t.history(period=period, interval=interval, auto_adjust=True)
        if df is None or len(df) < 10:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [
                str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                for c in df.columns
            ]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        close_col = df["close"].squeeze()

        # For 4h: group 1h bars into 4-bar windows
        if tf == "4h" and interval == "1h":
            groups = len(close_col) // 4
            if groups < 3:
                return None
            close_col = close_col.groupby(
                [i // 4 for i in range(len(close_col))]
            ).last()

        rsi, ema20, ema50 = _yf_indicators(close_col)
        close = float(close_col.iloc[-1])

        if rsi >= 55 and close > ema50:
            bias = "BUY"
            strength = min(1.0, (rsi - 50) / 30)
        elif rsi <= 45 and close < ema50:
            bias = "SELL"
            strength = min(1.0, (50 - rsi) / 30)
        else:
            bias = "NEUTRAL"
            strength = 0.2

        return {
            "bias":     bias,
            "rsi":      rsi,
            "ema20":    ema20,
            "ema50":    ema50,
            "close":    close,
            "strength": round(strength, 3),
            "buy_v":    0,
            "sell_v":   0,
            "ok":       True,
            "source":   "yfinance",
        }
    except Exception as e:
        print(f"[candle_feed] YF {pair} {tf}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

ALL_TFS = ["1m", "5m", "15m", "1h", "4h", "1d", "1W"]


def get_single_tf(pair: str, tf: str) -> dict:
    """Fetch indicator data for one pair / timeframe.

    Returns a dict with keys: bias, rsi, ema20, ema50, close, strength,
    ok, source — or an empty dict on failure.
    Cache TTL varies by TF (30s for 1m up to 10min for 1W).
    """
    pair = _clean_pair(pair)
    key  = (pair, tf)
    now  = time.time()
    ttl  = _CACHE_TTL.get(tf, 60)
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < ttl:
        return cached[1]

    result = _tv_fetch(pair, tf) or _yf_fetch(pair, tf) or {}
    _CACHE[key] = (now, result)
    return result


def get_mtf_bias(pair: str,
                 tfs: Optional[list[str]] = None) -> dict:
    """Multi-timeframe consensus bias for a pair.

    Queries 1m, 5m, 15m, 1h, 4h, 1d, 1W (or the subset in `tfs`).
    Short TFs weight lower; long TFs weight higher.

    Returns:
        {
          "bias":     "BUY" | "SELL" | "NEUTRAL",
          "strength": 0.0-1.0,
          "source":   "tradingview" | "yfinance" | "mixed" | "none",
          "tfs":      { tf: {...per-TF result...} }
        }
    """
    pair = _clean_pair(pair)
    now  = time.time()
    cached = _MTF_CACHE.get(pair)
    if cached and (now - cached[0]) < _MTF_TTL:
        return cached[1]

    target_tfs = tfs or ALL_TFS
    # TF weights: longer TF = higher weight
    weights = {"1m": 1, "5m": 2, "15m": 3, "1h": 5, "4h": 7, "1d": 9, "1W": 10}

    tf_results: dict[str, dict] = {}
    buy_score = sell_score = 0.0
    sources: set[str] = set()

    for tf in target_tfs:
        r = get_single_tf(pair, tf)
        tf_results[tf] = r
        if not r.get("ok"):
            continue
        w = weights.get(tf, 1)
        src = r.get("source", "")
        if src:
            sources.add(src)
        b = r.get("bias", "NEUTRAL")
        s = float(r.get("strength", 0.3))
        if b == "BUY":
            buy_score  += w * s
        elif b == "SELL":
            sell_score += w * s

    total = buy_score + sell_score
    if total < 0.5:
        overall_bias = "NEUTRAL"
        overall_str  = 0.0
    elif buy_score > sell_score:
        overall_bias = "BUY"
        overall_str  = round(buy_score / max(total, 1), 3)
    else:
        overall_bias = "SELL"
        overall_str  = round(sell_score / max(total, 1), 3)

    if len(sources) == 0:
        src_label = "none"
    elif "tradingview" in sources and "yfinance" in sources:
        src_label = "mixed"
    elif "tradingview" in sources:
        src_label = "tradingview"
    else:
        src_label = "yfinance"

    result = {
        "bias":     overall_bias,
        "strength": overall_str,
        "source":   src_label,
        "tfs":      tf_results,
    }
    _MTF_CACHE[pair] = (now, result)
    return result
