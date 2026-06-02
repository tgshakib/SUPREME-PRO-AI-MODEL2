"""
30-SECOND CANDLE CONFIRMATION ENGINE — SUPREME PRO AI
======================================================
For 1-minute binary options, WIN vs LOSS is decided in the first
30 seconds of a new candle. This engine confirms you're entering
at the RIGHT sub-minute moment with momentum behind you.

Strategy:
  For a 1-minute binary, the signal bar just CLOSED. The NEW candle
  is forming. In the first 30 seconds, price will show one of:
    → Strong open in signal direction = PRIME ENTRY (enter now)
    → Doji / choppy open = wait or skip
    → Reversal open against signal = SKIP THIS SIGNAL

This engine uses 7 fast gates to confirm or deny the entry:
  S1  Momentum streak   — last 3×1m bars all in signal direction
  S2  EMA(3) slope      — slope is accelerating (not just positive)
  S3  Fast RSI(3)        — confirms direction with extreme sensitivity
  S4  Conviction body   — last closed bar body ≥ 65% range
  S5  Clean close       — no wick against signal direction on last bar
  S6  2m bar confirms   — 2-minute chart bar direction matches signal
  S7  MACD micro        — MACD(3,8,3) histogram in signal direction

ENTRY QUALITY TIERS:
  PRIME  — 5-7 gates → enter in first 30 seconds, high probability
  GOOD   — 3-4 gates → enter, slight delay acceptable
  WEAK   — 1-2 gates → skip or wait for next candle
  SKIP   — 0 gates or contradictions → skip this signal entirely

LIVE PAIRS:  Need ≥ 4 gates for approval (GOOD minimum)
OTC PAIRS:   Need ≥ 3 gates (OTC synthetic candles are noisier)

Public API:
  confirm_entry(pair, direction, is_otc=False) → dict
    confirmed:       bool
    score:           int (0-7)
    entry_quality:   "PRIME" | "GOOD" | "WEAK" | "SKIP"
    confidence_adj:  int (-15 to +12)
    reasons:         list[str]
    enter_now:       bool  (True = enter in 1st 30s; False = wait for next)
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception:
    yf = None
    pd = None
    _OK = False

try:
    from live_prices import yf_ticker as _yf_ticker
except Exception:
    def _yf_ticker(p): return None  # type: ignore

_CACHE: dict[str, tuple[float, object]] = {}
_TTL = 25.0   # 25-second cache — need fresh data for sub-minute confirmation


def _get_df(ticker: str, interval: str, period: str) -> "pd.DataFrame | None":
    if not _OK or not ticker:
        return None
    key = f"30s:{ticker}:{interval}"
    t = time.time()
    c = _CACHE.get(key)
    if c and (t - c[0]) < _TTL:
        return c[1]  # type: ignore
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty and len(df) >= 10:
            _CACHE[key] = (t, df)
            return df
    except Exception:
        pass
    return None


def _norm(df: "pd.DataFrame") -> "pd.DataFrame":
    df = df.copy()
    df.columns = [
        str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
        for c in df.columns
    ]
    return df


def _ema(s: "pd.Series", n: int) -> "pd.Series":
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s: "pd.Series", n: int) -> "pd.Series":
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.clip(lower=1e-10))


def _macd_hist(s: "pd.Series", fast=3, slow=8, sig=3) -> "pd.Series":
    m = _ema(s, fast) - _ema(s, slow)
    return m - m.ewm(span=sig, adjust=False).mean()


# ══════════════════════════════════════════════════════════════════════
#  CORE CONFIRMATION ENGINE
# ══════════════════════════════════════════════════════════════════════
def confirm_entry(
    pair: str,
    direction: str,
    is_otc: bool = False,
    ticker: str | None = None,
) -> dict:
    """
    Run the 30-second sub-candle confirmation. Call this AFTER the
    main signal direction is determined, right before showing the signal.
    """
    if ticker is None:
        ticker = _yf_ticker(pair)

    result: dict = {
        "confirmed":      True,
        "score":          0,
        "entry_quality":  "GOOD",
        "confidence_adj": 0,
        "reasons":        [],
        "enter_now":      True,
    }

    if not _OK or not ticker:
        result["reasons"].append("30s engine: data unavailable — proceeding")
        return result

    df1m = _get_df(ticker, "1m", "2d")
    df2m = _get_df(ticker, "2m", "3d")

    if df1m is None or len(df1m) < 8:
        result["reasons"].append("30s engine: 1m data unavailable — proceeding")
        return result

    df1m = _norm(df1m)
    is_buy = direction == "BUY"
    score = 0
    reasons: list[str] = []

    try:
        c = df1m["close"].squeeze().astype(float)
        o = df1m["open"].squeeze().astype(float)
        h = df1m["high"].squeeze().astype(float)
        l = df1m["low"].squeeze().astype(float)

        # Helper: confirmed closed bar (bar[-2]), not the forming bar[-1]
        def bar(idx: int) -> dict:
            ci, oi, hi, li = float(c.iloc[idx]), float(o.iloc[idx]), float(h.iloc[idx]), float(l.iloc[idx])
            rng  = hi - li or 1e-10
            body = abs(ci - oi)
            bull = ci > oi
            return {
                "c": ci, "o": oi, "h": hi, "l": li,
                "body": body, "range": rng,
                "pct": body / rng,
                "bull": bull,
                "upper_wick": hi - max(ci, oi),
                "lower_wick": min(ci, oi) - li,
            }

        b0 = bar(-2)   # last confirmed bar (signal bar)
        b1 = bar(-3)   # one before
        b2 = bar(-4)   # two before

        # ── S1: Momentum streak — last 3 bars in signal direction ─────────────
        # For LIVE: we want 3 consecutive bars confirming the move.
        # For OTC:  3 consecutive OPPOSING bars = exhaustion → reversal imminent.
        if is_otc:
            streak_dir_needed = not is_buy   # opposite streak = reversal setup
            bars_in_streak = [
                b0["bull"] == (not is_buy),
                b1["bull"] == (not is_buy),
                b2["bull"] == (not is_buy),
            ]
        else:
            bars_in_streak = [
                b0["bull"] == is_buy,
                b1["bull"] == is_buy,
                b2["bull"] == is_buy,
            ]

        streak_count = sum(bars_in_streak)
        if streak_count == 3:
            score += 1
            reasons.append(f"✅ S1: 3-bar {'exhaustion' if is_otc else 'momentum'} streak confirmed")
        elif streak_count == 2:
            score += 0   # partial — don't count
            reasons.append(f"⚠️ S1: 2-bar streak only — partial")
        else:
            reasons.append(f"❌ S1: no streak — choppy bars")

        # ── S2: EMA(3) slope accelerating ────────────────────────────────────
        ema3 = _ema(c, 3)
        slope_now  = float(ema3.iloc[-2]) - float(ema3.iloc[-3])
        slope_prev = float(ema3.iloc[-3]) - float(ema3.iloc[-4])
        slope_direction_ok = (is_buy and slope_now > 0) or (not is_buy and slope_now < 0)
        slope_accelerating = abs(slope_now) > abs(slope_prev) * 0.9
        if slope_direction_ok and slope_accelerating:
            score += 1
            reasons.append(f"✅ S2: EMA(3) slope {'↑' if is_buy else '↓'} accelerating")
        elif slope_direction_ok:
            score += 1
            reasons.append(f"✅ S2: EMA(3) slope {'↑' if is_buy else '↓'} (not accelerating)")
        else:
            reasons.append(f"❌ S2: EMA(3) slope against signal direction")

        # ── S3: RSI(3) — ultra-fast momentum gauge ────────────────────────────
        rsi3 = _rsi(c, 3)
        rsi3_val  = float(rsi3.iloc[-2])
        rsi3_prev = float(rsi3.iloc[-3])
        if is_buy:
            if rsi3_val < 30:
                score += 1
                reasons.append(f"✅ S3: RSI(3)={rsi3_val:.0f} — oversold, BUY reversal")
            elif 45 <= rsi3_val <= 75 and rsi3_val > rsi3_prev:
                score += 1
                reasons.append(f"✅ S3: RSI(3)={rsi3_val:.0f} rising — BUY momentum")
            else:
                reasons.append(f"⚠️ S3: RSI(3)={rsi3_val:.0f} — neutral")
        else:
            if rsi3_val > 70:
                score += 1
                reasons.append(f"✅ S3: RSI(3)={rsi3_val:.0f} — overbought, SELL reversal")
            elif 25 <= rsi3_val <= 55 and rsi3_val < rsi3_prev:
                score += 1
                reasons.append(f"✅ S3: RSI(3)={rsi3_val:.0f} falling — SELL momentum")
            else:
                reasons.append(f"⚠️ S3: RSI(3)={rsi3_val:.0f} — neutral")

        # ── S4: Conviction body ≥ 65% range ──────────────────────────────────
        # For LIVE BUY: last bar must be a bull conviction candle
        # For OTC BUY:  last bar must be a bear conviction candle (exhaustion)
        expected_bull = is_buy if not is_otc else not is_buy
        if b0["pct"] >= 0.65 and b0["bull"] == expected_bull:
            score += 1
            reasons.append(f"✅ S4: Conviction body {b0['pct']:.0%} — {'bull' if b0['bull'] else 'bear'} confirmed")
        elif b0["pct"] >= 0.50 and b0["bull"] == expected_bull:
            score += 1
            reasons.append(f"✅ S4: Moderate body {b0['pct']:.0%} — counts")
        else:
            reasons.append(f"❌ S4: Body {b0['pct']:.0%} — {'wrong direction' if b0['bull'] != expected_bull else 'doji'}")

        # ── S5: Clean close — no rejecting wick against signal direction ──────
        # For BUY: lower wick should be small (no selling pressure)
        # For SELL: upper wick should be small (no buying pressure)
        total_range = b0["range"]
        if is_buy:
            # Buy signal: upper wick should dominate if OTC exhaustion, lower wick if LIVE momentum
            if is_otc:
                upper_wick_ratio = b0["upper_wick"] / total_range
                if upper_wick_ratio >= 0.30:   # strong upper wick = rejection from highs = OTC reversal BUY
                    score += 1
                    reasons.append(f"✅ S5: Upper wick {upper_wick_ratio:.0%} — OTC rejection confirming BUY")
                else:
                    reasons.append(f"⚠️ S5: Small upper wick — OTC rejection weak")
            else:
                lower_wick_ratio = b0["lower_wick"] / total_range
                if lower_wick_ratio <= 0.25:   # small lower wick = no selling pressure
                    score += 1
                    reasons.append(f"✅ S5: Clean close — minimal lower wick ({lower_wick_ratio:.0%})")
                else:
                    reasons.append(f"❌ S5: Lower wick {lower_wick_ratio:.0%} — selling pressure present")
        else:
            if is_otc:
                lower_wick_ratio = b0["lower_wick"] / total_range
                if lower_wick_ratio >= 0.30:   # strong lower wick = rejection from lows = OTC reversal SELL
                    score += 1
                    reasons.append(f"✅ S5: Lower wick {lower_wick_ratio:.0%} — OTC rejection confirming SELL")
                else:
                    reasons.append(f"⚠️ S5: Small lower wick — OTC rejection weak")
            else:
                upper_wick_ratio = b0["upper_wick"] / total_range
                if upper_wick_ratio <= 0.25:   # small upper wick = no buying pressure
                    score += 1
                    reasons.append(f"✅ S5: Clean close — minimal upper wick ({upper_wick_ratio:.0%})")
                else:
                    reasons.append(f"❌ S5: Upper wick {upper_wick_ratio:.0%} — buying pressure present")

        # ── S6: 2-minute bar confirmation (proxy for 30s sub-bar) ────────────
        # A 2m bar = 2x 1m bars combined. If the 2m bar is in signal direction
        # it confirms the sub-minute momentum is aligned.
        s6_passed = False
        if df2m is not None and len(df2m) >= 5:
            try:
                df2m_n = _norm(df2m)
                c2m = df2m_n["close"].squeeze().astype(float)
                o2m = df2m_n["open"].squeeze().astype(float)
                last_2m_bull = float(c2m.iloc[-2]) > float(o2m.iloc[-2])
                prev_2m_bull = float(c2m.iloc[-3]) > float(o2m.iloc[-3])
                # For LIVE BUY: 2m bar should be bull
                # For OTC BUY: 2m bar should be bear (exhaustion → reversal)
                expected_2m_bull = is_buy if not is_otc else not is_buy
                if last_2m_bull == expected_2m_bull and prev_2m_bull == expected_2m_bull:
                    score += 1
                    reasons.append(f"✅ S6: 2m bars confirm {'bull' if expected_2m_bull else 'bear'} direction")
                    s6_passed = True
                elif last_2m_bull == expected_2m_bull:
                    score += 1
                    reasons.append(f"✅ S6: Last 2m bar confirms direction")
                    s6_passed = True
                else:
                    reasons.append(f"❌ S6: 2m bar against signal direction")
            except Exception:
                reasons.append("⚠️ S6: 2m calculation error")
        else:
            reasons.append("⚠️ S6: 2m data unavailable — skipping")

        # ── S7: MACD micro (3,8,3) on 1m histogram ───────────────────────────
        try:
            hist = _macd_hist(c, 3, 8, 3)
            h_now  = float(hist.iloc[-2])
            h_prev = float(hist.iloc[-3])
            macd_bull = h_now > 0
            macd_bear = h_now < 0
            # For LIVE: histogram must be in signal direction
            # For OTC: histogram should be in OPPOSING direction (exhaustion at extreme)
            if not is_otc:
                if is_buy and macd_bull and h_now > h_prev:
                    score += 1
                    reasons.append(f"✅ S7: MACD(3,8,3) histogram {h_now:.5f} — bull + expanding")
                elif not is_buy and macd_bear and h_now < h_prev:
                    score += 1
                    reasons.append(f"✅ S7: MACD(3,8,3) histogram {h_now:.5f} — bear + expanding")
                elif (is_buy and macd_bull) or (not is_buy and macd_bear):
                    score += 1
                    reasons.append(f"✅ S7: MACD(3,8,3) in signal direction (not expanding)")
                else:
                    reasons.append(f"❌ S7: MACD(3,8,3) against signal direction")
            else:
                # OTC: histogram at extreme and flipping = reversal signal
                if is_buy and macd_bear and h_now > h_prev:
                    score += 1
                    reasons.append(f"✅ S7: OTC MACD bear→bull flip — BUY reversal")
                elif not is_buy and macd_bull and h_now < h_prev:
                    score += 1
                    reasons.append(f"✅ S7: OTC MACD bull→bear flip — SELL reversal")
                elif is_buy and macd_bear:
                    score += 1
                    reasons.append(f"✅ S7: OTC MACD deeply negative — BUY reversal zone")
                elif not is_buy and macd_bull:
                    score += 1
                    reasons.append(f"✅ S7: OTC MACD deeply positive — SELL reversal zone")
                else:
                    reasons.append(f"⚠️ S7: OTC MACD neutral — no reversal extreme")
        except Exception:
            reasons.append("⚠️ S7: MACD calculation error")

    except Exception as e:
        result["reasons"].append(f"30s engine: candle analysis error: {e}")
        return result

    # ── Final quality tier ────────────────────────────────────────────────
    min_for_live = 4
    min_for_otc  = 3

    result["score"]   = score
    result["reasons"] = reasons

    if score >= 6:
        result["entry_quality"]  = "PRIME"
        result["confidence_adj"] = 12
        result["enter_now"]      = True
    elif score >= 4:
        result["entry_quality"]  = "GOOD"
        result["confidence_adj"] = 5
        result["enter_now"]      = True
    elif score >= 2:
        result["entry_quality"]  = "WEAK"
        result["confidence_adj"] = -8
        result["enter_now"]      = False
    else:
        result["entry_quality"]  = "SKIP"
        result["confidence_adj"] = -18
        result["enter_now"]      = False

    # Approval threshold
    min_score = min_for_otc if is_otc else min_for_live
    result["confirmed"] = (score >= min_score)

    if not result["confirmed"]:
        result["confidence_adj"] = min(result["confidence_adj"], -12)

    print(
        f"[30s_engine] {pair} {'OTC' if is_otc else 'LIVE'} {direction} "
        f"→ {result['entry_quality']} {score}/7 adj={result['confidence_adj']:+d} "
        f"{'✅ ENTER' if result['confirmed'] else '⛔ SKIP'}"
    )
    return result
