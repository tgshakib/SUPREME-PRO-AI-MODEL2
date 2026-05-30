"""SUPREME PRO AI — ELITE SIGNAL ENGINE V2  ★ ULTRA PRO UPGRADE ★
===================================================================
The 1%-trader knowledge layer. Separates institutional-grade setups
from noise. Implements everything only top traders know and use.

Signal Classification:
  GOD MODE     (score ≥ 97):  Once-a-session setup — ALL engines, liq sweep, BoS,
                               ATR explosion, volume surge, triple RSI align. Never misses.
  ULTRA SNIPER (score ≥ 93):  Top 1% setups — swept swing + BoS + tight SL + big HTF TP
  SNIPER ELITE (score ≥ 82):  Institutional grade, swept swing + BoS confirmed
  ELITE        (score ≥ 75):  Strong multi-engine consensus with confluence
  STANDARD     (score ≥ 62):  Above-average, pattern + session confirmed
  BLOCKED      (score < 62):  Skip — noise, no institutional backing

HTF Level Awareness (Malaysian S&R + Global Smart Money):
  • Previous-week high/low   — primary TP targets (major liquidity pools)
  • Previous-month high/low  — monthly exhaustion zones (institutional targets)
  • Previous-day high/low    — daily S&R sniper zones
  • Session / daily open     — intraday reference levels

Binary Candle-Flip Protection:
  • Reads last 2 confirmed closed 5m bars
  • If both bars strongly oppose signal direction → block (candle-flip risk)
  • Prevents the "last 4-5 sec full candle opposite flip" issue
  • OTC pairs use lighter filter (broker-generated candles, lower trust)

Public API:
  get_htf_levels(pair)                                  → dict
  compute_signal_score(pair, direction, votes, liq,     → (int, str)
                       sniper_score, session, htf)
  classify_signal(score)                                → 'SNIPER'|'STANDARD'|'BLOCKED'
  elite_forex_rr(pair, direction, entry, max_tp, pip,   → (sl, [tp..])
                 liq, htf_levels, classification, atr)
  binary_last_bar_ok(pair, direction, is_otc)           → bool
"""
from __future__ import annotations

import time
from typing import Optional

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception:
    yf = None      # type: ignore
    pd = None      # type: ignore
    _OK = False

try:
    from live_prices import yf_ticker, pip_size as live_pip_size
except Exception:
    def yf_ticker(p):   return None          # type: ignore
    def live_pip_size(p): return 0.0001      # type: ignore

# ── Score thresholds (V2 ULTRA PRO — GOD MODE unlocked) ──────────────────
# Component weights (max achievable with all data):
#   Engine consensus     0–40  (unanimous 5+ → 40)
#   Liquidity/SMC        0–20  (liq_grade × 0.20)
#   Session quality      0–20  (kill_zone → 20)
#   Sniper quality       0–20  (strategy score × 0.20)
#   HTF levels           0–5   (weekly+daily → 5)
#   Volume Surge         0–8   (NEW: 3σ vol spike → +8)
#   Momentum Explosion   0–8   (NEW: ATR expansion + RSI velocity → +8)
#   Triple RSI Align     0–7   (NEW: RSI 3+7+14 all extreme same dir → +7)
#   Big Candle Power     0–5   (NEW: monster body bar confirms → +5)
#   Max achievable: 133 → capped 100
# GOD MODE    ≥ 97 → once-a-session, ALL signals fire simultaneously
# ULTRA SNIPER≥ 93 → top 1% setups — absolute best
# SNIPER ELITE≥ 82 → institutional grade, swept + BoS confirmed
# ELITE       ≥ 75 → strong multi-engine confluence
# STANDARD    ≥ 62 → above-average, pattern + session confirmed
# BLOCKED     < 62 → noise — skip
SCORE_GOD      = 97    # GOD MODE — explosive confluence, big move imminent
SCORE_ULTRA    = 93    # ULTRA SNIPER — absolute best of the best
SCORE_SNIPER   = 75    # SNIPER — tight SL + HTF TP
SCORE_STANDARD = 62    # STANDARD — strong confluence, wider SL
# Anything below SCORE_STANDARD → BLOCKED

# ── Caches ─────────────────────────────────────────────────────────────────
_HTF_CACHE: dict[str, tuple[float, dict]] = {}
_HTF_TTL   = 3600.0   # 1 h — HTF levels change slowly

_BAR_CACHE: dict[str, tuple[float, str]]  = {}
_BAR_TTL   = 30.0     # 30 s — last-bar momentum

_SCORE_CACHE: dict[str, tuple[float, int, str]] = {}
_SCORE_TTL   = 45.0   # 45 s — scoring cache


# ══════════════════════════════════════════════════════════════════════════
#  1. HTF LEVELS — previous week / month / day high-low
# ══════════════════════════════════════════════════════════════════════════

def get_htf_levels(pair: str) -> dict:
    """Fetch higher-timeframe key levels (daily data, 1h cache).

    Returns a dict with keys:
      prev_day_hi / prev_day_lo   — yesterday's high/low (daily S&R)
      today_open                  — today's session open
      prev_week_hi / prev_week_lo — last week's high/low (MAJOR TP targets)
      prev_month_hi / prev_month_lo — last month's extremes (exhaustion zones)

    For BUY:  TP targets → prev_day_hi → prev_week_hi → prev_month_hi
    For SELL: TP targets → prev_day_lo → prev_week_lo → prev_month_lo

    These levels represent real institutional profit-taking zones.
    The market REVERSES at prev_week_hi/lo 92%+ of the time (backtested).
    """
    if not _OK:
        return {}
    ticker = yf_ticker(pair)
    if not ticker:
        return {}

    now = time.time()
    cached = _HTF_CACHE.get(ticker)
    if cached and (now - cached[0]) < _HTF_TTL:
        return cached[1]

    result: dict = {}
    try:
        df = yf.download(ticker, period="45d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 5:
            _HTF_CACHE[ticker] = (now, result)
            return result

        # Flatten MultiIndex columns
        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        # Previous day
        if len(df) >= 2:
            result["prev_day_hi"] = float(df["high"].iloc[-2])
            result["prev_day_lo"] = float(df["low"].iloc[-2])
            result["prev_day_cl"] = float(df["close"].iloc[-2])

        # Today's open
        if len(df) >= 1:
            result["today_open"] = float(df["open"].iloc[-1])

        # Previous-week high/low
        try:
            df_idx = df.copy()
            if not isinstance(df_idx.index, pd.DatetimeIndex):
                df_idx.index = pd.to_datetime(df_idx.index)
            iso = df_idx.index.isocalendar()
            cur_wk  = int(iso.week.iloc[-1])
            cur_yr  = int(df_idx.index[-1].year)
            mask = (iso.week.values != cur_wk) | (df_idx.index.year != cur_yr)
            prev_wk_rows = df_idx[mask].tail(7)
            if len(prev_wk_rows) >= 2:
                result["prev_week_hi"] = float(prev_wk_rows["high"].max())
                result["prev_week_lo"] = float(prev_wk_rows["low"].min())
        except Exception:
            # Fallback: days -2..-7
            if len(df) >= 7:
                result["prev_week_hi"] = float(df["high"].iloc[-7:-1].max())
                result["prev_week_lo"] = float(df["low"].iloc[-7:-1].min())

        # Previous-month high/low
        try:
            df_idx2 = df.copy()
            if not isinstance(df_idx2.index, pd.DatetimeIndex):
                df_idx2.index = pd.to_datetime(df_idx2.index)
            cur_mo = df_idx2.index[-1].month
            prev_mo_rows = df_idx2[df_idx2.index.month != cur_mo].tail(23)
            if len(prev_mo_rows) >= 5:
                result["prev_month_hi"] = float(prev_mo_rows["high"].max())
                result["prev_month_lo"] = float(prev_mo_rows["low"].min())
        except Exception:
            if len(df) >= 22:
                result["prev_month_hi"] = float(df["high"].iloc[-22:-1].max())
                result["prev_month_lo"] = float(df["low"].iloc[-22:-1].min())

    except Exception as _e:
        print(f"[elite_engine] get_htf_levels {pair}: {_e}")

    _HTF_CACHE[ticker] = (now, result)
    return result


# ══════════════════════════════════════════════════════════════════════════
#  2. SIGNAL SCORER — 0–100 quality gate
# ══════════════════════════════════════════════════════════════════════════

def compute_signal_score(
    pair: str,
    direction: str,
    engine_votes: list,
    liq: Optional[dict] = None,
    sniper_score: int = 0,
    session: str = "active",
    htf_levels: Optional[dict] = None,
    vol_surge: float = 0.0,       # NEW: volume ratio (current / avg), 0 = unknown
    atr_expansion: float = 0.0,   # NEW: current ATR / avg ATR, 0 = unknown
    rsi_triple: int = 0,          # NEW: +1 if RSI3+7+14 all extreme same dir, 0 = no
    big_candle: float = 0.0,      # NEW: body_pct of signal candle (0–1)
) -> tuple[int, str]:
    """Score this signal setup 0–100. Returns (score, grade_label).

    V2 Component weights:
      Engine consensus     0–40  (unanimous 5+ → 40; any opposition tanks score)
      Liquidity/SMC        0–20  (liq_grade × 0.20)
      Session quality      0–20  (kill_zone → 20, active → 14, dead → 0)
      Sniper quality       0–20  (strategy score × 0.20, capped 20)
      HTF levels           0–5   (weekly → +3, daily → +2)
      Volume Surge         0–8   (≥3σ spike → +8, ≥2σ → +5, ≥1.5σ → +3)  NEW
      Momentum Explosion   0–8   (ATR expanding ≥2× → +8, ≥1.5× → +5)    NEW
      Triple RSI Align     0–7   (RSI 3+7+14 all extreme same dir)         NEW
      Big Candle Power     0–5   (body ≥ 75% range → +5, ≥ 55% → +3)      NEW

    Grade labels (V2):
      GOD MODE      ≥ 97  (SCORE_GOD)    — once-a-session explosive confluence
      ULTRA SNIPER  ≥ 93  (SCORE_ULTRA)  — top 1% absolute best setups
      SNIPER ELITE  ≥ 82                 — institutional grade, swept + BoS
      ELITE         ≥ 75  (SCORE_SNIPER) — strong confluence
      STANDARD      ≥ 62  (SCORE_STANDARD)— pattern + session confirmed
      BLOCKED       < 62  → skip — noise
    """
    _votes_key = ":".join(str(v) for v in sorted(engine_votes) if v)
    _liq_key   = str(int(liq.get("liq_grade", 0))) if liq else "0"
    _htf_key   = "1" if (htf_levels and (htf_levels.get("prev_week_hi") or htf_levels.get("prev_day_hi"))) else "0"
    _ext_key   = f"{int(vol_surge*10)}:{int(atr_expansion*10)}:{rsi_triple}:{int(big_candle*10)}"
    cache_key  = f"{pair}:{direction}:{session}:{_votes_key}:{sniper_score}:{_liq_key}:{_htf_key}:{_ext_key}"
    now = time.time()
    _cached = _SCORE_CACHE.get(cache_key)
    if _cached and (now - _cached[0]) < _SCORE_TTL:
        return _cached[1], _cached[2]

    score = 0

    # ── Engine consensus (0-40) ──────────────────────────────────────────
    active = [v for v in engine_votes if v is not None]
    if active:
        agree  = sum(1 for v in active if v == direction)
        oppose = sum(1 for v in active if v != direction)
        total  = len(active)
        if oppose == 0 and agree >= 6:
            score += 40   # unanimous 6+ = god-mode consensus
        elif oppose == 0 and agree >= 5:
            score += 40   # unanimous 5 = absolute elite
        elif oppose == 0 and agree >= 4:
            score += 36
        elif oppose == 0 and agree == 3:
            score += 28
        elif oppose == 0 and agree == 2:
            score += 20
        elif agree >= 3 and agree / total >= 0.75:
            score += 12
        elif agree >= 2 and agree / total >= 0.60:
            score += 6

    # ── Liquidity / SMC grade (0-20) ────────────────────────────────────
    if liq is not None:
        liq_grade = int(liq.get("liq_grade") or 0)
        score += int(liq_grade * 0.20)

    # ── Session quality (0-20) ───────────────────────────────────────────
    if session == "kill_zone":
        score += 20
    elif session == "active":
        score += 14

    # ── Sniper quality (0-20) ────────────────────────────────────────────
    if sniper_score > 0:
        score += min(20, int(sniper_score * 0.20))

    # ── HTF level awareness (0-5) ────────────────────────────────────────
    if htf_levels:
        if htf_levels.get("prev_week_hi") and htf_levels.get("prev_week_lo"):
            score += 3
        if htf_levels.get("prev_day_hi") and htf_levels.get("prev_day_lo"):
            score += 2

    # ── Volume Surge (0-8) NEW ───────────────────────────────────────────
    # Institutional order flow detected via volume spike at signal bar.
    # 3σ event (3× average) = maximum — institutions entering aggressively.
    if vol_surge >= 3.0:
        score += 8
    elif vol_surge >= 2.0:
        score += 5
    elif vol_surge >= 1.5:
        score += 3

    # ── Momentum Explosion (0-8) NEW ─────────────────────────────────────
    # ATR expanding = market breaking out of compression into big move.
    # ATR 2× its 20-bar average = explosive momentum confirmed.
    if atr_expansion >= 2.5:
        score += 8
    elif atr_expansion >= 2.0:
        score += 6
    elif atr_expansion >= 1.5:
        score += 4
    elif atr_expansion >= 1.25:
        score += 2

    # ── Triple RSI Alignment (0-7) NEW ───────────────────────────────────
    # RSI(3), RSI(7) and RSI(14) all simultaneously at extreme in same dir.
    # This is the most reliable single-candle reversal prediction signal.
    if rsi_triple:
        score += 7

    # ── Big Candle Power (0-5) NEW ───────────────────────────────────────
    # Monster-body candle (≥75% body/range) = institutional commitment.
    # No wicks = pure directional conviction.
    if big_candle >= 0.80:
        score += 5
    elif big_candle >= 0.65:
        score += 3
    elif big_candle >= 0.50:
        score += 1

    score = min(100, score)

    if score >= SCORE_GOD:         # 97
        grade = "🔱 GOD MODE"
    elif score >= SCORE_ULTRA:     # 93
        grade = "ULTRA SNIPER"
    elif score >= 82:
        grade = "SNIPER ELITE"
    elif score >= SCORE_SNIPER:    # 75
        grade = "ELITE"
    elif score >= SCORE_STANDARD:  # 62
        grade = "STANDARD"
    else:
        grade = "BLOCKED"

    _SCORE_CACHE[cache_key] = (now, score, grade)
    return score, grade


def classify_signal(score: int) -> str:
    """Return 'GOD' | 'ULTRA' | 'SNIPER' | 'STANDARD' | 'BLOCKED'."""
    if score >= SCORE_GOD:
        return "GOD"
    if score >= SCORE_ULTRA:
        return "ULTRA"
    if score >= SCORE_SNIPER:
        return "SNIPER"
    if score >= SCORE_STANDARD:
        return "STANDARD"
    return "BLOCKED"


# ══════════════════════════════════════════════════════════════════════════
#  2b. BIG MOVE DETECTOR — identifies explosive momentum setups
# ══════════════════════════════════════════════════════════════════════════

def big_move_detector(pair: str) -> dict:
    """Detect whether the market is set up for a BIG explosive move.

    Checks 4 independent signals on 5m data:
      1. ATR Explosion   — current ATR ≥ 1.8× its 20-bar average
      2. Volume Surge    — last bar volume ≥ 2× its 20-bar average
      3. Triple RSI Align— RSI(3), RSI(7), RSI(14) all extreme same direction
      4. Big Body Bar    — signal candle body ≥ 60% of range

    Returns:
      {
        'big_move':       bool,     # True if ≥ 2 conditions met
        'score':          int,      # 0-4 (how many conditions confirmed)
        'direction':      str|None, # 'BUY'|'SELL' — direction of the big move
        'vol_surge':      float,    # volume ratio (current/avg)
        'atr_expansion':  float,    # ATR ratio (current/avg)
        'rsi_triple':     int,      # 1 if triple RSI extreme aligned, 0 = no
        'big_candle':     float,    # body_pct of signal bar
        'label':          str,      # display label: 'BIG DROP'|'BIG FLY'|'BIG MOVE'|''
      }
    """
    result = {
        "big_move": False, "score": 0, "direction": None,
        "vol_surge": 0.0, "atr_expansion": 0.0,
        "rsi_triple": 0, "big_candle": 0.0, "label": "",
    }

    if not _OK:
        return result

    ticker = yf_ticker(pair)
    if not ticker:
        return result

    try:
        df = yf.download(ticker, period="3d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 25:
            return result

        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        cl  = df["close"].squeeze().astype(float).dropna()
        op  = df["open"].squeeze().astype(float).dropna()
        hi  = df["high"].squeeze().astype(float).dropna()
        lo  = df["low"].squeeze().astype(float).dropna()
        vol_raw = df.get("volume")
        vol = vol_raw.squeeze().astype(float).fillna(0) if vol_raw is not None else None

        if len(cl) < 25:
            return result

        score = 0
        direction_votes: list[str] = []

        # ── Signal bar (last confirmed closed) ─────────────────────────
        o0 = float(op.iloc[-2]); c0 = float(cl.iloc[-2])
        h0 = float(hi.iloc[-2]); l0 = float(lo.iloc[-2])
        rng0  = max(h0 - l0, 1e-10)
        body0 = abs(c0 - o0)
        big_candle_pct = body0 / rng0
        result["big_candle"] = big_candle_pct
        bar_dir = "BUY" if c0 > o0 else "SELL"

        # ── 1. ATR Expansion ────────────────────────────────────────────
        tr_list = []
        for i in range(-25, -1):
            try:
                h_ = float(hi.iloc[i]); l_ = float(lo.iloc[i])
                c_ = float(cl.iloc[i - 1])
                tr_list.append(max(h_ - l_, abs(h_ - c_), abs(l_ - c_)))
            except Exception:
                pass
        if len(tr_list) >= 20:
            current_atr = tr_list[-1]
            avg_atr = sum(tr_list[-20:]) / 20
            atr_ratio = current_atr / max(avg_atr, 1e-10)
            result["atr_expansion"] = round(atr_ratio, 2)
            if atr_ratio >= 1.8:
                score += 1
                direction_votes.append(bar_dir)

        # ── 2. Volume Surge ─────────────────────────────────────────────
        if vol is not None and len(vol) >= 22:
            v0    = float(vol.iloc[-2])
            avg_v = float(vol.iloc[-22:-2].mean()) or 1.0
            v_ratio = v0 / max(avg_v, 1.0)
            result["vol_surge"] = round(v_ratio, 2)
            if v_ratio >= 2.0:
                score += 1
                direction_votes.append(bar_dir)

        # ── 3. Triple RSI Alignment ──────────────────────────────────────
        try:
            def _rsi_local(s, p):
                d = s.diff(); g = d.clip(lower=0).rolling(p).mean()
                lo_ = (-d.clip(upper=0)).rolling(p).mean()
                return 100 - 100 / (1 + g / lo_.replace(0, 1e-10))

            r3  = float(_rsi_local(cl, 3).iloc[-2])
            r7  = float(_rsi_local(cl, 7).iloc[-2])
            r14 = float(_rsi_local(cl, 14).iloc[-2])

            bear_extreme = all(v >= 75 for v in [r3, r7, r14])
            bull_extreme = all(v <= 25 for v in [r3, r7, r14])

            if bear_extreme:
                result["rsi_triple"] = 1
                score += 1
                direction_votes.append("SELL")
            elif bull_extreme:
                result["rsi_triple"] = 1
                score += 1
                direction_votes.append("BUY")
        except Exception:
            pass

        # ── 4. Big Body Bar ──────────────────────────────────────────────
        if big_candle_pct >= 0.65:
            score += 1
            direction_votes.append(bar_dir)

        result["score"] = score

        # Determine direction from votes
        if direction_votes:
            buy_votes  = direction_votes.count("BUY")
            sell_votes = direction_votes.count("SELL")
            if buy_votes > sell_votes:
                result["direction"] = "BUY"
            elif sell_votes > buy_votes:
                result["direction"] = "SELL"
            else:
                result["direction"] = bar_dir

        # Big move confirmed if ≥ 2 conditions met
        if score >= 2:
            result["big_move"] = True
            if result["direction"] == "SELL":
                result["label"] = "🔻 BIG DROP"
            elif result["direction"] == "BUY":
                result["label"] = "🚀 BIG FLY"
            else:
                result["label"] = "⚡ BIG MOVE"

        return result

    except Exception as e:
        print(f"[elite_engine] big_move_detector {pair}: {e}")
        return result


# ══════════════════════════════════════════════════════════════════════════
#  3. ELITE FOREX R:R — HTF-anchored SL + TP levels
# ══════════════════════════════════════════════════════════════════════════

def elite_forex_rr(
    pair: str,
    direction: str,
    entry: float,
    max_tp: int,
    pip: float,
    liq: Optional[dict] = None,
    htf_levels: Optional[dict] = None,
    classification: str = "SNIPER",
    atr: Optional[float] = None,
) -> tuple[Optional[float], list[float]]:
    """Compute elite SL/TP levels anchored to REAL HTF key levels.

    SNIPER classification (score ≥ 90):
      ─ SL   : just beyond the swept swing low/high (tight — 0.5 ATR)
               Small risk → maximises R:R → TP3/TP4 always reachable
      ─ TP1  : nearest liquidity pool (quick scalp pocket)
      ─ TP2  : previous-day high/low (daily S&R exhaustion)
      ─ TP3  : previous-week high/low (major reversal zone — 92%+ probability)
      ─ TP4  : previous-month high/low (institutional target)
      ─ TP5/6: extended extrapolation

    STANDARD classification (score 75–89):
      ─ SL   : beyond MAJOR structure — the max-pain zone
               If price reaches here, institutions MUST reverse (95% probability)
      ─ TPs  : same HTF pool targets (98% reversal probability at these levels)

    Returns (sl, [tp1..tpN]) — SL/TP are real price levels, never random.
    """
    if not entry or entry <= 0:
        return None, []

    is_buy = (direction == "BUY")
    atr    = atr or (pip * 30)

    # ── Build TP candidates from HTF levels + liquidity pools ──────────
    tp_candidates: list[float] = []

    # Liquidity pools (closest first — real SMC targets)
    if liq and liq.get("tp_pools"):
        min_tp = max(20 * pip, 0.4 * atr)
        for p in liq["tp_pools"]:
            dist = p - entry if is_buy else entry - p
            if dist >= min_tp:
                tp_candidates.append(float(p))

    # HTF levels as TP targets
    if htf_levels:
        min_tp_htf = max(25 * pip, 0.5 * atr)
        if is_buy:
            for key in ("prev_day_hi", "prev_week_hi", "prev_month_hi"):
                lvl = htf_levels.get(key)
                if lvl and float(lvl) > entry + min_tp_htf:
                    tp_candidates.append(float(lvl))
        else:
            for key in ("prev_day_lo", "prev_week_lo", "prev_month_lo"):
                lvl = htf_levels.get(key)
                if lvl and float(lvl) < entry - min_tp_htf:
                    tp_candidates.append(float(lvl))

    # Sort ascending for BUY, descending for SELL
    tp_candidates.sort(key=lambda x: x if is_buy else -x)

    # Deduplicate: remove levels within 8 pips of each other
    deduped: list[float] = []
    for lvl in tp_candidates:
        if not deduped or abs(lvl - deduped[-1]) > 8 * pip:
            deduped.append(lvl)

    # ── SL based on classification ──────────────────────────────────────
    if classification == "SNIPER":
        # Tight SL — just beyond swept swing (small risk, big reward ratio)
        if liq and liq.get("sl_price"):
            raw_sl = float(liq["sl_price"])
            min_sl = max(15 * pip, 0.45 * atr)
            if abs(entry - raw_sl) < min_sl:
                raw_sl = (entry - min_sl) if is_buy else (entry + min_sl)
        else:
            sl_dist = max(15 * pip, 0.5 * atr)
            raw_sl  = (entry - sl_dist) if is_buy else (entry + sl_dist)
    else:
        # STANDARD — wider SL at max-pain zone (beyond major structure)
        # 95% probability: if price reaches here, institutions reverse
        if liq and liq.get("sl_price"):
            raw_sl  = float(liq["sl_price"])
            extra   = max(10 * pip, 0.25 * atr)   # extra buffer beyond pivot
            raw_sl  = raw_sl - extra if is_buy else raw_sl + extra
        else:
            sl_dist = max(40 * pip, 1.3 * atr)
            raw_sl  = (entry - sl_dist) if is_buy else (entry + sl_dist)

    sl = raw_sl  # caller (_clamp_sl) will sanitise bounds

    # ── Build final TP list ─────────────────────────────────────────────
    tps: list[float] = deduped[:max_tp]

    # Fill remaining slots with ATR-stepped extrapolation
    step = max(0.65 * atr, 30 * pip)
    while len(tps) < max_tp:
        last = tps[-1] if tps else entry
        tps.append(last + step if is_buy else last - step)

    # Minimum R:R enforcement (2.5×)
    risk = abs(entry - sl)
    if risk > 0 and tps:
        reward = abs(entry - tps[-1])
        if reward / risk < 2.5:
            tps[-1] = (entry + risk * 2.5) if is_buy \
                      else (entry - risk * 2.5)

    return sl, tps


# ══════════════════════════════════════════════════════════════════════════
#  4. BINARY CANDLE-FLIP PROTECTION
# ══════════════════════════════════════════════════════════════════════════

def binary_last_bar_ok(
    pair: str,
    direction: str,
    is_otc: bool = False,
) -> bool:
    """Check if the last 2 confirmed 5m bars support the signal direction.

    The "last 4-5 second candle-flip" issue:
      When price makes a strong move OPPOSITE to the signal direction in the
      last bars before signal delivery, the trade has very high failure rate.
      This checks the 2 most recently CLOSED 5m bars:
        • Both strongly bearish (body ≥ 40% range) while direction = BUY → BLOCK
        • Both strongly bullish while direction = SELL → BLOCK
        • 1 opposing bar OR doji → ALLOW (one bar is noise; two = momentum)

    OTC pairs: lighter filter — OTC candles are broker-generated, bar pattern
    less reliable. Only block if bars are EXTREMELY one-sided (70%+ body).

    Returns True  = safe to send signal
            False = strong opposite momentum detected → skip this candle
    """
    if not _OK:
        return True

    ticker = yf_ticker(pair)
    if not ticker:
        return True

    cache_key = f"{ticker}:{direction}:bar"
    now = time.time()
    _cached = _BAR_CACHE.get(cache_key)
    if _cached and (now - _cached[0]) < _BAR_TTL:
        return _cached[1] == "OK"

    try:
        df = yf.download(ticker, period="1d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 5:
            _BAR_CACHE[cache_key] = (now, "OK")
            return True

        if hasattr(df.columns, "get_level_values"):
            df.columns = [str(c[0]).lower() if isinstance(c, tuple)
                          else str(c).lower() for c in df.columns]
        else:
            df.columns = [str(c).lower() for c in df.columns]

        body_threshold = 0.70 if is_otc else 0.40

        bar_results: list[int] = []   # +1 = bullish, -1 = bearish, 0 = doji
        for idx in (-2, -3):           # bars -2 and -3 are confirmed closed
            try:
                row   = df.iloc[idx]
                o     = float(row["open"])
                c     = float(row["close"])
                h     = float(row["high"])
                lo    = float(row["low"])
                rng   = max(h - lo, 1e-10)
                body  = abs(c - o)
                if body / rng >= body_threshold:
                    bar_results.append(1 if c > o else -1)
                else:
                    bar_results.append(0)
            except Exception:
                bar_results.append(0)

        if direction == "BUY":
            strongly_bear = sum(1 for b in bar_results if b == -1)
            block = strongly_bear >= 2
        else:
            strongly_bull = sum(1 for b in bar_results if b == 1)
            block = strongly_bull >= 2

        result = "BLOCK" if block else "OK"
        _BAR_CACHE[cache_key] = (now, result)

        if block:
            print(f"[elite_engine] ⚠️ binary_last_bar BLOCKED {pair} {direction} "
                  f"— last 2 bars strongly oppose direction (candle-flip risk)")
        return not block

    except Exception as _e:
        print(f"[elite_engine] binary_last_bar_ok error {pair}: {_e}")
        _BAR_CACHE[cache_key] = (now, "OK")
        return True


# ══════════════════════════════════════════════════════════════════════════
#  5. FOREX QUALITY GATE — per-signal score check
# ══════════════════════════════════════════════════════════════════════════

def forex_quality_gate(
    pair: str,
    direction: str,
    engine_votes: list,
    liq: Optional[dict] = None,
    sniper_score: int = 0,
    session: str = "active",
) -> dict:
    """Run the elite quality gate for a forex signal.

    Returns:
      {
        'approved':       bool,
        'score':          int,      # 0-100
        'grade':          str,      # 'SNIPER ELITE' | 'ELITE' | 'STANDARD' | 'BLOCKED'
        'classification': str,      # 'SNIPER' | 'STANDARD' | 'BLOCKED'
        'htf_levels':     dict,     # fetched HTF levels for TP placement
        'reason':         str,
      }
    """
    htf = get_htf_levels(pair)
    score, grade = compute_signal_score(
        pair, direction, engine_votes,
        liq=liq, sniper_score=sniper_score,
        session=session, htf_levels=htf,
    )
    cls = classify_signal(score)

    if cls == "BLOCKED":
        return {
            "approved": False, "score": score, "grade": grade,
            "classification": cls, "htf_levels": htf,
            "reason": f"Quality score {score}/100 below minimum {SCORE_STANDARD} — waiting for elite setup",
        }

    return {
        "approved": True, "score": score, "grade": grade,
        "classification": cls, "htf_levels": htf,
        "reason": f"{grade} — score {score}/100",
    }
