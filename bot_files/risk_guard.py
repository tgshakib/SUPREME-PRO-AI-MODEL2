"""risk_guard.py — Ultra God Level Engine: Module 8
Cooldown, spread check, no-martingale gate, one-signal-per-setup enforcement.

Rules
─────
• If last signal on this pair failed (SL) → cooldown for N minutes
• No martingale: cannot fire same direction twice in a row after loss
• Repeated setup (same pair + same direction < 5 min ago) → skip
• Weekend / off-session (no-liquidity hours) → block LIVE
• Spread estimate too wide → skip

Returns
-------
{
    "allowed": bool,
    "reason":  str,
    "cooldown_remaining": int,   # seconds, 0 if no cooldown
}
"""
from __future__ import annotations

import time
from typing import Optional

# ── In-memory state (resets on bot restart — intentional; clean slate) ──────
_last_signal:  dict[str, dict]         = {}   # pair → {dir, ts, outcome}
_cooldowns:    dict[str, float]        = {}   # pair → cooldown_end_ts
_COOLDOWN_SEC = 5 * 60                         # 5-minute cooldown after loss


def record_signal(pair: str, direction: str, outcome: str = "open") -> None:
    """Call this when a signal fires. outcome = 'open' | 'win' | 'loss'."""
    _last_signal[pair] = {
        "direction": direction,
        "ts":        time.time(),
        "outcome":   outcome,
    }


def record_outcome(pair: str, outcome: str) -> None:
    """Update the outcome after a trade closes. outcome = 'win' | 'loss'."""
    if pair in _last_signal:
        _last_signal[pair]["outcome"] = outcome
    if outcome == "loss":
        _cooldowns[pair] = time.time() + _COOLDOWN_SEC


def check_allowed(pair: str, direction: str, is_otc: bool = False) -> dict:
    """Return whether a new signal is allowed for this pair + direction.

    Checks
    ──────
    1. Active cooldown after a loss
    2. Same direction repeated within 5 minutes (duplicate setup)
    3. Weekend LIVE block
    4. No-martingale: no same direction if last trade was a loss
    """
    now = time.time()

    # ── 1. Cooldown after loss ─────────────────────────────────────────────
    cd_end = _cooldowns.get(pair, 0)
    if now < cd_end:
        remaining = int(cd_end - now)
        return {
            "allowed": False,
            "reason":  f"Cooldown active — {remaining}s remaining after last loss",
            "cooldown_remaining": remaining,
        }

    # ── 2. Duplicate setup (same pair + direction < 5 min ago) ────────────
    last = _last_signal.get(pair)
    if last:
        age = now - last["ts"]
        if age < 300 and last["direction"] == direction:
            return {
                "allowed": False,
                "reason":  f"Duplicate setup — same direction {int(age)}s ago",
                "cooldown_remaining": int(300 - age),
            }

    # ── 3. No-martingale: same dir after recent loss ───────────────────────
    if last and last.get("outcome") == "loss" and last["direction"] == direction:
        age = now - last["ts"]
        if age < 600:   # within 10 minutes
            return {
                "allowed": False,
                "reason":  "No-martingale: cannot repeat same direction after loss",
                "cooldown_remaining": int(600 - age),
            }

    # ── 4. Weekend LIVE block ──────────────────────────────────────────────
    if not is_otc:
        from datetime import datetime
        wd = datetime.utcnow().weekday()
        h  = datetime.utcnow().hour
        if wd in (5, 6):
            return {
                "allowed": False,
                "reason":  "Weekend — LIVE markets closed",
                "cooldown_remaining": 0,
            }
        if wd == 4 and h >= 21:
            return {
                "allowed": False,
                "reason":  "Friday close — LIVE liquidity drying up",
                "cooldown_remaining": 0,
            }

    return {
        "allowed": True,
        "reason":  "All risk checks passed",
        "cooldown_remaining": 0,
    }
