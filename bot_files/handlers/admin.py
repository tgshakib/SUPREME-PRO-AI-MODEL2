"""Admin panel: stats, members, pending payments, remove, transfer, add user."""
import asyncio
from datetime import timedelta
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import database as db
from chat_clean import show_screen, delete_active, safe_delete
try:
    from self_improve import get_improvement_report as _si_report
    _SI_OK = True
except Exception:
    _SI_OK = False
    _si_report = None  # type: ignore
from keyboards import (
    admin_panel_kb, admin_back_kb, admin_cancel_input_kb,
    add_user_duration_kb, ADD_USER_DURATIONS, payment_received_kb,
    mailing_audience_kb, mailing_confirm_kb, winrate_dashboard_kb,
)

router = Router()


class AdmState(StatesGroup):
    awaiting_remove_id = State()
    awaiting_transfer = State()
    awaiting_add_username = State()
    awaiting_mailing_text = State()


def _is_admin(uid: int) -> bool:
    return int(uid) == int(db.get_admin_id())


def _duration_meta(code: str):
    """Return (label, unit, amount) for a duration code, or None."""
    for label, c, unit, amount in ADD_USER_DURATIONS:
        if c == code:
            return label, unit, amount
    return None


def _delta_for(unit: str, amount: int):
    if unit == "minutes":
        return timedelta(minutes=amount)
    if unit == "hours":
        return timedelta(hours=amount)
    if unit == "days":
        return timedelta(days=amount)
    if unit == "months":
        return timedelta(days=30 * amount)  # approx
    return None  # lifetime


@router.callback_query(F.data == "adm:open")
async def cb_open(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    await state.clear()
    await call.answer()
    s = db.stats()
    text = (
        f"🛡️ <b>ADMINISTRATION PANEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total users: <b>{s['total_users']}</b>\n"
        f"⏳ Active temporary: <b>{s['active_temporary']}</b>\n"
        f"♾️ Lifetime: <b>{s['lifetime']}</b>\n"
        f"📥 Pending payments: <b>{s['pending_payments']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Choose an action 👇"
    )
    await show_screen(call.bot, call.message.chat.id, text, admin_panel_kb())


@router.callback_query(F.data == "adm:close")
async def cb_close(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    await state.clear()
    await call.answer("Closed")
    await delete_active(call.bot, call.message.chat.id)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    s = db.stats()
    text = (
        f"📊 <b>STATS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total users: <b>{s['total_users']}</b>\n"
        f"⏳ Active temporary: <b>{s['active_temporary']}</b>\n"
        f"♾️ Lifetime members: <b>{s['lifetime']}</b>\n"
        f"📥 Pending payments: <b>{s['pending_payments']}</b>"
    )
    await call.answer()
    await show_screen(call.bot, call.message.chat.id, text, admin_back_kb())


@router.callback_query(F.data == "adm:list_access")
async def cb_list_access(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    rows = db.list_active_access()
    if not rows:
        text = "📋 <b>MEMBERS WITH ACCESS</b>\n\nNo active members."
    else:
        lines = ["📋 <b>MEMBERS WITH ACCESS</b>", "━━━━━━━━━━━━━━━━━━━"]
        for r in rows[:50]:
            uname = f"@{r['username']}" if r.get("username") else "(no username)"
            if r["access_type"] == "lifetime":
                tag = "♾️ LIFETIME"
            else:
                tag = f"⏳ until {r['expires_at'][:16].replace('T',' ')} UTC"
            lines.append(
                f"• {uname}  <code>{r['user_id']}</code>\n"
                f"   {r.get('package_label') or '-'}  ·  {tag}"
            )
        if len(rows) > 50:
            lines.append(f"\n…and {len(rows) - 50} more.")
        text = "\n".join(lines)
    await call.answer()
    await show_screen(call.bot, call.message.chat.id, text, admin_back_kb())


@router.callback_query(F.data == "adm:pending")
async def cb_pending(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    rows = db.list_pending_payments()
    if not rows:
        text = "⏳ <b>PENDING PAYMENTS</b>\n\nNothing pending."
    else:
        lines = ["⏳ <b>PENDING PAYMENTS</b>", "━━━━━━━━━━━━━━━━━━━"]
        for r in rows[:30]:
            uname = f"@{r['username']}" if r.get("username") else "(no username)"
            ts = r['submitted_at'][:16].replace('T', ' ') if r['submitted_at'] else ''
            lines.append(
                f"#{r['id']} • {uname} <code>{r['user_id']}</code>\n"
                f"   {r['package_label']} · ${r['amount']} · {ts}"
            )
        text = "\n".join(lines)
    await call.answer()
    await show_screen(call.bot, call.message.chat.id, text, admin_back_kb())


@router.callback_query(F.data == "adm:remove")
async def cb_remove(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    await state.set_state(AdmState.awaiting_remove_id)
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "🚫 <b>REMOVE USER ACCESS</b>\n\n"
        "Send the <b>user chat ID</b> to revoke access.\n"
        "Example: <code>123456789</code>",
        admin_cancel_input_kb(),
    )


@router.message(AdmState.awaiting_remove_id)
async def msg_remove_id(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    await safe_delete(bot, message.chat.id, message.message_id)
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await show_screen(
            bot, message.chat.id,
            "❌ Invalid ID. Send a numeric chat ID.",
            admin_cancel_input_kb(),
        )
        return
    target = int(raw)
    if _is_admin(target):
        await state.clear()
        await show_screen(
            bot, message.chat.id,
            "🚫 <b>Not available.</b>\n\n"
            "You're the <b>Owner</b> of this bot — your Lifetime access "
            "cannot be removed.",
            admin_back_kb(),
        )
        return
    a = db.get_access(target)
    if not a:
        await state.clear()
        await show_screen(
            bot, message.chat.id,
            f"ℹ️ No active access for <code>{target}</code>.",
            admin_back_kb(),
        )
        return
    db.revoke_access(target)
    # Mirror the auto-expiry behaviour: the moment the member loses access
    # (admin revoke OR scheduled expiry, temporary OR lifetime), unpin and
    # delete their pinned 'Payment Received' card so the chat goes clean.
    try:
        from expiry_watcher import _cleanup_pinned_card
        await _cleanup_pinned_card(bot, target)
    except Exception:
        pass
    await state.clear()
    try:
        await bot.send_message(
            target,
            "⚠️ <b>Your access has been removed by admin.</b>\n"
            "Tap /start to renew access.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await show_screen(
        bot, message.chat.id,
        f"✅ Access removed for <code>{target}</code>.",
        admin_back_kb(),
    )


@router.callback_query(F.data == "adm:transfer")
async def cb_transfer(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    await state.set_state(AdmState.awaiting_transfer)
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "🔁 <b>OWNERSHIP TRANSFER</b>\n\n"
        "Send the new owner's data in this format:\n"
        "<code>chat_id @username</code>\n\n"
        "Example: <code>123456789 @newowner</code>\n\n"
        "⚠️ After transfer, you will <b>lose admin access</b>.",
        admin_cancel_input_kb(),
    )


@router.message(AdmState.awaiting_transfer)
async def msg_transfer(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    await safe_delete(bot, message.chat.id, message.message_id)
    parts = (message.text or "").strip().split()
    if len(parts) < 1 or not parts[0].lstrip("-").isdigit():
        await show_screen(
            bot, message.chat.id,
            "❌ Invalid input. Format: <code>chat_id @username</code>",
            admin_cancel_input_kb(),
        )
        return
    new_id = int(parts[0])
    new_uname = parts[1] if len(parts) > 1 else ""
    db.set_admin_id(new_id)
    db.upsert_user(new_id, new_uname.lstrip("@") if new_uname else None, None)
    await state.clear()
    try:
        await bot.send_message(
            new_id,
            "👑 <b>You are now the admin of this bot.</b>\n"
            "Use /start to access the panel.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await show_screen(
        bot, message.chat.id,
        f"✅ Ownership transferred to <code>{new_id}</code> "
        f"{new_uname}.\n\nYou are no longer the admin.",
        reply_markup=None,
    )


# ─────────────────────────────────────────────────────────
# ADD USER FLOW
# Step 1: pick duration (admin presses ADD USER)
# Step 2: send @username (or numeric chat_id) to grant access
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "adm:add_user")
async def cb_add_user(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    await state.clear()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "➕ <b>ADD USER — Grant Bot Access</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Pick the access <b>duration</b> for the new user 👇\n\n"
        "Choose from a quick option (1/2 minute, hour, day, month) "
        "or grant <b>LIFETIME</b> access.",
        add_user_duration_kb(),
    )


@router.callback_query(F.data.startswith("adm:dur:"))
async def cb_add_user_duration(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    code = call.data.split(":")[2]
    meta = _duration_meta(code)
    if not meta:
        await call.answer("Unknown duration", show_alert=True); return
    label, unit, amount = meta
    await state.set_state(AdmState.awaiting_add_username)
    await state.update_data(dur_code=code, dur_label=label,
                            dur_unit=unit, dur_amount=amount)
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"➕ <b>ADD USER — {label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Now send the member's <b>@username</b> (or numeric chat ID).\n\n"
        f"Example: <code>@johndoe</code>  or  <code>123456789</code>\n\n"
        f"⚠️ The user must have started this bot at least once "
        f"(so we have their chat ID on file).",
        admin_cancel_input_kb(),
    )


@router.message(AdmState.awaiting_add_username)
async def msg_add_username(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message.from_user.id):
        return
    await safe_delete(bot, message.chat.id, message.message_id)
    raw = (message.text or "").strip()
    if not raw:
        await show_screen(
            bot, message.chat.id,
            "❌ Please send a @username or numeric chat ID.",
            admin_cancel_input_kb(),
        )
        return

    data = await state.get_data()
    code = data.get("dur_code")
    label = data.get("dur_label", "")
    unit = data.get("dur_unit")
    amount = int(data.get("dur_amount", 0))

    # Resolve target user
    target = None
    if raw.lstrip("-").isdigit():
        target = db.get_user(int(raw))
        if not target:
            db.upsert_user(int(raw), None, None)
            target = {"user_id": int(raw), "username": None}
    else:
        target = db.get_user_by_username(raw)
        if not target:
            await show_screen(
                bot, message.chat.id,
                f"❌ <b>User not found.</b>\n\n"
                f"No record of <code>{raw}</code> in the database.\n"
                f"Ask them to send <b>/start</b> to this bot first, "
                f"then try again.",
                admin_cancel_input_kb(),
            )
            return

    target_id = int(target["user_id"])
    uname = target.get("username") or ""

    # Grant access
    if code == "life":
        access_type = "lifetime"
        delta = None
        pkg_label = "ADMIN GRANT — LIFETIME"
    else:
        access_type = "temporary"
        delta = _delta_for(unit, amount)
        pkg_label = f"ADMIN GRANT — {label.replace('⏱️','').replace('⏰','').replace('📅','').replace('🗓️','').strip()}"

    db.grant_access_delta(
        user_id=target_id,
        access_type=access_type,
        delta=delta,
        package_id=f"admin_{code}",
        package_label=pkg_label,
    )
    await state.clear()

    # Notify target with the new "Payment Received" pinned screen
    try:
        from handlers.purchase import send_payment_received_screen
        await send_payment_received_screen(
            bot=bot,
            user_id=target_id,
            access_type=access_type,
            duration_text=("Lifetime" if access_type == "lifetime"
                           else label.replace("⏱️", "").replace("⏰", "")
                                     .replace("📅", "").replace("🗓️", "").strip()),
            trade_type="Binary / Forex",
        )
    except Exception as e:
        print(f"[admin add_user] notify error: {e}")

    uname_disp = f"@{uname}" if uname else "(no username)"
    await show_screen(
        bot, message.chat.id,
        f"✅ <b>Access granted</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {uname_disp}\n"
        f"🆔 Chat ID: <code>{target_id}</code>\n"
        f"⏳ Duration: <b>{label}</b>\n"
        f"📦 Type: <b>{access_type.upper()}</b>",
        admin_back_kb(),
    )


# ──────────────────────────────────────────────────────────
#  MAILING — admin broadcast (auto-deletes from chat after 72h)
# ──────────────────────────────────────────────────────────
_MAIL_PROMPT = (
    "📨 <b>MAILING</b>\n"
    "━━━━━━━━━━━━━━━━━━━\n"
    "❇️ <b>Enter New Message.</b>\n\n"
    "You can also <b>«Forward»</b> text from another chat or channel.\n\n"
    "<i>The message will auto-remove from every recipient's chat "
    "after 72 hours.</i>"
)

_AUDIENCE_LABELS = {
    "access": "💎 BOT ACCESS USER (temporary + lifetime)",
    "non":    "🆓 NON ACCESS USER",
    "all":    "📣 SEND ALL (access + non-access)",
}


def _audience_user_ids(audience: str) -> list[int]:
    if audience == "access":
        return db.list_access_user_ids()
    if audience == "non":
        return db.list_non_access_user_ids()
    if audience == "all":
        return db.all_user_ids()
    return []


@router.callback_query(F.data == "adm:mail")
async def cb_mail_open(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    await state.clear()
    await state.set_state(AdmState.awaiting_mailing_text)
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id, _MAIL_PROMPT, admin_cancel_input_kb(),
    )


@router.message(AdmState.awaiting_mailing_text)
async def msg_mailing_text(message: Message, state: FSMContext, bot: Bot):
    """Captures the admin's mailing source message (typed OR forwarded).
    We DON'T delete the source yet — copy_message needs it alive for the
    actual broadcast. It gets deleted after SEND."""
    if not _is_admin(message.from_user.id):
        return
    # Reject empty content (no text/caption AND no forwarded payload)
    has_text = bool((message.text or "").strip()
                    or (message.caption or "").strip())
    if not has_text and not message.forward_origin:
        await safe_delete(bot, message.chat.id, message.message_id)
        await show_screen(
            bot, message.chat.id,
            "❌ Please send <b>text</b> or <b>forward</b> a message.\n\n"
            + _MAIL_PROMPT,
            admin_cancel_input_kb(),
        )
        return

    await state.update_data(
        mail_chat_id=message.chat.id,
        mail_msg_id=message.message_id,
    )
    # Build a small preview so the admin sees what they're about to broadcast
    preview = (message.text or message.caption or "").strip()
    if len(preview) > 280:
        preview = preview[:277] + "…"
    if not preview and message.forward_origin:
        preview = "<i>(forwarded message — content kept as-is)</i>"
    await show_screen(
        bot, message.chat.id,
        "📨 <b>MAILING — pick audience</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Preview:</b>\n{preview}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Choose <b>who receives this broadcast</b> 👇",
        mailing_audience_kb(),
    )


@router.callback_query(F.data.startswith("adm:mail:aud:"))
async def cb_mail_audience(call: CallbackQuery, state: FSMContext):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    audience = call.data.split(":")[3]
    if audience not in _AUDIENCE_LABELS:
        await call.answer("Unknown audience", show_alert=True); return
    data = await state.get_data()
    if not data.get("mail_chat_id") or not data.get("mail_msg_id"):
        await call.answer(
            "Mailing source lost — please start again.", show_alert=True,
        )
        return
    audience_ids = _audience_user_ids(audience)
    await state.update_data(mail_audience=audience)
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "📨 <b>MAILING — confirm SEND</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Audience: <b>{_AUDIENCE_LABELS[audience]}</b>\n"
        f"📤 Recipients: <b>{len(audience_ids)}</b>\n"
        f"⏱️ Auto-removes from each chat after <b>72 hours</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Tap <b>📨 SEND</b> below to broadcast.",
        mailing_confirm_kb(audience),
    )


@router.callback_query(F.data.startswith("adm:mail:send:"))
async def cb_mail_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    audience = call.data.split(":")[3]
    data = await state.get_data()
    src_chat = data.get("mail_chat_id")
    src_msg  = data.get("mail_msg_id")
    if not src_chat or not src_msg:
        await call.answer(
            "Mailing source lost — please start again.", show_alert=True,
        )
        return

    audience_ids = _audience_user_ids(audience)
    if not audience_ids:
        await call.answer("No users in that audience.", show_alert=True)
        return

    await call.answer("📨 Sending… please wait")

    sent = 0
    blocked = 0
    failed = 0
    for uid in audience_ids:
        try:
            copied = await bot.copy_message(
                chat_id=int(uid),
                from_chat_id=int(src_chat),
                message_id=int(src_msg),
            )
            db.log_mailing_message(int(uid), int(copied.message_id))
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramBadRequest:
            failed += 1
        except Exception:
            failed += 1
        # Telegram global limit is 30 msgs/sec — sleep keeps us safely below
        await asyncio.sleep(0.05)

    # Now delete the admin's original source message from the admin chat
    await safe_delete(bot, int(src_chat), int(src_msg))
    await state.clear()

    await show_screen(
        bot, call.message.chat.id,
        "✅ <b>MAILING — broadcast complete</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Audience: <b>{_AUDIENCE_LABELS[audience]}</b>\n"
        f"📨 Delivered: <b>{sent}</b>\n"
        f"🚫 Blocked the bot: <b>{blocked}</b>\n"
        f"⚠️ Failed: <b>{failed}</b>\n"
        f"⏱️ Auto-removes from each chat in <b>72 hours</b>",
        admin_back_kb(),
    )


# ──────────────────────────────────────────────────────────
#  72h auto-purge loop for mailing messages
# ──────────────────────────────────────────────────────────
@router.callback_query(F.data == "adm:ai_report")
async def cb_ai_report(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer("Not authorized", show_alert=True); return
    await call.answer()
    if _SI_OK and _si_report is not None:
        try:
            report = _si_report(days=7)
        except Exception as e:
            report = f"⚠️ Report generation error: {e}"
    else:
        report = "⚠️ Self-improve engine not loaded."
    await show_screen(call.bot, call.message.chat.id, report, admin_back_kb())


@router.callback_query(F.data == "adm:winrate")
async def cb_winrate_dashboard(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    await call.answer()

    try:
        from winrate_guardian import BOOST_LABELS, BOOST_ADJUSTMENTS, _progress_bar
        s1 = db.winrate_stats(days=1)
        s2 = db.winrate_stats(days=2)
        boost_level = db.get_boost_level()

        def _bar(rate):
            return _progress_bar(rate, 100, width=10)

        def _streak_icon(n):
            if n >= 10: return "🔥🔥🔥"
            if n >= 5:  return "🔥🔥"
            if n >= 2:  return "🔥"
            return "─"

        def _rate_icon(rate):
            if rate >= 91: return "✅"
            if rate >= 75: return "⚠️"
            return "❌"

        # Top pairs (best win rate, min 2 signals)
        pair_rows = sorted(
            [(p, d["wins"], d["total"])
             for p, d in s2["pair_stats"].items() if d["total"] >= 2],
            key=lambda x: x[1] / x[2],
            reverse=True,
        )
        best_pairs_lines = []
        for pair, wins, total in pair_rows[:5]:
            rate = wins / total * 100
            mini = "█" * int(rate / 20) + "░" * (5 - int(rate / 20))
            best_pairs_lines.append(
                f"  {_rate_icon(rate)} <b>{pair}</b>  {wins}W/{total-wins}L  [{mini}] {rate:.0f}%"
            )
        worst_pairs_lines = []
        for pair, wins, total in reversed(pair_rows[-3:]):
            rate = wins / total * 100
            mini = "█" * int(rate / 20) + "░" * (5 - int(rate / 20))
            worst_pairs_lines.append(
                f"  ⚠️ <b>{pair}</b>  {wins}W/{total-wins}L  [{mini}] {rate:.0f}%"
            )

        boost_adj = BOOST_ADJUSTMENTS[boost_level]
        boost_mode_label = BOOST_LABELS[boost_level]

        if boost_level == 0:
            boost_note = "Win rate is healthy — running at normal power."
        else:
            boost_note = (
                f"Win rate below 91% — AI tightened:\n"
                f"  • PA gate +{boost_adj['pa_delta']:.1f}  "
                f"• OTC votes +{boost_adj['otc_delta']}  "
                f"• Conf cap {boost_adj['conf_cap']}%"
            )

        lines = [
            "📈 <b>WIN RATE DASHBOARD</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📅 <b>TODAY</b>",
            f"  Signals : <b>{s1['total']}</b>  ✅ Wins: <b>{s1['wins']}</b>  ❌ Losses: <b>{s1['losses']}</b>",
            f"  Win Rate: {_rate_icon(s1['win_rate'])} <b>{s1['win_rate']:.1f}%</b>",
            f"  {_bar(s1['win_rate'])}",
            f"  Win Streak: {_streak_icon(s1['streak'])} <b>{s1['streak']} in a row</b>",
            "",
            f"📅 <b>LAST 2 DAYS</b>",
            f"  Signals : <b>{s2['total']}</b>  ✅ Wins: <b>{s2['wins']}</b>  ❌ Losses: <b>{s2['losses']}</b>",
            f"  Win Rate: {_rate_icon(s2['win_rate'])} <b>{s2['win_rate']:.1f}%</b>  (target ≥ 91%)",
            f"  {_bar(s2['win_rate'])}",
            f"  Win Streak: {_streak_icon(s2['streak'])} <b>{s2['streak']} in a row</b>",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🤖 <b>AI AUTO-BOOST STATUS</b>",
            f"  {boost_mode_label}",
            f"  <i>{boost_note}</i>",
            "",
        ]

        if best_pairs_lines:
            lines.append("🏆 <b>BEST PAIRS  (2 days)</b>")
            lines.extend(best_pairs_lines)
            lines.append("")

        if worst_pairs_lines and len(pair_rows) > 3:
            lines.append("📉 <b>PAIRS NEEDING ATTENTION</b>")
            lines.extend(worst_pairs_lines)
            lines.append("")

        if s2["total"] == 0:
            lines.append("<i>⚠️ No completed signals yet — data appears after first signal outcomes are recorded.</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Tap 🔄 Refresh to purge old data &amp; reload · Tap ⬅️ BACK to return</i>")

        text = "\n".join(lines)
    except Exception as exc:
        text = f"📈 <b>WIN RATE DASHBOARD</b>\n\n⚠️ Error loading stats: {exc}"

    await show_screen(call.bot, call.message.chat.id, text, winrate_dashboard_kb())


@router.callback_query(F.data == "adm:winrate_refresh")
async def cb_winrate_refresh(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        await call.answer(); return
    await call.answer("🔄 Refreshing…")

    # Purge all signal_outcomes older than 2 days and reset the cycle timer
    try:
        deleted = db.purge_old_winrate_data(days=2)
        from winrate_guardian import _LAST_PURGE_KEY
        db.set_setting(_LAST_PURGE_KEY, __import__("time").time().__str__())
    except Exception:
        deleted = 0

    # Re-render the dashboard with fresh data
    try:
        from winrate_guardian import BOOST_LABELS, BOOST_ADJUSTMENTS, _progress_bar
        s1 = db.winrate_stats(days=1)
        s2 = db.winrate_stats(days=2)
        boost_level = db.get_boost_level()

        def _bar(rate):
            return _progress_bar(rate, 100, width=10)

        def _streak_icon(n):
            if n >= 10: return "🔥🔥🔥"
            if n >= 5:  return "🔥🔥"
            if n >= 2:  return "🔥"
            return "─"

        def _rate_icon(rate):
            if rate >= 91: return "✅"
            if rate >= 75: return "⚠️"
            return "❌"

        pair_rows = sorted(
            [(p, d["wins"], d["total"])
             for p, d in s2["pair_stats"].items() if d["total"] >= 2],
            key=lambda x: x[1] / x[2],
            reverse=True,
        )
        best_pairs_lines = []
        for pair, wins, total in pair_rows[:5]:
            rate = wins / total * 100
            mini = "█" * int(rate / 20) + "░" * (5 - int(rate / 20))
            best_pairs_lines.append(
                f"  {_rate_icon(rate)} <b>{pair}</b>  {wins}W/{total-wins}L  [{mini}] {rate:.0f}%"
            )
        worst_pairs_lines = []
        for pair, wins, total in reversed(pair_rows[-3:]):
            rate = wins / total * 100
            mini = "█" * int(rate / 20) + "░" * (5 - int(rate / 20))
            worst_pairs_lines.append(
                f"  ⚠️ <b>{pair}</b>  {wins}W/{total-wins}L  [{mini}] {rate:.0f}%"
            )

        boost_adj = BOOST_ADJUSTMENTS[boost_level]
        boost_mode_label = BOOST_LABELS[boost_level]
        if boost_level == 0:
            boost_note = "Win rate is healthy — running at normal power."
        else:
            boost_note = (
                f"Win rate below 91% — AI tightened:\n"
                f"  • PA gate +{boost_adj['pa_delta']:.1f}  "
                f"• OTC votes +{boost_adj['otc_delta']}  "
                f"• Conf cap {boost_adj['conf_cap']}%"
            )

        lines = [
            "📈 <b>WIN RATE DASHBOARD</b>",
            f"<i>🔄 Refreshed — {deleted} old record(s) cleared</i>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📅 <b>TODAY</b>",
            f"  Signals : <b>{s1['total']}</b>  ✅ Wins: <b>{s1['wins']}</b>  ❌ Losses: <b>{s1['losses']}</b>",
            f"  Win Rate: {_rate_icon(s1['win_rate'])} <b>{s1['win_rate']:.1f}%</b>",
            f"  {_bar(s1['win_rate'])}",
            f"  Win Streak: {_streak_icon(s1['streak'])} <b>{s1['streak']} in a row</b>",
            "",
            f"📅 <b>LAST 2 DAYS</b>",
            f"  Signals : <b>{s2['total']}</b>  ✅ Wins: <b>{s2['wins']}</b>  ❌ Losses: <b>{s2['losses']}</b>",
            f"  Win Rate: {_rate_icon(s2['win_rate'])} <b>{s2['win_rate']:.1f}%</b>  (target ≥ 91%)",
            f"  {_bar(s2['win_rate'])}",
            f"  Win Streak: {_streak_icon(s2['streak'])} <b>{s2['streak']} in a row</b>",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🤖 <b>AI AUTO-BOOST STATUS</b>",
            f"  {boost_mode_label}",
            f"  <i>{boost_note}</i>",
            "",
        ]
        if best_pairs_lines:
            lines.append("🏆 <b>BEST PAIRS  (2 days)</b>")
            lines.extend(best_pairs_lines)
            lines.append("")
        if worst_pairs_lines and len(pair_rows) > 3:
            lines.append("📉 <b>PAIRS NEEDING ATTENTION</b>")
            lines.extend(worst_pairs_lines)
            lines.append("")
        if s2["total"] == 0:
            lines.append("<i>⚠️ No completed signals yet — data appears after first signal outcomes are recorded.</i>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Tap 🔄 Refresh to purge old data &amp; reload · Tap ⬅️ BACK to return</i>")
        text = "\n".join(lines)
    except Exception as exc:
        text = f"📈 <b>WIN RATE DASHBOARD</b>\n\n⚠️ Refresh error: {exc}"

    # Update the dashboard message
    await show_screen(call.bot, call.message.chat.id, text, winrate_dashboard_kb())

    # Send a temporary notice that auto-deletes after 8 seconds
    import asyncio as _asyncio
    try:
        notice = await call.bot.send_message(
            call.message.chat.id,
            "✅ <b>Dashboard refreshed.</b> Old data cleared.",
            parse_mode="HTML",
        )
        async def _delete_notice(bot, chat_id, msg_id):
            await _asyncio.sleep(8)
            try:
                await bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
        _asyncio.create_task(_delete_notice(call.bot, call.message.chat.id, notice.message_id))
    except Exception:
        pass


@router.message(F.text.regexp(r"^/updatessid\s+\S+"))
async def cmd_updatessid(msg: Message):
    """Admin command: /updatessid <new_ssid_token>
    Saves a fresh Pocket Option SSID obtained from the user's browser.
    """
    if not _is_admin(msg.from_user.id):
        return
    parts = msg.text.strip().split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer("❌ Usage: <code>/updatessid YOUR_PO_SSID_TOKEN</code>", parse_mode="HTML")
        return
    new_ssid = parts[1].strip()
    try:
        from po_auth import _save_ssid, _notify_services
        _save_ssid(new_ssid)
        _notify_services(new_ssid)
        await msg.answer(
            f"✅ <b>PO SSID updated</b> ({len(new_ssid)} chars).\n"
            f"All WebSocket services will reconnect with the new token.",
            parse_mode="HTML",
        )
    except Exception as e:
        await msg.answer(f"❌ Failed to update SSID: {e}")


async def run_mailing_purge_loop(bot: Bot):
    """Background task: every 30 minutes, scan `mailing_log` for messages
    older than 72h and delete them from each user's chat."""
    while True:
        try:
            for row in db.list_mailing_to_purge(72):
                try:
                    await bot.delete_message(
                        chat_id=int(row["user_id"]),
                        message_id=int(row["message_id"]),
                    )
                except Exception:
                    # Already gone / blocked / etc — still mark as deleted
                    pass
                db.mark_mailing_deleted(int(row["id"]))
                await asyncio.sleep(0.05)
        except Exception as e:
            print(f"[mailing_purge] loop error: {e}")
        await asyncio.sleep(30 * 60)  # 30 minutes
