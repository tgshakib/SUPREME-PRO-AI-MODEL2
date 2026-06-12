"""FINORIX AI — MULTI-STRATEGY SIGNAL ENGINE
============================================
6-strategy master voting engine. Drop-in addition.
ZERO changes to existing signal text/formatting.

Compatible: Quotex OTC, Pocket Option OTC, Forex/Live, all asset classes.

Strategies:
  S1 Sniper         — Liquidity grab + double bottom at support
  S2 FRVP           — Fixed Range Volume Profile (VAH/VAL/POC traps)
  S3 Turning Point  — HTF significant zones + conviction candle
  S4 Pre-Market S/R — Gap classification + S/R level trade
  S5 Breakout       — Real vs fake breakout filter (momentum + body)
  S6 Liq Scalp      — Daily high/low liquidity grab + confirmation

Signal text contract: NEVER touched. Engine returns direction/grade/confidence
metadata only. signals.py uses these as silent extra votes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

try:
    import numpy as np
    import pandas as pd
    _PD_OK = True
except Exception:
    np = None  # type: ignore
    pd = None  # type: ignore
    _PD_OK = False

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 18.0   # seconds — same cadence as other engines


# ══════════════════════════════════════════════════════════════════════════════
#  ENUMS & CONFIG
# ══════════════════════════════════════════════════════════════════════════════

class _Sig(Enum):
    BUY  = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

class _Trend(Enum):
    BULLISH  = "bullish"
    BEARISH  = "bearish"
    SIDEWAYS = "sideways"

@dataclass
class _Cfg:
    sniper_lookback:       int   = 30
    sniper_shakeout_pct:   float = 0.003
    sniper_db_tolerance:   float = 0.001
    frvp_session_bars:     int   = 390
    frvp_value_area_pct:   float = 0.70
    tp_htf_bars:           int   = 100
    tp_zone_tolerance:     float = 0.002
    premarket_heavy_gap:   float = 0.005
    premarket_normal_gap:  float = 0.002
    bo_momentum_bars:      int   = 5
    bo_revisit_tolerance:  float = 0.0005
    liq_confirmation_bars: int   = 3


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _ema(s: "pd.Series", p: int) -> "pd.Series":
    return s.ewm(span=p, adjust=False).mean()

def _atr(df: "pd.DataFrame", p: int = 14) -> "pd.Series":
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def _trend(df: "pd.DataFrame") -> "_Trend":
    fast = _ema(df["close"], 20)
    slow = _ema(df["close"], 50)
    last = df.tail(10)
    prev = df["high"].iloc[-20:-10]
    hh = last["high"].max() > prev.max()
    hl = last["low"].min()  > df["low"].iloc[-20:-10].min()
    lh = last["high"].max() < prev.max()
    ll = last["low"].min()  < df["low"].iloc[-20:-10].min()
    if fast.iloc[-1] > slow.iloc[-1] and hh and hl:
        return _Trend.BULLISH
    if fast.iloc[-1] < slow.iloc[-1] and lh and ll:
        return _Trend.BEARISH
    return _Trend.SIDEWAYS

def _sr(df: "pd.DataFrame", lookback: int = 50) -> tuple[float, float]:
    w = df.tail(lookback)
    return w["low"].nsmallest(3).mean(), w["high"].nlargest(3).mean()


# ══════════════════════════════════════════════════════════════════════════════
#  S1 — SNIPER  (liquidity grab + double bottom)
# ══════════════════════════════════════════════════════════════════════════════

def _s1_sniper(df: "pd.DataFrame", cfg: _Cfg) -> tuple["_Sig", float]:
    n = len(df)
    if n < cfg.sniper_lookback + 5:
        return _Sig.WAIT, 0.0
    try:
        if _trend(df) != _Trend.BULLISH:
            return _Sig.WAIT, 0.0
        c, h, l = df["close"], df["high"], df["low"]
        win = df.iloc[-cfg.sniper_lookback:-1]
        sup = win["low"].nsmallest(5).mean()
        shakeout_lvl = sup * (1 - cfg.sniper_shakeout_pct)
        shakeout = l.iloc[-3] < shakeout_lvl or l.iloc[-4] < shakeout_lvl
        liq_grab = shakeout and float(c.iloc[-1]) > sup
        lows2 = l.iloc[-8:].nsmallest(2)
        double_bot = False
        if len(lows2) == 2:
            diff = abs(float(lows2.iloc[0]) - float(lows2.iloc[1])) / max(abs(float(lows2.iloc[0])), 1e-10)
            double_bot = diff < cfg.sniper_db_tolerance
        last = df.iloc[-1]
        body = abs(float(last["close"]) - float(last["open"]))
        lwick = float(last["open"]) - float(last["low"]) if float(last["close"]) > float(last["open"]) else float(last["close"]) - float(last["low"])
        rejection = lwick > body * 1.5
        conf = sum([
            0.25 if shakeout else 0,
            0.25 if liq_grab  else 0,
            0.30 if double_bot else (0.20 if rejection else 0),
            0.20,
        ])
        if liq_grab and (double_bot or rejection) and conf >= 0.70:
            return _Sig.BUY, conf
    except Exception:
        pass
    return _Sig.WAIT, 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  S2 — FRVP  (Fixed Range Volume Profile — VAH / VAL / POC traps)
# ══════════════════════════════════════════════════════════════════════════════

def _s2_frvp(df: "pd.DataFrame", cfg: _Cfg) -> tuple["_Sig", float]:
    if len(df) < cfg.frvp_session_bars + 20:
        return _Sig.WAIT, 0.0
    try:
        sess = df.tail(cfg.frvp_session_bars)
        lo, hi = float(sess["low"].min()), float(sess["high"].max())
        buckets = 100
        bins = np.linspace(lo, hi, buckets)
        vol_dist = np.zeros(buckets - 1)
        for _, row in sess.iterrows():
            rlo, rhi, vol = float(row["low"]), float(row["high"]), float(row.get("volume", 1) or 1)
            for i, (b1, b2) in enumerate(zip(bins[:-1], bins[1:])):
                overlap = max(0.0, min(rhi, b2) - max(rlo, b1))
                if overlap > 0:
                    vol_dist[i] += vol * (overlap / (rhi - rlo + 1e-9))
        poc_idx = int(vol_dist.argmax())
        target = vol_dist.sum() * cfg.frvp_value_area_pct
        lo_i = hi_i = poc_idx
        acc = vol_dist[poc_idx]
        while acc < target:
            lo_exp = vol_dist[lo_i - 1] if lo_i > 0 else 0
            hi_exp = vol_dist[hi_i + 1] if hi_i < len(vol_dist) - 1 else 0
            if lo_exp >= hi_exp and lo_i > 0:
                lo_i -= 1; acc += lo_exp
            elif hi_i < len(vol_dist) - 1:
                hi_i += 1; acc += hi_exp
            else:
                break
        val = (bins[lo_i] + bins[lo_i + 1]) / 2
        vah = (bins[hi_i] + bins[hi_i + 1]) / 2
        last, prev = df.iloc[-1], df.iloc[-2]
        price = float(last["close"])
        broke_above = float(prev["high"]) > vah and price < vah
        broke_below = float(prev["low"])  < val and price > val
        trap_up   = broke_below and float(last["close"]) > float(last["open"])
        trap_down = broke_above and float(last["close"]) < float(last["open"])
        if trap_up:   return _Sig.BUY,  0.80
        if trap_down: return _Sig.SELL, 0.80
    except Exception:
        pass
    return _Sig.WAIT, 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  S3 — TURNING POINT  (HTF significant zones + conviction candle)
# ══════════════════════════════════════════════════════════════════════════════

def _s3_turning(df: "pd.DataFrame", cfg: _Cfg) -> tuple["_Sig", float]:
    if len(df) < cfg.tp_htf_bars + 10:
        return _Sig.WAIT, 0.0
    try:
        htf = df.tail(cfg.tp_htf_bars)
        pivots: list[float] = []
        for i in range(2, len(htf) - 2):
            rh = float(htf.iloc[i]["high"])
            rl = float(htf.iloc[i]["low"])
            if (rh > float(htf.iloc[i-1]["high"]) and rh > float(htf.iloc[i-2]["high"])
                    and rh > float(htf.iloc[i+1]["high"]) and rh > float(htf.iloc[i+2]["high"])):
                pivots.append(rh)
            if (rl < float(htf.iloc[i-1]["low"]) and rl < float(htf.iloc[i-2]["low"])
                    and rl < float(htf.iloc[i+1]["low"]) and rl < float(htf.iloc[i+2]["low"])):
                pivots.append(rl)
        if not pivots:
            return _Sig.WAIT, 0.0
        price = float(df["close"].iloc[-1])
        zone  = min(pivots, key=lambda z: abs(z - price))
        at_zone = abs(price - zone) / max(price, 1e-10) < cfg.tp_zone_tolerance
        if not at_zone:
            return _Sig.WAIT, 0.0
        last = df.iloc[-1]
        body = abs(float(last["close"]) - float(last["open"]))
        avg_body = float(abs(df["close"] - df["open"]).rolling(20).mean().iloc[-1])
        sig_candle = body > avg_body * 1.8
        recent_price = float(df["close"].iloc[-10])
        is_sup = zone < recent_price
        is_res = zone > recent_price
        if sig_candle and is_sup:   return _Sig.BUY,  0.75
        if sig_candle and is_res:   return _Sig.SELL, 0.75
        if at_zone:                 return _Sig.WAIT, 0.40
    except Exception:
        pass
    return _Sig.WAIT, 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  S4 — PRE-MARKET S/R + GAP CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def _s4_premarket(df: "pd.DataFrame", cfg: _Cfg) -> tuple["_Sig", float]:
    if len(df) < 30:
        return _Sig.WAIT, 0.0
    try:
        prev_close  = float(df["close"].iloc[-2])
        open_price  = float(df["open"].iloc[-1])
        price       = float(df["close"].iloc[-1])
        gap_pct     = (open_price - prev_close) / max(prev_close, 1e-10)
        sup, res    = _sr(df, 40)
        tol         = cfg.tp_zone_tolerance * 2
        near_res    = abs(price - res) / max(res, 1e-10) < tol
        near_sup    = abs(price - sup) / max(sup, 1e-10) < tol
        bull_c      = float(df["close"].iloc[-1]) > float(df["open"].iloc[-1])
        bear_c      = not bull_c
        heavy_up    = gap_pct >=  cfg.premarket_heavy_gap
        normal_up   = cfg.premarket_normal_gap <= gap_pct < cfg.premarket_heavy_gap
        heavy_dn    = gap_pct <= -cfg.premarket_heavy_gap
        normal_dn   = -cfg.premarket_heavy_gap < gap_pct <= -cfg.premarket_normal_gap
        flat        = abs(gap_pct) < cfg.premarket_normal_gap
        if heavy_up or normal_up:
            if near_res and bear_c: return _Sig.SELL, 0.70
            if near_sup and bull_c: return _Sig.BUY,  0.60
        elif heavy_dn or normal_dn:
            if near_sup and bull_c: return _Sig.BUY,  0.70
            if near_res and bear_c: return _Sig.SELL, 0.60
        elif flat:
            if near_res and bear_c: return _Sig.SELL, 0.55
            if near_sup and bull_c: return _Sig.BUY,  0.55
    except Exception:
        pass
    return _Sig.WAIT, 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  S5 — BREAKOUT FILTER  (real vs fake)
# ══════════════════════════════════════════════════════════════════════════════

def _s5_breakout(df: "pd.DataFrame", cfg: _Cfg) -> tuple["_Sig", float]:
    if len(df) < 30:
        return _Sig.WAIT, 0.0
    try:
        tr = _trend(df)
        if tr == _Trend.SIDEWAYS:
            return _Sig.WAIT, 0.0
        sup, res = _sr(df)
        last  = df.iloc[-1]
        prev2 = df.iloc[-3:-1]
        price = float(last["close"])
        broke_up   = float(prev2["high"].max()) < res < price
        broke_down = float(prev2["low"].min())  > sup > price
        if not (broke_up or broke_down):
            return _Sig.WAIT, 0.0
        # Momentum: sum of directional bars / ATR-normalized
        recent = df.tail(cfg.bo_momentum_bars)
        moves  = float((recent["close"] - recent["open"]).sum())
        atr_v  = float(_atr(df, 14).iloc[-1])
        mom    = moves / (atr_v * cfg.bo_momentum_bars + 1e-9)
        body_pct = (abs(float(last["close"]) - float(last["open"])) /
                    (float(last["high"]) - float(last["low"]) + 1e-9))
        small_body = body_pct < 0.30
        revisit    = abs(price - (res if broke_up else sup)) < atr_v * cfg.bo_revisit_tolerance * 100
        real = abs(mom) > 0.5 and not small_body and not revisit
        if broke_up  and tr == _Trend.BULLISH  and real:
            return _Sig.BUY,  min(0.85, 0.60 + abs(mom) * 0.15)
        if broke_down and tr == _Trend.BEARISH and real:
            return _Sig.SELL, min(0.85, 0.60 + abs(mom) * 0.15)
    except Exception:
        pass
    return _Sig.WAIT, 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  S6 — LIQUIDITY SCALP  (daily high/low grab + confirmation)
# ══════════════════════════════════════════════════════════════════════════════

def _s6_liq_scalp(df: "pd.DataFrame", cfg: _Cfg) -> tuple["_Sig", float]:
    daily_bars = 288   # 5m candles per day
    if len(df) < daily_bars + 20:
        return _Sig.WAIT, 0.0
    try:
        prev = df.iloc[-(daily_bars + daily_bars): -daily_bars]
        if len(prev) < 10:
            return _Sig.WAIT, 0.0
        pd_high = float(prev["high"].max())
        pd_low  = float(prev["low"].min())
        last    = df.iloc[-1]
        price   = float(last["close"])
        atr_v   = float(_atr(df, 14).iloc[-1])
        zone    = atr_v * 0.1
        touch_h = abs(price - pd_high) < zone or float(last["high"]) >= pd_high
        touch_l = abs(price - pd_low)  < zone or float(last["low"])  <= pd_low
        if not (touch_h or touch_l):
            return _Sig.WAIT, 0.0
        cw = df.tail(cfg.liq_confirmation_bars)
        if touch_h:
            if (cw["close"] < cw["open"]).sum() >= 2:
                return _Sig.SELL, 0.78
        if touch_l:
            if (cw["close"] > cw["open"]).sum() >= 2:
                return _Sig.BUY, 0.78
    except Exception:
        pass
    return _Sig.WAIT, 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  MASTER VOTING ENGINE — combines S1-S6
# ══════════════════════════════════════════════════════════════════════════════

def _grade(conf: float, votes: int) -> str:
    if conf >= 0.90 and votes >= 4: return "A+++"
    if conf >= 0.82 and votes >= 3: return "A++"
    if conf >= 0.74 and votes >= 3: return "A+"
    if conf >= 0.65 and votes >= 2: return "A"
    if conf >= 0.55 and votes >= 2: return "B"
    return "C"


def _run_master(df: "pd.DataFrame") -> dict:
    cfg = _Cfg()
    if "volume" not in df.columns:
        df = df.copy()
        df["volume"] = 1.0

    strats = [
        ("S1_Sniper",        _s1_sniper(df, cfg)),
        ("S2_FRVP",          _s2_frvp(df, cfg)),
        ("S3_TurningPoint",  _s3_turning(df, cfg)),
        ("S4_PreMarket",     _s4_premarket(df, cfg)),
        ("S5_Breakout",      _s5_breakout(df, cfg)),
        ("S6_LiqScalp",      _s6_liq_scalp(df, cfg)),
    ]

    votes_buy = votes_sell = 0
    w_sum = w_tot = 0.0
    active: list[str] = []
    breakdown: dict = {}

    for name, (sig, conf) in strats:
        breakdown[name] = {"signal": sig.value, "confidence": round(conf * 100, 1)}
        if sig == _Sig.BUY:
            votes_buy  += 1; w_sum += conf; w_tot += 1; active.append(name)
        elif sig == _Sig.SELL:
            votes_sell += 1; w_sum += conf; w_tot += 1; active.append(name)

    avg_conf = (w_sum / w_tot) if w_tot > 0 else 0.0

    if   votes_buy  > votes_sell and votes_buy  >= 2: final = "BUY"
    elif votes_sell > votes_buy  and votes_sell >= 2: final = "SELL"
    else:                                              final = "WAIT"; avg_conf = 0.0

    vmx = max(votes_buy, votes_sell) if final != "WAIT" else 0
    g   = _grade(avg_conf, vmx) if final != "WAIT" else "C"

    try:
        atr_v = float(_atr(df, 14).iloc[-1])
    except Exception:
        atr_v = 0.0

    return {
        "direction":  final,
        "grade":      g,
        "confidence": round(avg_conf * 100, 1),
        "votes_buy":  votes_buy,
        "votes_sell": votes_sell,
        "strategies": active,
        "breakdown":  breakdown,
        "sl_pips":    round(atr_v * 1.5, 5),
        "tp_pips":    round(atr_v * 3.0, 5),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  DATA FETCH — real-time bridge → yfinance fallback
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_df(pair: str, is_otc: bool) -> "Optional[pd.DataFrame]":
    if not _PD_OK:
        return None

    # OTC pairs: try live broker WS first
    if is_otc:
        try:
            from otc_realtime_bridge import get_otc_df as _rt
            df = _rt(pair, "5m", count=500)
            if df is not None and len(df) >= 50:
                return df
        except Exception:
            pass
        try:
            from otc_feed import get_otc_df as _otc
            df = _otc(pair, "5m", count=500)
            if df is not None and len(df) >= 50:
                return df
        except Exception:
            pass

    # yfinance fallback (live pairs + OTC fallback)
    try:
        import yfinance as yf
        from live_prices import yf_ticker
        ticker = yf_ticker(pair)
        if not ticker:
            return None
        df = yf.download(ticker, period="5d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 50:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = [
                str(c[0]).lower() if isinstance(c, tuple) else str(c).lower()
                for c in df.columns
            ]
        else:
            df.columns = [str(c).lower() for c in df.columns]
        if not {"open", "high", "low", "close"}.issubset(df.columns):
            return None
        return df.tail(500).reset_index(drop=True)
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def finorix_multi_analyze(pair: str, is_otc: bool = False) -> Optional[dict]:
    """Run all 6 strategies and return consensus result, or None on failure.

    Return dict keys (compatible with signals.py vote system):
        direction   "BUY" | "SELL" | "WAIT"
        grade       "A+++" | "A++" | "A+" | "A" | "B" | "C"
        confidence  float 0-100
        votes_buy   int
        votes_sell  int
        strategies  list[str]   — names of strategies that fired
        breakdown   dict        — per-strategy detail
        sl_pips     float
        tp_pips     float

    Returns None when:
        • pandas/numpy unavailable
        • insufficient candle data
        • analysis raises unexpectedly
    """
    if not _PD_OK:
        return None

    now = time.time()
    cached = _CACHE.get(pair)
    if cached and (now - cached[0]) < _TTL:
        return cached[1]

    try:
        df = _fetch_df(pair, is_otc)
        if df is None or len(df) < 50:
            _CACHE[pair] = (now, None)
            return None

        result = _run_master(df)
        _CACHE[pair] = (now, result)
        return result

    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("[finorix_multi] %s error: %s", pair, e)
        _CACHE[pair] = (now, None)
        return None
