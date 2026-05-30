"""Helpers to keep the chat clean by replacing the previous bot message
instead of stacking new ones.

Transition strategy (smooth — no blank-gap flicker):
  text  → text   edit message text in-place           (seamless)
  photo → photo  edit_message_media in-place           (seamless)
  text  → photo  send new first, then delete old       (no gap)
  photo → text   edit_message_caption in-place         (seamless)
                 fallback: send new first, delete old  (no gap)
"""
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardMarkup, FSInputFile,
    InputMediaPhoto,
)
from typing import Optional

import database as db


async def show_screen(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML",
) -> int:
    """Replace the active bot message with a text panel — smooth transition."""
    active_id = db.get_active_msg(chat_id)

    if active_id:
        # ── Try 1: active msg is a text message — edit in-place (seamless) ──
        try:
            msg = await bot.edit_message_text(
                chat_id=chat_id,
                message_id=active_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            db.set_active_msg(chat_id, msg.message_id)
            return msg.message_id
        except TelegramBadRequest as e:
            err = str(e)
            # ── Try 2: active msg is a photo — edit its caption in-place ──
            if "there is no text in the message" in err or "message is not modified" not in err:
                try:
                    msg = await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=active_id,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                    )
                    db.set_active_msg(chat_id, msg.message_id)
                    return msg.message_id
                except TelegramBadRequest:
                    pass

        # ── Fallback: send new first (no blank gap), then delete old ──
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        db.set_active_msg(chat_id, sent.message_id)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=active_id)
        except TelegramBadRequest:
            pass
        return sent.message_id

    # No previous active message — just send fresh
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
    )
    db.set_active_msg(chat_id, sent.message_id)
    return sent.message_id


async def show_photo_screen(
    bot: Bot,
    chat_id: int,
    photo_path: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML",
) -> int:
    """Replace the active bot message with a photo panel — smooth transition."""
    active_id = db.get_active_msg(chat_id)

    if active_id:
        # ── Try: swap media in-place (photo → photo, seamless) ──
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=active_id,
                media=InputMediaPhoto(
                    media=FSInputFile(photo_path),
                    caption=caption,
                    parse_mode=parse_mode,
                ),
            )
            if reply_markup:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=active_id,
                    reply_markup=reply_markup,
                )
            db.set_active_msg(chat_id, active_id)
            return active_id
        except TelegramBadRequest:
            pass

        # ── Fallback: send new photo first (no blank gap), then delete old ──
        sent = await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(photo_path),
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        db.set_active_msg(chat_id, sent.message_id)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=active_id)
        except TelegramBadRequest:
            pass
        return sent.message_id

    # No previous active message — just send fresh
    sent = await bot.send_photo(
        chat_id=chat_id,
        photo=FSInputFile(photo_path),
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    db.set_active_msg(chat_id, sent.message_id)
    return sent.message_id


async def delete_active(bot: Bot, chat_id: int):
    active_id = db.get_active_msg(chat_id)
    if active_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=active_id)
        except TelegramBadRequest:
            pass
        db.clear_active_msg(chat_id)


async def safe_delete(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass


async def wipe_closed_forex_signals(bot: Bot, user_id: int) -> int:
    """Delete forex signal cards from chat whose status is 'closed' (TP/SL
    already done). Used by the engine before posting a fresh signal so the
    chat only ever shows fresh tracking + new signal updates — no stale
    closed-trade chatter piling up."""
    rows = db.list_closed_forex_signal_messages(user_id, limit=50)
    deleted: list[int] = []
    for chat_id, msg_id, sig_id in rows:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted.append(sig_id)
        except TelegramBadRequest:
            deleted.append(sig_id)
        except Exception:
            pass
    if deleted:
        db.clear_forex_signal_message_ids(deleted)
    return len(deleted)


async def wipe_user_signals(bot: Bot, user_id: int) -> int:
    """Delete every recent forex signal card we sent the user (best-effort).
    Used when the user clicks WORKPLACE / opens the home screen so the chat
    feed clears down to just the active home message. Telegram silently
    refuses deletes older than ~48h — that's fine, we just skip those.

    Returns the number of messages we successfully deleted."""
    rows = db.list_user_forex_signal_messages(user_id, limit=50)
    deleted = []
    for chat_id, msg_id, sig_id in rows:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted.append(sig_id)
        except TelegramBadRequest:
            deleted.append(sig_id)
        except Exception:
            pass
    if deleted:
        db.clear_forex_signal_message_ids(deleted)
    return len(deleted)
