"""SMART AI · Trade Entry Signals
=================================

Direct Python port of the user-supplied Pine v6 indicator
("Trade Entry Signals" — sweep ▸ BoS ▸ MS) with three additional
production-grade filters layered on top:

    1. TRENDLINE THEORY
       Buys must break the descending trendline drawn through the
       most recent pivot highs.  Sells must break the ascending
       trendline drawn through the most recent pivot lows.

    2. TRUE BREAKOUT
       The trigger candle's CLOSE must clear the level by at least
       0.10 × ATR(14) AND the candle body must be ≥ 50 % of its
       full range.  This filters out wicks that pretend to break
       structure but never actually close beyond it.

    3. WICK ENTRY CONFIRMATION
       For a BUY, the trigger candle must show a rejection wick on
       the LOWER side that is ≥ 35 % of the candle's range
       (i.e. price was sold into the level then bought back hard).
       Mirror condition for SELL.

The three filters together stop the "sweep + BoS but no follow-
through" prints that were producing back-to-back losses.

Public API
----------
    analyze(pair) -> dict | None

The dict carries everything the forex engine needs to build a
high-quality entry:

    direction        : "BUY" | "SELL"
    entry            : float   – close of the trigger candle (live tick)
    swept_swing      : float   – opposing liquidity that just got swept
                                 (use this as the SL anchor)
    broken_swing     : float   – the BoS level that was taken out
    ms_swing         : float   – the MS swing that was broken
    atr              : float   – 1H ATR(14)
    true_breakout    : bool    – body / close confirmation passed
    wick_confirm     : bool    – rejection wick confirmation passed
    trendline_ok     : bool    – broke the active trendline
    grade            : int     – 0..100 confluence score
    notes            : list[str]
"""
from __future__ import annotations

import math
import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception as _e:                              # pragma: no cover
    print(f"[trade_entry] yfinance/pandas import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

# ── Tunables ────────────────────────────────────────────────
TIMEFRAME            = "1h"
LOOKBACK_BARS        = 300
ATR_PERIOD           = 14
PIVOT_WINDOW         = 3        # candle is pivot if extreme of ±N bars
TRENDLINE_PIVOTS     = 4        # use last N pivots to fit the trendline
SIGNAL_FRESHNESS_BARS = 8       # MS signal must have fired in last N bars

# True-breakout filter knobs
MIN_CLOSE_BUFFER_ATR = 0.10     # close must clear level by ≥ 0.10 * ATR
MIN_BODY_RATIO       = 0.50     # body ≥ 50 % of full range

# Wick confirmation knob
MIN_REJECTION_WICK   = 0.35     # opposing wick ≥ 35 % of full range

# 90-second cache so the engine can call analyze() per pair without
# hammering yfinance every signal cycle.
TTL = 90.0
_CACHE: dict[str, tuple[float, Optional[dict]]] = {}


# ────────────────────────────────────────────────────────────
#  Data fetch (mirrors liquidity.py for consistency)
# ────────────────────────────────────────────────────────────
def _fetch(pair: str):
    if not _OK:
        return None
    ticker = yf_ticker(pair)
    if not ticker:
        return None
    try:
        df = yf.download(
            ticker, period="60d", interval=TIMEFRAME,
            progress=False, auto_adjust=True,
        )
        if df is None or df.empty or len(df) < 60:
            return None
        # Flatten possibly-MultiIndex columns and lowercase
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df.tail(LOOKBACK_BARS).copy()
    except Exception as e:
        print(f"[trade_entry] fetch error {ticker}: {e}")
        return None


def _atr(df, period: int = ATR_PERIOD) -> Optional[float]:
    try:
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        tr = pd.concat([
            (h - l).abs(),
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ], axis=1).max(axis=1)
        a = tr.rolling(period).mean().iloc[-1]
        return float(a) if a == a else None      # NaN check
    except Exception:
        return None


# ────────────────────────────────────────────────────────────
#  Pivots (used by trendline + as a sanity check on swept liquidity)
# ────────────────────────────────────────────────────────────
def _pivots(df, win: int = PIVOT_WINDOW):
    """Return (pivot_highs, pivot_lows) as lists of (index, price)."""
    highs, lows = [], []
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    n = len(df)
    for i in range(win, n - win):
        if h[i] == max(h[i - win: i + win + 1]):
            highs.append((i, float(h[i])))
        if l[i] == min(l[i - win: i + win + 1]):
            lows.append((i, float(l[i])))
    return highs, lows


def _trendline_break(df, direction: str, pivots) -> bool:
    """True if the LAST CLOSED bar broke the active trendline.

    BUY  → must close ABOVE the descending line through last N pivot HIGHS.
    SELL → must close BELOW the ascending  line through last N pivot LOWS.
    """
    pts = pivots[-TRENDLINE_PIVOTS:]
    if len(pts) < 2:
        return True                              # not enough data — pass
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n)) or 1e-9
    slope = num / den
    intercept = mean_y - slope * mean_x

    last_idx = len(df) - 1
    last_close = float(df["close"].iloc[-1])
    line_at_last = slope * last_idx + intercept

    if direction == "BUY":
        # Descending line through pivot HIGHS — slope should be negative
        # for an active down-trendline. If slope is upward there isn't
        # really a trendline to break, so we let the trade through.
        if slope >= 0:
            return True
        return last_close > line_at_last
    else:
        if slope <= 0:
            return True
        return last_close < line_at_last


# ────────────────────────────────────────────────────────────
#  Pine state machine (sweep ▸ BoS ▸ MS)
# ────────────────────────────────────────────────────────────
def _run_state_machine(df) -> dict:
    """Walk through the bars and emit the latest MS signal with the
    swing levels that produced it."""
    o = df["open"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    n = len(df)

    pendingBreakLevel = math.nan
    opposingLevel     = math.nan
    pendingDirection  = 0
    trackedHigh       = math.nan
    trackedLow        = math.nan

    lastBullSwingLow  = math.nan
    lastBearSwingHigh = math.nan

    msDirection = 0
    last_signal = None      # dict | None

    for i in range(1, n):
        # ── SWEEP detection (close beyond prior bar's range) ──
        downSweep = (h[i] > l[i - 1]) and (c[i] < l[i - 1])
        upSweep   = (l[i] < h[i - 1]) and (c[i] > h[i - 1])

        # ── Set pendingDirection on a fresh sweep ──
        if pendingDirection == 0:
            sweepSizeDown = h[i - 1] - l[i]
            sweepSizeUp   = h[i] - l[i - 1]
            if downSweep and upSweep:
                if sweepSizeDown >= sweepSizeUp:
                    pendingDirection  = -1
                    pendingBreakLevel = l[i - 1]
                    opposingLevel     = h[i - 1]
                else:
                    pendingDirection  = +1
                    pendingBreakLevel = h[i - 1]
                    opposingLevel     = l[i - 1]
            elif downSweep:
                pendingDirection  = -1
                pendingBreakLevel = l[i - 1]
                opposingLevel     = h[i - 1]
            elif upSweep:
                pendingDirection  = +1
                pendingBreakLevel = h[i - 1]
                opposingLevel     = l[i - 1]
            if pendingDirection != 0:
                trackedHigh = h[i]
                trackedLow  = l[i]

        # ── While a sweep is pending, track extremes ──
        if pendingDirection != 0:
            trackedHigh = max(trackedHigh, h[i])
            trackedLow  = min(trackedLow,  l[i])

        # ── BoS / invalidation evaluation ──
        downMove   = pendingDirection == -1 and c[i] < pendingBreakLevel
        upMove     = pendingDirection == +1 and c[i] > pendingBreakLevel
        bearInvalid = pendingDirection == -1 and c[i] > opposingLevel
        bullInvalid = pendingDirection == +1 and c[i] < opposingLevel

        if upMove:
            lastBullSwingLow = trackedLow
        if downMove:
            lastBearSwingHigh = trackedHigh

        if downMove or upMove or bearInvalid or bullInvalid:
            pendingBreakLevel = math.nan
            pendingDirection  = 0
            trackedHigh       = math.nan
            trackedLow        = math.nan

        # ── MS (Market Structure shift) ──
        msBullBreak = (not math.isnan(lastBearSwingHigh)
                       and c[i] > lastBearSwingHigh
                       and msDirection != +1)
        msBearBreak = (not math.isnan(lastBullSwingLow)
                       and c[i] < lastBullSwingLow
                       and msDirection != -1)

        if msBullBreak:
            last_signal = {
                "bar"          : i,
                "direction"    : "BUY",
                "broken_swing" : float(lastBearSwingHigh),
                "swept_swing"  : float(lastBullSwingLow)
                                  if not math.isnan(lastBullSwingLow)
                                  else float(min(l[max(0, i-20):i+1])),
                "trigger_o"    : float(o[i]),
                "trigger_h"    : float(h[i]),
                "trigger_l"    : float(l[i]),
                "trigger_c"    : float(c[i]),
            }
            msDirection = +1

        if msBearBreak:
            last_signal = {
                "bar"          : i,
                "direction"    : "SELL",
                "broken_swing" : float(lastBullSwingLow),
                "swept_swing"  : float(lastBearSwingHigh)
                                  if not math.isnan(lastBearSwingHigh)
                                  else float(max(h[max(0, i-20):i+1])),
                "trigger_o"    : float(o[i]),
                "trigger_h"    : float(h[i]),
                "trigger_l"    : float(l[i]),
                "trigger_c"    : float(c[i]),
            }
            msDirection = -1

    return {"signal": last_signal, "last_bar": n - 1}


# ────────────────────────────────────────────────────────────
#  Confirmation filters
# ────────────────────────────────────────────────────────────
def _true_breakout(sig: dict, atr: float) -> bool:
    """Trigger candle's close cleared the broken swing by ≥ 0.10·ATR
    AND the body is ≥ 50 % of the full bar range."""
    if atr <= 0:
        return True
    o = sig["trigger_o"]; c = sig["trigger_c"]
    h = sig["trigger_h"]; l = sig["trigger_l"]
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    if body / rng < MIN_BODY_RATIO:
        return False
    buffer = MIN_CLOSE_BUFFER_ATR * atr
    if sig["direction"] == "BUY":
        return c >= sig["broken_swing"] + buffer
    return c <= sig["broken_swing"] - buffer


def _wick_confirm(sig: dict) -> bool:
    """Rejection wick on the OPPOSING side of the trigger candle."""
    o = sig["trigger_o"]; c = sig["trigger_c"]
    h = sig["trigger_h"]; l = sig["trigger_l"]
    rng = max(h - l, 1e-9)
    if sig["direction"] == "BUY":
        lower_wick = min(o, c) - l
        return (lower_wick / rng) >= MIN_REJECTION_WICK
    upper_wick = h - max(o, c)
    return (upper_wick / rng) >= MIN_REJECTION_WICK


# ────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────
def analyze(pair: str) -> Optional[dict]:
    """Return the SMART AI entry packet for `pair` or None when no
    fresh / clean signal is available."""
    now = time.time()
    if pair in _CACHE:
        ts, val = _CACHE[pair]
        if now - ts < TTL:
            return val

    df = _fetch(pair)
    if df is None:
        _CACHE[pair] = (now, None)
        return None

    state = _run_state_machine(df)
    sig = state["signal"]
    last_bar = state["last_bar"]

    if not sig:
        _CACHE[pair] = (now, None)
        return None

    # Freshness — only act on a signal that's still "alive"
    if last_bar - sig["bar"] > SIGNAL_FRESHNESS_BARS:
        _CACHE[pair] = (now, None)
        return None

    atr = _atr(df) or 0.0
    pivot_highs, pivot_lows = _pivots(df)

    direction = sig["direction"]

    # Confirmation filters
    true_bo  = _true_breakout(sig, atr)
    wick_ok  = _wick_confirm(sig)
    trend_ok = _trendline_break(
        df, direction,
        pivot_highs if direction == "BUY" else pivot_lows,
    )

    # Confluence grade
    grade = 60                                    # base — Pine setup itself
    if true_bo:  grade += 15
    if wick_ok:  grade += 15
    if trend_ok: grade += 10
    grade = min(100, grade)

    notes: list[str] = []
    notes.append(f"Sweep ▸ BoS ▸ MS confirmed ({direction})")
    notes.append("True breakout ✅"     if true_bo  else "Weak breakout body ⚠️")
    notes.append("Rejection wick ✅"    if wick_ok  else "Flat trigger candle ⚠️")
    notes.append("Trendline broken ✅"  if trend_ok else "Trendline still intact ⚠️")

    out = {
        "direction"     : direction,
        "entry"         : sig["trigger_c"],
        "swept_swing"   : sig["swept_swing"],
        "broken_swing"  : sig["broken_swing"],
        "ms_swing"      : sig["broken_swing"],
        "atr"           : atr,
        "true_breakout" : true_bo,
        "wick_confirm"  : wick_ok,
        "trendline_ok"  : trend_ok,
        "grade"         : grade,
        "notes"         : notes,
        "bar"           : sig["bar"],
        "last_bar"      : last_bar,
    }
    _CACHE[pair] = (now, out)
    return out


def is_valid(packet: Optional[dict], min_grade: int = 75) -> bool:
    """Convenience predicate — packet exists, the TRUE-BREAKOUT filter
    passed (mandatory: structure must actually close beyond the BoS
    level by ≥ 0.10·ATR), at least ONE of (wick rejection / trendline
    break) confirms the move, and the confluence grade beats `min_grade`.

    We deliberately don't require ALL three confirmations — in live
    markets you very rarely get a perfect 3/3 print, and demanding
    it would mean we never fire. The Pine state machine itself
    (sweep ▸ BoS ▸ MS) is the primary edge; the extra filters are
    quality bumps."""
    if not packet:
        return False
    if not packet.get("true_breakout"):
        return False
    if not (packet.get("wick_confirm") or packet.get("trendline_ok")):
        return False
    return packet.get("grade", 0) >= min_grade
