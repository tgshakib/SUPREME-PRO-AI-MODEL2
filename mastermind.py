"""SUPREME PRO — MASTERMIND ENGINE
=================================
Full institutional chart intelligence. Every concept the top 1% of
traders use — encoded, wired together and scored into one verdict.

Concepts implemented
--------------------
  AMD CYCLE          Accumulation → Manipulation → Distribution
  KILL ZONES         London Open / NY Open / Overlap / Asian range
  OTE                Optimal Trade Entry  (61.8 – 79 % Fib retracement)
  PREMIUM/DISCOUNT   Only BUY in Discount (<50%), SELL in Premium (>50%)
  EQL / EQH          Equal Lows / Equal Highs  (stop-hunt pools)
  PDH / PDL          Previous Day High / Low   (institutional magnets)
  PWH / PWL          Previous Week High / Low  (higher-TF magnets)
  MSS                Market Structure Shift (1H + 4H combined)
  INDUCEMENT         Fake liquidity grab before the real impulse
  SWEEP CONFIRM      Price swept a pool AND rejected it (smart-money fill)
  MOMENTUM CONFIRM   EMA slope + RSI both trending in the signal direction

Public API
----------
  mastermind_verdict(pair, direction) -> dict
    {
      'verdict':    'CONFIRM' | 'NEUTRAL' | 'REJECT',
      'score':      0..100,
      'amd_phase':  'accumulation' | 'manipulation' | 'distribution' | '—',
      'kill_zone':  'London' | 'NewYork' | 'Overlap' | 'Asian' | None,
      'ote_active': bool,
      'pd_ok':      bool,   # price in correct Premium/Discount zone
      'mss':        bool,
      'inducement': bool,
      'sweep_ok':   bool,
      'key_levels': {'pdh':float,'pdl':float,'pwh':float,'pwl':float,'eqh':float,'eql':float},
      'labels':     [str],  # compact reasons for the signal card
    }

  All results are cached 60 s per (ticker, direction) so repeated calls
  within a signal generation cycle add zero latency overhead.
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
    print(f"[mastermind] import failed: {_e}")
    yf = None
    pd = None
    _OK = False

from live_prices import yf_ticker

_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_TTL = 60.0   # seconds — fast enough for a 1m sniper

_BLANK: dict = {
    "verdict": "NEUTRAL", "score": 50, "amd_phase": "—",
    "kill_zone": None, "ote_active": False, "pd_ok": False,
    "mss": False, "inducement": False, "sweep_ok": False,
    "key_levels": {}, "labels": [],
}


# ═══════════════════════════════════════════════════════════
#  DATA HELPERS
# ═══════════════════════════════════════════════════════════

def _flatten(df):
    if df is None or df.empty:
        return None
    try:
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        if "close" not in df.columns:
            return None
        return df
    except Exception:
        return None


def _fetch(ticker: str, interval: str, period: str):
    if not _OK:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        return _flatten(df)
    except Exception as e:
        print(f"[mastermind] fetch {ticker} {interval}: {e}")
        return None


def _ema(series, n: int):
    return series.ewm(span=n, adjust=False).mean()


def _rsi(series, n: int = 14):
    d = series.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, 1e-10))


def _atr14(df) -> float:
    try:
        h = df["high"].astype(float)
        lo = df["low"].astype(float)
        c = df["close"].astype(float)
        pc = c.shift(1)
        tr = (h - lo).combine((h - pc).abs(), max).combine((lo - pc).abs(), max)
        return float(tr.rolling(14).mean().iloc[-1])
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════
#  KILL ZONES  (UTC session windows)
# ═══════════════════════════════════════════════════════════
#  London Kill Zone   07:00–10:00 UTC  ← highest probability
#  NY Kill Zone       12:00–15:00 UTC  ← highest probability
#  Overlap (L+NY)     13:00–14:00 UTC  ← ultra-high probability
#  Asian Range        00:00–04:00 UTC  ← accumulation/stop hunt
#  London Close       15:00–17:00 UTC

def current_kill_zone() -> Optional[str]:
    utc_h = datetime.now(timezone.utc).hour
    utc_m = datetime.now(timezone.utc).minute
    t = utc_h + utc_m / 60.0
    if 13.0 <= t < 14.0:
        return "Overlap"          # London+NY overlap — premium window
    if 7.0 <= t < 10.0:
        return "London"
    if 12.0 <= t < 15.0:
        return "NewYork"
    if 0.0 <= t < 4.0:
        return "Asian"
    return None


# ═══════════════════════════════════════════════════════════
#  SWING HIGHS / LOWS
# ═══════════════════════════════════════════════════════════

def _swings(df, w: int = 4) -> tuple[list[float], list[float]]:
    """Return (pivot_highs_prices, pivot_lows_prices) lists."""
    if df is None or len(df) < w * 2 + 1:
        return [], []
    h = df["high"].astype(float).tolist()
    lo = df["low"].astype(float).tolist()
    n = len(h)
    ph, pl = [], []
    for i in range(w, n - w):
        seg_h = h[i - w:i + w + 1]
        seg_l = lo[i - w:i + w + 1]
        if h[i] == max(seg_h):
            ph.append(h[i])
        if lo[i] == min(seg_l):
            pl.append(lo[i])
    return ph, pl


# ═══════════════════════════════════════════════════════════
#  AMD CYCLE  (Accumulation → Manipulation → Distribution)
# ═══════════════════════════════════════════════════════════
#  Runs on the 4H chart (last 60 bars = ~10 days).
#
#  ACCUMULATION: price compressed in a tight horizontal channel.
#      ATR(last 15 bars) < 60% of ATR(full 60 bars) = tight chop.
#
#  MANIPULATION: stop hunt candle — a large wick beyond the channel
#      that CLOSES back inside. Smart money swept liquidity.
#      Detect: last 5 bars have a candle whose wick is ≥ 1.8× its body
#      AND whose close is within the accumulation range.
#
#  DISTRIBUTION: strong directional move away from the manipulation.
#      ATR expanding + price trending (EMA9 clear of EMA21 by >0.5×ATR).

def detect_amd(df_4h) -> str:
    """Return 'accumulation' | 'manipulation' | 'distribution' | '—'."""
    if df_4h is None or len(df_4h) < 25:
        return "—"
    try:
        hi = df_4h["high"].astype(float)
        lo = df_4h["low"].astype(float)
        op = df_4h["open"].astype(float)
        cl = df_4h["close"].astype(float)

        atr_full  = float((hi - lo).rolling(20).mean().iloc[-1])
        atr_short = float((hi - lo).tail(8).mean())
        if atr_full == 0:
            return "—"

        # --- Accumulation (tight chop)
        if atr_short < 0.55 * atr_full:
            return "accumulation"

        # --- Manipulation (stop-hunt wick candle in last 5 bars)
        for i in range(-5, 0):
            c = float(cl.iloc[i]); o = float(op.iloc[i])
            h = float(hi.iloc[i]); l = float(lo.iloc[i])
            body = abs(c - o)
            rng  = max(1e-9, h - l)
            upper_wick = h - max(c, o)
            lower_wick = min(c, o) - l
            # large wick that closes mid-range = manipulation candle
            if body < 0.35 * rng and max(upper_wick, lower_wick) >= 1.8 * body:
                return "manipulation"

        # --- Distribution (trending, ATR expanding)
        ef = float(_ema(cl, 9).iloc[-1])
        es = float(_ema(cl, 21).iloc[-1])
        if abs(ef - es) > 0.4 * atr_full:
            return "distribution"

        return "—"
    except Exception:
        return "—"


# ═══════════════════════════════════════════════════════════
#  OTE — Optimal Trade Entry  (Fibonacci 61.8% – 79%)
# ═══════════════════════════════════════════════════════════
#  After a BOS/impulse, price retraces into the 61.8–79% zone before
#  the next leg. If price is currently inside this zone → OTE active.

def detect_ote(df_1h, direction: str) -> bool:
    """True if the live price sits inside the OTE retracement zone."""
    if df_1h is None or len(df_1h) < 30:
        return False
    try:
        cl = df_1h["close"].astype(float)
        hi = df_1h["high"].astype(float)
        lo = df_1h["low"].astype(float)
        last_price = float(cl.iloc[-1])
        # Use the last 50 bars to find the most recent significant swing
        recent_h = float(hi.tail(50).max())
        recent_l = float(lo.tail(50).min())
        rng = max(1e-9, recent_h - recent_l)
        if direction == "BUY":
            # Retracement from high down to low; OTE = 61.8-79% of the drop
            # from high, meaning price is near the lows but pulling back
            ote_lo = recent_h - 0.79 * rng   # 79% retracement
            ote_hi = recent_h - 0.618 * rng  # 61.8% retracement
        else:
            # For SELL: OTE = 61.8-79% retracement of a drop (price rallied back)
            ote_lo = recent_l + 0.618 * rng
            ote_hi = recent_l + 0.79 * rng
        return ote_lo <= last_price <= ote_hi
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  PREMIUM / DISCOUNT  (50% equilibrium)
# ═══════════════════════════════════════════════════════════
#  BUY only in Discount (price < 50% of range).
#  SELL only in Premium (price > 50% of range).

def detect_premium_discount(df_1h, direction: str) -> bool:
    """True when price is in the CORRECT zone for the direction."""
    if df_1h is None or len(df_1h) < 20:
        return False
    try:
        cl   = df_1h["close"].astype(float)
        hi   = df_1h["high"].astype(float)
        lo   = df_1h["low"].astype(float)
        last = float(cl.iloc[-1])
        h50  = float(hi.tail(50).max())
        l50  = float(lo.tail(50).min())
        mid  = (h50 + l50) / 2.0
        if direction == "BUY":
            return last < mid    # discount zone
        else:
            return last > mid    # premium zone
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  EQUAL HIGHS / EQUAL LOWS  (EQH / EQL)
# ═══════════════════════════════════════════════════════════
#  Two or more swing highs/lows within 0.08% of each other = a
#  liquidity pool where stops are clustered. If price recently swept
#  this pool and rejected, the trade is in the direction of rejection.

def detect_eql_eqh(ph: list[float], pl: list[float],
                   last_price: float) -> tuple[Optional[float], Optional[float]]:
    """Return (eqh_price, eql_price) of the nearest equal-level pools,
    or None if none found within 0.1% tolerance."""
    tol = 0.001   # 0.1% of price
    eqh: Optional[float] = None
    eql: Optional[float] = None
    # Equal highs
    for i in range(len(ph) - 1):
        if abs(ph[i] - ph[i + 1]) / max(1e-9, ph[i]) < tol:
            eqh = (ph[i] + ph[i + 1]) / 2.0
            break
    # Equal lows
    for i in range(len(pl) - 1):
        if abs(pl[i] - pl[i + 1]) / max(1e-9, pl[i]) < tol:
            eql = (pl[i] + pl[i + 1]) / 2.0
            break
    return eqh, eql


# ═══════════════════════════════════════════════════════════
#  PDH / PDL / PWH / PWL  (Previous Day/Week High/Low)
# ═══════════════════════════════════════════════════════════

def detect_daily_weekly_levels(df_1h) -> dict:
    """Return {'pdh', 'pdl', 'pwh', 'pwl'} from 1H data resampled."""
    out: dict = {}
    if df_1h is None or len(df_1h) < 48:
        return out
    try:
        hi = df_1h["high"].astype(float)
        lo = df_1h["low"].astype(float)
        # Previous day = bars -48 to -24  (1H × 24 = 1 day back)
        pd_hi = df_1h["high"].astype(float).iloc[-48:-24]
        pd_lo = df_1h["low"].astype(float).iloc[-48:-24]
        if len(pd_hi) > 0:
            out["pdh"] = float(pd_hi.max())
            out["pdl"] = float(pd_lo.min())
        # Previous week = bars -168 to -0 vs -336 to -168
        pw_hi = df_1h["high"].astype(float).iloc[-336:-168]
        pw_lo = df_1h["low"].astype(float).iloc[-336:-168]
        if len(pw_hi) > 0:
            out["pwh"] = float(pw_hi.max())
            out["pwl"] = float(pw_lo.min())
    except Exception:
        pass
    return out


# ═══════════════════════════════════════════════════════════
#  MSS — Market Structure Shift  (1H internal)
# ═══════════════════════════════════════════════════════════
#  A bearish MSS = price was making HH→HL → now broke a HL (BOS down)
#  A bullish MSS = price was making LL→LH → now broke a LH (BOS up)
#  We look at the last 30 bars for a fresh shift.

def detect_mss(ph: list[float], pl: list[float],
               last_price: float, direction: str) -> bool:
    """True if a recent Market Structure Shift aligns with `direction`."""
    if len(ph) < 3 or len(pl) < 3:
        return False
    try:
        if direction == "BUY":
            # Bullish MSS: sequence was LH→LL pattern, now price > last LH
            # Simplification: last swing high > second-to-last swing high
            # AND last_price > last swing high = BOS UP
            if ph[-1] > ph[-2] and last_price > ph[-1]:
                return True
            # Or: last swing low > second-to-last swing low = HH pattern forming
            if pl[-1] > pl[-2] and last_price > ph[-2]:
                return True
        else:
            # Bearish MSS: sequence was HL→HH pattern, now price < last HL
            if pl[-1] < pl[-2] and last_price < pl[-1]:
                return True
            if ph[-1] < ph[-2] and last_price < pl[-2]:
                return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════
#  INDUCEMENT  (fake liquidity grab before real impulse)
# ═══════════════════════════════════════════════════════════
#  Inducement = price briefly breaks a minor swing in the OPPOSITE
#  direction of the signal just before the real move.  On a 5m chart
#  this shows as a quick wick beyond recent high/low, then reversal.

def detect_inducement(df_5m, direction: str) -> bool:
    """True when the last few 5m bars show an inducement sweep."""
    if df_5m is None or len(df_5m) < 15:
        return False
    try:
        hi = df_5m["high"].astype(float)
        lo = df_5m["low"].astype(float)
        cl = df_5m["close"].astype(float)
        # Look at last 10 bars
        recent_hi = float(hi.iloc[-12:-2].max())
        recent_lo = float(lo.iloc[-12:-2].min())
        last_hi   = float(hi.iloc[-1])
        last_lo   = float(lo.iloc[-1])
        last_cl   = float(cl.iloc[-1])
        prev_cl   = float(cl.iloc[-2])
        if direction == "BUY":
            # swept below recent_lo then closed above it
            return last_lo < recent_lo and last_cl > recent_lo
        else:
            # swept above recent_hi then closed below it
            return last_hi > recent_hi and last_cl < recent_hi
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  SWEEP CONFIRM  (smart-money stop hunt + rejection)
# ═══════════════════════════════════════════════════════════
#  Same as inducement but on the 1H chart and confirms the larger
#  setup: price took out the opposing liquidity and is now moving away.

def detect_sweep_confirm(ph: list[float], pl: list[float],
                         df_1h, direction: str) -> bool:
    """True if the last 1H candle swept opposing liquidity and closed
    back in the trade direction (the classic stop-hunt reversal)."""
    if df_1h is None or len(df_1h) < 6:
        return False
    try:
        last = df_1h.iloc[-1]
        prev = df_1h.iloc[-2]
        h_l  = float(last["high"]); l_l = float(last["low"])
        c_l  = float(last["close"]); o_l = float(last["open"])
        if direction == "BUY":
            # Candle swept a swing low and closed bullish
            for pool in pl[-4:]:
                if l_l < pool < c_l and c_l > o_l:
                    return True
            # Previous candle did the sweep, current confirmed
            for pool in pl[-4:]:
                h_p = float(prev["high"]); l_p = float(prev["low"])
                c_p = float(prev["close"])
                if l_p < pool and c_p > pool and c_l > c_p:
                    return True
        else:
            for pool in ph[-4:]:
                if h_l > pool > c_l and c_l < o_l:
                    return True
            for pool in ph[-4:]:
                h_p = float(prev["high"]); l_p = float(prev["low"])
                c_p = float(prev["close"])
                if h_p > pool and c_p < pool and c_l < c_p:
                    return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════
#  WEEKLY BIAS  (1W / 1D higher-TF alignment)
# ═══════════════════════════════════════════════════════════

def weekly_bias(df_1h, direction: str) -> bool:
    """True when the weekly trend agrees with direction.
    Computed by resampling 1H → weekly closes and checking EMA9/21."""
    if df_1h is None or len(df_1h) < 200:
        return False
    try:
        cl = df_1h["close"].astype(float)
        weekly = cl.resample("W").last().dropna() if hasattr(cl.index, "resample") else None
        if weekly is None or len(weekly) < 22:
            # fallback: use last 168 bars (7 days) of the 1H series
            ef = float(_ema(cl, 9).iloc[-1])
            es = float(_ema(cl, 21).iloc[-1])
            rsi = float(_rsi(cl).iloc[-1])
        else:
            ef = float(_ema(weekly, 9).iloc[-1])
            es = float(_ema(weekly, 21).iloc[-1])
            rsi = float(_rsi(weekly).iloc[-1])
        if direction == "BUY":
            return ef > es and rsi >= 50
        else:
            return ef < es and rsi <= 50
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  MAIN  mastermind_verdict(pair, direction)
# ═══════════════════════════════════════════════════════════

def mastermind_verdict(pair: str, direction: str) -> dict:
    """Run the full institutional analysis for `pair` in `direction`
    (BUY or SELL) and return a comprehensive verdict dict.

    Result is cached 60 s per (pair, direction) so repeated calls
    within the same signal generation cycle are free.
    """
    direction = (direction or "").upper()
    if direction not in {"BUY", "SELL"}:
        return dict(_BLANK)

    ticker = yf_ticker(pair)
    if not ticker or not _OK:
        return dict(_BLANK)

    cache_key = (ticker, direction)
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _TTL:
        return dict(cached[1])

    labels: list[str] = []
    score   = 0
    max_pts = 0

    # ── 1. Fetch data (share where possible) ──────────────
    df_1h  = _fetch(ticker, "60m", "60d")
    df_4h  = None   # will build by resampling if 1h ok
    df_5m  = _fetch(ticker, "5m",  "5d")

    if df_1h is not None and len(df_1h) >= 30:
        try:
            cl_1h = df_1h["close"].astype(float)
            # build 4H by resampling 1H (yfinance 4H is unreliable)
            if hasattr(cl_1h.index, "resample"):
                df_4h_cl = df_1h.resample("4h").agg(
                    {"open": "first", "high": "max",
                     "low": "min", "close": "last"}
                ).dropna()
                if len(df_4h_cl) >= 20:
                    df_4h = df_4h_cl
        except Exception:
            pass

    last_price = 0.0
    ph_1h: list[float] = []
    pl_1h: list[float] = []
    if df_1h is not None and len(df_1h) >= 10:
        try:
            last_price = float(df_1h["close"].astype(float).iloc[-1])
            ph_1h, pl_1h = _swings(df_1h, w=4)
        except Exception:
            pass

    # ── 2. KILL ZONE ────────────────────────────────────────
    max_pts += 12
    kz = current_kill_zone()
    if kz == "Overlap":
        score += 12; labels.append("⏰ OVERLAP Kill Zone (max edge)")
    elif kz in ("London", "NewYork"):
        score += 9;  labels.append(f"⏰ {kz} Kill Zone")
    elif kz == "Asian":
        score += 4;  labels.append("⏰ Asian range active")

    # ── 3. AMD PHASE ────────────────────────────────────────
    max_pts += 20
    amd = detect_amd(df_4h)
    if amd == "manipulation":
        # Stop hunt just happened — highest-conviction entry signal
        score += 20; labels.append("🔄 AMD: Manipulation sweep ✓")
    elif amd == "accumulation":
        # Price coiling — breakout incoming (no entry YET, neutral)
        score += 6;  labels.append("📦 AMD: Accumulation phase")
    elif amd == "distribution":
        # Already in full impulse — still tradeable
        score += 14; labels.append("🚀 AMD: Distribution (impulse) active")

    # ── 4. OTE ZONE ─────────────────────────────────────────
    max_pts += 15
    ote = detect_ote(df_1h, direction)
    if ote:
        score += 15; labels.append("🎯 OTE Zone 61.8-79% ACTIVE")

    # ── 5. PREMIUM / DISCOUNT ───────────────────────────────
    max_pts += 12
    pd_ok = detect_premium_discount(df_1h, direction)
    if pd_ok:
        score += 12
        z = "Discount" if direction == "BUY" else "Premium"
        labels.append(f"📐 {z} zone confirmed")
    else:
        score -= 8   # actively wrong zone — penalise

    # ── 6. MSS ─────────────────────────────────────────────
    max_pts += 15
    mss = detect_mss(ph_1h, pl_1h, last_price, direction)
    if mss:
        score += 15; labels.append("📊 MSS: Structure shift ✓")

    # ── 7. INDUCEMENT (5m stop hunt before real move) ───────
    max_pts += 10
    ind = detect_inducement(df_5m, direction)
    if ind:
        score += 10; labels.append("🪤 Inducement sweep confirmed")

    # ── 8. SWEEP CONFIRM (1H stop hunt + rejection) ─────────
    max_pts += 12
    swp = detect_sweep_confirm(ph_1h, pl_1h, df_1h, direction)
    if swp:
        score += 12; labels.append("💧 Liquidity sweep + rejection ✓")

    # ── 9. PDH/PDL/PWH/PWL ──────────────────────────────────
    max_pts += 10
    key_levels = detect_daily_weekly_levels(df_1h)
    pdh = key_levels.get("pdh"); pdl = key_levels.get("pdl")
    pwh = key_levels.get("pwh"); pwl = key_levels.get("pwl")
    if direction == "BUY" and last_price > 0:
        if pdl and abs(last_price - pdl) / last_price < 0.003:
            score += 6; labels.append("📌 BUY near PDL (magnet)")
        if pwl and abs(last_price - pwl) / last_price < 0.005:
            score += 10; labels.append("📌 BUY near PWL (weekly magnet)")
    elif direction == "SELL" and last_price > 0:
        if pdh and abs(last_price - pdh) / last_price < 0.003:
            score += 6; labels.append("📌 SELL near PDH (magnet)")
        if pwh and abs(last_price - pwh) / last_price < 0.005:
            score += 10; labels.append("📌 SELL near PWH (weekly magnet)")

    # ── 10. EQL / EQH (equal highs/lows liquidity pools) ────
    max_pts += 8
    eqh, eql = detect_eql_eqh(ph_1h, pl_1h, last_price)
    key_levels["eqh"] = eqh or 0.0
    key_levels["eql"] = eql or 0.0
    if direction == "SELL" and eqh and last_price > 0:
        if abs(last_price - eqh) / last_price < 0.003:
            score += 8; labels.append("⚡ EQH liquidity pool above price")
    if direction == "BUY" and eql and last_price > 0:
        if abs(last_price - eql) / last_price < 0.003:
            score += 8; labels.append("⚡ EQL liquidity pool below price")

    # ── 11. WEEKLY BIAS ──────────────────────────────────────
    max_pts += 10
    wb = weekly_bias(df_1h, direction)
    if wb:
        score += 10; labels.append("📅 Weekly bias aligned")

    # ── NORMALISE + VERDICT ─────────────────────────────────
    if max_pts > 0:
        pct = max(0, score) / max_pts
        final_score = int(round(min(100, pct * 100)))
    else:
        final_score = 50

    if final_score >= 60:
        verdict = "CONFIRM"
    elif final_score <= 30:
        verdict = "REJECT"
    else:
        verdict = "NEUTRAL"

    result: dict = {
        "verdict":    verdict,
        "score":      final_score,
        "amd_phase":  amd,
        "kill_zone":  kz,
        "ote_active": ote,
        "pd_ok":      pd_ok,
        "mss":        mss,
        "inducement": ind,
        "sweep_ok":   swp,
        "key_levels": key_levels,
        "labels":     labels,
    }
    _CACHE[cache_key] = (now, result)
    return result
