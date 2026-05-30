"""OTC CANDLE MANIPULATION PATTERN ENGINE
========================================
OTC (Over-The-Counter) broker-generated market synthetic analysis.
Detects patterns unique to OTC/synthetic instruments that don't exist
in live market data — engineered price sequences, Martingale trap zones,
session-boundary anomalies, and tick-level micro-structure.

Engines
-------
  OTC-M1  Repetitive Sequence Detection
           — candle direction (up/down) repeating every N bars
           — same OHLC shape repeating in the sequence
           — probability matrix of next candle given last 3-5 candles

  OTC-M2  Martingale Trap Zones
           — broker-engineered reversal levels (price oscillates to
             exhaust doublers before the real move)
           — detect oscillation count at a price band → pre-reversal

  OTC-M3  Session Boundary Anomaly
           — OTC synthetic resets at session close / open
           — price spikes, gap-fills, or sudden direction flips at
             boundary bars

  OTC-M4  Tick Data Analysis (1m proxy)
           — micro-structure: number of up-ticks vs down-ticks in bar
           — velocity: fast close toward high/low = directional tick flow
           — tick exhaustion: high velocity then sudden reversal bar

  OTC-M5  OTC Session Heatmap
           — bar-by-bar volatility pattern within the OTC session window
           — hot zones (high ATR bars) vs cold zones (consolidation)
           — entry when transitioning from cold → hot zone

  OTC-M6  Candle Sequence Probability Matrix
           — build empirical table from last 200 bars:
             given [last 3 candle directions], what % time was next UP?
           — signal when historical probability ≥ 65%

Public API
----------
  otc_manipulation_analyze(pair) -> dict | None
    { direction, score, engines, reasons }
  Returns None when < 3 of 6 engines agree.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception as _e:
    print(f"[otc_manipulation] import error: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 90.0
_MIN_AGREE = 3


# ── helpers ──────────────────────────────────────────────────────

def _flatten(df):
    if df is None or df.empty:
        return None
    try:
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df if "close" in df.columns else None
    except Exception:
        return None


def _atr(h, lo, c, n=14):
    tr = (h - lo).combine((h - c.shift()).abs(), max).combine(
         (lo - c.shift()).abs(), max)
    return tr.rolling(n).mean()


# ── OTC-M1: Repetitive Sequence Detection ─────────────────────

def _repetitive_sequence(c, o):
    """Detect if the last 5 candles match a pattern that occurred before."""
    try:
        dirs = [1 if float(c.iloc[i]) >= float(o.iloc[i]) else 0
                for i in range(len(c))]
        if len(dirs) < 30:
            return None

        pattern_len = 5
        current = tuple(dirs[-pattern_len:])

        # Search for same pattern in prior 150 bars
        matches = []
        for i in range(len(dirs) - pattern_len * 2, max(0, len(dirs) - 150), -1):
            candidate = tuple(dirs[i:i + pattern_len])
            if candidate == current:
                # What was the NEXT candle direction after this match?
                if i + pattern_len < len(dirs):
                    matches.append(dirs[i + pattern_len])

        if len(matches) < 4:
            return None

        next_up   = sum(matches)
        next_down = len(matches) - next_up
        total     = len(matches)
        bull_prob = next_up / total
        bear_prob = next_down / total

        if bull_prob >= 0.65:
            return ("BUY",
                    f"OTC-M1 SEQUENCE MATCH — pattern repeated {total}× next BUY {bull_prob:.0%}")
        if bear_prob >= 0.65:
            return ("SELL",
                    f"OTC-M1 SEQUENCE MATCH — pattern repeated {total}× next SELL {bear_prob:.0%}")
        return None
    except Exception:
        return None


# ── OTC-M2: Martingale Trap Zones ────────────────────────────

def _martingale_trap(h, lo, c):
    """Detect oscillation exhaustion at a price band — pre-reversal signal."""
    try:
        if len(c) < 20:
            return None

        px = float(c.iloc[-1])
        recent_h = h.iloc[-20:]
        recent_l = lo.iloc[-20:]
        recent_c = c.iloc[-20:]

        # Price band width = 10% of ATR
        atr_val = float(_atr(h, lo, c, 14).iloc[-1])
        band_w  = atr_val * 0.5

        # Count how many times price has touched the current zone
        zone_hi = px + band_w
        zone_lo = px - band_w
        touches = int(((recent_h >= zone_lo) & (recent_l <= zone_hi)).sum())

        if touches < 5:
            return None

        # Direction: is the oscillation trending slightly in one direction?
        zone_closes = recent_c[(recent_h >= zone_lo) & (recent_l <= zone_hi)]
        if len(zone_closes) < 3:
            return None

        trend = float(zone_closes.iloc[-1]) - float(zone_closes.iloc[0])

        # After N oscillations in a band, expect breakout in direction of last push
        if trend > atr_val * 0.1:
            return ("BUY",
                    f"OTC-M2 MARTINGALE TRAP ZONE — {touches} oscillations, breakout BUY")
        if trend < -atr_val * 0.1:
            return ("SELL",
                    f"OTC-M2 MARTINGALE TRAP ZONE — {touches} oscillations, breakout SELL")
        return None
    except Exception:
        return None


# ── OTC-M3: Session Boundary Anomaly ─────────────────────────

def _session_boundary(h, lo, c, o):
    """Detect OTC session-close/open anomalies (resets, gap fills)."""
    try:
        if len(c) < 10:
            return None

        now_utc = datetime.now(timezone.utc)
        # OTC session boundaries (UTC): 00:00, 08:00, 16:00
        hour = now_utc.hour
        near_boundary = hour in [0, 1, 7, 8, 15, 16, 23]
        if not near_boundary:
            return None

        # At boundary: check if there's a gap or a spike
        gap = abs(float(o.iloc[-1]) - float(c.iloc[-2]))
        atr_val = float(_atr(h, lo, c, 14).iloc[-1])
        if gap < atr_val * 0.3:
            return None

        # Gap direction → fill direction is opposite
        if float(o.iloc[-1]) > float(c.iloc[-2]):
            return ("SELL",
                    f"OTC-M3 SESSION BOUNDARY — gap-up at OTC reset, mean-fill SELL")
        return ("BUY",
                f"OTC-M3 SESSION BOUNDARY — gap-down at OTC reset, mean-fill BUY")
    except Exception:
        return None


# ── OTC-M4: Tick Data Analysis (1m proxy) ────────────────────

def _tick_analysis(df1m):
    """Analyse 1m bar micro-structure as tick-flow proxy."""
    try:
        if df1m is None or len(df1m) < 10:
            return None

        h  = df1m["high"].squeeze().astype(float)
        lo = df1m["low"].squeeze().astype(float)
        c  = df1m["close"].squeeze().astype(float)
        o  = df1m["open"].squeeze().astype(float)

        last_close = float(c.iloc[-1])
        last_open  = float(o.iloc[-1])
        last_hi    = float(h.iloc[-1])
        last_lo    = float(lo.iloc[-1])
        rng        = last_hi - last_lo

        if rng < 1e-10:
            return None

        # Velocity: where did price close within the range?
        # 1.0 = closed at top (strong up tick flow), 0.0 = closed at bottom
        close_pos = (last_close - last_lo) / rng

        # Tick exhaustion: prior bar strong momentum then current bar reversal
        prior_close_pos = (float(c.iloc[-2]) - float(lo.iloc[-2])) / max(
            float(h.iloc[-2]) - float(lo.iloc[-2]), 1e-10)

        if prior_close_pos > 0.80 and close_pos < 0.30:
            return ("SELL",
                    "OTC-M4 TICK EXHAUSTION — up-tick velocity reversed, SELL signal")
        if prior_close_pos < 0.20 and close_pos > 0.70:
            return ("BUY",
                    "OTC-M4 TICK EXHAUSTION — down-tick velocity reversed, BUY signal")

        # Current bar strong tick flow
        if close_pos > 0.80:
            return ("BUY",  "OTC-M4 TICK FLOW BULL — strong up-tick velocity BUY")
        if close_pos < 0.20:
            return ("SELL", "OTC-M4 TICK FLOW BEAR — strong down-tick velocity SELL")

        return None
    except Exception:
        return None


# ── OTC-M5: OTC Session Heatmap ───────────────────────────────

def _session_heatmap(h, lo, c):
    """Detect cold→hot zone transition — entry timing signal."""
    try:
        if len(c) < 30:
            return None

        atr_s = _atr(h, lo, c, 5)   # fast ATR for heatmap
        if atr_s is None:
            return None

        avg_atr = float(atr_s.iloc[-20:].mean())
        last_3  = atr_s.iloc[-3:]

        cold_prior = all(float(last_3.iloc[i]) < avg_atr * 0.75 for i in range(2))
        hot_now    = float(atr_s.iloc[-1]) > avg_atr * 1.20

        if cold_prior and hot_now:
            # Transition from cold to hot — directional move starting
            trend_dir = "BUY" if float(c.iloc[-1]) > float(c.iloc[-3]) else "SELL"
            return (trend_dir,
                    f"OTC-M5 HEATMAP ACTIVATION — cold→hot zone transition {trend_dir}")
        return None
    except Exception:
        return None


# ── OTC-M6: Candle Sequence Probability Matrix ────────────────

def _probability_matrix(c, o):
    """Empirical next-candle probability from last 200 bars."""
    try:
        if len(c) < 50:
            return None

        dirs = [1 if float(c.iloc[i]) >= float(o.iloc[i]) else 0
                for i in range(len(c))]

        key_len = 3
        current_key = tuple(dirs[-key_len:])

        table: dict[tuple, list[int]] = {}
        for i in range(len(dirs) - key_len - 1):
            k = tuple(dirs[i:i + key_len])
            nxt = dirs[i + key_len]
            table.setdefault(k, []).append(nxt)

        outcomes = table.get(current_key, [])
        if len(outcomes) < 6:
            return None

        bull_p = sum(outcomes) / len(outcomes)
        bear_p = 1 - bull_p

        if bull_p >= 0.68:
            return ("BUY",
                    f"OTC-M6 PROBABILITY MATRIX — {bull_p:.0%} bull probability for next candle BUY")
        if bear_p >= 0.68:
            return ("SELL",
                    f"OTC-M6 PROBABILITY MATRIX — {bear_p:.0%} bear probability for next candle SELL")
        return None
    except Exception:
        return None


# ── PUBLIC API ─────────────────────────────────────────────────

def otc_manipulation_analyze(pair: str) -> Optional[dict]:
    """Run all OTC manipulation detectors. Returns dict or None."""
    if not _OK:
        return None

    cache_key = f"otcm|{pair}"
    now_ts = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < _TTL:
        return cached[1]

    ticker = yf_ticker(pair)
    if not ticker:
        _CACHE[cache_key] = (now_ts, None)
        return None

    df5  = None
    df1m = None
    try:
        df5  = _flatten(yf.download(ticker, period="10d", interval="5m",
                                    progress=False, auto_adjust=True))
        df1m = _flatten(yf.download(ticker, period="2d",  interval="1m",
                                    progress=False, auto_adjust=True))
    except Exception:
        pass

    if df5 is None or len(df5) < 20:
        _CACHE[cache_key] = (now_ts, None)
        return None

    h5 = df5["high"].squeeze().astype(float)
    lo5 = df5["low"].squeeze().astype(float)
    c5  = df5["close"].squeeze().astype(float)
    o5  = df5["open"].squeeze().astype(float)

    raw_results = [
        _repetitive_sequence(c5, o5),
        _martingale_trap(h5, lo5, c5),
        _session_boundary(h5, lo5, c5, o5),
        _tick_analysis(df1m),
        _session_heatmap(h5, lo5, c5),
        _probability_matrix(c5, o5),
    ]

    votes = [(reason, direction)
             for result in raw_results
             if result is not None
             for direction, reason in [result]]

    buy_votes  = [(r, d) for r, d in votes if d == "BUY"]
    sell_votes = [(r, d) for r, d in votes if d == "SELL"]

    if len(buy_votes) >= len(sell_votes):
        winner, agree, top = "BUY",  len(buy_votes),  [r for r, _ in buy_votes]
    else:
        winner, agree, top = "SELL", len(sell_votes), [r for r, _ in sell_votes]

    if agree < _MIN_AGREE:
        _CACHE[cache_key] = (now_ts, None)
        return None

    result = {
        "direction": winner,
        "score":     int(agree / 6 * 100),
        "engines":   agree,
        "reasons":   top[:3],
    }
    _CACHE[cache_key] = (now_ts, result)
    return result
