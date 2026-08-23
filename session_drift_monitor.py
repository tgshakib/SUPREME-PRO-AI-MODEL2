"""Authenticated Quotex session safety monitor.

This module intentionally contains no Telegram rendering or signal-generation
logic.  It is the single authority that decides whether QX tick data belongs to
the currently authenticated WebSocket session and may reach analysis.
"""
from __future__ import annotations

import logging
import os
import time
from threading import RLock
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _bounded_env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


# The requested 15–30 minute re-authentication window, configurable without
# accepting unsafe values outside that range.
MAX_SESSION_AGE_SEC = _bounded_env_int(
    "QX_MAX_SESSION_AGE_SEC", 20 * 60, 15 * 60, 30 * 60,
)
# Percentage of the terminal price.  0.10 means one tenth of one percent.
DRIFT_THRESHOLD_PERCENT = _bounded_env_float(
    "QX_DRIFT_THRESHOLD_PERCENT", 0.10, 0.001, 5.0,
)

_LOCK = RLock()
_STATE: Dict[str, Any] = {
    "session_id": None,
    "session_started_at": 0.0,
    "authenticated": False,
    "blocked_reason": "not_authenticated",
    "requires_reauth": False,
    "last_drift": None,
}


def begin_reauthentication(reason: str) -> None:
    """Block QX analysis until a newly connected authenticated session is set."""
    with _LOCK:
        _STATE.update(
            authenticated=False,
            blocked_reason=reason,
            requires_reauth=True,
        )
    logger.info("[qx_drift] QX analysis blocked; re-authentication requested (%s)", reason)


def mark_authenticated(session_id: str, started_at: Optional[float] = None) -> None:
    """Mark a newly authenticated QX stream as the only analysis-eligible tape.

    ``session_id`` is an opaque local audit id, never a broker token.
    """
    now = time.time() if started_at is None else float(started_at)
    with _LOCK:
        _STATE.update(
            session_id=str(session_id),
            session_started_at=now,
            authenticated=True,
            blocked_reason=None,
            requires_reauth=False,
            last_drift=None,
        )
    logger.info(
        "[qx_drift] QX authenticated session active at %s",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    )


def mark_disconnected(reason: str = "connection_lost") -> None:
    """Immediately stop QX analysis when the authenticated stream ends."""
    with _LOCK:
        _STATE.update(
            authenticated=False,
            blocked_reason=reason,
            requires_reauth=True,
        )
    logger.warning("[qx_drift] QX analysis blocked (%s)", reason)


def end_session(session_id: str, reason: str = "connection_lost") -> None:
    """End the named session without replacing a more specific drift block."""
    with _LOCK:
        if _STATE["session_id"] != session_id:
            return
        if _STATE["blocked_reason"]:
            return
    mark_disconnected(reason)


def _refresh_expiry_locked(now: float) -> None:
    if (
        _STATE["authenticated"]
        and now - float(_STATE["session_started_at"] or 0.0) > MAX_SESSION_AGE_SEC
    ):
        _STATE.update(
            authenticated=False,
            blocked_reason="session_age_exceeded",
            requires_reauth=True,
        )
        logger.warning("[qx_drift] QX session aged out; authenticated data withheld")


def is_qualified(
    *,
    session_id: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """Whether data from the active session may reach QX analysis."""
    current = time.time() if now is None else float(now)
    with _LOCK:
        _refresh_expiry_locked(current)
        if not _STATE["authenticated"] or _STATE["blocked_reason"]:
            return False
        return session_id is None or session_id == _STATE["session_id"]


def needs_reauthentication() -> bool:
    with _LOCK:
        _refresh_expiry_locked(time.time())
        return bool(_STATE["requires_reauth"])


def active_session_metadata() -> Optional[Dict[str, Any]]:
    """Safe audit fields that may be copied to a tick; never includes a token."""
    with _LOCK:
        _refresh_expiry_locked(time.time())
        if not _STATE["authenticated"] or not _STATE["session_id"]:
            return None
        return {
            "source": "qx",
            "session_id": _STATE["session_id"],
            "session_started_at": float(_STATE["session_started_at"]),
        }


def status() -> Dict[str, Any]:
    """Return safe session diagnostics for the owner spot-check command."""
    with _LOCK:
        now = time.time()
        _refresh_expiry_locked(now)
        started_at = float(_STATE["session_started_at"] or 0.0)
        return {
            "qualified": bool(
                _STATE["authenticated"] and not _STATE["blocked_reason"]
            ),
            "session_id": _STATE["session_id"],
            "session_started_at": started_at or None,
            "session_age_sec": max(0.0, now - started_at) if started_at else None,
            "blocked_reason": _STATE["blocked_reason"],
            "requires_reauth": bool(_STATE["requires_reauth"]),
            "max_session_age_sec": MAX_SESSION_AGE_SEC,
            "drift_threshold_percent": DRIFT_THRESHOLD_PERCENT,
            "last_drift": dict(_STATE["last_drift"]) if _STATE["last_drift"] else None,
        }


def check_manual_price(
    *,
    pair: str,
    bot_price: float,
    real_price: float,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Compare a terminal price against the authenticated QX tape.

    Material drift blocks new QX analysis and forces a fresh session before
    data becomes eligible again.  The comparison is deliberately per pair.
    """
    timestamp = time.time() if now is None else float(now)
    if bot_price <= 0 or real_price <= 0:
        raise ValueError("prices must be positive")

    denominator = max(abs(real_price), 1e-12)
    drift_percent = abs(bot_price - real_price) / denominator * 100.0
    flagged = drift_percent > DRIFT_THRESHOLD_PERCENT
    event = {
        "pair": pair,
        "bot_price": float(bot_price),
        "real_price": float(real_price),
        "drift_percent": drift_percent,
        "threshold_percent": DRIFT_THRESHOLD_PERCENT,
        "timestamp": timestamp,
        "flagged": flagged,
    }
    with _LOCK:
        _STATE["last_drift"] = event
        if flagged:
            _STATE.update(
                authenticated=False,
                blocked_reason="manual_price_drift",
                requires_reauth=True,
            )

    if flagged:
        logger.warning(
            "[qx_drift] drift detected pair=%s bot=%.10g terminal=%.10g drift=%.4f%% "
            "threshold=%.4f%% at=%s; QX analysis blocked",
            pair, bot_price, real_price, drift_percent, DRIFT_THRESHOLD_PERCENT,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
        )
    else:
        logger.info(
            "[qx_drift] drift check passed pair=%s bot=%.10g terminal=%.10g "
            "drift=%.4f%% at=%s",
            pair, bot_price, real_price, drift_percent,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
        )
    return event


def _reset_for_tests() -> None:
    """Restore the initial monitor state for isolated regression tests."""
    with _LOCK:
        _STATE.update(
            session_id=None,
            session_started_at=0.0,
            authenticated=False,
            blocked_reason="not_authenticated",
            requires_reauth=False,
            last_drift=None,
        )