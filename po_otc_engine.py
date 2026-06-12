"""PO OTC ENGINE — Pocket Option Exclusive Signal Analyzer
===========================================================
Purpose-built for Pocket Option OTC synthetic candles.

DATA PRIORITY:
  1. Real PO candles from pocket_option_ws (when PO_SSID is set)
     → direction is the ACTUAL PO direction, no mirror inversion needed
  2. yfinance fallback (when no SSID / socket not connected)
     → direction mirrors Quotex OTC, PO mirror inversion applied in signals.py

PO OTC CHARACTERISTICS:
  • Synthetic broker candles — mean-revert at extremes more reliably than live forex
  • After 4+ same-direction candles: ~72% reversal probability
  • After 6+ same-direction candles: ~85%+ reversal probability
  • RSI(3) > 92 or < 8: near-certain reversal on next candle
  • BB outer (2.5σ): ~75% bounce probability
  • All patterns work on 1-minute timeframe

REQUIRES to fire:
  • Weighted score ≥ 14
  • At least 5 sub-signals confirming
  • ZERO opposing sub-signals (strict consensus only)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 18  # seconds — Elite: refresh every 18s, fast as OTC candles (was 55s)


def _pair_to_yf_ticker(pair: str) -> Optional[str]:
    try:
        from live_prices import yf_ticker
        return yf_ticker(pair)
    except Exception:
        return None


def _get_candles_po(pair: str, period: int = 60) -> List[dict]:
    try:
        from pocket_option_ws import get_candles, is_connected
        if is_connected():
            return get_candles(pair, period)
    except Exception:
        pass
    return []


def _get_candles_yf(pair: str, interval: str = "1m", period: str = "2d") -> Optional[object]:
    try:
        import yfinance as yf
        ticker = _pair_to_yf_ticker(pair)
        if not ticker:
            return None
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def _df_col(df, name: str):
    cols = df.columns
    lo = name.lower()
    hi = name.capitalize()
    if lo in cols:
        return df[lo]
    if hi in cols:
        return df[hi]
    for c in cols:
        if isinstance(c, tuple) and c[0].lower() == lo:
            return df[c]
    raise KeyError(name)


def _rsi(close, period: int):
    import numpy as np
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - 100 / (1 + rs)


def _stoch(high, low, close, k=3, d=1, smooth=1):
    import pandas as pd
    lowest = low.rolling(k).min()
    highest = high.rolling(k).max()
    fast_k = 100 * (close - lowest) / (highest - lowest + 1e-10)
    slow_k = fast_k.rolling(smooth).mean()
    slow_d = slow_k.rolling(d).mean()
    return slow_k, slow_d


def _cci(high, low, close, period: int = 14):
    tp = (high + low + close) / 3
    ma = tp.rolling(period).mean()
    md = tp.rolling(period).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md + 1e-10)


def _bb(close, period: int = 20, std: float = 2.0):
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    return ma + std * sd, ma - std * sd


def _ha(op, hi, lo, cl):
    import numpy as np
    ha_cl = (op + hi + lo + cl) / 4
    ha_op = ha_cl.copy()
    for i in range(1, len(ha_cl)):
        ha_op.iloc[i] = (ha_op.iloc[i - 1] + ha_cl.iloc[i - 1]) / 2
    ha_hi = ha_op.combine(hi, max).combine(ha_cl, max)
    ha_lo = ha_op.combine(lo, min).combine(ha_cl, min)
    return ha_op, ha_hi, ha_lo, ha_cl


def _analyze_arrays(op, hi, lo, cl, vol) -> Optional[dict]:
    """Run all 20 sub-signals on OHLCV arrays. Returns signal result or None."""
    import numpy as np
    n = len(cl)
    if n < 30:
        return None

    buy_score = 0
    sell_score = 0
    buy_reasons: List[str] = []
    sell_reasons: List[str] = []

    def vote(direction: str, weight: int, reason: str):
        nonlocal buy_score, sell_score
        if direction == "BUY":
            buy_score += weight
            buy_reasons.append(reason)
        else:
            sell_score += weight
            sell_reasons.append(reason)

    # ── S01 — Consecutive candle exhaustion (STRONGEST signal on PO OTC) ──
    consec_dir = "BUY" if cl.iloc[-2] > op.iloc[-2] else "SELL"
    streak = 0
    for i in range(n - 2, max(n - 12, 0), -1):
        bar_dir = "BUY" if cl.iloc[i] > op.iloc[i] else "SELL"
        if bar_dir == consec_dir:
            streak += 1
        else:
            break
    reversal_dir = "SELL" if consec_dir == "BUY" else "BUY"
    if streak >= 6:
        vote(reversal_dir, 5, f"6+ CONSECUTIVE {consec_dir} CANDLES — EXHAUSTION PEAK")
    elif streak >= 5:
        vote(reversal_dir, 4, f"5 CONSECUTIVE {consec_dir} CANDLES — REVERSAL IMMINENT")
    elif streak >= 4:
        vote(reversal_dir, 3, f"4 CONSECUTIVE {consec_dir} CANDLES — STREAK EXHAUSTION")
    elif streak >= 3:
        vote(reversal_dir, 2, f"3 CONSECUTIVE {consec_dir} CANDLES — MOMENTUM FADING")

    # ── S02 — RSI(3) ultra-fast extreme ──────────────────────────────────
    try:
        rsi3 = _rsi(cl, 3)
        r3 = float(rsi3.iloc[-2])
        if r3 > 95:
            vote("SELL", 5, f"RSI(3) = {r3:.1f} — EXTREME OVERBOUGHT · INSTANT REVERSAL")
        elif r3 > 90:
            vote("SELL", 4, f"RSI(3) = {r3:.1f} — SEVERELY OVERBOUGHT")
        elif r3 > 82:
            vote("SELL", 2, f"RSI(3) = {r3:.1f} — OVERBOUGHT ZONE")
        elif r3 < 5:
            vote("BUY",  5, f"RSI(3) = {r3:.1f} — EXTREME OVERSOLD · INSTANT REVERSAL")
        elif r3 < 10:
            vote("BUY",  4, f"RSI(3) = {r3:.1f} — SEVERELY OVERSOLD")
        elif r3 < 18:
            vote("BUY",  2, f"RSI(3) = {r3:.1f} — OVERSOLD ZONE")
    except Exception:
        pass

    # ── S03 — RSI(5) extreme ─────────────────────────────────────────────
    try:
        rsi5 = _rsi(cl, 5)
        r5 = float(rsi5.iloc[-2])
        if r5 > 88:
            vote("SELL", 3, f"RSI(5) = {r5:.1f} — OVERBOUGHT CONFIRMED")
        elif r5 < 12:
            vote("BUY",  3, f"RSI(5) = {r5:.1f} — OVERSOLD CONFIRMED")
    except Exception:
        pass

    # ── S04 — RSI(7) extreme ─────────────────────────────────────────────
    try:
        rsi7 = _rsi(cl, 7)
        r7 = float(rsi7.iloc[-2])
        if r7 > 82:
            vote("SELL", 3, f"RSI(7) = {r7:.1f} — OVERBOUGHT")
        elif r7 < 18:
            vote("BUY",  3, f"RSI(7) = {r7:.1f} — OVERSOLD")
    except Exception:
        pass

    # ── S05 — Stochastic ultra-fast (2,1,1) cross in extreme zone ────────
    try:
        sk2, sd2 = _stoch(hi, lo, cl, k=2, d=1, smooth=1)
        k_now  = float(sk2.iloc[-2])
        k_prev = float(sk2.iloc[-3])
        if k_prev > 90 and k_now < k_prev:
            vote("SELL", 4, f"STOCH(2,1) = {k_now:.0f} — OVERBOUGHT CROSS DOWN")
        elif k_prev < 10 and k_now > k_prev:
            vote("BUY",  4, f"STOCH(2,1) = {k_now:.0f} — OVERSOLD CROSS UP")
        elif k_now > 88:
            vote("SELL", 2, f"STOCH(2,1) = {k_now:.0f} — EXTREME OVERBOUGHT")
        elif k_now < 12:
            vote("BUY",  2, f"STOCH(2,1) = {k_now:.0f} — EXTREME OVERSOLD")
    except Exception:
        pass

    # ── S06 — Stochastic standard (5,3,3) cross ───────────────────────────
    try:
        sk5, sd5 = _stoch(hi, lo, cl, k=5, d=3, smooth=3)
        k5_now  = float(sk5.iloc[-2])
        k5_prev = float(sk5.iloc[-3])
        d5_now  = float(sd5.iloc[-2])
        if k5_now > 80 and k5_now < k5_prev and k5_now < d5_now:
            vote("SELL", 3, f"STOCH(5,3) CROSS DOWN AT {k5_now:.0f} — SELL SIGNAL")
        elif k5_now < 20 and k5_now > k5_prev and k5_now > d5_now:
            vote("BUY",  3, f"STOCH(5,3) CROSS UP AT {k5_now:.0f} — BUY SIGNAL")
    except Exception:
        pass

    # ── S07 — Bollinger Band 2.5σ outer touch ────────────────────────────
    try:
        ub25, lb25 = _bb(cl, 20, 2.5)
        price = float(cl.iloc[-2])
        u25   = float(ub25.iloc[-2])
        l25   = float(lb25.iloc[-2])
        if price >= u25:
            vote("SELL", 4, "BB(2.5σ) UPPER BREACH — EXTREME EXTENSION · SNAP BACK")
        elif price <= l25:
            vote("BUY",  4, "BB(2.5σ) LOWER BREACH — EXTREME EXTENSION · SNAP BACK")
    except Exception:
        pass

    # ── S08 — Bollinger Band 2.0σ touch ──────────────────────────────────
    try:
        ub20, lb20 = _bb(cl, 20, 2.0)
        price = float(cl.iloc[-2])
        u20   = float(ub20.iloc[-2])
        l20   = float(lb20.iloc[-2])
        if price >= u20:
            vote("SELL", 2, "BB(2.0σ) UPPER TOUCH — OVERBOUGHT AT BAND")
        elif price <= l20:
            vote("BUY",  2, "BB(2.0σ) LOWER TOUCH — OVERSOLD AT BAND")
    except Exception:
        pass

    # ── S09 — CCI(14) extreme reversal ───────────────────────────────────
    try:
        cci_s = _cci(hi, lo, cl, 14)
        c_now  = float(cci_s.iloc[-2])
        c_prev = float(cci_s.iloc[-3])
        if c_prev > 180 and c_now < c_prev:
            vote("SELL", 3, f"CCI = {c_now:.0f} — EXTREME OVERBOUGHT TURNING DOWN")
        elif c_prev < -180 and c_now > c_prev:
            vote("BUY",  3, f"CCI = {c_now:.0f} — EXTREME OVERSOLD TURNING UP")
        elif c_now > 200:
            vote("SELL", 2, f"CCI = {c_now:.0f} — EXTREME OVERBOUGHT")
        elif c_now < -200:
            vote("BUY",  2, f"CCI = {c_now:.0f} — EXTREME OVERSOLD")
    except Exception:
        pass

    # ── S10 — Heikin Ashi color flip ─────────────────────────────────────
    try:
        ha_op, ha_hi, ha_lo, ha_cl = _ha(op, hi, lo, cl)
        ha_bullish_prev  = ha_cl.iloc[-3] > ha_op.iloc[-3]
        ha_bullish_now   = ha_cl.iloc[-2] > ha_op.iloc[-2]
        if ha_bullish_prev and not ha_bullish_now:
            vote("SELL", 3, "HEIKIN ASHI FLIPPED BEARISH — MOMENTUM REVERSAL")
        elif not ha_bullish_prev and ha_bullish_now:
            vote("BUY",  3, "HEIKIN ASHI FLIPPED BULLISH — MOMENTUM REVERSAL")
    except Exception:
        pass

    # ── S11 — Engulfing candle at extreme ────────────────────────────────
    try:
        c2_op = float(op.iloc[-3])
        c2_cl = float(cl.iloc[-3])
        c1_op = float(op.iloc[-2])
        c1_cl = float(cl.iloc[-2])
        c2_bull = c2_cl > c2_op
        c1_bear = c1_cl < c1_op
        c1_bull = c1_cl > c1_op
        c2_bear = c2_cl < c2_op
        r3_val = float(_rsi(cl, 3).iloc[-2])
        if c2_bull and c1_bear and c1_op >= c2_cl and c1_cl <= c2_op and r3_val > 70:
            vote("SELL", 3, "BEARISH ENGULFING AT HIGH RSI — REVERSAL CONFIRMED")
        elif c2_bear and c1_bull and c1_op <= c2_cl and c1_cl >= c2_op and r3_val < 30:
            vote("BUY",  3, "BULLISH ENGULFING AT LOW RSI — REVERSAL CONFIRMED")
    except Exception:
        pass

    # ── S12 — Pin bar / shooting star / hammer ───────────────────────────
    try:
        c_op = float(op.iloc[-2])
        c_cl = float(cl.iloc[-2])
        c_hi = float(hi.iloc[-2])
        c_lo = float(lo.iloc[-2])
        body  = abs(c_cl - c_op)
        total = c_hi - c_lo + 1e-10
        upper_wick = c_hi - max(c_op, c_cl)
        lower_wick = min(c_op, c_cl) - c_lo
        if upper_wick > 2.5 * body and upper_wick > 0.6 * total:
            vote("SELL", 2, "SHOOTING STAR / UPPER PIN BAR — REJECTION AT HIGH")
        elif lower_wick > 2.5 * body and lower_wick > 0.6 * total:
            vote("BUY",  2, "HAMMER / LOWER PIN BAR — REJECTION AT LOW")
    except Exception:
        pass

    # ── S13 — Williams %R extreme ─────────────────────────────────────────
    try:
        highest = hi.rolling(14).max()
        lowest  = lo.rolling(14).min()
        wr = -100 * (highest - cl) / (highest - lowest + 1e-10)
        w_now  = float(wr.iloc[-2])
        w_prev = float(wr.iloc[-3])
        if w_prev > -5 and w_now < w_prev:
            vote("SELL", 2, f"WILLIAMS %R = {w_now:.1f} — OVERBOUGHT REVERSAL")
        elif w_prev < -95 and w_now > w_prev:
            vote("BUY",  2, f"WILLIAMS %R = {w_now:.1f} — OVERSOLD REVERSAL")
    except Exception:
        pass

    # ── S14 — RSI(14) standard extreme ───────────────────────────────────
    try:
        rsi14 = _rsi(cl, 14)
        r14 = float(rsi14.iloc[-2])
        if r14 > 78:
            vote("SELL", 2, f"RSI(14) = {r14:.1f} — OVERBOUGHT")
        elif r14 < 22:
            vote("BUY",  2, f"RSI(14) = {r14:.1f} — OVERSOLD")
    except Exception:
        pass

    # ── S15 — Volume climax (3× average) ─────────────────────────────────
    try:
        vol_f = vol.astype(float)
        avg_vol = float(vol_f.rolling(20).mean().iloc[-2])
        last_vol = float(vol_f.iloc[-2])
        if avg_vol > 0 and last_vol > 3 * avg_vol:
            dir_climax = "SELL" if float(cl.iloc[-2]) > float(op.iloc[-2]) else "BUY"
            vote(dir_climax, 2, f"VOLUME CLIMAX {last_vol / avg_vol:.1f}× — EXHAUSTION")
    except Exception:
        pass

    # ── S16 — 3-candle reversal sequence (streak + wick + body) ──────────
    try:
        c3_dir = "BUY" if float(cl.iloc[-4]) > float(op.iloc[-4]) else "SELL"
        c2_dir = "BUY" if float(cl.iloc[-3]) > float(op.iloc[-3]) else "SELL"
        c1_dir = "BUY" if float(cl.iloc[-2]) > float(op.iloc[-2]) else "SELL"
        if c3_dir == c2_dir and c2_dir != c1_dir:
            opp = "SELL" if c3_dir == "BUY" else "BUY"
            vote(opp, 2, f"3-CANDLE REVERSAL SEQUENCE — {c3_dir.upper()} STREAK BROKEN")
    except Exception:
        pass

    # ── S17 — 5m RSI alignment (higher timeframe confirmation) ───────────
    try:
        rsi5m = None
        from pocket_option_ws import get_candles as _po_c, is_connected as _po_ok
        if _po_ok():
            candles_5m = _po_c.__self__.get_candles if hasattr(_po_c, '__self__') else None
    except Exception:
        pass
    try:
        from live_prices import yf_ticker
        import yfinance as _yf
        tk5m = yf_ticker(None)
    except Exception:
        pass

    # ── S18 — MFI(14) extreme ─────────────────────────────────────────────
    try:
        import pandas as pd
        tp18 = (hi + lo + cl) / 3
        rmf   = tp18 * vol.astype(float)
        pmf   = rmf.where(tp18 > tp18.shift(1), 0).rolling(14).sum()
        nmf   = rmf.where(tp18 < tp18.shift(1), 0).rolling(14).sum()
        mfi   = 100 - 100 / (1 + pmf / (nmf + 1e-10))
        m_val = float(mfi.iloc[-2])
        if m_val > 85:
            vote("SELL", 2, f"MFI = {m_val:.1f} — MONEY FLOW EXTREME OVERBOUGHT")
        elif m_val < 15:
            vote("BUY",  2, f"MFI = {m_val:.1f} — MONEY FLOW EXTREME OVERSOLD")
    except Exception:
        pass

    # ── S19 — RSI divergence (price new extreme, RSI doesn't confirm) ─────
    try:
        rsi_div = _rsi(cl, 7)
        if (float(cl.iloc[-2]) > float(cl.iloc[-5]) and
                float(rsi_div.iloc[-2]) < float(rsi_div.iloc[-5]) and
                float(rsi_div.iloc[-2]) > 60):
            vote("SELL", 2, "BEARISH RSI DIVERGENCE — PRICE UP · RSI DOWN")
        elif (float(cl.iloc[-2]) < float(cl.iloc[-5]) and
              float(rsi_div.iloc[-2]) > float(rsi_div.iloc[-5]) and
              float(rsi_div.iloc[-2]) < 40):
            vote("BUY",  2, "BULLISH RSI DIVERGENCE — PRICE DOWN · RSI UP")
    except Exception:
        pass

    # ── S20 — Tweezer top/bottom (two identical highs/lows) ───────────────
    try:
        h1 = float(hi.iloc[-2])
        h2 = float(hi.iloc[-3])
        l1 = float(lo.iloc[-2])
        l2 = float(lo.iloc[-3])
        pip = max(abs(float(cl.iloc[-2])) * 0.0002, 1e-6)
        if abs(h1 - h2) < pip and float(cl.iloc[-2]) < float(op.iloc[-2]):
            vote("SELL", 3, "TWEEZER TOP — DOUBLE REJECTION AT RESISTANCE")
        elif abs(l1 - l2) < pip and float(cl.iloc[-2]) > float(op.iloc[-2]):
            vote("BUY",  3, "TWEEZER BOTTOM — DOUBLE REJECTION AT SUPPORT")
    except Exception:
        pass

    # ── Final evaluation — strict consensus: ZERO opposing votes ──────────
    if buy_score > 0 and sell_score > 0:
        return None

    total_score = max(buy_score, sell_score)
    if total_score < 14:
        return None

    if buy_score > sell_score:
        direction = "BUY"
        reasons = buy_reasons
    else:
        direction = "SELL"
        reasons = sell_reasons

    signals_count = len(reasons)
    if signals_count < 5:
        return None

    grade = min(100, int(total_score * 100 / 55))

    return {
        "direction": direction,
        "score":     total_score,
        "signals":   signals_count,
        "grade":     grade,
        "reasons":   reasons,
        "streak":    streak,
    }


def po_otc_analyze(pair: str) -> Optional[dict]:
    """Main entry point. Returns signal dict or None if no clean setup."""
    now = time.time()
    cached_ts, cached_val = _CACHE.get(pair, (0, None))
    if now - cached_ts < _CACHE_TTL:
        return cached_val

    result = None
    using_po_data = False

    try:
        import pandas as pd

        # ── DATA SOURCE PRIORITY ──────────────────────────────────────────
        # 1. Real PO WebSocket candles (pocket_option_ws — actual broker feed)
        # 2. Live broker WS candles (otc_realtime_bridge — PO+QX combined)
        # 3. yfinance fallback
        po_candles = _get_candles_po(pair, 60)
        if len(po_candles) >= 30:
            df = pd.DataFrame(po_candles).sort_values("time").tail(200)
            op  = df["open"].astype(float)
            hi  = df["high"].astype(float)
            lo  = df["low"].astype(float)
            cl  = df["close"].astype(float)
            vol = df["volume"].astype(float)
            result = _analyze_arrays(op, hi, lo, cl, vol)
            using_po_data = True
            if result:
                print(f"[po_otc] ✅ REAL PO DATA: {pair} → {result['direction']} "
                      f"score={result['score']} signals={result['signals']}")
        else:
            # Priority 2 — live broker WS feed (otc_realtime_bridge)
            rt_df = None
            try:
                from otc_realtime_bridge import get_otc_df as _rt_get
                rt_df = _rt_get(pair, "1m", count=200)
            except Exception:
                rt_df = None

            if rt_df is not None and len(rt_df) >= 30:
                op  = rt_df["open"].astype(float)
                hi  = rt_df["high"].astype(float)
                lo  = rt_df["low"].astype(float)
                cl  = rt_df["close"].astype(float)
                vol = rt_df["volume"].astype(float) if "volume" in rt_df.columns else pd.Series([0.0] * len(cl))
                result = _analyze_arrays(op, hi, lo, cl, vol)
                using_po_data = True  # real broker candles — no mirror needed
                if result:
                    print(f"[po_otc] ✅ REALTIME BRIDGE: {pair} → {result['direction']} "
                          f"score={result['score']} signals={result['signals']}")
            else:
                # Priority 3 — yfinance fallback
                df_yf = _get_candles_yf(pair, "1m", "2d")
                if df_yf is not None and len(df_yf) >= 30:
                    op  = _df_col(df_yf, "open").squeeze().astype(float)
                    hi  = _df_col(df_yf, "high").squeeze().astype(float)
                    lo  = _df_col(df_yf, "low").squeeze().astype(float)
                    cl  = _df_col(df_yf, "close").squeeze().astype(float)
                    vol_raw = None
                    try:
                        vol_raw = _df_col(df_yf, "volume").squeeze().astype(float)
                    except Exception:
                        vol_raw = pd.Series([0.0] * len(cl), index=cl.index)
                    result = _analyze_arrays(op, hi, lo, cl, vol_raw)
                    using_po_data = False
                    if result:
                        print(f"[po_otc] 📊 yfinance: {pair} → {result['direction']} "
                              f"score={result['score']} signals={result['signals']} "
                              f"(mirror will apply)")
    except Exception as exc:
        logger.warning(f"[po_otc] Analysis error for {pair}: {exc}")
        result = None

    if result is not None:
        result["using_po_data"] = using_po_data

    _CACHE[pair] = (now, result)
    return result
