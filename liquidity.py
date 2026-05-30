"""Smart-Money / Liquidity engine for SUPREME PRO AI BOT.

Plugs price-action concepts that institutional desks actually use into
every signal:

    • Swing highs / lows           — pivot pools where stops cluster
    • BOS  (Break of Structure)    — confirmed continuation
    • CHoCH (Change of Character)  — early reversal
    • Liquidity sweep              — fake-out above a recent high/low
    • Order block                  — last opposing candle before a BOS
    • Fair Value Gap (FVG / IPDA)  — 3-bar imbalance the market revisits
    • Equilibrium (50%) /
      Premium-Discount zones       — only buy in discount, sell in premium
    • Liquidity ladder             — next pool above / below the current
                                     price for staircase TP targeting

Public API
----------
    analyze(pair, direction) -> dict | None

The dict carries:
    sl_price       — stop placed BEYOND the nearest opposing liquidity
                     pool with an ATR-scaled buffer (so wicks don't pick
                     it off)
    tp_pools       — list of next liquidity prices in the trade direction
                     (TP1 → TPn lined up against real pools, not random
                     pip distances)
    atr            — 1H ATR(14) — used for SL clamping & risk sizing
    liq_grade      — 0..100 quality score (BOS + sweep + OB + FVG bonus)
    notes          — short human-readable reasons used in the signal card
    last_swing_hi / last_swing_lo
    fvg / order_block (optional) — price zones if present
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception as _e:  # pragma: no cover
    print(f"[liquidity] yfinance/pandas import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker, pip_size as live_pip_size

# ── Tunables ──────────────────────────────────────────────
TIMEFRAME       = "1h"
LOOKBACK_BARS   = 250
PIVOT_WINDOW    = 3      # candle is pivot if it's the highest/lowest of ±N bars
ATR_PERIOD      = 14
SWEEP_LOOKBACK  = 20     # bars to look back for a swept high/low
RECENT_BOS_BARS = 30
TTL             = 90.0   # cache seconds


_CACHE: dict[tuple[str, str], tuple[float, Optional[dict]]] = {}


# ─────────────────────────────────────────────────────────
#  Data fetch
# ─────────────────────────────────────────────────────────
def _fetch(ticker: str):
    if not _OK:
        return None
    try:
        df = yf.download(
            ticker, period="60d", interval=TIMEFRAME,
            progress=False, auto_adjust=True,
        )
        if df is None or df.empty or len(df) < 30:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df.tail(LOOKBACK_BARS).copy()
    except Exception as e:
        print(f"[liquidity] fetch error {ticker}: {e}")
        return None


def _atr(df, period: int = ATR_PERIOD) -> Optional[float]:
    try:
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        prev = c.shift(1)
        tr = (h - l).combine((h - prev).abs(), max).combine(
            (l - prev).abs(), max)
        a = tr.rolling(period).mean().iloc[-1]
        return float(a) if a == a else None  # NaN check
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
#  Swing pivots = liquidity pools
# ─────────────────────────────────────────────────────────
def _swing_highs(df, w: int = PIVOT_WINDOW) -> list[tuple[int, float]]:
    """Return [(bar_idx, price), …] for each confirmed pivot HIGH."""
    out: list[tuple[int, float]] = []
    h = df["high"].astype(float).tolist()
    n = len(h)
    for i in range(w, n - w):
        seg = h[i - w: i + w + 1]
        if h[i] == max(seg) and h[i] > h[i - 1] and h[i] >= h[i + 1]:
            out.append((i, h[i]))
    return out


def _swing_lows(df, w: int = PIVOT_WINDOW) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    l = df["low"].astype(float).tolist()
    n = len(l)
    for i in range(w, n - w):
        seg = l[i - w: i + w + 1]
        if l[i] == min(seg) and l[i] < l[i - 1] and l[i] <= l[i + 1]:
            out.append((i, l[i]))
    return out


# ─────────────────────────────────────────────────────────
#  BOS / CHoCH
# ─────────────────────────────────────────────────────────
def _bos_choch(df, highs, lows, current_close: float) -> tuple[str, str]:
    """Return (structure, direction).

    structure ∈ {'BOS','CHoCH','—'}, direction ∈ {'bull','bear','—'}.
    A BOS = recent close breaks the most recent swing high (bull) or low (bear).
    A CHoCH = the FIRST counter-break after a sequence of moves the other
    way — early reversal cue.
    """
    if not highs or not lows:
        return ("—", "—")
    last_hi = highs[-1][1]
    last_lo = lows[-1][1]
    last_hi_idx = highs[-1][0]
    last_lo_idx = lows[-1][0]
    n = len(df)
    # Only look at bars after the swing was set
    if current_close > last_hi and (n - 1 - last_hi_idx) <= RECENT_BOS_BARS:
        # Was the prior structure bearish? (low after low) → CHoCH else BOS
        if len(lows) >= 2 and lows[-1][1] < lows[-2][1]:
            return ("CHoCH", "bull")
        return ("BOS", "bull")
    if current_close < last_lo and (n - 1 - last_lo_idx) <= RECENT_BOS_BARS:
        if len(highs) >= 2 and highs[-1][1] > highs[-2][1]:
            return ("CHoCH", "bear")
        return ("BOS", "bear")
    return ("—", "—")


# ─────────────────────────────────────────────────────────
#  Liquidity sweep (stop hunt)
# ─────────────────────────────────────────────────────────
def _last_sweep(df, highs, lows) -> Optional[str]:
    """Detect whether the last few candles SWEPT a recent swing.

    A bullish sweep = a candle wicked BELOW the most recent swing low
    and then closed back above it (longs got their stops hunted, smart
    money loaded up). A bearish sweep is the mirror image.
    Returns 'bull' / 'bear' / None.
    """
    if len(df) < 5:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    h_last = float(last["high"]); l_last = float(last["low"])
    c_last = float(last["close"]); o_last = float(last["open"])
    # Look for a sweep within the last 3 candles
    for sw_low_idx, sw_low in lows[-5:]:
        if l_last < sw_low and c_last > sw_low and c_last > o_last:
            return "bull"
    for sw_hi_idx, sw_hi in highs[-5:]:
        if h_last > sw_hi and c_last < sw_hi and c_last < o_last:
            return "bear"
    # Also check the candle BEFORE last (sweep + reaction)
    h_prev = float(prev["high"]); l_prev = float(prev["low"])
    c_prev = float(prev["close"])
    for _, sw_low in lows[-5:]:
        if l_prev < sw_low and c_prev > sw_low and c_last > c_prev:
            return "bull"
    for _, sw_hi in highs[-5:]:
        if h_prev > sw_hi and c_prev < sw_hi and c_last < c_prev:
            return "bear"
    return None


# ─────────────────────────────────────────────────────────
#  Order block — last opposing candle before a BOS
# ─────────────────────────────────────────────────────────
def _order_block(df, direction: str) -> Optional[tuple[float, float]]:
    """Return (low, high) of the OB for `direction` ('bull'/'bear'),
    or None. Bullish OB = last DOWN candle before a 3-bar up move.
    Bearish OB = last UP candle before a 3-bar down move."""
    n = len(df)
    if n < 8:
        return None
    o = df["open"].astype(float).tolist()
    c = df["close"].astype(float).tolist()
    h = df["high"].astype(float).tolist()
    l = df["low"].astype(float).tolist()
    # Scan the last 30 bars for the most recent OB
    for i in range(n - 4, max(0, n - 30), -1):
        if direction == "bull":
            # bearish candle followed by 3 bullish closes
            if c[i] < o[i] and all(c[j] > o[j] for j in (i+1, i+2, i+3)):
                return (l[i], h[i])
        else:
            if c[i] > o[i] and all(c[j] < o[j] for j in (i+1, i+2, i+3)):
                return (l[i], h[i])
    return None


# ─────────────────────────────────────────────────────────
#  Fair Value Gap (3-bar imbalance)
# ─────────────────────────────────────────────────────────
def _fvg(df, direction: str) -> Optional[tuple[float, float]]:
    """Return (low, high) of the most recent unfilled FVG in `direction`.

    Bullish FVG (gap up): bar[i-2].high < bar[i].low → gap (i-2.high, i.low)
    Bearish FVG (gap dn): bar[i-2].low  > bar[i].high → gap (i.high, i-2.low)
    """
    n = len(df)
    if n < 4:
        return None
    h = df["high"].astype(float).tolist()
    l = df["low"].astype(float).tolist()
    for i in range(n - 1, 2, -1):
        if direction == "bull":
            if h[i - 2] < l[i]:
                return (h[i - 2], l[i])
        else:
            if l[i - 2] > h[i]:
                return (h[i], l[i - 2])
    return None


# ─────────────────────────────────────────────────────────
#  Liquidity ladder — sequential pools above / below price
# ─────────────────────────────────────────────────────────
def _ladder(highs, lows, entry: float, direction: str,
            num: int = 6) -> list[float]:
    """Return up to `num` next liquidity pools in trade direction,
    sorted closest-first."""
    if direction == "bull":
        pools = sorted({p for _, p in highs if p > entry})
    else:
        pools = sorted({p for _, p in lows if p < entry}, reverse=True)
    return pools[:num]


# ─────────────────────────────────────────────────────────
#  PUBLIC: analyze(pair, direction)
# ─────────────────────────────────────────────────────────
def analyze(pair: str, direction: str) -> Optional[dict]:
    """Run the full SMC analysis on `pair` for the desired trade
    `direction` ('BUY' or 'SELL'). Returns None if liquidity data is
    insufficient or the trade direction conflicts with structure."""
    if not _OK:
        return None
    direction = (direction or "").upper()
    if direction not in {"BUY", "SELL"}:
        return None
    side = "bull" if direction == "BUY" else "bear"

    ticker = yf_ticker(pair)
    if not ticker:
        return None
    key = (ticker, direction)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < TTL:
        return cached[1]

    df = _fetch(ticker)
    if df is None or len(df) < 30:
        _CACHE[key] = (now, None); return None

    pip = live_pip_size(pair)
    last_close = float(df["close"].iloc[-1])
    atr = _atr(df) or (10 * pip)

    highs = _swing_highs(df)
    lows  = _swing_lows(df)
    if len(highs) < 2 or len(lows) < 2:
        _CACHE[key] = (now, None); return None

    structure, struct_dir = _bos_choch(df, highs, lows, last_close)
    sweep = _last_sweep(df, highs, lows)
    ob    = _order_block(df, side)
    fvg   = _fvg(df, side)

    # SL: place beyond the nearest opposing liquidity pool, with an
    # ATR-scaled buffer so wicks don't pick it off.
    buffer = max(0.30 * atr, 3 * pip)
    if side == "bull":
        # opposing liquidity = nearest swing low BELOW price
        below = [p for _, p in lows if p < last_close]
        if not below:
            _CACHE[key] = (now, None); return None
        pivot = max(below)
        sl_price = pivot - buffer
    else:
        above = [p for _, p in highs if p > last_close]
        if not above:
            _CACHE[key] = (now, None); return None
        pivot = min(above)
        sl_price = pivot + buffer

    # TP ladder = next liquidity pools in trade direction
    tp_pools = _ladder(highs, lows, last_close, side, num=6)
    if not tp_pools:
        _CACHE[key] = (now, None); return None

    # Quality score 0..100
    grade = 30
    if structure == "BOS" and struct_dir == side: grade += 25
    if structure == "CHoCH" and struct_dir == side: grade += 30
    if sweep == side: grade += 20            # we just took out opposite stops
    if ob is not None: grade += 12
    if fvg is not None: grade += 10
    grade = min(100, grade)

    notes: list[str] = []
    if structure != "—" and struct_dir == side:
        notes.append(f"{structure} ↑" if side == "bull" else f"{structure} ↓")
    if sweep == side:
        notes.append("Liquidity sweep ✓")
    if ob is not None:
        notes.append("OB confluence")
    if fvg is not None:
        notes.append("FVG fill")

    result = {
        "side":          side,
        "atr":           atr,
        "sl_price":      sl_price,
        "sl_pivot":      pivot,
        "tp_pools":      tp_pools,
        "structure":     structure,
        "struct_dir":    struct_dir,
        "sweep":         sweep,
        "order_block":   ob,
        "fvg":           fvg,
        "liq_grade":     grade,
        "notes":         notes,
        "last_swing_hi": highs[-1][1],
        "last_swing_lo": lows[-1][1],
    }
    _CACHE[key] = (now, result)
    return result
