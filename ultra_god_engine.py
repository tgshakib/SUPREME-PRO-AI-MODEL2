"""ultra_god_engine.py — Ultra God Level Analysis Orchestrator
Chains all 9 modules into a single call:
  regime_filter → htf_alignment → liquidity_zones → momentum_gate
  → volatility_adapter → entry_precision → confidence_engine
  → risk_guard → debug_report

Contract
────────
• NEVER modifies any signal text, menu, command, or bot message format.
• Acts as a silent confidence gate — the existing signal engines keep running.
• Returns "accept" bool + confidence score; wires into signals.py as extra vote.
• All modules use TradingView TA (candle_feed.py) — no pandas/yfinance needed.
• Completes in < 3 seconds (20-second per-pair cache on each module).

Usage
─────
    from ultra_god_engine import ultra_analyze

    result = ultra_analyze("EURUSD", direction="BUY", is_otc=False)
    if result["accept"]:
        # direction is confirmed — add to signal engine votes
"""
from __future__ import annotations

import time
from typing import Any, Optional

# ── Module imports (all fail-safe) ─────────────────────────────────────────
try:
    from regime_filter     import detect_regime
    _REGIME_OK = True
except Exception as _e:
    print(f"[ultra_god] regime_filter import failed: {_e}")
    detect_regime = None  # type: ignore
    _REGIME_OK = False

try:
    from htf_alignment     import check_htf_alignment
    _HTF_OK = True
except Exception as _e:
    print(f"[ultra_god] htf_alignment import failed: {_e}")
    check_htf_alignment = None  # type: ignore
    _HTF_OK = False

try:
    from liquidity_zones   import analyze_liquidity_zones
    _LIQ_OK = True
except Exception as _e:
    print(f"[ultra_god] liquidity_zones import failed: {_e}")
    analyze_liquidity_zones = None  # type: ignore
    _LIQ_OK = False

try:
    from momentum_gate     import check_momentum
    _MOM_OK = True
except Exception as _e:
    print(f"[ultra_god] momentum_gate import failed: {_e}")
    check_momentum = None  # type: ignore
    _MOM_OK = False

try:
    from volatility_adapter import check_volatility
    _VOL_OK = True
except Exception as _e:
    print(f"[ultra_god] volatility_adapter import failed: {_e}")
    check_volatility = None  # type: ignore
    _VOL_OK = False

try:
    from entry_precision   import assess_entry
    _ENTRY_OK = True
except Exception as _e:
    print(f"[ultra_god] entry_precision import failed: {_e}")
    assess_entry = None  # type: ignore
    _ENTRY_OK = False

try:
    from confidence_engine import calculate_confidence
    _CONF_OK = True
except Exception as _e:
    print(f"[ultra_god] confidence_engine import failed: {_e}")
    calculate_confidence = None  # type: ignore
    _CONF_OK = False

try:
    from risk_guard        import check_allowed, record_signal
    _RISK_OK = True
except Exception as _e:
    print(f"[ultra_god] risk_guard import failed: {_e}")
    check_allowed  = None  # type: ignore
    record_signal  = None  # type: ignore
    _RISK_OK = False

try:
    from debug_report      import log_decision
    _DBG_OK = True
except Exception as _e:
    log_decision = None  # type: ignore
    _DBG_OK = False

# ── Result cache (20s) ──────────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL   = 20.0


def _cached(pair: str, direction: str | None, is_otc: bool) -> Optional[dict]:
    key = f"{pair}|{direction}|{is_otc}"
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[0]) < _TTL:
        return entry[1]
    return None


def _store(pair: str, direction: str | None, is_otc: bool, result: dict) -> dict:
    _CACHE[f"{pair}|{direction}|{is_otc}"] = (time.time(), result)
    return result


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_call(fn, *args, default=None, **kwargs):
    """Call a module function; return default on any exception."""
    if fn is None:
        return default
    try:
        return fn(*args, **kwargs)
    except Exception as _e:
        print(f"[ultra_god] {fn.__name__} error: {_e}")
        return default


# ── Main entry point ─────────────────────────────────────────────────────────

def ultra_analyze(
    pair:            str,
    direction:       str | None = None,
    is_otc:          bool = False,
    market:          str  = "LIVE",
    _override_data:  dict | None = None,   # for demo/backtest mode
) -> dict:
    """Run the full 9-module ultra-strict analysis pipeline.

    Parameters
    ──────────
    pair       : e.g. "EURUSD", "XAUUSD", "BTCUSD"
    direction  : "BUY" | "SELL" | None
                 When None the engine tries to infer direction from HTF + regime.
    is_otc     : True for OTC (Pocket Option / Quotex) markets
    market     : "OTC" | "LIVE" — used in risk_guard session checks
    _override_data : In demo/backtest mode only — inject a candle dict

    Returns
    ───────
    {
        "accept":       bool,
        "confidence":   int 0-100,
        "grade":        "GOD" | "ELITE" | "STRONG" | "WEAK" | "SKIP",
        "direction":    "BUY" | "SELL" | None,
        "reason":       str,
        "breakdown":    dict,
        "modules":      dict  (per-module results for debugging),
        "risk_allowed": bool,
    }
    """
    cached = _cached(pair, direction, is_otc)
    if cached is not None:
        return cached

    modules: dict[str, Any] = {}

    # ── Module 1: Regime Filter ─────────────────────────────────────────────
    regime = _safe_call(detect_regime, pair,
                        default={"regime": "UNKNOWN", "quality": 0.5, "score": 5,
                                 "reason": "unavailable"})
    modules["regime_filter"] = regime

    # Skip if market is ranging badly (regime quality < 0.25 + RANGE label)
    if regime["regime"] == "RANGE" and regime["quality"] < 0.25:
        result = _skip(pair, direction, "Market is ranging badly — signal skipped",
                       modules, is_otc)
        return _store(pair, direction, is_otc, result)

    # ── Module 2: HTF Alignment ─────────────────────────────────────────────
    htf = _safe_call(check_htf_alignment, pair,
                     default={"aligned": False, "direction": None, "score": 0,
                               "tf_votes": {}, "reason": "unavailable"})
    modules["htf_alignment"] = htf

    # Infer direction from HTF if caller didn't specify one
    if direction is None and htf.get("direction"):
        direction = htf["direction"]

    # ── Module 3: Liquidity Zones ───────────────────────────────────────────
    liq = _safe_call(analyze_liquidity_zones, pair, direction,
                     default={"zone_type": "neutral", "zone_dir": None,
                               "quality": 0.3, "score": 0, "near_zone": False,
                               "reason": "unavailable"})
    modules["liquidity_zones"] = liq

    # Update direction from liquidity zone if still unknown
    if direction is None and liq.get("zone_dir"):
        direction = liq["zone_dir"]

    # Hard skip: fakeout detected
    if liq["zone_type"] == "fakeout" and liq["quality"] < 0.30:
        result = _skip(pair, direction, "Fakeout zone — signal skipped", modules, is_otc)
        return _store(pair, direction, is_otc, result)

    # ── Module 4: Momentum Gate ─────────────────────────────────────────────
    mom = _safe_call(check_momentum, pair, direction,
                     default={"pass": True, "direction": direction, "rsi_ok": True,
                               "score": 8, "reason": "unavailable"})
    modules["momentum_gate"] = mom

    # ── Module 5: Volatility Adapter ────────────────────────────────────────
    vol = _safe_call(check_volatility, pair,
                     default={"pass": True, "condition": "OK",
                               "vol_score": 10, "body_score": 7, "total_score": 17,
                               "atr_pct": 0.001, "body_ratio": 0.5,
                               "reason": "unavailable"})
    modules["volatility_adapter"] = vol

    # Hard skip: dead or spike market
    if not vol["pass"]:
        result = _skip(pair, direction,
                       f"Volatility blocked [{vol['condition']}] — signal skipped",
                       modules, is_otc)
        return _store(pair, direction, is_otc, result)

    # ── Module 6: Entry Precision ───────────────────────────────────────────
    entry = _safe_call(assess_entry, pair, direction, liq.get("quality", 0.5),
                       default={"quality": "ACCEPTABLE", "score": 5,
                                 "reason": "unavailable"})
    modules["entry_precision"] = entry

    # Skip late entries
    if entry["quality"] == "LATE":
        result = _skip(pair, direction, "Late entry — price already extended",
                       modules, is_otc)
        return _store(pair, direction, is_otc, result)

    # ── Module 7: Confidence Engine ─────────────────────────────────────────
    if _CONF_OK and calculate_confidence is not None:
        conf_result = calculate_confidence(
            htf_score    = htf["score"],
            liq_score    = liq["score"],
            mom_score    = mom["score"],
            vol_score    = vol["vol_score"],
            body_score   = vol["body_score"],
            entry_score  = entry["score"],
            regime_score = regime["score"],
        )
    else:
        raw = (htf["score"] + liq["score"] + mom["score"] +
               vol["vol_score"] + vol["body_score"] + entry["score"] + regime["score"])
        conf_result = {
            "confidence": raw,
            "accept":     raw >= 80,
            "grade":      "STRONG" if raw >= 80 else "SKIP",
            "breakdown":  {},
            "reason":     f"Confidence={raw} (fallback)",
        }
    modules["confidence_engine"] = conf_result

    confidence = conf_result["confidence"]
    accept     = conf_result["accept"]
    grade      = conf_result["grade"]

    # ── Module 8: Risk Guard ────────────────────────────────────────────────
    risk_ok = True
    risk_reason = "ok"
    if _RISK_OK and check_allowed is not None and accept:
        risk = _safe_call(check_allowed, pair, direction or "BUY", is_otc,
                          default={"allowed": True, "reason": "unavailable",
                                    "cooldown_remaining": 0})
        modules["risk_guard"] = risk
        risk_ok     = risk["allowed"]
        risk_reason = risk["reason"]
        if not risk_ok:
            accept = False
            grade  = "SKIP"

    # ── Module 9: Debug Report ─────────────────────────────────────────────
    if _DBG_OK and log_decision is not None:
        _safe_call(log_decision,
                   pair=pair, direction=direction, accepted=accept,
                   confidence=confidence, modules=modules,
                   reason=conf_result["reason"])

    # Record signal in risk_guard memory
    if accept and _RISK_OK and record_signal is not None:
        _safe_call(record_signal, pair, direction or "BUY", "open")

    full_reason = conf_result["reason"]
    if not risk_ok:
        full_reason = f"RISK BLOCKED: {risk_reason}"

    result = {
        "accept":       accept,
        "confidence":   confidence,
        "grade":        grade,
        "direction":    direction,
        "reason":       full_reason,
        "breakdown":    conf_result.get("breakdown", {}),
        "modules":      modules,
        "risk_allowed": risk_ok,
    }

    print(f"[ultra_god] {pair} {direction or 'AUTO'} "
          f"{'OTC' if is_otc else 'LIVE'} → "
          f"{'✅ ACCEPT' if accept else '❌ REJECT'} "
          f"conf={confidence} grade={grade}")

    return _store(pair, direction, is_otc, result)


def _skip(pair: str, direction: str | None, reason: str,
          modules: dict, is_otc: bool) -> dict:
    """Return a rejection result and log it."""
    if _DBG_OK and log_decision is not None:
        _safe_call(log_decision,
                   pair=pair, direction=direction, accepted=False,
                   confidence=0, modules=modules, reason=reason)
    print(f"[ultra_god] {pair} SKIP → {reason}")
    return {
        "accept":       False,
        "confidence":   0,
        "grade":        "SKIP",
        "direction":    direction,
        "reason":       reason,
        "breakdown":    {},
        "modules":      modules,
        "risk_allowed": True,
    }
