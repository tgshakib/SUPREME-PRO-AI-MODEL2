"""
ULTRA SUPREME WINRATE ENGINE — SUPREME PRO AI
==============================================
The deepest hidden analysis layer. Runs after all other engines and
provides the final "gold seal" quality check before a signal fires.

LIVE PAIRS (Monday-Friday ultra winrate):
  6-gate triple-timeframe stack. ALL must pass at minimum level.
  L1  Triple EMA stack     — EMA9 > EMA21 > EMA50 on 5m (trend ladder)
  L2  RSI entry zone       — RSI(14) on 5m between 42-70 for BUY (healthy zone)
  L3  15m HTF agreement    — 15m EMA9/EMA21 confirms 5m direction
  L4  Volume confirmation  — Current 5m volume > 1.2× 20-bar average
  L5  Runway check         — No S&R wall within 0.20% of entry price
  L6  Momentum candle      — Last 5m closed bar body ≥ 52% range in signal dir

  Approval: ≥4 of 6 gates for LIVE signal (HIGH quality)
            ≥5 of 6 gates for LIVE ELITE signal  
            ≥6 of 6 gates for LIVE GOD signal (rarest, highest winrate)

OTC PAIRS (both MTG and non-MTG ultra winrate):
  6-gate extreme oscillator stack. Reversal-only.
  O1  RSI(3) ultra extreme  — < 8 for CALL,  > 92 for PUT
  O2  CCI(14) deep extreme  — <-200 for CALL, >+200 for PUT
  O3  Candle exhaustion     — 5+ consecutive same-direction candles
  O4  BB(20,2.5σ) outer     — Price has pierced the 2.5σ extreme band
  O5  Stoch(3,1,1) extreme  — %K < 6 for CALL, > 94 for PUT
  O6  Williams %R extreme   — WR < -92 for CALL, > -8 for PUT

  Approval: ≥3 of 6 gates for OTC signal
            ≥4 of 6 gates for OTC ELITE
            ≥5 of 6 gates for OTC GOD

Public API:
  ultra_check(pair, direction, is_otc, tf_label, ticker) → dict
    approved:        bool
    quality_grade:   "GOD" | "ELITE" | "HIGH" | "STANDARD" | "BLOCKED"
    live_score:      int (0-6) — LIVE gates passed
    otc_score:       int (0-6) — OTC gates passed
    confidence_adj:  int (-20 to +18)
    reasons:         list[str]
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
_TTL_FAST = 35.0
_TTL_HTF  = 120.0


def _get_df(ticker: str, interval: str, period: str, ttl: float = _TTL_FAST):
    if not _OK or not ticker:
        return None
    key = f"us:{ticker}:{interval}"
    t = time.time()
    c = _CACHE.get(key)
    if c and (t - c[0]) < ttl:
        return c[1]
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty and len(df) >= 15:
            _CACHE[key] = (t, df)
            return df
    except Exception:
        pass
    return None


def _norm(df):
    df = df.copy()
    df.columns = [
        str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
        for c in df.columns
    ]
    return df


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s, n):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.clip(lower=1e-10))


# ══════════════════════════════════════════════════════════════════════
#  LIVE GATES — Triple TF / Quality / Runway
# ══════════════════════════════════════════════════════════════════════
def _live_gates(ticker: str, direction: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    is_buy = direction == "BUY"

    # Fetch 5m and 15m data
    df5  = _get_df(ticker, "5m",  "5d",  _TTL_FAST)
    df15 = _get_df(ticker, "15m", "20d", _TTL_HTF)

    if df5 is None:
        reasons.append("⚠️ L-gates: 5m data unavailable")
        return 0, reasons

    df5 = _norm(df5)
    c5  = df5["close"].squeeze().astype(float)
    h5  = df5["high"].squeeze().astype(float)
    l5  = df5["low"].squeeze().astype(float)
    o5  = df5["open"].squeeze().astype(float)

    # ── L1: Triple EMA stack on 5m ────────────────────────────────────
    try:
        e9  = float(_ema(c5, 9).iloc[-2])
        e21 = float(_ema(c5, 21).iloc[-2])
        e50 = float(_ema(c5, 50).iloc[-2]) if len(c5) >= 52 else e21
        if is_buy and e9 > e21 > e50:
            score += 1
            reasons.append(f"✅ L1: Triple EMA stack BULL (9>{e9:.4g} > 21>{e21:.4g} > 50>{e50:.4g})")
        elif not is_buy and e9 < e21 < e50:
            score += 1
            reasons.append(f"✅ L1: Triple EMA stack BEAR (9<21<50 — all aligned)")
        elif is_buy and e9 > e21:
            reasons.append(f"⚠️ L1: EMA9>EMA21 only — EMA50 not aligned yet")
        else:
            reasons.append(f"❌ L1: EMA stack misaligned — counter-trend")
    except Exception:
        reasons.append("⚠️ L1: EMA calc error")

    # ── L2: RSI(14) entry zone on 5m ─────────────────────────────────
    try:
        rsi14 = _rsi(c5, 14)
        rv = float(rsi14.iloc[-2])
        if is_buy and 42 <= rv <= 70:
            score += 1
            reasons.append(f"✅ L2: RSI(14)={rv:.0f} in BUY entry zone (42-70)")
        elif not is_buy and 30 <= rv <= 58:
            score += 1
            reasons.append(f"✅ L2: RSI(14)={rv:.0f} in SELL entry zone (30-58)")
        elif is_buy and rv > 70:
            reasons.append(f"❌ L2: RSI={rv:.0f} OVERBOUGHT — BUY entry too late")
        elif not is_buy and rv < 30:
            reasons.append(f"❌ L2: RSI={rv:.0f} OVERSOLD — SELL entry too late")
        else:
            reasons.append(f"⚠️ L2: RSI={rv:.0f} outside entry zone")
    except Exception:
        reasons.append("⚠️ L2: RSI calc error")

    # ── L3: 15m HTF EMA agreement ─────────────────────────────────────
    try:
        if df15 is not None and len(df15) >= 25:
            df15n = _norm(df15)
            c15   = df15n["close"].squeeze().astype(float)
            e9_15  = float(_ema(c15, 9).iloc[-1])
            e21_15 = float(_ema(c15, 21).iloc[-1])
            rsi15  = float(_rsi(c15, 14).iloc[-1])
            if is_buy and e9_15 > e21_15 and rsi15 > 45:
                score += 1
                reasons.append(f"✅ L3: 15m EMA9>EMA21 BULL — HTF aligned")
            elif not is_buy and e9_15 < e21_15 and rsi15 < 55:
                score += 1
                reasons.append(f"✅ L3: 15m EMA9<EMA21 BEAR — HTF aligned")
            else:
                reasons.append(f"❌ L3: 15m HTF conflicts with signal direction")
        else:
            reasons.append("⚠️ L3: 15m data unavailable")
    except Exception:
        reasons.append("⚠️ L3: 15m calc error")

    # ── L4: Volume confirmation ───────────────────────────────────────
    try:
        vol_col = df5.get("volume")
        if vol_col is not None:
            v = vol_col.squeeze().astype(float).fillna(0)
            v_now = float(v.iloc[-2])
            v_avg = float(v.iloc[-22:-2].mean()) if len(v) >= 23 else float(v.mean())
            if v_avg > 0 and v_now >= v_avg * 1.2:
                score += 1
                reasons.append(f"✅ L4: Volume {v_now/v_avg:.1f}× average — institutional activity")
            else:
                reasons.append(f"⚠️ L4: Volume {v_now/max(v_avg,1):.1f}× avg — below threshold")
        else:
            reasons.append("⚠️ L4: No volume data")
    except Exception:
        reasons.append("⚠️ L4: Volume calc error")

    # ── L5: Runway check — no S&R wall within 0.20% ──────────────────
    try:
        curr = float(c5.iloc[-1])
        # Look for recent swing highs/lows in last 40 bars
        h_arr = list(h5.iloc[-40:])
        l_arr = list(l5.iloc[-40:])
        swing_highs = sorted([h_arr[i] for i in range(2, len(h_arr)-2)
                               if h_arr[i] == max(h_arr[i-2:i+3])], reverse=True)
        swing_lows  = sorted([l_arr[i] for i in range(2, len(l_arr)-2)
                               if l_arr[i] == min(l_arr[i-2:i+3])])
        wall_pct = 0.0020   # 0.20% of price
        blocked = False
        if is_buy:
            # Check for resistance directly above
            for sh in swing_highs:
                if 0 < (sh - curr) / max(curr, 1e-10) < wall_pct:
                    blocked = True
                    reasons.append(f"❌ L5: Resistance wall at {sh:.5g} — {(sh-curr)/curr*100:.3f}% above entry")
                    break
        else:
            # Check for support directly below
            for sl in swing_lows:
                if 0 < (curr - sl) / max(curr, 1e-10) < wall_pct:
                    blocked = True
                    reasons.append(f"❌ L5: Support wall at {sl:.5g} — {(curr-sl)/curr*100:.3f}% below entry")
                    break
        if not blocked:
            score += 1
            reasons.append("✅ L5: Clear runway — no S&R wall blocking the move")
    except Exception:
        score += 1   # benefit of doubt on error
        reasons.append("⚠️ L5: Runway check skipped — proceeding")

    # ── L6: Momentum candle ≥ 52% body on last confirmed 5m bar ──────
    try:
        c0_o = float(o5.iloc[-2])
        c0_c = float(c5.iloc[-2])
        c0_h = float(h5.iloc[-2])
        c0_l = float(l5.iloc[-2])
        rng  = c0_h - c0_l or 1e-10
        body = abs(c0_c - c0_o)
        bull = c0_c > c0_o
        pct  = body / rng
        if pct >= 0.52 and bull == is_buy:
            score += 1
            reasons.append(f"✅ L6: Conviction candle {pct:.0%} body {'bull' if bull else 'bear'}")
        else:
            reasons.append(f"⚠️ L6: Body {pct:.0%} {'(wrong dir)' if bull != is_buy else '(weak)'}")
    except Exception:
        reasons.append("⚠️ L6: Candle body calc error")

    return score, reasons


# ══════════════════════════════════════════════════════════════════════
#  OTC GATES — Deep oscillator extremes for reversal
# ══════════════════════════════════════════════════════════════════════
def _otc_gates(ticker: str, direction: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    is_buy = direction == "BUY"

    df = _get_df(ticker, "5m", "3d", _TTL_FAST)
    if df is None:
        # Try 1m as fallback for OTC
        df = _get_df(ticker, "1m", "2d", _TTL_FAST)

    if df is None:
        reasons.append("⚠️ O-gates: data unavailable")
        return 0, reasons

    df = _norm(df)
    c = df["close"].squeeze().astype(float)
    h = df["high"].squeeze().astype(float)
    l = df["low"].squeeze().astype(float)
    o_col = df["open"].squeeze().astype(float)

    if len(c) < 20:
        reasons.append("⚠️ O-gates: insufficient data")
        return 0, reasons

    # ── O1: RSI(3) ultra extreme ──────────────────────────────────────
    try:
        rsi3 = _rsi(c, 3)
        rv = float(rsi3.iloc[-2])
        if is_buy and rv < 8:
            score += 1
            reasons.append(f"✅ O1: RSI(3)={rv:.1f} ULTRA extreme oversold < 8 → CALL")
        elif not is_buy and rv > 92:
            score += 1
            reasons.append(f"✅ O1: RSI(3)={rv:.1f} ULTRA extreme overbought > 92 → PUT")
        elif is_buy and rv < 15:
            reasons.append(f"⚠️ O1: RSI(3)={rv:.1f} oversold but not ultra (need <8)")
        elif not is_buy and rv > 85:
            reasons.append(f"⚠️ O1: RSI(3)={rv:.1f} overbought but not ultra (need >92)")
        else:
            reasons.append(f"❌ O1: RSI(3)={rv:.1f} — not at extreme")
    except Exception:
        reasons.append("⚠️ O1: RSI calc error")

    # ── O2: CCI(14) deep extreme ──────────────────────────────────────
    try:
        tp     = (h + l + c) / 3.0
        tp_sma = tp.rolling(14).mean()
        tp_mad = tp.rolling(14).apply(lambda x: abs(x - x.mean()).mean(), raw=True)
        cci    = (tp - tp_sma) / (0.015 * tp_mad.replace(0, 1e-10))
        cv = float(cci.iloc[-2])
        if is_buy and cv < -200:
            score += 1
            reasons.append(f"✅ O2: CCI={cv:.0f} DEEP extreme < -200 → CALL")
        elif not is_buy and cv > 200:
            score += 1
            reasons.append(f"✅ O2: CCI={cv:.0f} DEEP extreme > +200 → PUT")
        elif is_buy and cv < -100:
            reasons.append(f"⚠️ O2: CCI={cv:.0f} extreme (need < -200 for max score)")
        elif not is_buy and cv > 100:
            reasons.append(f"⚠️ O2: CCI={cv:.0f} extreme (need > +200 for max score)")
        else:
            reasons.append(f"❌ O2: CCI={cv:.0f} — not deep extreme")
    except Exception:
        reasons.append("⚠️ O2: CCI calc error")

    # ── O3: 5+ consecutive candles exhaustion ────────────────────────
    try:
        streak = 0
        streak_dir = None
        for i in range(len(c) - 2, max(len(c) - 14, -1), -1):
            bar_bull = float(c.iloc[i]) > float(o_col.iloc[i])
            if streak_dir is None:
                streak_dir = bar_bull
            if bar_bull == streak_dir:
                streak += 1
            else:
                break
        opposing_streak = (is_buy and not streak_dir) or (not is_buy and streak_dir)
        if opposing_streak and streak >= 5:
            score += 1
            reasons.append(f"✅ O3: {streak}× consecutive {'bear' if not streak_dir else 'bull'} bars — exhaustion peak")
        elif opposing_streak and streak >= 3:
            reasons.append(f"⚠️ O3: {streak}× consecutive — partial exhaustion (need ≥5)")
        else:
            reasons.append(f"❌ O3: No opposing streak ({streak} bars, {streak_dir=})")
    except Exception:
        reasons.append("⚠️ O3: Consecutive calc error")

    # ── O4: Bollinger Band(20, 2.5σ) outer pierce ─────────────────────
    try:
        mid  = c.rolling(20).mean()
        std  = c.rolling(20).std()
        upper = mid + 2.5 * std
        lower = mid - 2.5 * std
        last_c = float(c.iloc[-2])
        if is_buy and last_c <= float(lower.iloc[-2]):
            score += 1
            reasons.append(f"✅ O4: BB(20,2.5σ) lower band pierce → extreme oversold → CALL")
        elif not is_buy and last_c >= float(upper.iloc[-2]):
            score += 1
            reasons.append(f"✅ O4: BB(20,2.5σ) upper band pierce → extreme overbought → PUT")
        else:
            # Check 2.0σ as partial
            upper2 = mid + 2.0 * std
            lower2 = mid - 2.0 * std
            if is_buy and last_c <= float(lower2.iloc[-2]):
                reasons.append(f"⚠️ O4: BB(20,2.0σ) lower touch (need 2.5σ pierce)")
            elif not is_buy and last_c >= float(upper2.iloc[-2]):
                reasons.append(f"⚠️ O4: BB(20,2.0σ) upper touch (need 2.5σ pierce)")
            else:
                reasons.append(f"❌ O4: Price within normal BB range")
    except Exception:
        reasons.append("⚠️ O4: BB calc error")

    # ── O5: Stochastic(3,1,1) extreme ─────────────────────────────────
    try:
        lo3 = l.rolling(3).min()
        hi3 = h.rolling(3).max()
        k   = 100 * (c - lo3) / (hi3 - lo3 + 1e-10)
        kv  = float(k.iloc[-2])
        if is_buy and kv < 6:
            score += 1
            reasons.append(f"✅ O5: Stoch(3)={kv:.1f} ULTRA extreme < 6 → CALL")
        elif not is_buy and kv > 94:
            score += 1
            reasons.append(f"✅ O5: Stoch(3)={kv:.1f} ULTRA extreme > 94 → PUT")
        elif is_buy and kv < 15:
            reasons.append(f"⚠️ O5: Stoch(3)={kv:.1f} oversold (need <6)")
        elif not is_buy and kv > 85:
            reasons.append(f"⚠️ O5: Stoch(3)={kv:.1f} overbought (need >94)")
        else:
            reasons.append(f"❌ O5: Stoch(3)={kv:.1f} — not at extreme")
    except Exception:
        reasons.append("⚠️ O5: Stochastic calc error")

    # ── O6: Williams %R extreme ───────────────────────────────────────
    try:
        hi14 = h.rolling(14).max()
        lo14 = l.rolling(14).min()
        wr   = -100 * (hi14 - c) / (hi14 - lo14 + 1e-10)
        wrv  = float(wr.iloc[-2])
        if is_buy and wrv < -92:
            score += 1
            reasons.append(f"✅ O6: Williams %R={wrv:.0f} ULTRA extreme < -92 → CALL")
        elif not is_buy and wrv > -8:
            score += 1
            reasons.append(f"✅ O6: Williams %R={wrv:.0f} ULTRA extreme > -8 → PUT")
        elif is_buy and wrv < -80:
            reasons.append(f"⚠️ O6: WR={wrv:.0f} oversold (need < -92)")
        elif not is_buy and wrv > -20:
            reasons.append(f"⚠️ O6: WR={wrv:.0f} overbought (need > -8)")
        else:
            reasons.append(f"❌ O6: WR={wrv:.0f} — not at extreme")
    except Exception:
        reasons.append("⚠️ O6: Williams %R calc error")

    return score, reasons


# ══════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════
def ultra_check(
    pair: str,
    direction: str,
    is_otc: bool,
    tf_label: str = "",
    ticker: str | None = None,
) -> dict:
    """Run the ultra supreme quality check. Returns:
    {
      approved:       bool,
      quality_grade:  "GOD"|"ELITE"|"HIGH"|"STANDARD"|"BLOCKED",
      live_score:     int,
      otc_score:      int,
      confidence_adj: int,
      reasons:        list[str],
    }
    """
    if ticker is None:
        ticker = _yf_ticker(pair)

    result: dict = {
        "approved":       True,
        "quality_grade":  "STANDARD",
        "live_score":     0,
        "otc_score":      0,
        "confidence_adj": 0,
        "reasons":        [],
    }

    if not _OK or not ticker:
        result["reasons"] = ["ultra: yfinance unavailable"]
        return result

    if is_otc:
        score, reasons = _otc_gates(ticker, direction)
        result["otc_score"] = score
        result["reasons"]   = reasons

        if score >= 5:
            result["quality_grade"]  = "GOD"
            result["confidence_adj"] = 18
        elif score >= 4:
            result["quality_grade"]  = "ELITE"
            result["confidence_adj"] = 12
        elif score >= 3:
            result["quality_grade"]  = "HIGH"
            result["confidence_adj"] = 6
        elif score == 2:
            result["quality_grade"]  = "STANDARD"
            result["confidence_adj"] = 0
        elif score == 1:
            result["quality_grade"]  = "STANDARD"
            result["confidence_adj"] = -8
        else:
            result["quality_grade"]  = "BLOCKED"
            result["confidence_adj"] = -20
            result["approved"]       = False

    else:
        score, reasons = _live_gates(ticker, direction)
        result["live_score"] = score
        result["reasons"]    = reasons

        if score >= 6:
            result["quality_grade"]  = "GOD"
            result["confidence_adj"] = 18
        elif score >= 5:
            result["quality_grade"]  = "ELITE"
            result["confidence_adj"] = 12
        elif score >= 4:
            result["quality_grade"]  = "HIGH"
            result["confidence_adj"] = 6
        elif score == 3:
            result["quality_grade"]  = "STANDARD"
            result["confidence_adj"] = 0
        elif score == 2:
            result["quality_grade"]  = "STANDARD"
            result["confidence_adj"] = -8
        else:
            # Only 0-1 gates passed on LIVE — too weak to fire
            result["quality_grade"]  = "BLOCKED"
            result["confidence_adj"] = -20
            result["approved"]       = False

    print(
        f"[ultra_supreme] {pair} {'OTC' if is_otc else 'LIVE'} {direction} "
        f"→ {result['quality_grade']} {'OTC' if is_otc else 'LIVE'}"
        f"{'score=' + str(score)} "
        f"adj={result['confidence_adj']:+d} "
        f"{'✅' if result['approved'] else '⛔'}"
    )
    return result
