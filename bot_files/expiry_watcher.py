"""Background task that pings users when their temporary access expires.
Also cleans up the pinned 'Payment Received' welcome card so the chat goes
back to a clean state once paid access ends."""
import asyncio
from datetime import datetime
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db


async def _cleanup_pinned_card(bot: Bot, user_id: int):
    """Unpin AND delete the user's 'Payment Received' / 'Account active'
    welcome card the moment their Binary or Forex access (Temporary OR
    Lifetime) is lost — whether through scheduled expiry or admin revoke.

    Also unpins any orphan pinned messages from prior renewals so the
    chat is left fully clean. Best-effort — silently swallows errors
    (Telegram may forbid the action if the user blocked the bot, etc.)."""
    pin_id = db.get_pinned_payment_msg(user_id)

    # Belt-and-suspenders: unpin every pinned message in the chat so any
    # orphan pins left over from earlier renewals are also cleared.
    try:
        await bot.unpin_all_chat_messages(chat_id=user_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass

    if pin_id:
        try:
            await bot.unpin_chat_message(chat_id=user_id, message_id=pin_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        try:
            await bot.delete_message(chat_id=user_id, message_id=pin_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        db.clear_pinned_payment_msg(user_id)


async def run_expiry_watcher(bot: Bot, interval_seconds: int = 600):
    notified: set[int] = set()
    while True:
        try:
            now = datetime.utcnow()
            for row in db.list_access():
                if row["access_type"] != "temporary":
                    continue
                if not row["expires_at"]:
                    continue
                try:
                    exp = datetime.fromisoformat(row["expires_at"])
                except Exception:
                    continue
                uid = row["user_id"]
                # If access was renewed (new expires_at in the future),
                # forget the prior notification so re-expiry fires cleanup
                # again.
                if exp > now and uid in notified:
                    notified.discard(uid)
                if exp <= now and uid not in notified:
                    # Unpin & delete the pinned welcome card first so the
                    # chat doesn't keep showing 'Account active' after expiry.
                    await _cleanup_pinned_card(bot, uid)
                    # Remove their access + payment history from admin records
                    db.revoke_access(uid)
                    try:
                        await bot.send_message(
                            uid,
                            "⌛ <b>Your access has just expired.</b>\n\n"
                            "Tap /start to renew and continue receiving "
                            "premium signals.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    notified.add(uid)
        except Exception as e:
            print(f"[expiry_watcher] error: {e}")
        await asyncio.sleep(interval_seconds)
