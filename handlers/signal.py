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
    from self_improve import (
        record_signal as _si_record,
        schedule_outcome_check as _si_schedule,
    )
    _SI_OK = True
except Exception:
    _SI_OK = False
    _si_record = None  # type: ignore
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
from signals import generate_chart_view_binary_fallback, generate_signal

router = Router()

# 30-second cooldown lock per user after a signal fires.
SIGNAL_COOLDOWN_SEC = 30
_recent_signal: dict[int, datetime] = {}
_active_analysis: dict[int, asyncio.Task] = {}


def _record_delivered_signal(sig: dict) -> int:
    """Persist outcome tracking only after the signal card was delivered."""
    payload = sig.get("signal_record")
    if not payload or not _SI_OK or _si_record is None:
        return -1
    try:
        return int(_si_record(**payload))
    except Exception:
        return -1


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
    user_id = call.from_user.id
    _has_access = db.has_active_access(user_id) or _is_admin(user_id)
    if os.path.exists(_TIME_PHOTO):
        await show_photo_screen(
            call.bot, call.message.chat.id,
            photo_path=_TIME_PHOTO,
            caption=_tf_caption,
            reply_markup=binary_tf_kb(market, broker, int(idx), has_access=_has_access),
        )
    else:
        await show_screen(call.bot, call.message.chat.id, _tf_caption,
                          binary_tf_kb(market, broker, int(idx), has_access=_has_access))


@router.callback_query(F.data.startswith("tf:"))
async def cb_tf(call: CallbackQuery):
    _, market, broker, idx, tf = call.data.split(":")
    if tf not in {code for _, code in BINARY_TIMEFRAMES}:
        await call.answer("This trading-time option is no longer available.", show_alert=True)
        return
    await _analyze_and_send(call, market, broker, int(idx), tf)


@router.callback_query(F.data.startswith("again:"))
async def cb_again(call: CallbackQuery):
    _, market, broker, idx, tf = call.data.split(":")
    if tf not in {code for _, code in BINARY_TIMEFRAMES}:
        await call.answer("This trading-time option is no longer available.", show_alert=True)
        return
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

    running = _active_analysis.get(user_id)
    if running is not None and not running.done():
        await call.answer(
            "⚠️ Your previous Binary analysis is still finishing. Please wait.",
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
    if not db.reserve_binary_signal(user_id, pair, market_name, tf_label):
        await call.answer(
            "⚠️ A Binary analysis is already running or was just sent. Please wait.",
            show_alert=True,
        )
        return

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

    # Use the original chart-view signal builder.  It owns the established
    # Binary/OTC/LIVE card text and timing; do not replace it with a new card
    # format in this handler.
    analysis_task = asyncio.create_task(
        asyncio.to_thread(
            generate_signal,
            pair, market_name, tf_label, user_id, broker,
        )
    )
    _active_analysis[user_id] = analysis_task

    def _consume_analysis_result(done: asyncio.Task) -> None:
        if _active_analysis.get(user_id) is done:
            _active_analysis.pop(user_id, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    analysis_task.add_done_callback(_consume_analysis_result)
    try:
        sig = await asyncio.wait_for(asyncio.shield(analysis_task), timeout=25.0)
    except asyncio.TimeoutError:
        # The full engine can spend longer than the callback window waiting on
        # an upstream chart provider. Recover through the established chart-view
        # engine and render the same legacy card, rather than replacing it with
        # an unavailable-data screen.
        try:
            sig = await asyncio.wait_for(
                asyncio.to_thread(
                    generate_chart_view_binary_fallback,
                    pair, market_name, tf_label, user_id, broker,
                ),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            sig = None
        except Exception:
            sig = None

        if sig is None:
            sig = {
                "is_trade": False,
                "text": (
                    "🔄 <b>CHART VIEW IS REFRESHING</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"💱 <b>{pair}</b>\n"
                    "No verified chart direction is available yet. "
                    "The current analysis is still completing."
                ),
            }
    # The legacy renderer predates the fast-path metadata flag.  Preserve its
    # established output and let the existing delivery flow treat it as a
    # signal without altering the rendered text.
    sig.setdefault(
        "is_trade",
        sig.get("direction") in {"BUY", "SELL"},
    )

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
        try:
            sent = await show_screen(
                call.bot, chat_id,
                sig["text"],
                signal_actions_kb(market, broker, idx, tf),
            )
        except Exception:
            sent = None
    else:
        # Track the photo card so the home/WORKPLACE screen can replace it
        # cleanly on the next click (show_screen will delete + resend).
        db.set_active_msg(chat_id, sent.message_id)

    if not sig.get("is_trade", False) or sent is None:
        db.release_binary_signal_reservation(user_id)
        return

    sig["signal_id"] = _record_delivered_signal(sig)
    db.finalize_binary_signal_reservation(user_id, SIGNAL_COOLDOWN_SEC)
    db.log_signal(user_id, pair, tf_label)
    _recent_signal[user_id] = datetime.utcnow()
    # ── Self-improve: schedule outcome only for a delivered trade. ──────
    if sig.get("entry_price") is not None and _SI_OK and _si_schedule is not None:
        try:
            _si_schedule(
                signal_id=sig.get("signal_id", -1), pair=pair,
                market=market_name, direction=sig.get("direction", "BUY"),
                entry_price=sig.get("entry_price"),
                expiry_minutes=sig.get("expiry_min", 5),
                engine=sig.get("engine", "unknown"), user_id=user_id,
                bot=call.bot, chat_id=chat_id,
                signal_timestamp=sig.get("signal_ts", 0),
            )
        except Exception:
            pass
