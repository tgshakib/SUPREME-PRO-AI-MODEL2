"""OTC Real-Time Bridge — SUPREME PRO AI BOT
============================================
Converts live broker WebSocket candles (from otc_feed_combined) into
pandas DataFrames that the signal analysis engines can use directly.

PRIORITY ORDER per pair:
  1. Pocket Option WS candles  (otc_feed_combined PO feed)
  2. Quotex WS candles         (otc_feed_combined QX feed)
  3. yfinance fallback         (existing engine behaviour — unchanged)

SELF-HEALING:
  • If the last candle for a pair is older than STALE_SEC the bridge
    reports the feed as stale and callers fall back to yfinance.
  • The combined WS feed reconnects automatically (handled by
    otc_feed_combined), so the bridge just needs to notice staleness.

SIGNAL TEXT CONTRACT: this module NEVER touches signal text.
It only supplies OHLCV DataFrames — exactly the same shape that the
engines currently receive from yfinance.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    _PD_OK = True
except Exception:
    pd = None  # type: ignore
    _PD_OK = False

# How old (seconds) a candle may be before we consider the feed stale
STALE_SEC = 90.0

# Minimum candles required for a usable DataFrame
MIN_CANDLES = 30


# ── Internal: label → otc_feed_combined asset key ────────────────────────────

def _to_feed_key(pair: str) -> Optional[str]:
    """Convert a bot pair label to the otc_feed_combined asset key.

    e.g. 'EUR/USD 〔OTC〕' → 'EURUSD-OTC'
         'EUR/USD (OTC)'  → 'EURUSD-OTC'
         'EURUSD-OTC'     → 'EURUSD-OTC'  (pass-through)
    """
    try:
        from otc_feed_combined import LABEL_TO_KEY
        # Direct match first
        if pair in LABEL_TO_KEY:
            return LABEL_TO_KEY[pair]
        # Normalise: strip OTC markers, slashes, spaces → uppercase
        import re
        s = pair.upper().strip()
        s = re.sub(r'\s*〔OTC〕\s*$', '', s).strip()
        s = re.sub(r'\s*\(OTC\)\s*$', '', s).strip()
        s = s.replace('/', '').replace(' ', '').replace('-', '')
        # Try to find a key in LABEL_TO_KEY whose compact form matches
        for label, key in LABEL_TO_KEY.items():
            if key is None:
                continue
            compact = label.upper()
            compact = re.sub(r'\s*〔OTC〕\s*$', '', compact).strip()
            compact = compact.replace('/', '').replace(' ', '')
            if compact == s:
                return key
        # Last resort: try constructing key directly e.g. EURUSD → EURUSD-OTC
        if not s.endswith('OTC'):
            candidate = f"{s}-OTC"
            if candidate in {v for v in LABEL_TO_KEY.values() if v}:
                return candidate
    except Exception:
        pass
    return None


# ── Core: get a DataFrame from the live WS feed ──────────────────────────────

def get_otc_df(pair: str, timeframe: str = "1m", count: int = 200,
               max_age_sec: float = STALE_SEC) -> Optional["pd.DataFrame"]:
    """Return a real-time OHLCV DataFrame for `pair` on `timeframe`.

    Returns None if:
      • pandas not available
      • pair not mapped to a feed key
      • fewer than MIN_CANDLES candles buffered
      • newest candle is older than max_age_sec
    """
    if not _PD_OK:
        return None

    try:
        from otc_feed_combined import otc_feed as _feed
    except Exception:
        return None

    feed_key = _to_feed_key(pair)
    if not feed_key:
        return None

    try:
        candles: List[dict] = _feed.get_candles(feed_key, timeframe, count=count)
    except Exception:
        return None

    if not candles or len(candles) < MIN_CANDLES:
        return None

    # Freshness check — last candle must not be stale
    try:
        last = candles[-1]
        ts_str = last.get("time") or ""
        if ts_str:
            import datetime as _dt
            ts = _dt.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=_dt.timezone.utc
            ).timestamp()
            if time.time() - ts > max_age_sec:
                logger.debug(
                    "[otc_bridge] %s %s stale (%.0fs old)",
                    feed_key, timeframe, time.time() - ts,
                )
                return None
    except Exception:
        pass

    # Convert list of dicts → DataFrame
    try:
        df = pd.DataFrame(candles)
        df.columns = [str(c).lower() for c in df.columns]
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        if len(df) < MIN_CANDLES:
            return None
        return df.tail(count).reset_index(drop=True)
    except Exception as e:
        logger.debug("[otc_bridge] DataFrame build error: %s", e)
        return None


def get_otc_price(pair: str) -> Optional[float]:
    """Return the latest live tick price for an OTC pair, or None."""
    try:
        from otc_feed_combined import otc_feed as _feed
        feed_key = _to_feed_key(pair)
        if not feed_key:
            return None
        return _feed.get_price(feed_key)
    except Exception:
        return None


def feed_is_live(pair: str, timeframe: str = "1m",
                 max_age_sec: float = STALE_SEC) -> bool:
    """True when the WS feed for `pair` has fresh candles within max_age_sec."""
    df = get_otc_df(pair, timeframe, count=5, max_age_sec=max_age_sec)
    return df is not None and len(df) >= 1


# ── Convenience: get multi-TF frames in one call ─────────────────────────────

def get_otc_mtf(
    pair: str,
    timeframes: tuple = ("1m", "5m", "15m", "30m"),
    count: int = 300,
) -> Dict[str, Optional["pd.DataFrame"]]:
    """Return dict of {tf: DataFrame|None} for requested timeframes."""
    return {tf: get_otc_df(pair, tf, count=count) for tf in timeframes}
