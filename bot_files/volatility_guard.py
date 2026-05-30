"""VOLATILITY GUARD — Supreme Pro AI Bot V6
==========================================
Detects dangerous market conditions and adapts signals accordingly.

Core problems solved
--------------------
  ① Friday close / news-day binary losses
       On high-impact days (NFP, CPI, FOMC, Friday NY close) price spikes
       3–5× the normal ATR. Trend-following engines give the WRONG direction
       because the spike reverses immediately after the news flush. This guard
       detects those windows and either BLOCKS the signal or restricts entries
       to pure momentum direction (ride the impulse, not fight it).

  ② Forex SL hits during volatile sessions
       Tight SL distances get grazed by normal volatility expansion. The guard
       returns a multiplier so SL is automatically widened on high-vol days,
       giving price room to breathe without losing the trade.

Public API
----------
  get_volatility_state(pair)          → dict  (mode, atr_ratio, flags, advice)
  binary_volatility_gate(pair, dir, tf_label) → "ALLOW" | "BLOCK" | "MOMENTUM_ONLY"
  forex_sl_multiplier(pair)           → float  (1.0 normal … 1.8 extreme)
  get_momentum_direction(pair)        → "BUY" | "SELL" | None
  is_high_impact_window()             → bool
  is_friday_close_zone()              → bool
  volatility_label(mode)              → str   (emoji label for signal card)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception:
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker


# ── Cache ──────────────────────────────────────────────────────────────────
_VOL_CACHE: dict[str, tuple[float, dict]] = {}
_MOM_CACHE: dict[str, tuple[float, Optional[str]]] = {}
_VOL_TTL = 30.0   # refresh every 30 s — we need fresh ATR on volatile days
_MOM_TTL = 20.0


# ═══════════════════════════════════════════════════════════════════════════
#  HIGH-IMPACT NEWS WINDOW DETECTOR
# ═══════════════════════════════════════════════════════════════════════════
# Block window = ±15 minutes around each release.
# These are UTC hours and minutes for the most-moved economic events.
_NEWS_SLOTS_UTC: list[tuple[int, int, str]] = [
    (7,  0,  "EU CPI/GDP"),
    (7, 45,  "ECB/BOE statement"),
    (8, 30,  "US/CA data: NFP·CPI·GDP·Retail"),
    (9,  0,  "CA data / EU PMI"),
    (10,  0, "US ISM/PMI/Consumer conf"),
    (12, 30, "US session open surge"),
    (13, 30, "US data"),
    (14,  0, "FOMC / US PMI"),
    (14, 30, "US crude oil inventories"),
    (18,  0, "FOMC rate decision"),
    (18, 30, "FOMC press conference"),
    (21,  0, "RBNZ/RBA decisions"),
    (23, 50, "JPY trade balance"),
]
_NEWS_BLOCK_MINUTES = 20   # block ±20 min around each slot


def is_high_impact_window() -> bool:
    """Return True if we are within ±20 minutes of a known high-impact news slot."""
    try:
        now = datetime.now(timezone.utc)
        now_mins = now.hour * 60 + now.minute
        for h, m, _ in _NEWS_SLOTS_UTC:
            slot_mins = h * 60 + m
            if abs(now_mins - slot_mins) <= _NEWS_BLOCK_MINUTES:
                return True
        # Extra: first Friday of month 08:30 UTC = NFP — always a big mover
        if now.weekday() == 4:   # Friday
            # 08:15–09:00 UTC = NFP danger window
            if 495 <= now_mins <= 540:
                return True
    except Exception:
        pass
    return False


def is_friday_close_zone() -> bool:
    """Return True during the Friday NY session close trap zone (19:00–22:30 UTC).

    This is the most dangerous 3-hour window of the week for binary trading:
    - Institutional stop hunts clear both sides before weekend gap
    - Spreads expand, liquidity evaporates
    - Price spikes 2–4× ATR then instantly reverses
    """
    try:
        now = datetime.now(timezone.utc)
        if now.weekday() != 4:   # not Friday
            return False
        now_mins = now.hour * 60 + now.minute
        # 19:00–22:30 UTC = NY close / pre-weekend liquidity grab
        return 1140 <= now_mins <= 1350
    except Exception:
        return False


def is_monday_open_zone() -> bool:
    """Return True during the Monday gap-open danger window (00:00–02:00 UTC)."""
    try:
        now = datetime.now(timezone.utc)
        if now.weekday() != 0:
            return False
        return now.hour < 2
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  ATR-BASED VOLATILITY STATE
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_5m(ticker: str):
    if not _OK:
        return None
    try:
        df = yf.download(ticker, period="5d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df
    except Exception:
        return None


def _calc_atr_ratio(df) -> float:
    """Compare current-bar range to rolling-20-bar ATR.

    Returns ratio > 1 = current bar bigger than average.
    2.0 = current bar is 2× normal ATR = high volatility.
    """
    try:
        hi = df["high"].squeeze().astype(float)
        lo = df["low"].squeeze().astype(float)
        cl = df["close"].squeeze().astype(float)
        prev_c = cl.shift(1)
        tr = (hi - lo).combine((hi - prev_c).abs(), max).combine(
             (lo - prev_c).abs(), max)
        avg_atr = float(tr.rolling(20).mean().iloc[-2])  # -2 = confirmed bar
        if avg_atr <= 0:
            return 1.0
        # Use last 3 bars to get a stable current-volatility reading
        recent_tr = float(tr.tail(4).mean())
        return round(recent_tr / avg_atr, 2)
    except Exception:
        return 1.0


def get_volatility_state(pair: str) -> dict:
    """Full volatility diagnosis for a pair.

    Returns
    -------
    {
      'mode':        'extreme' | 'high' | 'normal' | 'low',
      'atr_ratio':   float,          # current ATR / rolling-20 avg ATR
      'is_news':     bool,
      'is_friday_close': bool,
      'is_monday_gap':   bool,
      'advice':      'AVOID' | 'MOMENTUM_ONLY' | 'WIDEN_SL' | 'NORMAL',
      'reason':      str,
    }
    """
    ticker = yf_ticker(pair)
    if ticker:
        now_ts = time.time()
        cached = _VOL_CACHE.get(ticker)
        if cached and (now_ts - cached[0]) < _VOL_TTL:
            return cached[1]

    is_news       = is_high_impact_window()
    is_fri_close  = is_friday_close_zone()
    is_mon_gap    = is_monday_open_zone()

    atr_ratio = 1.0
    if ticker:
        df = _fetch_5m(ticker)
        if df is not None:
            atr_ratio = _calc_atr_ratio(df)

    # Classify volatility mode
    if atr_ratio >= 2.5 or (is_news and atr_ratio >= 1.6):
        mode = "extreme"
    elif atr_ratio >= 1.7 or is_news or is_fri_close:
        mode = "high"
    elif atr_ratio <= 0.65:
        mode = "low"
    else:
        mode = "normal"

    # Trading advice
    if is_fri_close:
        advice = "AVOID"
        reason = f"Friday NY close zone — liquidity grab / stop hunt active (ATR×{atr_ratio:.1f})"
    elif is_mon_gap:
        advice = "AVOID"
        reason = "Monday gap-open — weekend gap risk, spreads wide"
    elif mode == "extreme":
        advice = "MOMENTUM_ONLY"
        reason = f"ATR spike {atr_ratio:.1f}× normal — only ride the impulse direction"
    elif mode == "high" and is_news:
        advice = "MOMENTUM_ONLY"
        reason = f"High-impact news window — ATR {atr_ratio:.1f}× — momentum entries only"
    elif mode == "high":
        advice = "WIDEN_SL"
        reason = f"Elevated volatility ATR×{atr_ratio:.1f} — SL widened, entries allowed"
    else:
        advice = "NORMAL"
        reason = f"Normal volatility ATR×{atr_ratio:.1f}"

    result = {
        "mode":            mode,
        "atr_ratio":       atr_ratio,
        "is_news":         is_news,
        "is_friday_close": is_fri_close,
        "is_monday_gap":   is_mon_gap,
        "advice":          advice,
        "reason":          reason,
    }

    if ticker:
        _VOL_CACHE[ticker] = (time.time(), result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  MOMENTUM DIRECTION (ride the impulse on volatile days)
# ═══════════════════════════════════════════════════════════════════════════

def get_momentum_direction(pair: str) -> Optional[str]:
    """Detect the current short-term impulse direction from real 5m candles.

    Uses three confirming signals:
      1. Last 3 closed 5m candle net direction (majority)
      2. 5m EMA(5) vs EMA(13) micro-trend
      3. 5m ATR expansion direction (price moving away from EMA)

    Returns 'BUY', 'SELL', or None (no clear impulse).
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now_ts = time.time()
    cached = _MOM_CACHE.get(ticker)
    if cached and (now_ts - cached[0]) < _MOM_TTL:
        return cached[1]

    if not _OK:
        _MOM_CACHE[ticker] = (now_ts, None)
        return None

    df = _fetch_5m(ticker)
    if df is None or "close" not in df.columns or len(df) < 20:
        _MOM_CACHE[ticker] = (now_ts, None)
        return None

    try:
        cl = df["close"].squeeze().astype(float).dropna()
        op = df["open"].squeeze().astype(float).dropna()
        hi = df["high"].squeeze().astype(float).dropna()
        lo = df["low"].squeeze().astype(float).dropna()

        votes = []

        # 1. Net candle direction — last 3 confirmed closed bars (skip -1)
        for i in [-2, -3, -4]:
            try:
                c = float(cl.iloc[i])
                o = float(op.iloc[i])
                h = float(hi.iloc[i])
                l = float(lo.iloc[i])
                rng = max(h - l, 1e-10)
                body = abs(c - o)
                if body / rng >= 0.40:   # meaningful candle
                    votes.append(1 if c > o else -1)
            except Exception:
                pass

        # 2. EMA(5) vs EMA(13) micro-cross
        def _ema(s, p):
            return s.ewm(span=p, adjust=False).mean()
        ef  = float(_ema(cl, 5).iloc[-1])
        es  = float(_ema(cl, 13).iloc[-1])
        if ef > es * 1.0001:
            votes.append(1)
        elif ef < es * 0.9999:
            votes.append(-1)

        # 3. ATR expansion direction (is price running UP or DOWN from midpoint?)
        try:
            mid = float(_ema(cl, 8).iloc[-1])
            last_cl = float(cl.iloc[-1])
            last_hi = float(hi.iloc[-1])
            last_lo = float(lo.iloc[-1])
            rng = last_hi - last_lo
            avg_atr_val = float((hi - lo).rolling(14).mean().iloc[-2])
            if rng >= 1.4 * avg_atr_val:
                votes.append(1 if last_cl > mid else -1)
        except Exception:
            pass

        if not votes:
            direction = None
        else:
            bull = sum(1 for v in votes if v > 0)
            bear = sum(1 for v in votes if v < 0)
            if bull >= 3 and bull > bear:
                direction = "BUY"
            elif bear >= 3 and bear > bull:
                direction = "SELL"
            elif bull > bear and len(votes) >= 4:
                direction = "BUY"
            elif bear > bull and len(votes) >= 4:
                direction = "SELL"
            else:
                direction = None
    except Exception:
        direction = None

    _MOM_CACHE[ticker] = (now_ts, direction)
    return direction


# ═══════════════════════════════════════════════════════════════════════════
#  BINARY VOLATILITY GATE
# ═══════════════════════════════════════════════════════════════════════════

def binary_volatility_gate(
    pair: str,
    direction: str,
    tf_label: str = "1 MIN",
) -> str:
    """Quality gate for binary signals during volatile conditions.

    Returns
    -------
    "ALLOW"         — normal conditions, proceed with signal
    "MOMENTUM_ONLY" — high vol: only allow if direction matches impulse
    "BLOCK"         — extreme/news/Friday: do not send signal
    """
    vs = get_volatility_state(pair)
    mode    = vs["mode"]
    advice  = vs["advice"]

    # Hard block zones: Friday close and Monday gap
    if vs["is_friday_close"] or vs["is_monday_gap"]:
        return "BLOCK"

    tf_up = (tf_label or "").strip().upper()
    is_fast = tf_up.startswith(("1 MIN", "2 MIN", "1MIN", "2MIN", "3 MIN", "3MIN"))

    # Extreme volatility: only momentum plays, only on fast TFs
    if mode == "extreme":
        if not is_fast:
            return "BLOCK"   # slow TF entries during extreme vol = stop hunt fodder
        mom = get_momentum_direction(pair)
        if mom is None:
            return "BLOCK"
        return "ALLOW" if mom == direction else "BLOCK"

    # High volatility + news: check momentum alignment
    if advice == "MOMENTUM_ONLY":
        mom = get_momentum_direction(pair)
        if mom is None:
            return "BLOCK"
        if mom != direction:
            return "BLOCK"
        return "ALLOW"

    # High volatility without hard block: allow but flag for wider SL
    if mode == "high":
        return "MOMENTUM_ONLY"

    return "ALLOW"


# ═══════════════════════════════════════════════════════════════════════════
#  FOREX SL MULTIPLIER
# ═══════════════════════════════════════════════════════════════════════════

def forex_sl_multiplier(pair: str) -> float:
    """Return the factor to multiply the base SL distance by.

    Normal:  1.0× — unchanged
    High:    1.35× — small buffer for vol expansion
    Extreme: 1.70× — price needs room to breathe
    Friday:  1.50× — stop hunts before weekly close

    SL is NEVER narrowed (multiplier never < 1.0).
    """
    vs = get_volatility_state(pair)
    if vs["is_friday_close"] or vs["is_monday_gap"]:
        return 1.50
    mode = vs["mode"]
    if mode == "extreme":
        return 1.70
    if mode == "high":
        return 1.35
    if mode == "low":
        return 1.10   # slight buffer even on "quiet" days
    return 1.0


# ═══════════════════════════════════════════════════════════════════════════
#  FOREX TP MULTIPLIER  (wider SL = higher TPs to preserve R:R)
# ═══════════════════════════════════════════════════════════════════════════

def forex_tp_multiplier(pair: str) -> float:
    """Scale TP proportionally when SL is widened to maintain R:R."""
    return forex_sl_multiplier(pair)


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def volatility_label(mode: str) -> str:
    """Short emoji label for the signal card."""
    return {
        "extreme": "🌋 EXTREME VOL",
        "high":    "⚡ HIGH VOL",
        "normal":  "✅ NORMAL",
        "low":     "😴 LOW VOL",
    }.get(mode, "✅ NORMAL")


def volatility_card_line(pair: str) -> str:
    """One line suitable for inserting into the signal card text.

    Returns empty string during normal conditions (no clutter).
    """
    try:
        vs = get_volatility_state(pair)
        mode = vs["mode"]
        if mode == "normal":
            return ""
        label = volatility_label(mode)
        atr_x = vs["atr_ratio"]
        news_flag = " 📰 NEWS WINDOW" if vs["is_news"] else ""
        fri_flag  = " 📅 FRIDAY CLOSE" if vs["is_friday_close"] else ""
        return f"⚠️ {label} · ATR×{atr_x:.1f}{news_flag}{fri_flag}\n"
    except Exception:
        return ""
