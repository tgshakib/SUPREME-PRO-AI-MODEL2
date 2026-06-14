"""
FOREX QUICK SNIPER ENGINE — SUPREME PRO AI
==========================================
Fast (< 1 second) multi-source consensus detector.
Runs at the START of every forex signal request, before the normal
analysis path.

Detects three high-probability setups:
  1. HUNT     — liquidity sweep: price wicked beyond prior swing and
                reversed hard (stop hunt, now real move opposite)
  2. FAKEOUT  — false breakout: price broke level but closed back
                inside (trapped breakout traders)
  3. REAL MOVE — momentum breakout: strong body close beyond prior
                swing with multi-engine confirmation

Contract:
  • NEVER modifies signal text.
  • NEVER adds text to signals.
  • Returns None when no qualifying setup found (grade < 80).
  • Returns a dict when a high-conviction setup is confirmed.
"""
from __future__ import annotations
from typing import Optional, Tuple
import time

# ── Optional imports (graceful degradation) ────────────────────────────────
try:
    from live_prices import (
        get_market_bias  as _get_bias,
        get_live_price   as _get_price,
        pip_size         as _pip_size,
    )
    _PRICES_OK = True
except Exception:
    _get_bias  = None   # type: ignore
    _get_price = None   # type: ignore
    _pip_size  = None   # type: ignore
    _PRICES_OK = False

try:
    from strategy import analyze_pair as _sniper_analyze
    _SNIPER_OK = True
except Exception:
    _sniper_analyze = None  # type: ignore
    _SNIPER_OK      = False

try:
    from fx_expert import fx_analyze as _fx_analyze
    _FX_OK = True
except Exception:
    _fx_analyze = None  # type: ignore
    _FX_OK      = False

try:
    from trade_entry import (
        analyze  as _smart_analyze,
        is_valid as _smart_valid,
    )
    _SMART_OK = True
except Exception:
    _smart_analyze = None   # type: ignore
    _smart_valid   = None   # type: ignore
    _SMART_OK      = False

try:
    from finorix_elite_engine import analyze as _elite_analyze
    _ELITE_OK = True
except Exception:
    _elite_analyze = None   # type: ignore
    _ELITE_OK      = False

# ── Minimum TP floors by asset class ─────────────────────────────────────
_MIN_TP_PIPS_FOREX = 100   # forex:   100 pips minimum first TP
_MIN_TP_USD_METAL  = 100   # Gold:    $100 minimum first TP
_MIN_TP_USD_CRYPTO = 500   # Crypto:  $500 minimum first TP
_MIN_TP_PTS_INDEX  = 100   # Indices: 100 points minimum first TP

# ── Result cache: avoid re-running within same 30 s window ────────────────
_cache: dict[str, Tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 30.0   # seconds


def _is_metal(pair: str) -> bool:
    p = (pair or "").upper()
    return any(k in p for k in ("XAU", "XAG", "GOLD", "SILVER"))


def _is_crypto(pair: str) -> bool:
    p = (pair or "").upper()
    return any(k in p for k in ("BTC", "ETH", "SOL", "USDT", "-USD"))


def _is_index(pair: str) -> bool:
    p = (pair or "").upper()
    return any(k in p for k in ("NAS", "NDX", "DJI", "SPX", "US30", "US500"))


def _min_tp_for(pair: str) -> float:
    if _is_metal(pair):
        return float(_MIN_TP_USD_METAL)
    if _is_crypto(pair):
        return float(_MIN_TP_USD_CRYPTO)
    if _is_index(pair):
        return float(_MIN_TP_PTS_INDEX)
    return float(_MIN_TP_PIPS_FOREX)


def _sl_pips_for(grade: int) -> int:
    """Tighter SL for higher-grade signals (pip count for forex)."""
    if grade >= 96:
        return 8
    if grade >= 90:
        return 10
    if grade >= 85:
        return 14
    return 16


def forex_quick_sniper(pair: str) -> Optional[dict]:
    """
    Run a fast multi-source consensus check for the given forex pair.

    Returns None  — when no qualifying setup is found (grade < 80) or
                    when too many engines disagree.
    Returns dict  — when a high-conviction setup is confirmed:
        {
            direction    : "BUY" | "SELL",
            grade        : int (0-100),
            sl_pips      : int (tight SL in pips for forex),
            min_tp       : float (minimum first-TP distance, pips or $),
            signal_type  : "HUNT" | "FAKEOUT" | "REAL_MOVE" | "CONSENSUS",
            engines_agree: int,
            engines_total: int,
        }
    """
    # ── Cache check ────────────────────────────────────────────────────────
    now = time.time()
    if pair in _cache:
        ts, cached = _cache[pair]
        if now - ts < _CACHE_TTL:
            return cached

    if not _PRICES_OK:
        _cache[pair] = (now, None)
        return None

    votes: dict[str, int] = {"BUY": 0, "SELL": 0}
    total_w = 0
    setup_types: list[str] = []
    smart_dir: Optional[str] = None

    # ── Vote 1: SMART AI (Sweep ▸ BoS ▸ MS) — weight 4 ──────────────────
    # The user's Pine v6 port — sweep/fakeout/real-move in one signal.
    try:
        if _SMART_OK and _smart_analyze and _smart_valid:
            sm = _smart_analyze(pair)
            if sm and _smart_valid(sm, min_grade=72):
                d = sm.get("direction")
                if d in votes:
                    votes[d] += 4
                    total_w  += 4
                    smart_dir = d
                    # Classify the sub-type based on SMART AI internals
                    if sm.get("ms_shift"):
                        setup_types.append("REAL_MOVE")
                    elif sm.get("bos"):
                        setup_types.append("FAKEOUT")
                    else:
                        setup_types.append("HUNT")
    except Exception:
        pass

    # ── Vote 2: Market Bias — weight 2 (3 if very strong) ───────────────
    bias_dir: Optional[str] = None
    bias_str: float         = 0.0
    try:
        if _get_bias is not None:
            bias = _get_bias(pair)
            if bias:
                bias_dir, bias_str = bias
                if bias_dir in votes:
                    w = 3 if bias_str >= 0.80 else 2
                    votes[bias_dir] += w
                    total_w        += w
    except Exception:
        pass

    # ── Vote 3: FX Expert (EMA Fib Ribbon + MACD + Stoch) — weight 2 ───
    try:
        if _FX_OK and _fx_analyze is not None:
            fx = _fx_analyze(pair)
            if fx and fx.get("direction") not in ("WAIT", None):
                d = fx["direction"]
                if d in votes:
                    w = 3 if fx.get("confidence", 0) >= 80 else 2
                    votes[d] += w
                    total_w  += w
    except Exception:
        pass

    # ── Vote 4: Sniper (EMA9/21 + RSI) — weight 1 ───────────────────────
    try:
        if _SNIPER_OK and _sniper_analyze is not None:
            snp = _sniper_analyze(pair)
            if snp and snp.get("direction") in votes:
                votes[snp["direction"]] += 1
                total_w += 1
    except Exception:
        pass

    # ── Vote 5: Finorix Elite Engine — weight 2 (3 if HIDDEN grade) ─────
    try:
        if _ELITE_OK and _elite_analyze is not None:
            fe = _elite_analyze(pair, "LIVE")
            fe_dir = fe.get("direction", "WAIT") if fe else "WAIT"
            if fe_dir not in ("WAIT", None) and fe_dir in votes:
                w = 3 if fe.get("grade") == "HIDDEN" else 2
                votes[fe_dir] += w
                total_w      += w
                if fe.get("trend_phase") == "REVERSAL":
                    setup_types.append("FAKEOUT")
    except Exception:
        pass

    # ── Consensus decision ─────────────────────────────────────────────────
    if total_w == 0:
        _cache[pair] = (now, None)
        return None

    win_dir   = max(votes, key=votes.get)
    win_w     = votes[win_dir]
    opp_dir   = "SELL" if win_dir == "BUY" else "BUY"
    opp_w     = votes.get(opp_dir, 0)
    opp_ratio = opp_w / max(total_w, 1)

    # Require: winning side has ≥ 4 weight points AND is a clear majority
    if win_w < 4 or opp_ratio >= 0.35:
        _cache[pair] = (now, None)
        return None

    # ── Grade calculation ─────────────────────────────────────────────────
    dominance = win_w / max(total_w, 1)
    grade     = int(72 + dominance * 28)

    # Bonuses
    if smart_dir == win_dir:
        grade = min(100, grade + 8)   # SMART AI agreement is strongest signal
    if bias_dir == win_dir and bias_str >= 0.80:
        grade = min(100, grade + 5)
    if opp_w == 0:
        grade = min(100, grade + 4)   # unanimous = maximum conviction

    if grade < 80:
        _cache[pair] = (now, None)
        return None

    # ── Setup type label ──────────────────────────────────────────────────
    if setup_types:
        from collections import Counter
        signal_type = Counter(setup_types).most_common(1)[0][0]
    elif bias_str >= 0.82:
        signal_type = "REAL_MOVE"
    else:
        signal_type = "CONSENSUS"

    result = {
        "direction":     win_dir,
        "grade":         grade,
        "sl_pips":       _sl_pips_for(grade),
        "min_tp":        _min_tp_for(pair),
        "signal_type":   signal_type,
        "engines_agree": win_w,
        "engines_total": total_w,
        "oppose_ratio":  round(opp_ratio, 3),
    }

    _cache[pair] = (now, result)
    return result
