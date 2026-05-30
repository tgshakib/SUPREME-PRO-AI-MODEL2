"""Referral system for free users.

Rules:
- Only non-access (free) users can generate referral links
- Links expire 1 hour after creation (message auto-deletes too)
- Only brand-new users (never used the bot) count as referrals
- Old referred users are never counted again
- Every 5 successful referrals → +3 bonus binary signals/day + +1 bonus forex/day
- Bonus lasts 1 week from the last milestone earned, then back to normal
"""
import asyncio
import secrets
import string
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import database as db
import os as _os
from chat_clean import show_photo_screen

_REFERRAL_PHOTO = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "assets", "referral.png"
)

router = Router()

_BOT_USERNAME_CACHE: str = ""


def _is_admin(uid: int) -> bool:
    return int(uid) == int(db.get_admin_id())


def _is_free(uid: int) -> bool:
    return not db.has_active_access(uid) and not _is_admin(uid)


def _gen_token(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _referral_kb(token: str | None, bot_username: str) -> InlineKeyboardMarkup:
    rows = []
    if token:
        link = f"https://t.me/{bot_username}?start=ref_{token}"
        rows.append([InlineKeyboardButton(
            text="🔗 Share My Referral Link",
            url=f"https://t.me/share/url?url={link}&text=Join%20Supreme%20Pro%20AI%20Bot%20and%20trade%20smarter!"
        )])
        rows.append([InlineKeyboardButton(text="🔄 Generate New Link", callback_data="ref:gen")])
    else:
        rows.append([InlineKeyboardButton(text="🔗 Generate Referral Link", callback_data="ref:gen")])
    rows.append([InlineKeyboardButton(text="📊 My Stats", callback_data="ref:stats")])
    rows.append([InlineKeyboardButton(text="⬅️ BACK TO MENU", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_bot_username(bot) -> str:
    global _BOT_USERNAME_CACHE
    if not _BOT_USERNAME_CACHE:
        me = await bot.get_me()
        _BOT_USERNAME_CACHE = me.username or "Ssupremeadvanced_Bot"
    return _BOT_USERNAME_CACHE


def _build_referral_text(user_id: int, token: str | None, bot_username: str) -> str:
    count = db.get_referral_count(user_id)
    bonus = db.get_referral_bonus(user_id)
    milestones = count // 5
    next_needed = 5 - (count % 5)

    bonus_text = ""
    if bonus["bonus_binary"] > 0 or bonus["bonus_forex"] > 0:
        exp_raw = bonus["expires_at"] or ""
        exp_str = exp_raw[:10] if exp_raw else "?"
        bonus_text = (
            f"\n🎁 <b>Your Active Bonus</b> <i>(expires {exp_str})</i>\n"
            f"   📊 +{bonus['bonus_binary']} bonus binary signals/day\n"
            f"   💹 +{bonus['bonus_forex']} bonus forex signal/day\n"
        )

    link_text = ""
    if token:
        link = f"https://t.me/{bot_username}?start=ref_{token}"
        link_text = (
            f"\n🔗 <b>Your Link:</b>\n"
            f"<code>{link}</code>\n"
            f"⏰ <i>Expires in 1 hour — share now!</i>\n"
        )

    milestone_bar = ""
    done = count % 5
    empty = 5 - done
    milestone_bar = "▰" * done + "▱" * empty + f"  {done}/5"

    return (
        "🎁 <b>FREE SIGNALS — REFERRAL PROGRAM</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Invite new traders and earn bonus daily signals!\n\n"
        "📋 <b>Reward Rules:</b>\n"
        "  • Every <b>5 new users</b> you refer:\n"
        "    📊 +3 bonus binary signals/day\n"
        "    💹 +1 bonus forex signal/day\n"
        "  • More invites = more daily bonus signals\n"
        "  • Bonus lasts <b>1 week</b> from last milestone\n"
        "  • Only <b>brand-new</b> users count\n"
        "  • Links auto-vanish after <b>1 hour</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Referrals:</b> {count}  |  "
        f"🏅 Milestones: {milestones}\n"
        f"📈 Progress: {milestone_bar}\n"
        f"🎯 Next reward in: <b>{next_needed} more invite{'s' if next_needed != 1 else ''}</b>"
        f"{bonus_text}"
        f"{link_text}"
    )


async def _auto_delete_msg(bot, chat_id: int, msg_id: int, delay: int = 3600):
    """Delete a Telegram message after `delay` seconds."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


@router.callback_query(F.data == "m:referral")
async def show_referral_screen(call: CallbackQuery):
    uid = call.from_user.id
    if not _is_free(uid):
        await call.answer(
            "Referral links are only for free users.", show_alert=True
        )
        return
    await call.answer()
    bot_username = await _get_bot_username(call.bot)
    link_row = db.get_valid_referral_link(uid)
    token = link_row["token"] if link_row else None
    text = _build_referral_text(uid, token, bot_username)
    await show_photo_screen(call.bot, call.message.chat.id, _REFERRAL_PHOTO, text, _referral_kb(token, bot_username))


@router.callback_query(F.data == "ref:gen")
async def gen_referral_link(call: CallbackQuery):
    uid = call.from_user.id
    if not _is_free(uid):
        await call.answer("Only free users can generate referral links.", show_alert=True)
        return
    await call.answer("🔗 Generating your link…", show_alert=False)

    token = _gen_token()
    expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    db.create_referral_link(uid, token, expires_at)

    bot_username = await _get_bot_username(call.bot)
    text = _build_referral_text(uid, token, bot_username)
    await show_photo_screen(call.bot, call.message.chat.id, _REFERRAL_PHOTO, text, _referral_kb(token, bot_username))

    msg_id = db.get_active_msg(uid)
    if msg_id:
        asyncio.create_task(
            _auto_delete_msg(call.bot, call.message.chat.id, msg_id, 3600)
        )


@router.callback_query(F.data == "ref:stats")
async def ref_stats(call: CallbackQuery):
    uid = call.from_user.id
    if not _is_free(uid):
        await call.answer("This feature is only for free users.", show_alert=True)
        return
    await call.answer()
    bot_username = await _get_bot_username(call.bot)
    link_row = db.get_valid_referral_link(uid)
    token = link_row["token"] if link_row else None
    text = _build_referral_text(uid, token, bot_username)
    await show_photo_screen(call.bot, call.message.chat.id, _REFERRAL_PHOTO, text, _referral_kb(token, bot_username))
