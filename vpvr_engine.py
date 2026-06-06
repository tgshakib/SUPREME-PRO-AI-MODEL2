"""VPVR Session-Break Strategy Engine — SUPREME PRO AI BOT
===========================================================
Implements the TradingView Volume Profile Visible Range (VPVR) strategy
anchored to the daily session break at 16:30 UTC.

STRATEGY CORE PRINCIPLE
-----------------------
After the 4:30 PM session break (16:30 UTC / NY pre-close zone):
  • VAH  (Value Area High)  → institutional SELL zone — smart money
    distributes into retail BUY orders above value area
  • VAL  (Value Area Low)   → institutional BUY zone  — smart money
    accumulates against retail SELL pressure below value area
  • POC  (Point of Control) → highest-volume price node — acts as
    magnet; price tends to revisit and react at POC

VOLUME PROFILE COMPUTATION
--------------------------
  1. Pull previous day's 1-minute OHLCV from yfinance
  2. Build 50-bucket price histogram spanning [day_low, day_high]
  3. Each bar contributes its volume to the bucket containing its VWAP
  4. For pairs with no real volume (most forex) → use tick-volume proxy
     (number of 1m bars closing at each bucket = price-density weight)
  5. POC  = bucket with maximum accumulated weight
  6. Value Area = expand from POC in both directions until 70% of total
     volume is captured
  7. VAH  = top of value area
  7. VAL  = bottom of value area

CONFIRMATIONS (each adds points toward 0-100 score)
-----------------------------------------------------
  C1  Time gate          — after session break (16:30 UTC)   → 15 pts
  C2  Proximity          — price within 0.30% of VAH/VAL      → 25 pts
  C3  RSI alignment      — RSI(14) matches direction           → 15 pts
  C4  EMA50 alignment    — 1H EMA50 matches direction          → 15 pts
  C5  Candle conviction  — body direction matches trade dir     → 10 pts
  C6  Wick rejection     — tail pointing away from level        → 10 pts
  C7  Volume cluster     — VAH/VAL bucket is high-volume node   → 10 pts
Max = 100; threshold to cast a vote = 55

CONTRACT
--------
  • Zero side-effects — never modifies signal text, keyboards, handlers
  • Returns a structured dict — callers read it as a silent vote
  • All errors caught internally — always safe to import and call

Public API
----------
  vpvr_levels(pair)      → dict {vah, val, poc, day_range, source} | None
  vpvr_session_vote(pair, direction) → dict {vote, score, reason, active}
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

# ── Optional deps ──────────────────────────────────────────────────────────
try:
    import yfinance as _yf
    import pandas as _pd
    _YF_OK = True
except Exception:
    _yf = None      # type: ignore
    _pd = None      # type: ignore
    _YF_OK = False

from live_prices import yf_ticker

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

VALUE_AREA_PCT   = 0.70   # 70% of volume captured in value area
N_BUCKETS        = 50     # price histogram resolution
SESSION_BREAK_H  = 16     # 16:30 UTC  (London close / NY pre-close)
SESSION_BREAK_M  = 30
VOTE_THRESHOLD   = 55     # minimum score to cast a directional vote
PROXIMITY_TIGHT  = 0.003  # 0.30% — forex majors
PROXIMITY_WIDE   = 0.006  # 0.60% — Gold / crypto / indices

# Pairs that use actual volume (not tick-proxy)
_REAL_VOLUME_PAIRS = {
    "XAU/USD", "GOLD", "XAG/USD", "SILVER", "USOIL",
    "BTC/USD", "ETH/USD", "BNB/USD", "SOL/USD", "XRP/USD",
    "BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "NAS100", "US100", "DJ30", "SP500",
}

# ── Per-pair cache: (timestamp, levels_dict) ──────────────────────────────
_LEVELS_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_LEVELS_TTL = 600.0   # 10 min — daily levels don't change fast

# ── Vote cache: (timestamp, vote_dict) ────────────────────────────────────
_VOTE_CACHE: dict[str, tuple[float, dict]] = {}
_VOTE_TTL = 45.0


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _clean_pair(pair: str) -> str:
    p = pair.upper()
    for sfx in (" 〔OTC〕", "(OTC)", " (OTC)", " OTC", "〔OTC〕"):
        p = p.replace(sfx, "")
    return p.strip()


def _use_real_volume(pair: str) -> bool:
    return _clean_pair(pair) in _REAL_VOLUME_PAIRS


def _proximity_threshold(pair: str) -> float:
    p = _clean_pair(pair)
    if any(x in p for x in ("XAU", "GOLD", "BTC", "ETH", "NAS", "DJ", "SP500", "USOIL")):
        return PROXIMITY_WIDE
    return PROXIMITY_TIGHT


def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series, period: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    return float((100 - (100 / (1 + rs))).iloc[-1])


def _flatten_cols(df) -> None:
    """Flatten MultiIndex columns from yfinance download() in place."""
    if hasattr(df.columns, "get_level_values"):
        df.columns = [
            str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
            for c in df.columns
        ]
    else:
        df.columns = [str(c).lower() for c in df.columns]


def _after_session_break() -> bool:
    """True if current UTC time is at or after SESSION_BREAK_H:SESSION_BREAK_M."""
    now = datetime.now(timezone.utc)
    return (now.hour, now.minute) >= (SESSION_BREAK_H, SESSION_BREAK_M)


def _minutes_past_break() -> int:
    """Minutes elapsed since the last session break (wraps at 24h)."""
    now = datetime.now(timezone.utc)
    break_today = now.replace(
        hour=SESSION_BREAK_H, minute=SESSION_BREAK_M, second=0, microsecond=0
    )
    if now < break_today:
        # Before today's break — measure from yesterday's break
        delta = now - (break_today.replace(day=now.day - 1)
                       if now.day > 1 else break_today)
    else:
        delta = now - break_today
    return int(delta.total_seconds() / 60)


# ═══════════════════════════════════════════════════════════════════════════
# Volume Profile Calculator
# ═══════════════════════════════════════════════════════════════════════════

def _compute_vp(df_1m) -> Optional[dict]:
    """Compute VAH, VAL, POC from a 1-minute OHLCV DataFrame.

    Works for both real-volume pairs (uses 'volume' column) and
    tick-proxy pairs (uses bar count at each price bucket).
    """
    if df_1m is None or len(df_1m) < 20:
        return None
    try:
        _flatten_cols(df_1m)
        hi  = df_1m["high"].astype(float).squeeze()
        lo  = df_1m["low"].astype(float).squeeze()
        cl  = df_1m["close"].astype(float).squeeze()
        op  = df_1m["open"].astype(float).squeeze()

        day_hi = float(hi.max())
        day_lo = float(lo.min())
        if day_hi <= day_lo:
            return None

        bucket_size = (day_hi - day_lo) / N_BUCKETS
        hist = [0.0] * N_BUCKETS

        has_vol = "volume" in df_1m.columns
        if has_vol:
            vol = df_1m["volume"].astype(float).squeeze()
        else:
            vol = None

        for i in range(len(cl)):
            # VWAP of bar = (H+L+C) / 3
            vwap = (float(hi.iloc[i]) + float(lo.iloc[i]) + float(cl.iloc[i])) / 3.0
            idx  = int((vwap - day_lo) / bucket_size)
            idx  = max(0, min(N_BUCKETS - 1, idx))
            weight = float(vol.iloc[i]) if (has_vol and vol is not None) else 1.0
            if weight <= 0:
                weight = 1.0
            hist[idx] += weight

        total = sum(hist)
        if total <= 0:
            return None

        # POC = bucket index with max volume
        poc_idx = hist.index(max(hist))
        poc     = day_lo + (poc_idx + 0.5) * bucket_size

        # Expand value area from POC until 70% captured
        va_vol  = hist[poc_idx]
        lo_idx  = poc_idx
        hi_idx  = poc_idx
        target  = total * VALUE_AREA_PCT

        while va_vol < target:
            # Choose which side to expand (pick the higher-volume adjacent bucket)
            next_lo = lo_idx - 1
            next_hi = hi_idx + 1
            expand_lo = hist[next_lo] if next_lo >= 0 else -1
            expand_hi = hist[next_hi] if next_hi < N_BUCKETS else -1
            if expand_lo <= 0 and expand_hi <= 0:
                break
            if expand_lo >= expand_hi:
                lo_idx  = next_lo
                va_vol += hist[lo_idx]
            else:
                hi_idx  = next_hi
                va_vol += hist[hi_idx]

        vah = day_lo + (hi_idx + 1) * bucket_size
        val = day_lo + lo_idx * bucket_size

        # Bucket volume at VAH and VAL (for C7 confirmation)
        vah_vol_pct = hist[hi_idx] / total if total > 0 else 0
        val_vol_pct = hist[lo_idx] / total if total > 0 else 0

        return {
            "vah":         round(vah, 6),
            "val":         round(val, 6),
            "poc":         round(poc, 6),
            "day_high":    round(day_hi, 6),
            "day_low":     round(day_lo, 6),
            "day_range":   round(day_hi - day_lo, 6),
            "vah_vol_pct": round(vah_vol_pct, 4),
            "val_vol_pct": round(val_vol_pct, 4),
            "n_bars":      len(cl),
        }
    except Exception as e:
        print(f"[vpvr] compute_vp error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Public API — levels
# ═══════════════════════════════════════════════════════════════════════════

def vpvr_levels(pair: str) -> Optional[dict]:
    """Compute and cache VPVR levels (VAH, VAL, POC) for a pair.

    Fetches the most recent full day of 1-minute bars from yfinance,
    builds the volume profile, and returns the value area levels.
    Returns None if the pair is unsupported or data is unavailable.
    """
    if not _YF_OK:
        return None
    pair = _clean_pair(pair)
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    now = time.time()
    cached = _LEVELS_CACHE.get(ticker)
    if cached and (now - cached[0]) < _LEVELS_TTL:
        return cached[1]

    try:
        t  = _yf.Ticker(ticker)
        df = t.history(period="2d", interval="1m", auto_adjust=True)
        if df is None or len(df) < 30:
            _LEVELS_CACHE[ticker] = (now, None)
            return None

        # Isolate "yesterday" bars (the day before the most recent bar)
        try:
            dates = df.index.normalize().unique()
            if len(dates) >= 2:
                prev_day = dates[-2]
                df_prev  = df[df.index.normalize() == prev_day]
            else:
                df_prev = df
        except Exception:
            df_prev = df

        result = _compute_vp(df_prev)
        if result:
            result["pair"]   = pair
            result["ticker"] = ticker
            result["source"] = "yfinance_1m"
        _LEVELS_CACHE[ticker] = (now, result)
        return result
    except Exception as e:
        print(f"[vpvr] levels fetch error {pair}: {e}")
        _LEVELS_CACHE[ticker] = (now, None)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Public API — session vote
# ═══════════════════════════════════════════════════════════════════════════

def vpvr_session_vote(pair: str, direction: str) -> dict:
    """Score the proposed trade direction against the VPVR session-break setup.

    Returns
    -------
    {
      "vote":    +1 (agree) | -1 (disagree) | 0 (no signal),
      "score":   int 0-100,
      "reason":  str  (human-readable, for debug logs only),
      "active":  bool  (True = meaningful VPVR signal is present),
      "vah":     float | None,
      "val":     float | None,
      "poc":     float | None,
    }
    """
    pair = _clean_pair(pair)
    direction = direction.upper()
    cache_key = f"{pair}|{direction}"
    now = time.time()

    cached = _VOTE_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _VOTE_TTL:
        return cached[1]

    _null = {"vote": 0, "score": 0, "reason": "no_data",
             "active": False, "vah": None, "val": None, "poc": None}

    # ── Get VPVR levels ────────────────────────────────────────────────
    levels = vpvr_levels(pair)
    if not levels:
        _VOTE_CACHE[cache_key] = (now, _null)
        return _null

    vah = levels["vah"]
    val = levels["val"]
    poc = levels["poc"]

    # ── Get current live price ─────────────────────────────────────────
    try:
        from live_prices import get_live_price as _glp
        live_price = _glp(pair)
    except Exception:
        live_price = None

    if not live_price or live_price <= 0:
        # fallback: use yfinance last tick
        try:
            ticker = yf_ticker(pair)
            t = _yf.Ticker(ticker)
            info = t.fast_info
            live_price = float(getattr(info, "last_price", 0) or 0)
        except Exception:
            pass

    if not live_price or live_price <= 0:
        _VOTE_CACHE[cache_key] = (now, _null)
        return _null

    # ── Get 1H OHLCV for indicator confirmations ───────────────────────
    df_1h  = None
    rsi_1h = 50.0
    ema50  = live_price
    last_close = live_price
    last_open  = live_price
    last_high  = live_price
    last_low   = live_price

    try:
        ticker = yf_ticker(pair)
        t = _yf.Ticker(ticker)
        df_1h = t.history(period="30d", interval="1h", auto_adjust=True)
        if df_1h is not None and len(df_1h) >= 20:
            _flatten_cols(df_1h)
            cl  = df_1h["close"].astype(float).squeeze()
            ema50       = float(_ema(cl, 50).iloc[-1])
            rsi_1h      = _rsi(cl, 14)
            last_close  = float(cl.iloc[-1])
            last_open   = float(df_1h["open"].astype(float).squeeze().iloc[-1])
            last_high   = float(df_1h["high"].astype(float).squeeze().iloc[-1])
            last_low    = float(df_1h["low"].astype(float).squeeze().iloc[-1])
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════════
    # SCORING — 7 confirmations
    # ═══════════════════════════════════════════════════════════════════
    score  = 0
    parts  = []
    prox   = _proximity_threshold(pair)

    # C1 — Time gate (after 4:30 PM session break, within 4 hours)
    after_break  = _after_session_break()
    mins_elapsed = _minutes_past_break()
    if after_break and mins_elapsed <= 240:
        score += 15
        parts.append(f"C1:time+15({mins_elapsed}m past break)")
    elif after_break:
        score += 5   # still after break but stale (>4h)
        parts.append("C1:time+5(stale)")

    # C2 — Price proximity to VAH (SELL) or VAL (BUY)
    dist_vah = abs(live_price - vah) / max(live_price, 1e-9)
    dist_val = abs(live_price - val) / max(live_price, 1e-9)

    at_vah = dist_vah <= prox
    at_val = dist_val <= prox

    if direction == "SELL" and at_vah:
        score += 25
        parts.append(f"C2:at_VAH+25({dist_vah*100:.2f}%)")
    elif direction == "BUY" and at_val:
        score += 25
        parts.append(f"C2:at_VAL+25({dist_val*100:.2f}%)")
    elif direction == "SELL" and dist_vah <= prox * 2:
        score += 10
        parts.append(f"C2:near_VAH+10")
    elif direction == "BUY" and dist_val <= prox * 2:
        score += 10
        parts.append(f"C2:near_VAL+10")

    # C3 — RSI(14) 1H alignment
    if direction == "SELL" and rsi_1h >= 55:
        score += 15
        parts.append(f"C3:RSI_bear+15({rsi_1h:.1f})")
    elif direction == "SELL" and rsi_1h >= 50:
        score += 7
        parts.append(f"C3:RSI_neutral-sell+7")
    elif direction == "BUY" and rsi_1h <= 45:
        score += 15
        parts.append(f"C3:RSI_bull+15({rsi_1h:.1f})")
    elif direction == "BUY" and rsi_1h <= 50:
        score += 7
        parts.append(f"C3:RSI_neutral-buy+7")

    # C4 — EMA50 1H alignment
    if direction == "SELL" and last_close < ema50:
        score += 15
        parts.append("C4:EMA50_bear+15")
    elif direction == "SELL" and last_close > ema50:
        score += 5
        parts.append("C4:EMA50_bull-penalise+5")
    elif direction == "BUY" and last_close > ema50:
        score += 15
        parts.append("C4:EMA50_bull+15")
    elif direction == "BUY" and last_close < ema50:
        score += 5
        parts.append("C4:EMA50_bear-penalise+5")

    # C5 — Candle body conviction (last 1H bar body matches direction)
    bar_range = max(1e-9, last_high - last_low)
    body      = abs(last_close - last_open)
    body_pct  = body / bar_range
    if direction == "SELL" and last_close < last_open and body_pct >= 0.50:
        score += 10
        parts.append(f"C5:bear_body+10({body_pct*100:.0f}%)")
    elif direction == "BUY" and last_close > last_open and body_pct >= 0.50:
        score += 10
        parts.append(f"C5:bull_body+10({body_pct*100:.0f}%)")
    elif body_pct >= 0.50:
        score += 3
        parts.append("C5:body+3")

    # C6 — Rejection wick (wick pointing away from level = rejection confirmed)
    upper_wick = last_high - max(last_open, last_close)
    lower_wick = min(last_open, last_close) - last_low
    wick_ratio = upper_wick / bar_range if bar_range > 0 else 0
    wick_ratio_lo = lower_wick / bar_range if bar_range > 0 else 0
    if direction == "SELL" and at_vah and wick_ratio >= 0.30:
        score += 10
        parts.append(f"C6:upper_wick+10({wick_ratio*100:.0f}%)")
    elif direction == "BUY" and at_val and wick_ratio_lo >= 0.30:
        score += 10
        parts.append(f"C6:lower_wick+10({wick_ratio_lo*100:.0f}%)")

    # C7 — VAH/VAL is a high-volume node (institutional zone confirmed)
    if direction == "SELL":
        vol_pct = levels.get("vah_vol_pct", 0)
    else:
        vol_pct = levels.get("val_vol_pct", 0)
    if vol_pct >= 0.04:       # ≥4% of day's volume at this level
        score += 10
        parts.append(f"C7:vol_node+10({vol_pct*100:.1f}%)")
    elif vol_pct >= 0.02:
        score += 5
        parts.append(f"C7:vol_node+5({vol_pct*100:.1f}%)")

    score = min(100, score)

    # ── Determine vote ─────────────────────────────────────────────────
    active = score >= VOTE_THRESHOLD and (at_vah or at_val)
    if active:
        # If we're at VAH → SELL is correct, BUY there is fighting structure
        if at_vah and direction == "SELL":
            vote = +1   # agrees
        elif at_val and direction == "BUY":
            vote = +1   # agrees
        elif at_vah and direction == "BUY":
            vote = -1   # disagrees — buying into resistance
        elif at_val and direction == "SELL":
            vote = -1   # disagrees — selling into support
        else:
            vote = 0
    else:
        vote = 0

    reason = " | ".join(parts) if parts else "below_threshold"
    result = {
        "vote":   vote,
        "score":  score,
        "reason": reason,
        "active": active,
        "vah":    round(vah, 6),
        "val":    round(val, 6),
        "poc":    round(poc, 6),
    }
    _VOTE_CACHE[cache_key] = (now, result)

    if active:
        print(f"[vpvr] {pair} {direction} score={score} vote={vote:+d} | {reason}")
    return result
