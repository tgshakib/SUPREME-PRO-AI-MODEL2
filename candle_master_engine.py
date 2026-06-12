"""CANDLE MASTER ENGINE — Elite Candle-by-Candle Reading
=========================================================
Pure price action: reads each candle in sequence to detect
high-probability directional momentum with master-level precision.

Signals fired (12 independent checks):
  C01  Consecutive same-dir bars      wt 3  (3+ bars same direction)
  C02  Engulfing sequence             wt 4  (engulf + confirmation bar)
  C03  Pin bar rejection cluster      wt 3  (2+ pin bars at same zone)
  C04  Inside bar breakout            wt 2  (IB + breakout candle)
  C05  Morning/Evening star pattern   wt 4  (3-bar reversal structure)
  C06  Three soldiers/crows           wt 4  (3 consecutive conviction bars)
  C07  Candle body momentum           wt 3  (avg body > avg wick × 1.4)
  C08  Wick rejection cascade         wt 3  (3 bars wick same side)
  C09  Volume surge confirmation      wt 2  (vol > 1.5× avg on signal bar)
  C10  Close-above-midpoint streak    wt 2  (3 bars closing > 60% of range)
  C11  ATR expansion into move        wt 2  (bar ATR > 1.2× 14-bar avg)
  C12  Multi-candle momentum lock     wt 4  (5-bar momentum score)

Max votes: 36
Threshold: CALL → ≥ 18 votes  |  PUT → ≥ 18 votes
Elite:     ≥ 26 votes, opposing ≤ 1

Non-reprint rule:
  Only bar[-2] and bar[-3] (confirmed, not live) are evaluated for
  pattern completion. The live bar[-1] is only used for ATR/volume.

Signal text contract: ZERO modifications to signal output text.
This engine returns only direction, grade, and vote counts.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
    _OK = True
except Exception:
    yf = None
    _OK = False

try:
    from live_prices import yf_ticker
except Exception:
    def yf_ticker(pair: str) -> Optional[str]:  # type: ignore
        return None

_INTERVAL  = "1m"
_PERIOD    = "1d"
_CANDLES   = 100
_TTL       = 15.0
_CACHE: dict = {}

_MIN_VOTES_OTC  = 18
_MIN_VOTES_LIVE = 18
_ELITE_VOTES    = 26


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr = (hi - lo).combine((hi - cl.shift()).abs(), max).combine(
        (lo - cl.shift()).abs(), max
    )
    return tr.rolling(n).mean()


def _is_bullish(o, c) -> bool:
    return float(c) > float(o)


def _is_bearish(o, c) -> bool:
    return float(c) < float(o)


def _body(o, c) -> float:
    return abs(float(c) - float(o))


def _upper_wick(o, h, c) -> float:
    return float(h) - max(float(o), float(c))


def _lower_wick(o, l, c) -> float:
    return min(float(o), float(c)) - float(l)


def _is_pin_bull(o, h, l, c) -> bool:
    """Long lower wick, small body at top — bullish pin bar."""
    body  = _body(o, c)
    lwk   = _lower_wick(o, l, c)
    rng   = float(h) - float(l)
    if rng < 1e-10:
        return False
    return lwk >= 0.55 * rng and body <= 0.30 * rng


def _is_pin_bear(o, h, l, c) -> bool:
    """Long upper wick, small body at bottom — bearish pin bar."""
    body  = _body(o, c)
    uwk   = _upper_wick(o, h, c)
    rng   = float(h) - float(l)
    if rng < 1e-10:
        return False
    return uwk >= 0.55 * rng and body <= 0.30 * rng


def _is_engulf_bull(prev_o, prev_c, curr_o, curr_c) -> bool:
    return (float(curr_c) > float(prev_o) and
            float(curr_o) <= float(prev_c) and
            _is_bearish(prev_o, prev_c) and
            _is_bullish(curr_o, curr_c))


def _is_engulf_bear(prev_o, prev_c, curr_o, curr_c) -> bool:
    return (float(curr_c) < float(prev_o) and
            float(curr_o) >= float(prev_c) and
            _is_bullish(prev_o, prev_c) and
            _is_bearish(curr_o, curr_c))


def _inside_bar(prev_h, prev_l, curr_h, curr_l) -> bool:
    return float(curr_h) < float(prev_h) and float(curr_l) > float(prev_l)


def candle_master_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """Return {direction, grade, votes_bull, votes_bear, elite, reasons} or None."""
    ticker    = yf_ticker(pair)
    cache_key = ticker or pair
    cache_key = f"{cache_key}|{is_otc}"
    now = time.time()
    if cache_key in _CACHE:
        ts, res = _CACHE[cache_key]
        if now - ts < _TTL:
            return res

    df = None
    _is_otc_cm = is_otc or "〔OTC〕" in pair or "(OTC)" in pair.upper()

    # Priority 1 — live broker WS candles (most accurate for OTC)
    if _is_otc_cm:
        try:
            from otc_realtime_bridge import get_otc_df as _rt_get
            df = _rt_get(pair, "1m", count=_CANDLES + 20)
        except Exception:
            df = None

        # Priority 2 — Twelve Data drift model
        if df is None:
            try:
                from otc_feed import get_otc_df as _otc_df
                df = _otc_df(pair, "1m", count=_CANDLES + 20)
            except Exception:
                df = None

    # Priority 3 — yfinance
    if df is None:
        if not _OK or yf is None or not ticker:
            _CACHE[cache_key] = (now, None)
            return None
        try:
            df = yf.download(ticker, period=_PERIOD, interval=_INTERVAL,
                             progress=False, auto_adjust=True)
            if df is None or df.empty or len(df) < 20:
                _CACHE[cache_key] = (now, None)
                return None
            # Flatten multi-index if present
            if hasattr(df.columns, "get_level_values"):
                df.columns = [
                    str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                    for c in df.columns
                ]
            else:
                df.columns = [str(c).lower() for c in df.columns]
        except Exception:
            _CACHE[cache_key] = (now, None)
            return None

    if df is None or len(df) < 20:
        _CACHE[cache_key] = (now, None)
        return None

    # Ensure lowercase columns
    if hasattr(df.columns, "get_level_values"):
        df.columns = [
            str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
            for c in df.columns
        ]
    else:
        df.columns = [str(c).lower() for c in df.columns]

    try:
        def _col(name: str) -> pd.Series:
            lo, cap = name.lower(), name.capitalize()
            cols = df.columns
            if lo  in cols: return df[lo].squeeze().astype(float)
            if cap in cols: return df[cap].squeeze().astype(float)
            for c in cols:
                if isinstance(c, tuple) and c[0].lower() == lo:
                    return df[c].squeeze().astype(float)
            raise KeyError(name)

        o  = _col("open").tail(_CANDLES).reset_index(drop=True)
        h  = _col("high").tail(_CANDLES).reset_index(drop=True)
        l  = _col("low").tail(_CANDLES).reset_index(drop=True)
        c  = _col("close").tail(_CANDLES).reset_index(drop=True)
        v_raw = None
        try:
            v_raw = _col("volume").tail(_CANDLES).reset_index(drop=True)
        except Exception:
            pass

        n = len(c)
        if n < 10:
            return None

        atr_series = _atr(pd.DataFrame({"high": h, "low": l, "close": c}))
        atr_avg = float(atr_series.iloc[-15:-1].mean()) if len(atr_series) >= 15 else 0.0
        atr_last = float(atr_series.iloc[-2]) if len(atr_series) >= 2 else 0.0

        votes_bull = 0
        votes_bear = 0
        reasons: list[str] = []

        # ── C01  Consecutive same-direction bars (confirmed bars [-2,-3,-4]) ──
        same_bull = sum(1 for i in range(n-4, n-1) if _is_bullish(o[i], c[i]))
        same_bear = sum(1 for i in range(n-4, n-1) if _is_bearish(o[i], c[i]))
        if same_bull >= 3:
            votes_bull += 3
            reasons.append("3+ CONSECUTIVE BULL BARS")
        elif same_bear >= 3:
            votes_bear += 3
            reasons.append("3+ CONSECUTIVE BEAR BARS")

        # ── C02  Engulfing sequence (confirmed: bar[-3] sets up, bar[-2] engulfs) ──
        if n >= 4:
            if _is_engulf_bull(o[n-3], c[n-3], o[n-2], c[n-2]):
                votes_bull += 4
                reasons.append("BULLISH ENGULFING SEQUENCE")
            elif _is_engulf_bear(o[n-3], c[n-3], o[n-2], c[n-2]):
                votes_bear += 4
                reasons.append("BEARISH ENGULFING SEQUENCE")
            elif _is_engulf_bull(o[n-4], c[n-4], o[n-3], c[n-3]) and _is_bullish(o[n-2], c[n-2]):
                votes_bull += 3
                reasons.append("ENGULF + CONFIRMATION BAR BULL")
            elif _is_engulf_bear(o[n-4], c[n-4], o[n-3], c[n-3]) and _is_bearish(o[n-2], c[n-2]):
                votes_bear += 3
                reasons.append("ENGULF + CONFIRMATION BAR BEAR")

        # ── C03  Pin bar rejection cluster (2+ pin bars in last 4 confirmed bars) ──
        pin_bull_count = sum(1 for i in range(n-5, n-1)
                             if _is_pin_bull(o[i], h[i], l[i], c[i]))
        pin_bear_count = sum(1 for i in range(n-5, n-1)
                             if _is_pin_bear(o[i], h[i], l[i], c[i]))
        if pin_bull_count >= 2:
            votes_bull += 3
            reasons.append("BULLISH PIN BAR REJECTION CLUSTER")
        elif pin_bear_count >= 2:
            votes_bear += 3
            reasons.append("BEARISH PIN BAR REJECTION CLUSTER")
        elif pin_bull_count == 1 and _is_bullish(o[n-2], c[n-2]):
            votes_bull += 2
            reasons.append("PIN BAR + BULL CONFIRM")
        elif pin_bear_count == 1 and _is_bearish(o[n-2], c[n-2]):
            votes_bear += 2
            reasons.append("PIN BAR + BEAR CONFIRM")

        # ── C04  Inside bar breakout ──
        if n >= 4 and _inside_bar(h[n-4], l[n-4], h[n-3], l[n-3]):
            if _is_bullish(o[n-2], c[n-2]) and float(c[n-2]) > float(h[n-4]):
                votes_bull += 2
                reasons.append("INSIDE BAR BULL BREAKOUT")
            elif _is_bearish(o[n-2], c[n-2]) and float(c[n-2]) < float(l[n-4]):
                votes_bear += 2
                reasons.append("INSIDE BAR BEAR BREAKOUT")

        # ── C05  Morning/Evening star (3-bar reversal) ──
        if n >= 5:
            # Morning star: bear | doji | bull
            b1_o, b1_c = float(o[n-4]), float(c[n-4])
            b2_o, b2_c = float(o[n-3]), float(c[n-3])
            b3_o, b3_c = float(o[n-2]), float(c[n-2])
            b2_body = _body(b2_o, b2_c)
            b1_body = _body(b1_o, b1_c)
            b3_body = _body(b3_o, b3_c)
            if (b1_body > 0 and b3_body > 0 and
                    _is_bearish(b1_o, b1_c) and
                    b2_body < b1_body * 0.4 and
                    _is_bullish(b3_o, b3_c) and
                    b3_body > b1_body * 0.5):
                votes_bull += 4
                reasons.append("MORNING STAR REVERSAL PATTERN")
            elif (b1_body > 0 and b3_body > 0 and
                    _is_bullish(b1_o, b1_c) and
                    b2_body < b1_body * 0.4 and
                    _is_bearish(b3_o, b3_c) and
                    b3_body > b1_body * 0.5):
                votes_bear += 4
                reasons.append("EVENING STAR REVERSAL PATTERN")

        # ── C06  Three white soldiers / Three black crows ──
        if n >= 5:
            bars = [(float(o[i]), float(c[i]), float(h[i]), float(l[i]))
                    for i in range(n-4, n-1)]
            bodies = [_body(b[0], b[1]) for b in bars]
            avg_body = sum(bodies) / len(bodies) if bodies else 0
            if (all(_is_bullish(b[0], b[1]) for b in bars) and
                    avg_body > 0 and
                    all(_body(b[0], b[1]) >= avg_body * 0.7 for b in bars)):
                votes_bull += 4
                reasons.append("THREE WHITE SOLDIERS — MASTER BULL")
            elif (all(_is_bearish(b[0], b[1]) for b in bars) and
                    avg_body > 0 and
                    all(_body(b[0], b[1]) >= avg_body * 0.7 for b in bars)):
                votes_bear += 4
                reasons.append("THREE BLACK CROWS — MASTER BEAR")

        # ── C07  Candle body momentum (avg body >> avg wick) ──
        recent = range(n-6, n-1)
        bodies_r  = [_body(o[i], c[i]) for i in recent]
        uwicks_r  = [_upper_wick(o[i], h[i], c[i]) for i in recent]
        lwicks_r  = [_lower_wick(o[i], l[i], c[i]) for i in recent]
        avg_b_r   = sum(bodies_r)  / len(bodies_r)  if bodies_r  else 0
        avg_uw_r  = sum(uwicks_r)  / len(uwicks_r)  if uwicks_r  else 0
        avg_lw_r  = sum(lwicks_r)  / len(lwicks_r)  if lwicks_r  else 0
        if avg_b_r > 0:
            bull_bars_r = sum(1 for i in recent if _is_bullish(o[i], c[i]))
            bear_bars_r = sum(1 for i in recent if _is_bearish(o[i], c[i]))
            if avg_b_r > avg_uw_r * 1.4 and bull_bars_r >= 4:
                votes_bull += 3
                reasons.append("BULLISH BODY MOMENTUM DOMINANCE")
            elif avg_b_r > avg_lw_r * 1.4 and bear_bars_r >= 4:
                votes_bear += 3
                reasons.append("BEARISH BODY MOMENTUM DOMINANCE")

        # ── C08  Wick rejection cascade (3+ bars wick same side) ──
        lower_rej = sum(1 for i in range(n-5, n-1)
                        if (float(l[i]) < min(float(o[i]), float(c[i])) and
                            _lower_wick(o[i], l[i], c[i]) > _body(o[i], c[i]) * 0.6))
        upper_rej = sum(1 for i in range(n-5, n-1)
                        if (float(h[i]) > max(float(o[i]), float(c[i])) and
                            _upper_wick(o[i], h[i], c[i]) > _body(o[i], c[i]) * 0.6))
        if lower_rej >= 3:
            votes_bull += 3
            reasons.append("LOWER WICK REJECTION CASCADE — BULL ZONE")
        elif upper_rej >= 3:
            votes_bear += 3
            reasons.append("UPPER WICK REJECTION CASCADE — BEAR ZONE")

        # ── C09  Volume surge on signal bar ──
        if v_raw is not None and len(v_raw) >= 10:
            v = v_raw.astype(float)
            v_avg = float(v.iloc[-10:-2].mean())
            v_last = float(v.iloc[-2])
            if v_avg > 0 and v_last > v_avg * 1.5:
                if _is_bullish(o[n-2], c[n-2]):
                    votes_bull += 2
                    reasons.append("VOLUME SURGE BULL BAR")
                elif _is_bearish(o[n-2], c[n-2]):
                    votes_bear += 2
                    reasons.append("VOLUME SURGE BEAR BAR")

        # ── C10  Close-above-midpoint streak (conviction closes) ──
        bull_conv = sum(1 for i in range(n-5, n-1)
                        if float(c[i]) > (float(h[i]) + float(l[i])) / 2 + (float(h[i]) - float(l[i])) * 0.1)
        bear_conv = sum(1 for i in range(n-5, n-1)
                        if float(c[i]) < (float(h[i]) + float(l[i])) / 2 - (float(h[i]) - float(l[i])) * 0.1)
        if bull_conv >= 4:
            votes_bull += 2
            reasons.append("CONVICTION CLOSE STREAK — BULL")
        elif bear_conv >= 4:
            votes_bear += 2
            reasons.append("CONVICTION CLOSE STREAK — BEAR")

        # ── C11  ATR expansion into move ──
        if atr_avg > 0 and atr_last > atr_avg * 1.2:
            if _is_bullish(o[n-2], c[n-2]):
                votes_bull += 2
                reasons.append("ATR EXPANSION INTO BULL MOVE")
            elif _is_bearish(o[n-2], c[n-2]):
                votes_bear += 2
                reasons.append("ATR EXPANSION INTO BEAR MOVE")

        # ── C12  5-bar momentum lock ──
        if n >= 6:
            momentum = sum(
                (1 if _is_bullish(o[i], c[i]) else -1) * min(2.0, _body(o[i], c[i]) / max(atr_avg, 1e-10))
                for i in range(n-6, n-1)
            )
            if momentum >= 3.0:
                votes_bull += 4
                reasons.append("5-BAR MOMENTUM LOCK — BULL")
            elif momentum <= -3.0:
                votes_bear += 4
                reasons.append("5-BAR MOMENTUM LOCK — BEAR")

        total_bull = votes_bull
        total_bear = votes_bear
        total      = total_bull + total_bear
        if total == 0:
            return None

        min_votes = _MIN_VOTES_OTC if is_otc else _MIN_VOTES_LIVE

        if total_bull >= min_votes and total_bull > total_bear:
            direction = "BUY"
            grade     = int(100 * total_bull / 36)
            opposing  = total_bear
        elif total_bear >= min_votes and total_bear > total_bull:
            direction = "SELL"
            grade     = int(100 * total_bear / 36)
            opposing  = total_bull
        else:
            return None

        elite = (max(total_bull, total_bear) >= _ELITE_VOTES and
                 min(total_bull, total_bear) <= 1)
        result = {
            "direction": direction,
            "grade":     grade,
            "votes_bull": total_bull,
            "votes_bear": total_bear,
            "opposing":  opposing,
            "elite":     elite,
            "reasons":   reasons[:4],
        }
        _CACHE[cache_key] = (now, result)
        return result

    except Exception as exc:
        print(f"[candle_master] {pair} error: {exc}")
        return None
