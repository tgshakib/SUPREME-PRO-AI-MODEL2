"""Binary Trading Daily Alert System.

Tracks per-user daily binary signal streaks and fires smart, market-aware
alerts with emoji highlights.

LOSS ALERTS  (max 3 per day per user)
  #1 — 2 back-to-back losses  → vanishing message  (market-specific advice)
  #2 — 2 more losses          → vanishing message  (stronger advice)
  #3 — 2 more losses          → vanishing message  (stop-trading warning)

WEEKDAY OTC SPECIAL RULE
  On Mon–Fri, if the user is trading OTC and hits back-to-back losses,
  TWO vanishing messages are sent simultaneously:
    msg 1 — OTC market warning
    msg 2 — live-pair switch recommendation

WIN ALERT  (max 1 per day per user)
  Fires once when consecutive_wins reaches 7 or 8 (non-vanishing message)

Counters reset fresh every UTC calendar day on first interaction.

CANDLE REFUND RULE
  If the outcome tracker marks a trade as "refund" (Doji / Dragon Fly /
  Small Weak Doji candle), that trade is NOT counted in any streak — the
  record_outcome() call simply ignores it (outcome not in win/loss).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import database as db

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_LOSS_ALERTS       = 3
WIN_STREAK_THRESHOLDS = {7, 8}
VANISH_SECONDS        = 9   # same vanish time for all alert messages


# ── Market-aware alert text banks ─────────────────────────────────────────────

# OTC signal back-to-back losses
_LOSS_ALERTS_OTC = [
    (
        "⚠️📉 Back-to-back losses on OTC detected!\n"
        "🔴 OTC market conditions seem unstable right now.\n"
        "✅ 👉 Switch to a LIVE Pair for safer, real-market trading! 🌐"
    ),
    (
        "⛔🔁 2nd OTC loss streak in a row!\n"
        "🕐 Take a short break, then try a LIVE Pair.\n"
        "💡 LIVE markets follow real price action — more reliable! 📊"
    ),
    (
        "🛑❌ 3rd back-to-back OTC loss — STOP TRADING OTC NOW!\n"
        "🔴 Switch to a trusted LIVE Pair or resume tomorrow. 🌐"
    ),
]

# LIVE signal back-to-back losses
_LOSS_ALERTS_LIVE = [
    (
        "⚠️📉 Back-to-back losses on LIVE market detected!\n"
        "🔴 Current pair conditions may be unfavourable.\n"
        "✅ 👉 Try a different LIVE Pair or wait for clearer setup! 📊"
    ),
    (
        "⛔🔁 2nd consecutive LIVE loss streak!\n"
        "🕐 Take a 15-min break before the next trade.\n"
        "💡 Consider switching to a stronger trending LIVE Pair! 🌐"
    ),
    (
        "🛑❌ 3rd back-to-back LIVE loss — STOP TRADING FOR NOW!\n"
        "🔴 Resume tomorrow with fresh market conditions. 🌐"
    ),
]

# Extra weekday OTC → LIVE suggestion (sent as 2nd message on weekdays)
_WEEKDAY_OTC_LIVE_SUGGEST = [
    (
        "🟢💡 It's a weekday — LIVE pairs are fully open!\n"
        "📡 Switch to a LIVE Pair now for real-market signals.\n"
        "🚀 LIVE pairs have genuine price movement and better accuracy!"
    ),
    (
        "🌐⚡ LIVE market is active right now!\n"
        "🔄 Ditch OTC and grab a LIVE Pair for your next signal.\n"
        "🏆 Real-market pairs = real candles = more reliable wins!"
    ),
    (
        "📊✅ Weekday means LIVE pairs are available 24/5!\n"
        "👉 Make the switch to LIVE now — stop losing on OTC.\n"
        "💎 LIVE Pair → better signals → better results!"
    ),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _is_weekday() -> bool:
    """Return True Mon–Fri (UTC).  Sat=5, Sun=6 are weekends."""
    return datetime.now(timezone.utc).weekday() < 5


def _is_otc_market(market_label: str) -> bool:
    label = (market_label or "").upper()
    return "OTC" in label


def _fresh_state(today: str, market: str = "OTC") -> dict:
    return {
        "date":                    today,
        "consecutive_losses":      0,
        "consecutive_wins":        0,
        "loss_alert_count":        0,
        "win_alert_sent":          False,
        "market_type":             market,
        "last_action_after_alert1": None,
    }


async def _send_vanishing(bot, chat_id: int, text: str) -> None:
    """Send a plain-text message and silently delete it after VANISH_SECONDS."""
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text)

        async def _delete():
            await asyncio.sleep(VANISH_SECONDS)
            try:
                await bot.delete_message(chat_id=chat_id,
                                         message_id=msg.message_id)
            except Exception:
                pass

        asyncio.create_task(_delete())
    except Exception as exc:
        log.warning("[DailyAlert] send_vanishing error: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

async def record_outcome(
    user_id:      int,
    outcome:      str,        # "win" | "loss"  ("refund"/"unknown" are skipped)
    market_label: str,        # "PO OTC" | "QX OTC" | "LIVE" …
    bot,
    chat_id:      int,
) -> None:
    """Called after every binary signal outcome is auto-detected.

    Updates the user's daily streak state and fires market-aware alerts
    when loss thresholds are crossed.

    REFUND / UNKNOWN outcomes are silently ignored — they do not affect
    streaks or trigger any alerts (Doji / Dragon Fly / Weak Doji candles).
    """
    # Only track real win / loss — refund and unknown are ignored
    if outcome not in ("win", "loss"):
        return

    today     = _today_utc()
    raw       = db.get_binary_daily_state(user_id)
    state     = raw if (raw and raw.get("date") == today) else _fresh_state(today, market_label)
    is_otc    = _is_otc_market(market_label)
    is_wkday  = _is_weekday()

    state["market_type"] = market_label

    # ── Log market switch AFTER loss alert #1 ────────────────────────────
    if (state.get("loss_alert_count", 0) == 1
            and state.get("last_action_after_alert1") is None):
        state["last_action_after_alert1"] = market_label
        log.info("[DailyAlert] user=%d post-alert1 market logged: %s",
                 user_id, market_label)

    # ── Update streaks ────────────────────────────────────────────────────
    if outcome == "win":
        state["consecutive_losses"] = 0
        state["consecutive_wins"]   = state.get("consecutive_wins", 0) + 1
    else:
        state["consecutive_wins"]   = 0
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1

    # ── Loss alerts ───────────────────────────────────────────────────────
    alert_count = state.get("loss_alert_count", 0)
    if state["consecutive_losses"] >= 2 and alert_count < MAX_LOSS_ALERTS:
        new_count = alert_count + 1
        state["loss_alert_count"]   = new_count
        state["consecutive_losses"] = 0   # reset so the NEXT 2 losses re-trigger

        idx = new_count - 1   # 0-based index into alert banks

        if is_otc:
            alert_text = _LOSS_ALERTS_OTC[idx]
        else:
            alert_text = _LOSS_ALERTS_LIVE[idx]

        # Primary alert — always sent
        asyncio.create_task(_send_vanishing(bot, chat_id, alert_text))
        log.info("[DailyAlert] user=%d loss alert #%d fired (%s)",
                 user_id, new_count,
                 "OTC" if is_otc else "LIVE")

        # ── Weekday OTC special: send 2nd message suggesting LIVE pair ────
        # On Mon–Fri live pairs are fully open, so we immediately follow
        # up with a live-pair recommendation as a second vanishing message.
        if is_otc and is_wkday:
            live_suggest = _WEEKDAY_OTC_LIVE_SUGGEST[idx]
            asyncio.create_task(_send_vanishing(bot, chat_id, live_suggest))
            log.info("[DailyAlert] user=%d weekday OTC → LIVE suggestion #%d sent",
                     user_id, new_count)

    # ── Win alert ─────────────────────────────────────────────────────────
    if (not state.get("win_alert_sent")
            and state["consecutive_wins"] in WIN_STREAK_THRESHOLDS):
        streak = state["consecutive_wins"]
        try:
            admin_un = db.get_setting("owner_username") or "@OAWHIDSHAKIB"
        except Exception:
            admin_un = "@OAWHIDSHAKIB"
        win_text = (
            f"🏆🎉 Amazing! You just closed {streak} back-to-back wins!\n"
            f"🌟 Keep up the supreme trading discipline!\n"
            f"📩 Share your feedback with our admin: {admin_un}"
        )
        try:
            await bot.send_message(chat_id=chat_id, text=win_text)
            state["win_alert_sent"] = True
            log.info("[DailyAlert] user=%d win alert fired (streak=%d)",
                     user_id, streak)
        except Exception as exc:
            log.warning("[DailyAlert] win alert send error: %s", exc)

    db.save_binary_daily_state(user_id, state)
