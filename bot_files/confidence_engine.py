"""confidence_engine.py — Ultra God Level Engine: Module 7
Combines all module scores into final 0-100 confidence.
Signal accepted only if confidence >= 80.

Score breakdown (total 100)
───────────────────────────
• HTF alignment    : 20  (htf_alignment)
• Liquidity zone   : 20  (liquidity_zones)
• Momentum gate    : 15  (momentum_gate)
• Volatility       : 15  (volatility_adapter)
• Candle body      : 10  (volatility_adapter)
• Entry precision  : 10  (entry_precision)
• Market regime    : 10  (regime_filter)

Returns
-------
{
    "confidence":  int 0-100,
    "accept":      bool,
    "grade":       "GOD" | "ELITE" | "STRONG" | "WEAK" | "SKIP",
    "breakdown":   dict[str, int],
    "reason":      str,
}
"""
from __future__ import annotations

# ── Minimum confidence threshold to fire a signal ──────────────────────────
MIN_CONFIDENCE = 80


def calculate_confidence(
    *,
    htf_score:   int = 0,
    liq_score:   int = 0,
    mom_score:   int = 0,
    vol_score:   int = 0,
    body_score:  int = 0,
    entry_score: int = 0,
    regime_score: int = 0,
) -> dict:
    """Combine all module scores into a single confidence rating.

    Parameters correspond to each module's score output.
    Total possible = 100.
    Threshold for signal = 80.
    """
    breakdown = {
        "htf_alignment":   min(20, htf_score),
        "liquidity_zone":  min(20, liq_score),
        "momentum":        min(15, mom_score),
        "volatility":      min(15, vol_score),
        "candle_body":     min(10, body_score),
        "entry_precision": min(10, entry_score),
        "market_regime":   min(10, regime_score),
    }
    confidence = sum(breakdown.values())
    accept     = confidence >= MIN_CONFIDENCE

    if   confidence >= 95: grade = "GOD"
    elif confidence >= 88: grade = "ELITE"
    elif confidence >= 80: grade = "STRONG"
    elif confidence >= 65: grade = "WEAK"
    else:                  grade = "SKIP"

    reason = (
        f"Confidence={confidence}/100 "
        f"HTF={breakdown['htf_alignment']} "
        f"LIQ={breakdown['liquidity_zone']} "
        f"MOM={breakdown['momentum']} "
        f"VOL={breakdown['volatility']} "
        f"BODY={breakdown['candle_body']} "
        f"ENTRY={breakdown['entry_precision']} "
        f"REGIME={breakdown['market_regime']} "
        f"→ {'ACCEPT' if accept else 'REJECT'} ({grade})"
    )

    return {
        "confidence": confidence,
        "accept":     accept,
        "grade":      grade,
        "breakdown":  breakdown,
        "reason":     reason,
    }
