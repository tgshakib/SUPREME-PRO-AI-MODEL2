"""SIGNAL TEXT LOCK — Admin-Only System
=======================================
This module permanently enforces that NO update/system/agent text
ever appears in a signal card sent to users.

LOCKED PERMANENTLY IN PYTHON.
Only the admin (ADMIN_ID in .env) can request additions via the bot.

Rules (immutable):
  1. Signal text contains ONLY trading data fields.
  2. AI agent status, boost levels, version notices, bug-fix notes,
     system updates — ALL are blocked from signal text.
  3. This filter runs as the LAST step before every signal is sent.
  4. Any violation is logged silently and the offending line stripped.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("signal_lock")

# ── Permanently banned content patterns in signal text ───────────────────────
# These patterns are case-insensitive. Add new ones ONLY via admin command.
_BANNED_PATTERNS = [
    r"update[d]?\b",
    r"bug[\s_-]?fix",
    r"patch(ed)?\b",
    r"version\s*\d",
    r"changelog",
    r"auto[\s-]?improv",
    r"self[\s-]?improv",
    r"boost\s+level",
    r"ai\s+(adjust|tuned|upgraded|relaxed)",
    r"winrate\s+guardian",
    r"agent[\s-]?[12]",
    r"claude[\s-]?advis",
    r"system\s+(notice|update|alert|message)",
    r"threshold\s+(tightened|relaxed|changed|adjusted)",
    r"error\s+(fixed|resolved|patched)",
    r"maintenance\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _BANNED_PATTERNS]

# ── Fields that are always allowed in signal text ─────────────────────────────
# (informational — not enforced here, just documented)
_ALLOWED_FIELDS = [
    "CALL / PUT / BUY / SELL header",
    "Pair name",
    "Market (OTC / LIVE)",
    "Timeframe",
    "Signal arrow",
    "Grade",
    "Trend",
    "Confidence",
    "MTG level",
    "Current price",
    "Community handle",
    "Timestamp + EXECUTE NOW",
    "Risk management note",
]


def clean_signal_text(text: str, admin_override: bool = False) -> str:
    """Remove any forbidden line from a signal text string.

    Args:
        text:           The raw signal text about to be sent to users.
        admin_override: If True (admin user), skip the filter (admin can see
                        anything, but signal cards sent to regular users always
                        pass through clean_signal_text(admin_override=False)).

    Returns:
        Sanitised text with all forbidden lines stripped.
    """
    if admin_override:
        return text   # admin bypass — not used for outgoing signal cards

    if not text:
        return text

    lines  = text.split("\n")
    result = []

    for line in lines:
        if _is_forbidden(line):
            log.warning("[SignalLock] BLOCKED forbidden line: %r", line[:80])
        else:
            result.append(line)

    cleaned = "\n".join(result)

    # Remove any double blank lines that stripping may create
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")

    return cleaned


def _is_forbidden(line: str) -> bool:
    """Return True if this line contains any banned pattern."""
    for pattern in _COMPILED:
        if pattern.search(line):
            return True
    return False


def assert_clean(text: str) -> None:
    """Raise ValueError if text contains any banned pattern (use in tests)."""
    for pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            raise ValueError(
                f"SignalLock: forbidden pattern '{m.group()}' found in signal text"
            )
