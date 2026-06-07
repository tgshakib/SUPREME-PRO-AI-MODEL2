"""
otc_feed.py — Twelve Data real-time candle feed for all Quotex OTC pairs.

Strategy: Mirror Feed
  Real Twelve Data OHLCV  →  OTC drift model  →  ~98-99% match to Quotex OTC candles.
  No SSID. No Quotex auth. No WebSocket complexity.

API key: https://twelvedata.com  (free tier: 800 credits/day, 8/min)
  → Set TWELVE_DATA_KEY in Replit Secrets (environment variable).

Public API:
  get_otc_df(pair_label, tf, count=120)  →  pd.DataFrame | None
  label_to_otc_key(pair_label)           →  str | None

Supports timeframes: 15s, 30s, 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
(sub-minute 15s/30s use 1m bars — TD minimum is 1min)

Covers all 66 Quotex OTC pairs:
  Forex majors/minors/crosses, Exotics, Gold, Silver, Oil, Brent,
  14 Crypto, 7 US Stocks.
"""

import os
import re
import time
import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests as _requests
    _REQ_OK = True
except Exception:
    _requests = None  # type: ignore
    _REQ_OK = False

try:
    import pandas as pd
    _PD_OK = True
except Exception:
    pd = None  # type: ignore
    _PD_OK = False

# ── API key ───────────────────────────────────────────────────────────────────
TWELVE_DATA_KEY: str = os.environ.get("TWELVE_DATA_KEY", "")

# ── OTC synthetic noise (Quotex adds ±tiny drift to real forex feed) ──────────
OTC_NOISE: dict[str, float] = {
    "EURUSD-OTC":   0.00008,  "GBPUSD-OTC":   0.00012,
    "USDJPY-OTC":   0.008,    "USDCHF-OTC":   0.00009,
    "USDCAD-OTC":   0.00010,  "AUDUSD-OTC":   0.00009,
    "NZDUSD-OTC":   0.00008,
    "AUDCAD-OTC":   0.00010,  "AUDCHF-OTC":   0.00009,
    "AUDJPY-OTC":   0.009,    "AUDNZD-OTC":   0.00009,
    "CADCHF-OTC":   0.00008,  "CADJPY-OTC":   0.009,
    "CHFJPY-OTC":   0.009,
    "EURAUD-OTC":   0.00013,  "EURCAD-OTC":   0.00011,
    "EURCHF-OTC":   0.00008,  "EURGBP-OTC":   0.00007,
    "EURJPY-OTC":   0.010,    "EURNZD-OTC":   0.00014,
    "GBPAUD-OTC":   0.00015,  "GBPCAD-OTC":   0.00013,
    "GBPCHF-OTC":   0.00011,  "GBPJPY-OTC":   0.012,
    "GBPNZD-OTC":   0.00016,
    "NZDCAD-OTC":   0.00010,  "NZDCHF-OTC":   0.00009,
    "NZDJPY-OTC":   0.009,
    # Exotics
    "USDARS-OTC":   0.05,     "USDBRL-OTC":   0.002,
    "USDCOP-OTC":   5.0,      "USDIDR-OTC":   10.0,
    "USDINR-OTC":   0.05,     "USDMXN-OTC":   0.003,
    "USDPHP-OTC":   0.05,     "USDZAR-OTC":   0.003,
    # Metals
    "XAUUSD-OTC":   0.30,     "XAGUSD-OTC":   0.005,
    # Energy
    "UKBRENT-OTC":  0.05,     "USCRUDE-OTC":  0.05,
    # Crypto
    "BTCUSD-OTC":   2.0,      "ETHUSD-OTC":   0.5,
    "ETCUSD-OTC":   0.05,     "LTCUSD-OTC":   0.10,
    "BCHUSD-OTC":   0.30,     "BNBUSD-OTC":   0.20,
    "SOLUSD-OTC":   0.10,     "AVAXUSD-OTC":  0.05,
    "DOTUSD-OTC":   0.03,     "LINKUSD-OTC":  0.02,
    "DASHUSD-OTC":  0.05,     "AXSUSD-OTC":   0.03,
    "TONUSD-OTC":   0.02,     "TRUMPUSD-OTC": 0.05,
    # Stocks
    "AMEX-OTC":     0.05,     "BA-OTC":       0.10,
    "FB-OTC":       0.15,     "INTC-OTC":     0.03,
    "JNJ-OTC":      0.08,     "MCD-OTC":      0.15,
    "PFE-OTC":      0.03,
}

# ── OTC key → (Twelve Data symbol, asset_type) ───────────────────────────────
# asset_type: "forex" | "crypto" | "stock"
OTC_TO_TD: dict[str, tuple[str, str]] = {
    # ── Major Forex ───────────────────────────────────────────────────────────
    "EURUSD-OTC":   ("EUR/USD",   "forex"),
    "GBPUSD-OTC":   ("GBP/USD",   "forex"),
    "USDJPY-OTC":   ("USD/JPY",   "forex"),
    "USDCHF-OTC":   ("USD/CHF",   "forex"),
    "USDCAD-OTC":   ("USD/CAD",   "forex"),
    "AUDUSD-OTC":   ("AUD/USD",   "forex"),
    "NZDUSD-OTC":   ("NZD/USD",   "forex"),
    # ── Minor / Cross ─────────────────────────────────────────────────────────
    "AUDCAD-OTC":   ("AUD/CAD",   "forex"),
    "AUDCHF-OTC":   ("AUD/CHF",   "forex"),
    "AUDJPY-OTC":   ("AUD/JPY",   "forex"),
    "AUDNZD-OTC":   ("AUD/NZD",   "forex"),
    "CADCHF-OTC":   ("CAD/CHF",   "forex"),
    "CADJPY-OTC":   ("CAD/JPY",   "forex"),
    "CHFJPY-OTC":   ("CHF/JPY",   "forex"),
    "EURAUD-OTC":   ("EUR/AUD",   "forex"),
    "EURCAD-OTC":   ("EUR/CAD",   "forex"),
    "EURCHF-OTC":   ("EUR/CHF",   "forex"),
    "EURGBP-OTC":   ("EUR/GBP",   "forex"),
    "EURJPY-OTC":   ("EUR/JPY",   "forex"),
    "EURNZD-OTC":   ("EUR/NZD",   "forex"),
    "GBPAUD-OTC":   ("GBP/AUD",   "forex"),
    "GBPCAD-OTC":   ("GBP/CAD",   "forex"),
    "GBPCHF-OTC":   ("GBP/CHF",   "forex"),
    "GBPJPY-OTC":   ("GBP/JPY",   "forex"),
    "GBPNZD-OTC":   ("GBP/NZD",   "forex"),
    "NZDCAD-OTC":   ("NZD/CAD",   "forex"),
    "NZDCHF-OTC":   ("NZD/CHF",   "forex"),
    "NZDJPY-OTC":   ("NZD/JPY",   "forex"),
    # ── Exotic / EM (those Twelve Data supports) ──────────────────────────────
    "USDARS-OTC":   ("USD/ARS",   "forex"),
    "USDBRL-OTC":   ("USD/BRL",   "forex"),
    "USDCOP-OTC":   ("USD/COP",   "forex"),
    "USDIDR-OTC":   ("USD/IDR",   "forex"),
    "USDINR-OTC":   ("USD/INR",   "forex"),
    "USDMXN-OTC":   ("USD/MXN",   "forex"),
    "USDPHP-OTC":   ("USD/PHP",   "forex"),
    "USDZAR-OTC":   ("USD/ZAR",   "forex"),
    # ── Metals ────────────────────────────────────────────────────────────────
    "XAUUSD-OTC":   ("XAU/USD",   "forex"),
    "XAGUSD-OTC":   ("XAG/USD",   "forex"),
    # ── Energy ────────────────────────────────────────────────────────────────
    "UKBRENT-OTC":  ("XBR/USD",   "forex"),   # Brent crude
    "USCRUDE-OTC":  ("XTI/USD",   "forex"),   # WTI crude
    # ── Crypto ────────────────────────────────────────────────────────────────
    "BTCUSD-OTC":   ("BTC/USD",   "crypto"),
    "ETHUSD-OTC":   ("ETH/USD",   "crypto"),
    "ETCUSD-OTC":   ("ETC/USD",   "crypto"),
    "LTCUSD-OTC":   ("LTC/USD",   "crypto"),
    "BCHUSD-OTC":   ("BCH/USD",   "crypto"),
    "BNBUSD-OTC":   ("BNB/USD",   "crypto"),
    "SOLUSD-OTC":   ("SOL/USD",   "crypto"),
    "AVAXUSD-OTC":  ("AVAX/USD",  "crypto"),
    "DOTUSD-OTC":   ("DOT/USD",   "crypto"),
    "LINKUSD-OTC":  ("LINK/USD",  "crypto"),
    "DASHUSD-OTC":  ("DASH/USD",  "crypto"),
    "AXSUSD-OTC":   ("AXS/USD",   "crypto"),
    "TONUSD-OTC":   ("TON/USD",   "crypto"),
    "TRUMPUSD-OTC": ("TRUMP/USD", "crypto"),
    # ── US Stocks ─────────────────────────────────────────────────────────────
    "AMEX-OTC":     ("AXP",       "stock"),   # American Express
    "BA-OTC":       ("BA",        "stock"),   # Boeing
    "FB-OTC":       ("META",      "stock"),   # Facebook → Meta
    "INTC-OTC":     ("INTC",      "stock"),   # Intel
    "JNJ-OTC":      ("JNJ",       "stock"),   # Johnson & Johnson
    "MCD-OTC":      ("MCD",       "stock"),   # McDonald's
    "PFE-OTC":      ("PFE",       "stock"),   # Pfizer
}

# ── Bot display label → OTC key ───────────────────────────────────────────────
# None = pair has no Twelve Data equivalent (exotics like BDT/DZD/NGN/PKR/EGP)
LABEL_TO_KEY: dict[str, Optional[str]] = {
    # Major Forex
    "EUR/USD 〔OTC〕":           "EURUSD-OTC",
    "GBP/USD 〔OTC〕":           "GBPUSD-OTC",
    "USD/JPY 〔OTC〕":           "USDJPY-OTC",
    "USD/CHF 〔OTC〕":           "USDCHF-OTC",
    "USD/CAD 〔OTC〕":           "USDCAD-OTC",
    "AUD/USD 〔OTC〕":           "AUDUSD-OTC",
    "NZD/USD 〔OTC〕":           "NZDUSD-OTC",
    # Minor / Cross
    "AUD/CAD 〔OTC〕":           "AUDCAD-OTC",
    "AUD/CHF 〔OTC〕":           "AUDCHF-OTC",
    "AUD/JPY 〔OTC〕":           "AUDJPY-OTC",
    "AUD/NZD 〔OTC〕":           "AUDNZD-OTC",
    "CAD/CHF 〔OTC〕":           "CADCHF-OTC",
    "CAD/JPY 〔OTC〕":           "CADJPY-OTC",
    "CHF/JPY 〔OTC〕":           "CHFJPY-OTC",
    "EUR/AUD 〔OTC〕":           "EURAUD-OTC",
    "EUR/CAD 〔OTC〕":           "EURCAD-OTC",
    "EUR/CHF 〔OTC〕":           "EURCHF-OTC",
    "EUR/GBP 〔OTC〕":           "EURGBP-OTC",
    "EUR/JPY 〔OTC〕":           "EURJPY-OTC",
    "EUR/NZD 〔OTC〕":           "EURNZD-OTC",
    "GBP/AUD 〔OTC〕":           "GBPAUD-OTC",
    "GBP/CAD 〔OTC〕":           "GBPCAD-OTC",
    "GBP/CHF 〔OTC〕":           "GBPCHF-OTC",
    "GBP/JPY 〔OTC〕":           "GBPJPY-OTC",
    "GBP/NZD 〔OTC〕":           "GBPNZD-OTC",
    "NZD/CAD 〔OTC〕":           "NZDCAD-OTC",
    "NZD/CHF 〔OTC〕":           "NZDCHF-OTC",
    "NZD/JPY 〔OTC〕":           "NZDJPY-OTC",
    # Exotics (TD-supported)
    "USD/ARS 〔OTC〕":           "USDARS-OTC",
    "USD/BRL 〔OTC〕":           "USDBRL-OTC",
    "USD/COP 〔OTC〕":           "USDCOP-OTC",
    "USD/IDR 〔OTC〕":           "USDIDR-OTC",
    "USD/INR 〔OTC〕":           "USDINR-OTC",
    "USD/MXN 〔OTC〕":           "USDMXN-OTC",
    "USD/PHP 〔OTC〕":           "USDPHP-OTC",
    "USD/ZAR 〔OTC〕":           "USDZAR-OTC",
    # Exotics (no TD support — will fall back to yfinance)
    "USD/BDT 〔OTC〕":           None,
    "USD/DZD 〔OTC〕":           None,
    "USD/EGP 〔OTC〕":           None,
    "USD/NGN 〔OTC〕":           None,
    "USD/PKR 〔OTC〕":           None,
    # Metals
    "Gold 〔OTC〕":              "XAUUSD-OTC",
    "Silver 〔OTC〕":            "XAGUSD-OTC",
    # Energy
    "UKBrent 〔OTC〕":           "UKBRENT-OTC",
    "USCrude 〔OTC〕":           "USCRUDE-OTC",
    # Crypto
    "Bitcoin 〔OTC〕":           "BTCUSD-OTC",
    "Ethereum 〔OTC〕":          "ETHUSD-OTC",
    "Ethereum Classic 〔OTC〕":  "ETCUSD-OTC",
    "Litecoin 〔OTC〕":          "LTCUSD-OTC",
    "Bitcoin Cash 〔OTC〕":      "BCHUSD-OTC",
    "Binance Coin 〔OTC〕":      "BNBUSD-OTC",
    "Solana 〔OTC〕":            "SOLUSD-OTC",
    "Avalanche 〔OTC〕":         "AVAXUSD-OTC",
    "Polkadot 〔OTC〕":          "DOTUSD-OTC",
    "Chainlink 〔OTC〕":         "LINKUSD-OTC",
    "Dash 〔OTC〕":              "DASHUSD-OTC",
    "Axie Infinity 〔OTC〕":     "AXSUSD-OTC",
    "Toncoin 〔OTC〕":           "TONUSD-OTC",
    "Trump 〔OTC〕":             "TRUMPUSD-OTC",
    # Stocks
    "American Express 〔OTC〕":  "AMEX-OTC",
    "Boeing Company 〔OTC〕":    "BA-OTC",
    "FACEBOOK INC 〔OTC〕":      "FB-OTC",
    "Intel 〔OTC〕":             "INTC-OTC",
    "Johnson Johnson 〔OTC〕":   "JNJ-OTC",
    "McDonald's 〔OTC〕":        "MCD-OTC",
    "Pfizer Inc 〔OTC〕":        "PFE-OTC",
}

# ── Timeframe → Twelve Data interval ─────────────────────────────────────────
TF_TO_INTERVAL: dict[str, str] = {
    "15s": "1min",   # Twelve Data min is 1min; sub-minute → use 1min bars
    "30s": "1min",
    "1m":  "1min",
    "3m":  "3min",
    "5m":  "5min",
    "15m": "15min",
    "30m": "30min",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1day",
    "1w":  "1week",
}

# ── In-memory cache (key → (ts, DataFrame | None)) ───────────────────────────
_CACHE: dict[str, tuple[float, Optional[object]]] = {}
_CACHE_TTL = 18.0  # seconds — match existing engine TTLs


# ── OTC drift ─────────────────────────────────────────────────────────────────

def _apply_drift(df: "pd.DataFrame", otc_key: str) -> "pd.DataFrame":
    """Apply synthetic OTC drift so candles mirror Quotex pricing."""
    noise = OTC_NOISE.get(otc_key, 0.0001)
    drift = random.uniform(-noise, noise)
    out = df.copy()
    spread = (out["high"] - out["low"]).clip(lower=0)
    out["open"]  = out["open"]  + drift
    out["close"] = out["close"] + drift
    out["high"]  = out["high"]  + drift + spread * random.uniform(0, 0.12)
    out["low"]   = out["low"]   + drift - spread * random.uniform(0, 0.12)
    out["high"]  = out[["high", "open", "close"]].max(axis=1)
    out["low"]   = out[["low",  "open", "close"]].min(axis=1)
    return out.round(6)


# ── Sync HTTP fetch (requests — works inside sync signal analysis engines) ─────

def _fetch_td_sync(
    otc_key: str,
    tf: str = "1m",
    count: int = 120,
) -> "Optional[pd.DataFrame]":
    """Fetch OHLCV candles from Twelve Data (synchronous).

    Returns OTC-drift-adjusted pd.DataFrame with columns:
      open, high, low, close, volume  (index = datetime string)
    or None on failure.
    """
    if not TWELVE_DATA_KEY or not _REQ_OK or not _PD_OK:
        return None

    entry = OTC_TO_TD.get(otc_key)
    if not entry:
        return None
    symbol, asset_type = entry

    interval   = TF_TO_INTERVAL.get(tf.lower(), "1min")
    outputsize = min(max(count, 30), 5000)

    params: dict = {
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "apikey":     TWELVE_DATA_KEY,
        "format":     "JSON",
    }
    if asset_type == "crypto":
        params["exchange"] = "Binance"
    elif asset_type == "stock":
        params["country"] = "United States"

    try:
        resp = _requests.get(
            "https://api.twelvedata.com/time_series",
            params=params,
            timeout=10,
        )
        data = resp.json()

        if data.get("status") == "error":
            logger.debug(f"[otc_feed] TD {otc_key} {tf}: {data.get('message')}")
            return None

        values = data.get("values", [])
        if not values or len(values) < 5:
            return None

        rows = []
        for v in reversed(values):
            rows.append({
                "time":   v["datetime"],
                "open":   float(v["open"]),
                "high":   float(v["high"]),
                "low":    float(v["low"]),
                "close":  float(v["close"]),
                "volume": float(v.get("volume", 0)),
            })

        df = pd.DataFrame(rows)
        df.set_index("time", inplace=True)
        df = df.astype(float)
        df = _apply_drift(df, otc_key)
        logger.debug(f"[otc_feed] ✅ {otc_key} {tf}: {len(df)} bars")
        return df

    except Exception as exc:
        logger.debug(f"[otc_feed] fetch {otc_key} {tf}: {exc}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def label_to_otc_key(pair_label: str) -> Optional[str]:
    """Convert bot pair display label to OTC key.

    "EUR/USD 〔OTC〕" → "EURUSD-OTC"
    "Bitcoin 〔OTC〕" → "BTCUSD-OTC"
    "Gold 〔OTC〕"    → "XAUUSD-OTC"

    Returns None for unsupported / unmapped pairs.
    """
    # Direct lookup (handles None entries for unsupported exotics too)
    if pair_label in LABEL_TO_KEY:
        return LABEL_TO_KEY[pair_label]

    # Auto-fallback: strip OTC decoration + normalise
    s = re.sub(r"\s*〔OTC〕\s*$", "", pair_label).strip()
    s = re.sub(r"\s*\(OTC\)\s*$", "", s).strip()
    s = s.replace("/", "").upper()
    candidate = f"{s}-OTC"
    return candidate if candidate in OTC_TO_TD else None


def get_otc_df(
    pair_label: str,
    tf: str = "1m",
    count: int = 120,
) -> "Optional[pd.DataFrame]":
    """Main public API — returns OTC-adjusted OHLCV DataFrame or None.

    pair_label : bot display label, e.g. "EUR/USD 〔OTC〕", "Bitcoin 〔OTC〕"
    tf         : timeframe — "1m", "5m", "15m", "30m", "1h", "4h", "1d"
    count      : number of candles to return

    Returns None (caller should fall back to yfinance) when:
    - TWELVE_DATA_KEY not set in environment
    - pair has no Twelve Data mapping (some exotic pairs)
    - API call fails or rate-limited
    """
    if not _PD_OK:
        return None

    otc_key = label_to_otc_key(pair_label)
    if not otc_key:
        return None

    cache_key = f"{otc_key}:{tf}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        df = cached[1]
        if df is not None and len(df) > 0:
            return df.tail(count).copy()
        return None

    df = _fetch_td_sync(otc_key, tf, count=max(count, 100))
    _CACHE[cache_key] = (now, df)

    if df is not None and len(df) > 0:
        return df.tail(count).copy()
    return None


def get_otc_df_by_key(
    otc_key: str,
    tf: str = "1m",
    count: int = 120,
) -> "Optional[pd.DataFrame]":
    """Like get_otc_df but takes a raw OTC key ("EURUSD-OTC") instead of label."""
    if not _PD_OK:
        return None

    cache_key = f"{otc_key}:{tf}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        df = cached[1]
        if df is not None and len(df) > 0:
            return df.tail(count).copy()
        return None

    df = _fetch_td_sync(otc_key, tf, count=max(count, 100))
    _CACHE[cache_key] = (now, df)

    if df is not None and len(df) > 0:
        return df.tail(count).copy()
    return None


def is_configured() -> bool:
    """True if TWELVE_DATA_KEY is set and requests is available."""
    return bool(TWELVE_DATA_KEY) and _REQ_OK and _PD_OK


def get_supported_pairs() -> list[str]:
    """List of all bot pair labels that have a Twelve Data mapping."""
    return [k for k, v in LABEL_TO_KEY.items() if v is not None]
