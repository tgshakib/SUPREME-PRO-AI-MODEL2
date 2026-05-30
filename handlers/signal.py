"""Binary: Pair → Timeframe → Analyze → Signal flow.
Loading message is intentionally minimal: 'Analyzing PAIR ...' (admin/paid)
or 'Analyzing PAIR ... (n/limit)' (free trial). After a signal fires we
block further analysis for 30 seconds.
"""
import asyncio
import os
import random
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db
from chat_clean import show_screen, show_photo_screen
try:
    from self_improve import schedule_outcome_check as _si_schedule
    _SI_OK = True
except Exception:
    _SI_OK = False
    _si_schedule = None  # type: ignore

_TIME_PHOTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "time.jpg",
)
from keyboards import (
    pair_by_index, binary_tf_kb, signal_actions_kb, pairs_kb, limit_reached_kb,
    binary_menu_kb,
)
from config import BINARY_TIMEFRAMES, DAILY_FREE_LIMIT
from signals import generate_signal

router = Router()

# 30-second cooldown lock per user after a signal fires.
SIGNAL_COOLDOWN_SEC = 30
_recent_signal: dict[int, datetime] = {}


def _is_admin(uid: int) -> bool:
    return int(uid) == int(db.get_admin_id())


def _is_weekend() -> bool:
    """Saturday=5, Sunday=6 in UTC. Used to block live binary pairs."""
    return datetime.utcnow().weekday() in (5, 6)


def _market_meta(market: str, broker: str):
    if market == "otc":
        broker_name = "POCKET OPTION" if broker == "po" else "QUOTEX"
        # Full market label stored in signal_outcomes so win-rate dashboard
        # can split PO OTC vs QX OTC vs LIVE separately.
        market_label = "PO OTC" if broker == "po" else "QX OTC"
        return (market_label, broker_name)
    return ("LIVE", "BINARY")


def _tf_label(code: str) -> str:
    if code == "auto":
        return random.choice(["1 MIN", "2 MIN", "3 MIN"])
    for label, c in BINARY_TIMEFRAMES:
        if c == code:
            return label
    return code


def _can_analyze(user_id: int) -> tuple[bool, int, int]:
    # Admin and paid users bypass the daily limit
    if db.has_active_access(user_id) or _is_admin(user_id):
        return True, 0, 0
    used = db.signals_today(user_id)
    bonus = db.get_referral_bonus(user_id)
    limit = DAILY_FREE_LIMIT + bonus["bonus_binary"]
    return used < limit, used, limit


def _cooldown_remaining(user_id: int) -> int:
    last = _recent_signal.get(user_id)
    if not last:
        return 0
    elapsed = (datetime.utcnow() - last).total_seconds()
    remaining = SIGNAL_COOLDOWN_SEC - int(elapsed)
    return max(0, remaining)


@router.callback_query(F.data.startswith("pg:"))
async def cb_page(call: CallbackQuery):
    _, market, broker, page = call.data.split(":")
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "📋 Choose a pair 👇",
        pairs_kb(market=market, broker=broker, page=int(page)),
    )


@router.callback_query(F.data.startswith("back_pairs:"))
async def cb_back_pairs(call: CallbackQuery):
    _, market, broker = call.data.split(":")
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "📋 Choose a pair 👇",
        pairs_kb(market=market, broker=broker, page=0),
    )


@router.callback_query(F.data.startswith("pair:"))
async def cb_pair(call: CallbackQuery):
    _, market, broker, idx = call.data.split(":")
    pair = pair_by_index(market, int(idx))
    if not pair:
        await call.answer("Pair unavailable", show_alert=True); return
    market_name, broker_name = _market_meta(market, broker)
    await call.answer()
    _tf_caption = (
        f"💱 <b>{pair}</b>\n"
        f"📊 Market: 🌐 <b>{market_name}</b> · {broker_name}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>SELECT ▸ TRADING TIME</b>"
    )
    if os.path.exists(_TIME_PHOTO):
        await show_photo_screen(
            call.bot, call.message.chat.id,
            photo_path=_TIME_PHOTO,
            caption=_tf_caption,
            reply_markup=binary_tf_kb(market, broker, int(idx)),
        )
    else:
        await show_screen(call.bot, call.message.chat.id, _tf_caption,
                          binary_tf_kb(market, broker, int(idx)))


@router.callback_query(F.data.startswith("tf:"))
async def cb_tf(call: CallbackQuery):
    _, market, broker, idx, tf = call.data.split(":")
    await _analyze_and_send(call, market, broker, int(idx), tf)


@router.callback_query(F.data.startswith("again:"))
async def cb_again(call: CallbackQuery):
    _, market, broker, idx, tf = call.data.split(":")
    await _analyze_and_send(call, market, broker, int(idx), tf)


async def _analyze_and_send(call: CallbackQuery, market: str, broker: str,
                            idx: int, tf: str):
    user_id = call.from_user.id
    pair = pair_by_index(market, idx)
    if not pair:
        await call.answer("Pair unavailable", show_alert=True); return

    # Weekend gate for LIVE pairs (real-market pairs don't move on Sat/Sun)
    if market == "live" and _is_weekend():
        await call.answer()
        _wknd_photo = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "weekend_otc.jpg",
        )
        _wknd_text = (
            "🛑 <b>Weekend — Live pair closed.</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Real-market LIVE pairs don't trade on <b>Sat &amp; Sun</b>.\n\n"
            "👉 Try the <b>OTC MARKET</b> (Pocket Option / Quotex) — "
            "OTC pairs run 24/7 including weekends."
        )
        if os.path.exists(_wknd_photo):
            await show_photo_screen(
                call.bot, call.message.chat.id,
                photo_path=_wknd_photo,
                caption=_wknd_text,
                reply_markup=binary_menu_kb(),
            )
        else:
            await show_screen(call.bot, call.message.chat.id, _wknd_text, binary_menu_kb())
        return

    # 30-second cooldown after a fresh signal
    cd = _cooldown_remaining(user_id)
    if cd > 0:
        await call.answer(
            f"⚠️ Can't take a trade now — a new signal is already running. "
            f"Wait {cd}s.",
            show_alert=True,
        )
        return

    allowed, used, limit = _can_analyze(user_id)
    if not allowed:
        await call.answer()
        text = (
            f"🚫 <b>Daily Free Limit Reached</b>\n\n"
            f"Current Limit: <b>{limit}</b>  |  Used: <b>{used}</b>\n\n"
            f"🚀 <b>NEED MORE ANALYSES?</b>\n"
            f"Join our VIP channel for free access — or buy access "
            f"to unlock <b>UNLIMITED daily signals</b>.\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎁 <b>Share This bot with Your 10 Trader Friend — after your "
            f"Friends Active and Buy Access you will Get a Bonus!</b>\n"
            f"<b>🔥 Don't miss this Opportunity!</b>"
        )
        await show_screen(call.bot, call.message.chat.id, text, limit_reached_kb())
        return

    market_name, broker_name = _market_meta(market, broker)
    tf_label = _tf_label(tf)

    await call.answer()
    # When this fires from AGAIN ANALYSE / TF, the previous photo card +
    # buttons (AGAIN / CHANGE PAIR / MENU / WORKPLACE) must vanish first
    # so the user only sees the loading animation while the AI is reading
    # candle-to-candle. show_screen falls back to delete+resend when the
    # active message is a photo, but we delete explicitly to avoid any
    # flicker where the old buttons stay visible.
    chat_id = call.message.chat.id
    try:
        from chat_clean import delete_active as _delete_active
        await _delete_active(call.bot, chat_id)
    except Exception:
        pass

    # Single-line loading text — admin/paid see clean line, free trial
    # users see the daily count appended.
    is_premium = db.has_active_access(user_id) or _is_admin(user_id)
    loading = (f"🤖 <b>SUPREME PRO AI Analyzing {pair} ...</b>"
               if is_premium
               else f"🤖 <b>SUPREME PRO AI Analyzing {pair} ... "
                    f"({used + 1}/{limit})</b>")
    loading_msg_id = await show_screen(
        call.bot, chat_id, loading, reply_markup=None,
    )

    # 6-second AI AUTO-BOOST: full deep scan window before signal fires
    await asyncio.sleep(6.0)

    sig = generate_signal(pair, market_name, tf_label, user_id=user_id, broker=broker)

    db.log_signal(user_id, pair, tf_label)
    _recent_signal[user_id] = datetime.utcnow()

    # ── Self-improve: schedule auto outcome check after expiry ────────
    # user_id / bot / chat_id are forwarded so the daily alert system
    # can fire streak-based loss/win notifications when outcome is known.
    if _SI_OK and _si_schedule is not None:
        try:
            _si_schedule(
                signal_id      = sig.get("signal_id", -1),
                pair           = pair,
                market         = market_name,
                direction      = sig.get("direction", "BUY"),
                entry_price    = sig.get("entry_price"),
                expiry_minutes = sig.get("expiry_min", 5),
                engine         = sig.get("engine", "unknown"),
                user_id        = user_id,
                bot            = call.bot,
                chat_id        = chat_id,
            )
        except Exception:
            pass

    # Send the signal card as a photo (BUY → green image, SELL → red image)
    # with the full caption + the signal-actions keyboard.
    #
    # Order matters: send the new card FIRST, then delete the loading
    # message. This way if the new card fails for any reason, the user
    # never gets stuck staring at an "Analyzing..." card with no result.
    photo_path = sig.get("photo")
    sent = None
    if photo_path and os.path.exists(photo_path):
        try:
            sent = await call.bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(photo_path),
                caption=sig["text"],
                parse_mode="HTML",
                reply_markup=signal_actions_kb(market, broker, idx, tf),
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            sent = None

    # Always remove the loading message, regardless of which path posts the
    # final card — that's what was leaving "Analyzing..." text stuck.
    try:
        await call.bot.delete_message(chat_id, loading_msg_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
    except Exception:
        pass
    db.clear_active_msg(chat_id)

    if sent is None:
        # Photo missing or send failed — fall back to a plain text card.
        await show_screen(
            call.bot, chat_id,
            sig["text"],
            signal_actions_kb(market, broker, idx, tf),
        )
    else:
        # Track the photo card so the home/WORKPLACE screen can replace it
        # cleanly on the next click (show_screen will delete + resend).
        db.set_active_msg(chat_id, sent.message_id)
