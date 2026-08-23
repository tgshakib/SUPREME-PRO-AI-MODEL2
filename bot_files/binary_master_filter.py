"""
BINARY MASTER FILTER ENGINE — SUPREME PRO AI
=============================================
The ultimate quality gate between analysis engines and signal output.
Ensures only highest-probability setups fire. Runs AFTER all engines
have voted and direction is determined — acts as the final arbiter.

OTC STRATEGY (Synthetic broker candles):
  Pure reversal at mathematical extremes. Never trend-following.
  Requires: oscillator extreme + candle exhaustion + zero opposing signals.

LIVE STRATEGY (Real market candles):
  Momentum-aligned entries with trend confirmation.
  Requires: trend alignment + ATR in healthy range + no news.

Hard Block Conditions:
  1. Major news window ±15 min (13:30, 08:30, 18:00 UTC high-impact slots)
  2. Friday NY close (19:45-21:15 UTC)
  3. Monday gap zone (20:45-23:59 UTC Sunday)
  4. ATR spike > 2.8× 14-bar average (news spike / manipulation)
  5. Doji or spinning top on last closed candle — no conviction
  6. Engine disagreement ratio > 60% (more oppose than agree)

OTC Reversal Quality Gates:
  A. RSI(3) extreme: < 12 for CALL, > 88 for PUT  [strongest OTC signal]
  B. 5+ consecutive candles same direction          [exhaustion peak]
  C. BB(20,2.0) outer band pierce or touch
  D. RSI(7) < 18 for CALL, > 82 for PUT
  Minimum 2 of A-D must be true, else confidence penalty.

LIVE Momentum Quality Gates:
  E. EMA9 > EMA21 for BUY, EMA9 < EMA21 for SELL  [trend aligned]
  F. RSI(14) between 42-68 for BUY (not overbought entry)
  G. ATR(14) between 0.7×avg and 2.2×avg            [healthy volatility]
  H. Candle close beyond prior bar's midpoint         [conviction close]

Public API:
  binary_master_check(pair, direction, is_otc, tf_label, ticker,
                      engine_agree, engine_oppose, total_engines) → dict
    approved:         bool
    quality_tier:     "ELITE" | "HIGH" | "STANDARD" | "WEAK" | "BLOCKED"
    confidence_adj:   int  (-25 to +15)
    block_reason:     str | None
    otc_gate_score:   int  (0-4 OTC gates passed)
    live_gate_score:  int  (0-4 LIVE gates passed)
    regime:           str
    news_safe:        bool
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

try:
    from live_prices import yf_ticker as _yf_ticker
except Exception:
    def _yf_ticker(p): return None  # type: ignore

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 40.0


# ══════════════════════════════════════════════════════════════════════
#  HIGH-IMPACT NEWS BLACKOUT SLOTS (UTC hour, minute, label)
#  Block window = ±15 minutes around each slot.
# ══════════════════════════════════════════════════════════════════════
_NEWS_SLOTS: list[tuple[int, int, str]] = [
    (7,  0,  "EU GDP/CPI"),
    (7, 45,  "ECB/BOE statement"),
    (8, 30,  "US NFP/CPI/GDP — EXTREME"),
    (9,  0,  "CA data/EU PMI"),
    (10,  0, "US ISM/PMI/Consumer"),
    (12, 30, "BoC rate decision"),
    (13, 30, "US Core PCE/Retail Sales"),
    (14,  0, "US Factory Orders"),
    (15,  0, "US ISM Services"),
    (17, 30, "BoE/Fed speeches"),
    (18,  0, "FOMC minutes/rate decision — EXTREME"),
    (18, 30, "FOMC statement"),
    (19,  0, "Fed Chair presser"),
    (2,  30, "RBA/BOJ decision"),
    (3,  30, "BOJ/China data"),
    (23, 50, "BOJ policy vote"),
]
_NEWS_BLOCK_MINUTES = 15


def _is_news_window() -> tuple[bool, str]:
    """True + reason if within ±15 min of a high-impact news slot."""
    now = datetime.utcnow()
    now_mins = now.hour * 60 + now.minute
    for (h, m, label) in _NEWS_SLOTS:
        slot_mins = h * 60 + m
        if abs(now_mins - slot_mins) <= _NEWS_BLOCK_MINUTES:
            return True, label
    return False, ""


def _is_friday_close() -> bool:
    now = datetime.utcnow()
    return now.weekday() == 4 and (now.hour * 60 + now.minute) >= (19 * 60 + 45)


def _is_monday_gap() -> bool:
    """Block Sunday night (gap opens) AND Monday morning until 10:00 UTC.
    After a weekend, EMAs haven't caught up to the gap price — 1m signals are unreliable.
    """
    now = datetime.utcnow()
    # Sunday 20:45+ UTC — market just opened with gap
    if now.weekday() == 6 and (now.hour * 60 + now.minute) >= (20 * 60 + 45):
        return True
    # Monday 00:00-10:00 UTC — EMAs still misaligned from weekend gap
    if now.weekday() == 0 and now.hour < 10:
        return True
    return False


# ══════════════════════════════════════════════════════════════════════
#  CANDLE DATA FETCHER (cached)
# ══════════════════════════════════════════════════════════════════════
def _get_df(ticker: str, interval: str = "5m", period: str = "3h") -> "pd.DataFrame | None":
    if not _OK or not ticker:
        return None
    key = f"bmf:{ticker}:{interval}"
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty and len(df) >= 20:
            _CACHE[key] = (now, df)
            return df
    except Exception:
        pass
    return None


def _norm_cols(df: "pd.DataFrame") -> "pd.DataFrame":
    df = df.copy()
    df.columns = [
        str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
        for c in df.columns
    ]
    return df


# ══════════════════════════════════════════════════════════════════════
#  INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════════════
def _rsi(series: "pd.Series", period: int) -> "pd.Series":
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.clip(lower=1e-10)
    return 100 - (100 / (1 + rs))


def _ema(series: "pd.Series", span: int) -> "pd.Series":
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: "pd.DataFrame", period: int = 14) -> "pd.Series":
    h = df["high"].squeeze().astype(float)
    l = df["low"].squeeze().astype(float)
    c = df["close"].squeeze().astype(float)
    tr = (h - l).combine((h - c.shift()).abs(), max).combine(
         (l - c.shift()).abs(), max)
    return tr.rolling(period).mean()


def _bb_outer_touch(df: "pd.DataFrame", direction: str,
                    period: int = 20, mult: float = 2.0) -> bool:
    c = df["close"].squeeze().astype(float)
    mid = c.rolling(period).mean()
    std = c.rolling(period).std()
    upper = mid + mult * std
    lower = mid - mult * std
    last_c = float(c.iloc[-2])
    if direction == "BUY":
        return last_c <= float(lower.iloc[-2])
    else:
        return last_c >= float(upper.iloc[-2])


def _consecutive_candles(df: "pd.DataFrame") -> tuple[int, str]:
    """Count consecutive same-direction candles on last N bars.
    Returns (count, direction_of_streak).
    """
    o = df["open"].squeeze().astype(float)
    c = df["close"].squeeze().astype(float)
    if len(c) < 2:
        return 0, ""
    streak_dir = "BUY" if float(c.iloc[-2]) > float(o.iloc[-2]) else "SELL"
    count = 0
    for i in range(len(c) - 2, max(len(c) - 12, -1), -1):
        bar_dir = "BUY" if float(c.iloc[i]) > float(o.iloc[i]) else "SELL"
        if bar_dir == streak_dir:
            count += 1
        else:
            break
    return count, streak_dir


def _is_doji(df: "pd.DataFrame") -> bool:
    o = float(df["open"].squeeze().astype(float).iloc[-2])
    c = float(df["close"].squeeze().astype(float).iloc[-2])
    h = float(df["high"].squeeze().astype(float).iloc[-2])
    l = float(df["low"].squeeze().astype(float).iloc[-2])
    total_range = h - l
    if total_range < 1e-10:
        return True
    body = abs(c - o)
    return (body / total_range) < 0.28


def _conviction_close(df: "pd.DataFrame", direction: str) -> bool:
    """Last candle closes beyond prior bar's midpoint in signal direction."""
    if len(df) < 3:
        return True
    h = df["high"].squeeze().astype(float)
    l = df["low"].squeeze().astype(float)
    c = df["close"].squeeze().astype(float)
    prior_mid = (float(h.iloc[-3]) + float(l.iloc[-3])) / 2
    last_close = float(c.iloc[-2])
    if direction == "BUY":
        return last_close > prior_mid
    else:
        return last_close < prior_mid


# ══════════════════════════════════════════════════════════════════════
#  OTC REVERSAL GATE — 4 hard tests
# ══════════════════════════════════════════════════════════════════════
def _otc_reversal_score(df: "pd.DataFrame", direction: str) -> tuple[int, list[str]]:
    """Return (gates_passed 0-4, list of reasons)."""
    score = 0
    reasons: list[str] = []
    is_buy = (direction == "BUY")
    c = df["close"].squeeze().astype(float)

    # Gate A — RSI(3) extreme
    rsi3 = _rsi(c, 3)
    rsi3_val = float(rsi3.iloc[-2])
    if is_buy and rsi3_val < 12:
        score += 1
        reasons.append(f"✅ RSI(3)={rsi3_val:.1f} < 12 — extreme oversold")
    elif not is_buy and rsi3_val > 88:
        score += 1
        reasons.append(f"✅ RSI(3)={rsi3_val:.1f} > 88 — extreme overbought")
    elif is_buy and rsi3_val < 20:
        reasons.append(f"⚠️ RSI(3)={rsi3_val:.1f} oversold (not extreme)")
    elif not is_buy and rsi3_val < 80:
        reasons.append(f"⚠️ RSI(3)={rsi3_val:.1f} overbought (not extreme)")

    # Gate B — Consecutive candle exhaustion (5+)
    streak_count, streak_dir = _consecutive_candles(df)
    opposing_streak = (is_buy and streak_dir == "SELL") or (not is_buy and streak_dir == "BUY")
    if opposing_streak and streak_count >= 5:
        score += 1
        reasons.append(f"✅ {streak_count}× consecutive {streak_dir} candles — exhaustion")
    elif opposing_streak and streak_count >= 3:
        reasons.append(f"⚠️ {streak_count}× consecutive {streak_dir} — partial exhaustion")

    # Gate C — Bollinger Band outer touch (20, 2.0)
    if _bb_outer_touch(df, direction, period=20, mult=2.0):
        score += 1
        reasons.append("✅ BB outer band touch — price at distribution extreme")

    # Gate D — RSI(7) extreme
    rsi7 = _rsi(c, 7)
    rsi7_val = float(rsi7.iloc[-2])
    if is_buy and rsi7_val < 18:
        score += 1
        reasons.append(f"✅ RSI(7)={rsi7_val:.1f} < 18 — deep oversold")
    elif not is_buy and rsi7_val > 82:
        score += 1
        reasons.append(f"✅ RSI(7)={rsi7_val:.1f} > 82 — deep overbought")

    return score, reasons


# ══════════════════════════════════════════════════════════════════════
#  LIVE MARKET QUALITY GATE — 4 tests
# ══════════════════════════════════════════════════════════════════════
def _live_quality_score(df: "pd.DataFrame", direction: str) -> tuple[int, list[str]]:
    """Return (gates_passed 0-4, list of reasons)."""
    score = 0
    reasons: list[str] = []
    is_buy = (direction == "BUY")
    c = df["close"].squeeze().astype(float)

    # Gate E — EMA trend alignment (EMA9 vs EMA21)
    ema9  = _ema(c, 9)
    ema21 = _ema(c, 21)
    trend_up = float(ema9.iloc[-2]) > float(ema21.iloc[-2])
    if is_buy and trend_up:
        score += 1
        reasons.append("✅ EMA9 > EMA21 — trend aligned BUY")
    elif not is_buy and not trend_up:
        score += 1
        reasons.append("✅ EMA9 < EMA21 — trend aligned SELL")
    else:
        reasons.append("⚠️ Counter-trend entry — EMA mismatch")

    # Gate F — RSI(14) in healthy entry zone (not overbought/oversold)
    rsi14 = _rsi(c, 14)
    rsi14_val = float(rsi14.iloc[-2])
    if is_buy and 42 <= rsi14_val <= 68:
        score += 1
        reasons.append(f"✅ RSI(14)={rsi14_val:.1f} in BUY zone (42-68)")
    elif not is_buy and 32 <= rsi14_val <= 58:
        score += 1
        reasons.append(f"✅ RSI(14)={rsi14_val:.1f} in SELL zone (32-58)")
    else:
        reasons.append(f"⚠️ RSI(14)={rsi14_val:.1f} outside optimal entry zone")

    # Gate G — ATR health (not too tight = choppy, not too wide = spiking)
    atr_series = _atr(df, 14)
    atr_now = float(atr_series.iloc[-2])
    atr_avg = float(atr_series.iloc[-15:-2].mean()) if len(atr_series) > 16 else atr_now
    atr_ratio = atr_now / max(atr_avg, 1e-10)
    if 0.7 <= atr_ratio <= 2.2:
        score += 1
        reasons.append(f"✅ ATR ratio {atr_ratio:.2f} — healthy volatility")
    elif atr_ratio > 2.2:
        reasons.append(f"⚠️ ATR spike {atr_ratio:.2f}× avg — news/manipulation risk")
    else:
        reasons.append(f"⚠️ ATR too low {atr_ratio:.2f}× avg — choppy/dead market")

    # Gate H — Conviction close (last candle closes past prior bar midpoint)
    if _conviction_close(df, direction):
        score += 1
        reasons.append("✅ Conviction close — candle cleared prior midpoint")
    else:
        reasons.append("⚠️ Weak close — no midpoint clearance")

    return score, reasons


# ══════════════════════════════════════════════════════════════════════
#  ATR SPIKE DETECTOR
# ══════════════════════════════════════════════════════════════════════
def _is_atr_spike(df: "pd.DataFrame", threshold: float = 2.8) -> bool:
    try:
        atr_series = _atr(df, 14)
        atr_now = float(atr_series.iloc[-2])
        atr_avg = float(atr_series.iloc[-15:-2].mean())
        return (atr_now / max(atr_avg, 1e-10)) > threshold
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════
#  MASTER CHECK — PUBLIC API
# ══════════════════════════════════════════════════════════════════════
def binary_master_check(
    pair: str,
    direction: str,
    is_otc: bool,
    tf_label: str,
    ticker: str | None = None,
    engine_agree: int = 0,
    engine_oppose: int = 0,
    total_engines: int = 0,
) -> dict:
    """
    Run the master binary filter. Returns:
    {
      approved:       bool,
      quality_tier:   "ELITE" | "HIGH" | "STANDARD" | "WEAK" | "BLOCKED",
      confidence_adj: int  (-25 to +15),
      block_reason:   str | None,
      otc_gate_score: int,
      live_gate_score:int,
      regime:         str,
      news_safe:      bool,
    }
    """
    if ticker is None:
        ticker = _yf_ticker(pair)

    result: dict = {
        "approved":        True,
        "quality_tier":    "STANDARD",
        "confidence_adj":  0,
        "block_reason":    None,
        "otc_gate_score":  0,
        "live_gate_score": 0,
        "regime":          "UNKNOWN",
        "news_safe":       True,
    }

    reasons: list[str] = []

    # ── 1. HARD TIME BLOCKS ───────────────────────────────────────────
    news_active, news_label = _is_news_window()
    if news_active and not is_otc:
        result["approved"]     = False
        result["quality_tier"] = "BLOCKED"
        result["block_reason"] = f"News window: {news_label}"
        result["news_safe"]    = False
        result["confidence_adj"] = -25
        return result

    if news_active:
        result["news_safe"]      = False
        result["confidence_adj"] -= 10

    if _is_friday_close() and not is_otc:
        result["approved"]     = False
        result["quality_tier"] = "BLOCKED"
        result["block_reason"] = "Friday NY close — spread widens, avoid"
        result["confidence_adj"] = -25
        return result

    if _is_monday_gap():
        result["approved"]     = False
        result["quality_tier"] = "BLOCKED"
        result["block_reason"] = "Monday gap zone — price jumps unpredictably"
        result["confidence_adj"] = -25
        return result

    # ── 2. ENGINE CONSENSUS CHECK ─────────────────────────────────────
    if total_engines >= 3:
        oppose_ratio = engine_oppose / max(total_engines, 1)
        agree_ratio  = engine_agree  / max(total_engines, 1)
        if oppose_ratio > 0.45:
            result["approved"]     = False
            result["quality_tier"] = "BLOCKED"
            result["block_reason"] = (
                f"Engine split: {engine_agree} agree vs {engine_oppose} oppose"
                f" ({oppose_ratio*100:.0f}% opposing)"
            )
            result["confidence_adj"] = -25
            return result
        if oppose_ratio > 0.30:
            result["confidence_adj"] -= 12
            reasons.append(f"⚠️ Engine conflict: {engine_oppose}/{total_engines} oppose")
        elif agree_ratio >= 0.75 and engine_oppose == 0:
            result["confidence_adj"] += 10
            reasons.append(f"✅ Unanimous engine consensus: {engine_agree}/{total_engines}")
        elif agree_ratio >= 0.60:
            result["confidence_adj"] += 5
            reasons.append(f"✅ Strong consensus: {engine_agree}/{total_engines}")

    # ── 3. CANDLE DATA ANALYSIS ───────────────────────────────────────
    df = None
    if ticker and _OK:
        _tf_upper = tf_label.strip().upper()
        interval = (
            "1m"
            if _tf_upper.startswith(("1 MIN", "2 MIN", "5 SEC", "15 SEC"))
            else "5m"
        )
        df = _get_df(ticker, interval=interval, period="3h")

    if df is None:
        # No candle data — quality determined purely by engine consensus.
        result["regime"] = "NO_DATA"
        _agree  = engine_agree  if isinstance(engine_agree,  int) else 0
        _oppose = engine_oppose if isinstance(engine_oppose, int) else 0
        _total  = total_engines if isinstance(total_engines, int) else 0

        if _total < 2:
            # Not enough engine votes and no candle data → weak, penalise
            result["quality_tier"]   = "WEAK"
            result["confidence_adj"] = max(result["confidence_adj"] - 12, -25)
        elif _oppose >= 2:
            # 2+ engines oppose the direction — block regardless of agree count
            result["approved"]       = False
            result["quality_tier"]   = "BLOCKED"
            result["block_reason"]   = (
                f"Engine conflict (no candle data): {_agree} agree, {_oppose} oppose"
            )
            result["confidence_adj"] = -25
        elif _oppose == 1:
            result["quality_tier"]   = "WEAK"
            result["confidence_adj"] = max(result["confidence_adj"] - 8, -25)
        elif _agree >= 5 and _oppose == 0:
            result["quality_tier"]   = "HIGH"
            result["confidence_adj"] = min(result["confidence_adj"] + 6, 15)
        elif _agree >= 3 and _oppose == 0:
            result["quality_tier"]   = "STANDARD"
            result["confidence_adj"] = max(result["confidence_adj"] - 2, -25)
        else:
            result["quality_tier"]   = "STANDARD"
            result["confidence_adj"] = max(result["confidence_adj"] - 5, -25)
        return result

    df = _norm_cols(df)

    # ── 4. ATR SPIKE HARD BLOCK ───────────────────────────────────────
    if _is_atr_spike(df, threshold=2.8) and not is_otc:
        result["approved"]     = False
        result["quality_tier"] = "BLOCKED"
        result["block_reason"] = "ATR spike >2.8× average — news flush/manipulation"
        result["confidence_adj"] = -25
        return result

    # ── 5. DOJI FILTER ────────────────────────────────────────────────
    try:
        if _is_doji(df):
            reasons.append("⚠️ Last candle doji — indecision, penalty applied")
            result["confidence_adj"] -= 12
    except Exception:
        pass

    # ── 6. OTC-SPECIFIC GATES ─────────────────────────────────────────
    if is_otc:
        otc_score, otc_reasons = _otc_reversal_score(df, direction)
        result["otc_gate_score"] = otc_score
        reasons.extend(otc_reasons)

        if otc_score >= 4:
            result["quality_tier"]  = "ELITE"
            result["confidence_adj"] += 15
            result["regime"]         = "OTC_REVERSAL_ELITE"
        elif otc_score >= 3:
            result["quality_tier"]  = "HIGH"
            result["confidence_adj"] += 8
            result["regime"]         = "OTC_REVERSAL_HIGH"
        elif otc_score >= 2:
            result["quality_tier"]  = "STANDARD"
            result["confidence_adj"] += 2
            result["regime"]         = "OTC_STANDARD"
        elif otc_score == 1:
            # Only 1 gate passed — weak OTC setup, penalise
            result["quality_tier"]  = "WEAK"
            result["confidence_adj"] -= 8
            result["regime"]         = "OTC_WEAK"
            reasons.append("⚠️ Only 1 OTC reversal gate passed — weak setup")
        else:
            # Zero reversal gates passed — block OTC signal
            result["approved"]       = False
            result["quality_tier"]   = "BLOCKED"
            result["block_reason"]   = "No OTC reversal setup — zero gates passed"
            result["confidence_adj"] = -25
            result["regime"]         = "OTC_NO_SETUP"
            reasons.append("❌ No OTC reversal setup — zero gates passed")

    # ── 7. LIVE MARKET GATES ──────────────────────────────────────────
    else:
        live_score, live_reasons = _live_quality_score(df, direction)
        result["live_gate_score"] = live_score
        reasons.extend(live_reasons)

        if live_score >= 4:
            result["quality_tier"]  = "ELITE"
            result["confidence_adj"] += 15
            result["regime"]         = "LIVE_ELITE"
        elif live_score >= 3:
            result["quality_tier"]  = "HIGH"
            result["confidence_adj"] += 8
            result["regime"]         = "LIVE_HIGH"
        elif live_score >= 2:
            result["quality_tier"]  = "STANDARD"
            result["confidence_adj"] += 2
            result["regime"]         = "LIVE_STANDARD"
        else:
            # 0 or 1 gate passed on LIVE — block to prevent poor-quality entries
            result["approved"]       = False
            result["quality_tier"]   = "BLOCKED"
            result["block_reason"]   = (
                f"LIVE quality gates: only {live_score}/4 passed — setup too weak"
            )
            result["confidence_adj"] = -25
            result["regime"]         = "LIVE_BLOCKED"
            reasons.append(f"❌ LIVE market quality gates: {live_score}/4 passed — blocked")

    # ── 8. FINAL APPROVAL DECISION ───────────────────────────────────
    if result["confidence_adj"] <= -20:
        result["approved"]     = False
        result["quality_tier"] = "BLOCKED"
        if not result["block_reason"]:
            result["block_reason"] = (
                f"Combined penalties ({result['confidence_adj']}) — "
                f"setup quality too low"
            )

    print(
        f"[master_filter] {pair} {'OTC' if is_otc else 'LIVE'} {direction} "
        f"→ {result['quality_tier']} adj={result['confidence_adj']:+d} "
        f"{'BLOCKED:' + str(result['block_reason']) if not result['approved'] else 'APPROVED'}"
    )
    return result
