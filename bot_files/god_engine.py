"""GOD LEVEL AI ENGINE — Supreme Analysis Consensus Layer V10
=============================================================
The master quality gate for ALL signals in SUPREME PRO AI BOT.

Every signal passes through this engine LAST.  It implements:

  1. Session / Kill-Zone awareness
       Never trade EUR/GBP/CHF/CAD during the Asian dead zone.
       London (07-16 UTC) and NY (12-21 UTC) are active.
       Overlap (12-16 UTC) is the kill zone — maximum edge.
       Crypto / metals are 24/7 and always pass the gate.

  2. Anti-whipsaw detector
       Reads the last 5 CONFIRMED closed candles on 5m.
       If the bars are alternating (chop), the gate rejects.
       At least 3 of 4 active-body bars must agree on direction.

  3. Cross-engine consensus
       At least 2 independent engines must vote the SAME direction
       with ZERO opposing votes (or 3+ vs ≤1 with clear dominance).

  4. ADX trend-strength gate
       ADX < 18 on 5m = flat/ranging market = skip signal.
       ADX ≥ 18 = directional = allow signal.

  5. Supreme binary gate (1-MIN / 2-MIN specific)
       For ultra-short timeframes: requires 5m momentum to AGREE
       with the signal direction. A bullish 5m structure while
       sending a SELL = automatic reject.

  6. Supreme forex gate
       Session + anti-whipsaw + ADX all checked. Any failure = skip.
       Result includes human-readable reason for the signal card.

Public API
----------
  session_gate(pair)                 → "kill_zone" | "active" | "dead"
  anti_whipsaw(pair, timeframe)      → "bull" | "bear" | "chop"
  adx_strength(pair, timeframe)      → float | None
  cross_engine_consensus(dirs, n)    → "BUY" | "SELL" | None
  supreme_binary_gate(pair, is_otc, engines, tf_label) → "BUY"|"SELL"|None
  supreme_forex_gate(pair, direction) → dict{approved, session, whipsaw, adx, reason}
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


# ── Indicator helpers ──────────────────────────────────────────────────────

def _ema(series, period: int):
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series, period: int):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _adx_calc(df, period: int = 14):
    try:
        h  = df["high"].astype(float)
        lo = df["low"].astype(float)
        c  = df["close"].astype(float)
        prev_h  = h.shift(1)
        prev_lo = lo.shift(1)
        prev_c  = c.shift(1)
        tr = (h - lo).combine((h - prev_c).abs(), max).combine(
            (lo - prev_c).abs(), max)
        up   = h - prev_h
        down = prev_lo - lo
        dm_p = up.where((up > down) & (up > 0), 0.0)
        dm_m = down.where((down > up) & (down > 0), 0.0)
        atr14 = tr.rolling(period).mean()
        di_p  = 100 * dm_p.rolling(period).mean() / atr14.replace(0, 1e-10)
        di_m  = 100 * dm_m.rolling(period).mean() / atr14.replace(0, 1e-10)
        dx    = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 1e-10)
        return dx.rolling(period).mean()
    except Exception:
        return None


def _fetch_tf(ticker: str, interval: str, period: str):
    if not _OK:
        return None
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [
                str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                for c in df.columns
            ]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        return df
    except Exception:
        return None


# ── Caches ─────────────────────────────────────────────────────────────────
_SESSION_CACHE:  dict[str, tuple[float, str]]           = {}
_WHIPSAW_CACHE:  dict[str, tuple[float, str]]           = {}
_ADX_CACHE:      dict[str, tuple[float, Optional[float]]] = {}
_SESSION_TTL = 60.0
_WHIPSAW_TTL = 20.0
_ADX_TTL     = 90.0


# ═══════════════════════════════════════════════════════════════════════════
#  1. SESSION / KILL ZONE GATE
# ═══════════════════════════════════════════════════════════════════════════

_CRYPTO_METAL = {"BTC", "ETH", "SOL", "XRP", "BNB", "XAU", "XAG", "GOLD", "SILVER"}
_ASIAN_SESSION = {"JPY", "AUD", "NZD", "SGD", "HKD", "CNH"}


def _pair_is_24_7(pair: str) -> bool:
    p = pair.upper().replace("/", "").replace(" ", "")
    return any(s in p for s in _CRYPTO_METAL)


def _pair_trades_asian(pair: str) -> bool:
    p = pair.upper().replace("/", "").replace(" ", "")
    return any(s in p for s in _ASIAN_SESSION)


def session_gate(pair: str) -> str:
    """Return 'kill_zone' | 'active' | 'dead'.

    kill_zone = London + NY overlap (12:00–16:00 UTC) — maximum institutional activity
    active    = London session (07:00–16:00) OR NY session (12:00–21:00)
    dead      = Asian dead zone for EUR/GBP/CHF/CAD pairs (22:00–07:00 UTC)

    Crypto and metals always return 'active' (24/7 markets).
    JPY, AUD, NZD pairs return 'active' during Asian hours.
    """
    if _pair_is_24_7(pair):
        return "active"

    cached = _SESSION_CACHE.get(pair)
    now_ts = time.time()
    if cached and (now_ts - cached[0]) < _SESSION_TTL:
        return cached[1]

    hour = datetime.now(timezone.utc).hour  # 0–23 UTC

    if 12 <= hour < 16:
        result = "kill_zone"           # London+NY overlap — best time
    elif 7 <= hour < 16:
        result = "active"              # London session
    elif 16 <= hour < 22:
        result = "active"              # NY session
    else:
        # 22:00–07:00 UTC — Asian dead zone
        if _pair_trades_asian(pair):
            result = "active"          # JPY/AUD/NZD trade well in Asian hours
        else:
            result = "dead"            # EUR/GBP/CHF/CAD — dead spread, stop hunts

    _SESSION_CACHE[pair] = (now_ts, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  2. ANTI-WHIPSAW DETECTOR
# ═══════════════════════════════════════════════════════════════════════════

def anti_whipsaw(pair: str, timeframe: str = "5m") -> str:
    """Return 'bull' | 'bear' | 'chop'.

    Reads the last 5 CONFIRMED closed candles on `timeframe`.
    A candle is counted only if its body is ≥ 35% of the full range.
    'chop'  = mixed direction = do not trade.
    'bull'  = 3+ of 4 active-body bars are bullish.
    'bear'  = 3+ of 4 active-body bars are bearish.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return "chop"

    cache_key = f"{ticker}|{timeframe}"
    cached = _WHIPSAW_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and (now_ts - cached[0]) < _WHIPSAW_TTL:
        return cached[1]

    period = "2d" if timeframe in ("1m", "5m") else "5d"
    df = _fetch_tf(ticker, timeframe, period)
    if df is None or "close" not in df.columns or len(df) < 10:
        _WHIPSAW_CACHE[cache_key] = (now_ts, "chop")
        return "chop"

    try:
        cl = df["close"].squeeze().astype(float).dropna()
        op = df["open"].squeeze().astype(float).dropna()
        hi = df["high"].squeeze().astype(float).dropna()
        lo = df["low"].squeeze().astype(float).dropna()

        # Use bars -2 through -6: confirmed closed (bar -1 may still be forming)
        bars: list[int] = []
        for i in range(-2, -7, -1):
            try:
                c = float(cl.iloc[i]); o = float(op.iloc[i])
                h = float(hi.iloc[i]); l = float(lo.iloc[i])
                full_range = max(h - l, 1e-10)
                body_pct   = abs(c - o) / full_range
                if body_pct >= 0.35:
                    bars.append(1 if c > o else -1)
                # dojis / small bodies → ignore (not directional)
            except Exception:
                pass

        active = [b for b in bars if b != 0]
        if len(active) < 3:
            result = "chop"
        else:
            bull = sum(1 for b in active if b > 0)
            bear = sum(1 for b in active if b < 0)
            total = bull + bear
            if bull >= 4 and bull / total >= 0.75:
                result = "bull"
            elif bear >= 4 and bear / total >= 0.75:
                result = "bear"
            else:
                result = "chop"
    except Exception:
        result = "chop"

    _WHIPSAW_CACHE[cache_key] = (now_ts, result)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  3. ADX TREND-STRENGTH FILTER
# ═══════════════════════════════════════════════════════════════════════════

def adx_strength(pair: str, timeframe: str = "5m") -> Optional[float]:
    """Return the current ADX value for `pair` on `timeframe`, or None.

    ADX < 18 = flat/ranging market = skip signal.
    ADX ≥ 18 = trend present = allow.
    ADX ≥ 25 = strong trend = ideal.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return None

    cache_key = f"{ticker}|{timeframe}"
    cached = _ADX_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and (now_ts - cached[0]) < _ADX_TTL:
        return cached[1]

    period = "5d" if timeframe in ("1m", "5m") else "20d"
    df = _fetch_tf(ticker, timeframe, period)
    if df is None or "close" not in df.columns or len(df) < 20:
        _ADX_CACHE[cache_key] = (now_ts, None)
        return None

    try:
        adx_s = _adx_calc(df)
        if adx_s is None or len(adx_s) == 0:
            val = None
        else:
            v = float(adx_s.iloc[-1])
            val = v if v == v else None   # NaN check
        _ADX_CACHE[cache_key] = (now_ts, val)
        return val
    except Exception:
        _ADX_CACHE[cache_key] = (now_ts, None)
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  4. CROSS-ENGINE CONSENSUS GATE
# ═══════════════════════════════════════════════════════════════════════════

def cross_engine_consensus(
    directions: list[Optional[str]],
    min_agree: int = 2,
) -> Optional[str]:
    """Require `min_agree` engines to vote the SAME direction with no opposition.

    Rules:
      * None entries = engine abstained = ignored.
      * min_agree votes + 0 opposing  → consensus direction.
      * 3+ votes vs exactly 1 opposing (dominant) → consensus direction.
      * Anything else → None (conflict, wait for cleaner setup).
    """
    active = [d for d in directions if d is not None]
    if len(active) < min_agree:
        return None

    buy_count  = sum(1 for d in active if d == "BUY")
    sell_count = sum(1 for d in active if d == "SELL")

    if buy_count >= min_agree and sell_count == 0:
        return "BUY"
    if sell_count >= min_agree and buy_count == 0:
        return "SELL"
    if buy_count >= 3 and sell_count <= 1 and buy_count > sell_count * 2:
        return "BUY"
    if sell_count >= 3 and buy_count <= 1 and sell_count > buy_count * 2:
        return "SELL"

    return None   # engines disagree — do not send signal


# ═══════════════════════════════════════════════════════════════════════════
#  5. SUPREME BINARY GATE (1-MIN / 2-MIN specific)
# ═══════════════════════════════════════════════════════════════════════════

def supreme_binary_gate(
    pair: str,
    is_otc: bool,
    engine_directions: list[Optional[str]],
    tf_label: str = "1 MIN",
) -> Optional[str]:
    """Master quality gate for binary signals.

    For ALL binary signals: requires cross-engine consensus (2+ agree, 0 opposing).
    For 1-MIN / 2-MIN: ALSO requires 5m momentum to confirm the direction.
      A 5m bullish momentum while signal says SELL = automatic reject (whipsaw).
    Volatility guard: Friday close / news windows / ATR spike → extra checks.

    Returns consensus direction or None.
    """
    consensus = cross_engine_consensus(engine_directions, min_agree=2)
    if consensus is None:
        return None

    # ── VOLATILITY GUARD — Friday close / news / ATR spike ────────────────
    try:
        from volatility_guard import binary_volatility_gate as _vg_gate
        _vg_result = _vg_gate(pair, consensus, tf_label)
        if _vg_result == "BLOCK":
            return None   # dangerous conditions — skip signal entirely
        # "MOMENTUM_ONLY" and "ALLOW" both let the signal through here
        # (momentum alignment was already checked inside the guard)
    except Exception:
        pass

    # For fast timeframes (1m, 2m): also require 5m momentum alignment
    tf_up = tf_label.strip().upper()
    is_fast = tf_up.startswith(("1 MIN", "2 MIN", "1MIN", "2MIN"))
    if is_fast:
        ws = anti_whipsaw(pair, "5m")
        if ws == "chop":
            return None   # 5m is ranging — skip 1m binary during chop
        if ws == "bull" and consensus == "SELL":
            return None   # 5m bullish candles, signal says SELL → whipsaw risk
        if ws == "bear" and consensus == "BUY":
            return None   # 5m bearish candles, signal says BUY → whipsaw risk

    return consensus


# ═══════════════════════════════════════════════════════════════════════════
#  6. SUPREME FOREX GATE
# ═══════════════════════════════════════════════════════════════════════════

def supreme_forex_gate(pair: str, direction: str) -> dict:
    """Final quality gate before sending a forex signal.

    Checks:
      1. Volatility Guard — Friday close / news window / ATR spike
      2. Session is active (not Asian dead zone for EUR/GBP/CHF)
      3. Anti-whipsaw: 5m candle flow agrees with signal direction
      4. ADX ≥ 18 on 5m (there is a real trend, not flat chop)

    Returns:
        {
          'approved':    bool,
          'session':     str,          # 'kill_zone' | 'active' | 'dead'
          'whipsaw':     str,          # 'bull' | 'bear' | 'chop'
          'adx':         float | None,
          'session_bonus': int,        # +2 if kill_zone, +1 if active
          'vol_mode':    str,          # volatility mode from the guard
          'sl_mult':     float,        # SL multiplier to apply (1.0–1.8)
          'reason':      str,
        }
    """
    # ── VOLATILITY GUARD ─────────────────────────────────────────────────
    _vol_mode = "normal"
    _sl_mult  = 1.0
    try:
        from volatility_guard import (
            get_volatility_state as _gvs,
            forex_sl_multiplier  as _fslm,
        )
        _vs = _gvs(pair)
        _vol_mode = _vs["mode"]
        _sl_mult  = _fslm(pair)

        # Hard block: Friday close zone — DO NOT send forex signals
        if _vs["is_friday_close"]:
            return {
                "approved": False, "session": "active",
                "whipsaw": "unknown", "adx": None,
                "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
                "reason": "🚫 Friday NY close zone — stop hunts active, no forex signals",
            }
        # Hard block: Monday gap
        if _vs["is_monday_gap"]:
            return {
                "approved": False, "session": "active",
                "whipsaw": "unknown", "adx": None,
                "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
                "reason": "🚫 Monday gap-open — weekend gap risk",
            }
        # Extreme volatility: only allow if momentum agrees with direction
        if _vol_mode == "extreme":
            from volatility_guard import get_momentum_direction as _gmd
            mom = _gmd(pair)
            if mom is not None and mom != direction:
                return {
                    "approved": False, "session": "active",
                    "whipsaw": "unknown", "adx": None,
                    "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
                    "reason": f"🌋 Extreme vol (ATR×{_vs['atr_ratio']:.1f}) — momentum={mom}, signal={direction} → BLOCKED",
                }
    except Exception:
        pass

    sess = session_gate(pair)
    if sess == "dead":
        return {
            "approved": False, "session": sess,
            "whipsaw": "unknown", "adx": None,
            "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
            "reason": "Asian dead zone — avoid EUR/GBP/CHF/CAD during low liquidity",
        }

    ws = anti_whipsaw(pair, "5m")
    if ws == "chop":
        return {
            "approved": False, "session": sess,
            "whipsaw": ws, "adx": None,
            "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
            "reason": "5m candles in chop/range — no clear directional momentum",
        }

    if ws == "bull" and direction == "SELL":
        return {
            "approved": False, "session": sess,
            "whipsaw": ws, "adx": None,
            "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
            "reason": "Anti-whipsaw: 5m bullish candle flow conflicts with SELL signal",
        }
    if ws == "bear" and direction == "BUY":
        return {
            "approved": False, "session": sess,
            "whipsaw": ws, "adx": None,
            "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
            "reason": "Anti-whipsaw: 5m bearish candle flow conflicts with BUY signal",
        }

    adx_val = adx_strength(pair, "5m")
    if adx_val is not None and adx_val < 22:
        return {
            "approved": False, "session": sess,
            "whipsaw": ws, "adx": adx_val,
            "session_bonus": 0, "vol_mode": _vol_mode, "sl_mult": _sl_mult,
            "reason": f"ADX {adx_val:.1f} < 22 on 5m — insufficient trend strength (GOLD filter)",
        }

    session_bonus = 2 if sess == "kill_zone" else 1
    adx_str = f"{adx_val:.1f}" if adx_val is not None else "N/A"
    vol_note = f" | VOL={_vol_mode.upper()} SL×{_sl_mult:.2f}" if _vol_mode != "normal" else ""
    return {
        "approved": True, "session": sess,
        "whipsaw": ws, "adx": adx_val,
        "session_bonus": session_bonus,
        "vol_mode": _vol_mode,
        "sl_mult": _sl_mult,
        "reason": f"Session={sess} | 5m={ws} | ADX={adx_str}{vol_note} — APPROVED",
    }


# ═══════════════════════════════════════════════════════════════════════════
#  7. HTFT (Higher-Timeframe Trend Filter) — quick 1H / 4H bias check
# ═══════════════════════════════════════════════════════════════════════════

def htf_trend(pair: str, direction: str) -> bool:
    """Return True if the 1H trend (EMA9 vs EMA21) agrees with `direction`.

    Used as a quick sanity check — if the 1H trend is STRONGLY against the
    proposed direction, the signal is likely a counter-trend entry in a bad
    location. True = aligned. False = fighting the 1H trend.
    """
    ticker = yf_ticker(pair)
    if not ticker:
        return True   # can't tell → don't block

    try:
        df = _fetch_tf(ticker, "60m", "30d")
        if df is None or "close" not in df.columns or len(df) < 25:
            return True
        cl = df["close"].squeeze().astype(float).dropna()
        ef = float(_ema(cl, 9).iloc[-1])
        es = float(_ema(cl, 21).iloc[-1])
        rsi = float(_rsi(cl, 14).iloc[-1])
        if direction == "BUY":
            return ef > es or rsi > 45    # allow if not strongly bearish
        else:
            return ef < es or rsi < 55    # allow if not strongly bullish
    except Exception:
        return True
