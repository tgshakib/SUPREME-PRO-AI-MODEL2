"""Buy access flow:
   Buy Full Access → 3 buttons (PAID JOIN, SVIP & FREE BOT, SUPPORT TEAM)
   PAID JOIN → BINARY TRADERS / FOREX TRADERS
   Each shows a price list and accepts payment screenshots."""
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db
from chat_clean import show_screen, safe_delete, delete_active
from keyboards import (
    buy_top_kb, paid_traders_kb, binary_packages_kb, forex_packages_kb,
    package_payment_kb, screenshot_check_kb, admin_review_kb,
    cancel_payment_kb, payment_received_kb,
)
from config import (
    PAYMENT_INFO_PAGE_1, PAYMENT_INFO_PAGE_2,
    MTG_PACKAGES, NONMTG_PACKAGES, GOLDZILA_PACKAGES,
    get_package, SUPPORT_USERNAME,
)


def _trade_type_for(package_id: str) -> str:
    if package_id.startswith("gz_"):
        return "Forex"
    if package_id.startswith("admin_"):
        return "Binary / Forex"
    return "Binary"


def _payment_received_text(trade_type: str, duration_text: str) -> str:
    """The exact 'Payment Received! Congratulations!' screen the user
    requested — formatted with bold so each point reads cleanly."""
    return (
        "🎉 <b>Payment Received! Congratulations!</b>\n"
        "🟢 <b>Your account is now active.</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🔮 <b>Type:</b> {trade_type}\n"
        f"⏳ <b>Duration:</b> {duration_text}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "💬 <b>Remember:</b>\n"
        "<i>\"Success in trading isn't luck; it's the result of "
        "discipline, patience, and knowledge.\"</i>\n\n"
        "📢 <b>Stay focused, stay disciplined, "
        "and let's make profits together!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>Proper · Create Your Own Stoploss And Target:</b>\n"
        "    Always trade with a good stop loss &amp; profit target.\n\n"
        "❒ <b>If back-to-back 2 losses in MTG → 30 min break.</b>\n"
        "    After break, take trade again.\n"
        "    <i>(40/50% recovery kore exit market)</i>\n\n"
        "‼️ <b>Do NOT refund a Trade in MTG!</b>\n"
        "    Refund means — no loss, no profit.\n\n"
        "✅ <b>Patience is Key:</b> Consistency wins over greed.\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "👉 Tap <b>START BOT</b> below to open your workplace."
    )


def _lifetime_welcome_text() -> str:
    """Special pinned welcome card shown ONLY to LIFETIME members.
    Triggered when admin grants Lifetime via Add User, OR when a member
    buys a Lifetime package (Binary MTG / NON-MTG / GOLDZILA Unlimited)."""
    return (
        "🟢 <b>Your account is now active.</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🔮 <b>Type:</b> ( FOREX &amp; BINARY )\n"
        "⏳ <b>Duration:</b> LIFETIME\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "💬 <b>Remember:</b>\n"
        "<i>\"Success in trading isn't luck; it's the result of "
        "discipline, patience, and knowledge.\"</i>\n\n"
        "📢 <b>Stay focused, stay disciplined, "
        "and let's make profits together!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>Proper · Create Your Own Stoploss And Target:</b>\n"
        "    Always trade with a good stop loss &amp; profit target.\n\n"
        "❒ <b>If back-to-back 2 losses in MTG → 30 min break.</b>\n"
        "    After break, take trade again.\n"
        "    <i>(40/50% recovery kore exit market)</i>\n\n"
        "‼️ <b>Do NOT refund a Trade in MTG!</b>\n"
        "    Refund means — no loss, no profit.\n\n"
        "✅ <b>Patience is Key:</b> Consistency wins over greed.\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "👉 Tap <b>START BOT</b> below to open your workplace."
    )


async def send_payment_received_screen(
    bot: Bot, user_id: int, access_type: str,
    duration_text: str, trade_type: str = "Binary / Forex",
):
    """Send the 'Payment Received' message, pin it in the user's chat, and
    wipe the previously tracked active screen so the chat looks clean."""
    # Wipe whatever 'active' bot screen was showing (e.g. pending review)
    await delete_active(bot, user_id)

    # LIFETIME members get a dedicated welcome card (no "Payment Received"
    # header, fixed 'FOREX & BINARY' label). Everyone else (temporary
    # access) gets the standard payment-received card.
    if access_type == "lifetime":
        text = _lifetime_welcome_text()
    else:
        text = _payment_received_text(trade_type, duration_text)
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=payment_received_kb(),
            disable_web_page_preview=True,
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        return None

    # Make this the active screen so it gets edited on next navigation
    db.set_active_msg(user_id, sent.message_id)

    # Auto-pin (best effort — may fail if user has restricted the bot)
    try:
        await bot.pin_chat_message(
            chat_id=user_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
        # Remember the pinned card so the expiry watcher can unpin + delete
        # it the moment the member's access actually expires.
        db.set_pinned_payment_msg(user_id, sent.message_id)
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    return sent

router = Router()


class PayState(StatesGroup):
    awaiting_screenshot = State()


# ── Buy menu (top) ────────────────────────────────────────
def _buy_top_text() -> str:
    return (
        "💎 <b>BUY FULL ACCESS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Choose what you need below 👇\n\n"
        "💎 <b>PAID JOIN</b> — see all paid plans\n"
        "⭐ <b>SVIP &amp; FREE BOT</b> — auto-join VIP\n"
        "💬 <b>SUPPORT TEAM</b> — talk to a human"
    )


@router.callback_query(F.data == "m:buy")
async def cb_buy(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await show_screen(call.bot, call.message.chat.id, _buy_top_text(), buy_top_kb())


# ── Paid Join → trader-type picker ────────────────────────
@router.callback_query(F.data == "buy:paid")
async def cb_buy_paid(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    text = (
        "💎 <b>PAID JOIN</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Pick what you trade 👇\n\n"
        "📊 <b>BINARY TRADERS</b> — MTG &amp; NON-MTG plans\n"
        "💹 <b>FOREX TRADERS</b> — GOLDZILA SVIP plans"
    )
    await show_screen(call.bot, call.message.chat.id, text, paid_traders_kb())


# ── Binary price list ─────────────────────────────────────
def _binary_packages_text() -> str:
    lines = [
        "📊 <b>BINARY TRADING — PRICE LIST</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "🟦 <b>1 MTG SIGNAL — COMPOUNDING</b>",
        "",
    ]
    for p in MTG_PACKAGES:
        lines.append(
            f"❒ <b>{p['label']:<10}</b>  "
            f"<s>${p['was']}</s>  ➜  <b>${p['price']}</b>"
        )
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━",
        "🟪 <b>NON-MTG AI  SIGNAL — COMPOUNDING</b>",
        "",
    ]
    for p in NONMTG_PACKAGES:
        lines.append(
            f"❒ <b>{p['label']:<10}</b>  "
            f"<s>${p['was']}</s>  ➜  <b>${p['price']}</b>"
        )
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━",
        "💳 <b>Payment methods accepted:</b>",
        "₿ Bitcoin  |  🔷 USDT TRC20  |  💛 Binance Pay",
        "━━━━━━━━━━━━━━━━━━━",
        "👇 <b>Tap a package to pay &amp; activate.</b>",
    ]
    return "\n".join(lines)


@router.callback_query(F.data == "buy:binary")
async def cb_buy_binary(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await show_screen(call.bot, call.message.chat.id,
                      _binary_packages_text(), binary_packages_kb())


# ── Forex GOLDZILA price list ────────────────────────────
def _forex_packages_text() -> str:
    lines = [
        "💹 <b>FOREX AI PAID JOIN</b>",
        "",
        "🚀 <b>BOOST YOUR CAPITAL 100x</b>",
        "with our <b>SUPREME PRO AI</b> Signals",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    for p in GOLDZILA_PACKAGES:
        lines.append(
            f"❒ <b>{p['label']:<11}</b> "
            f"<s>${p['was']}</s>  ➜  <b>${p['price']}</b>"
        )
    lines += [
        "━━━━━━━━━━━━━━━━━━━",
        "💳 <b>Payment methods accepted:</b>",
        "₿ Bitcoin  |  🔷 USDT TRC20  |  💛 Binance Pay",
        "━━━━━━━━━━━━━━━━━━━",
        "♻️ <b>Lifetime access</b> with partner link Vip JOIN",
        "And get <b>FREE BOT lifetime access</b>",
        "•  <b>FOREX ADVANCE AI BOT</b> PAID JOIN USER",
        "<i>(IB change — MOST IF YOUR EXNESS USER "
        "For 1: unlimited leverage)</i>",
        "— contact 👉 <b>Support</b>",
        "",
        "👇 <b>Tap a package to pay &amp; activate.</b>",
    ]
    return "\n".join(lines)


@router.callback_query(F.data == "buy:forex")
async def cb_buy_forex(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await show_screen(call.bot, call.message.chat.id,
                      _forex_packages_text(), forex_packages_kb())


# ── Package selection → payment instructions ──────────────
def _back_for(pkg_id: str) -> str:
    if pkg_id.startswith("gz_"):
        return "buy:forex"
    return "buy:binary"


@router.callback_query(F.data.startswith("pkg:"))
async def cb_pkg(call: CallbackQuery, state: FSMContext):
    pkg_id = call.data.split(":", 1)[1]
    pkg = get_package(pkg_id)
    if not pkg:
        await call.answer("Package not found", show_alert=True); return
    await state.clear()
    text = (
        f"📦 <b>Selected:</b> {pkg['type']} · {pkg['label']}\n"
        f"💵 <b>Price:</b> ${pkg['price']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{PAYMENT_INFO_PAGE_1}\n\n"
        f"➡️ After paying, tap <b>SEND SCREENSHOT</b> below."
    )
    await call.answer()
    await show_screen(call.bot, call.message.chat.id, text,
                      package_payment_kb(pkg["id"], _back_for(pkg["id"])))


@router.callback_query(F.data.startswith("pay:page:"))
async def cb_payment_page(call: CallbackQuery, state: FSMContext):
    """Switch wallet page while keeping the selected package and flow."""
    _, _, pkg_id, raw_page = call.data.split(":", 3)
    pkg = get_package(pkg_id)
    if not pkg:
        await call.answer("Package not found", show_alert=True)
        return
    page = 2 if raw_page == "2" else 1
    wallet_text = PAYMENT_INFO_PAGE_2 if page == 2 else PAYMENT_INFO_PAGE_1
    text = (
        f"📦 <b>Selected:</b> {pkg['type']} · {pkg['label']}\n"
        f"💵 <b>Price:</b> ${pkg['price']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{wallet_text}\n\n"
        f"➡️ After paying, tap <b>SEND SCREENSHOT</b> below."
    )
    await call.answer(f"Payment methods page {page}/2")
    await show_screen(
        call.bot, call.message.chat.id, text,
        package_payment_kb(pkg["id"], _back_for(pkg["id"]), page=page),
    )


@router.callback_query(F.data.startswith("send_ss:"))
async def cb_send_ss(call: CallbackQuery, state: FSMContext):
    pkg_id = call.data.split(":", 1)[1]
    pkg = get_package(pkg_id)
    if not pkg:
        await call.answer("Package not found", show_alert=True); return

    payment_id = db.create_payment(call.from_user.id, call.from_user.username, pkg)
    await state.set_state(PayState.awaiting_screenshot)
    await state.update_data(payment_id=payment_id, pkg_id=pkg_id)

    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"📸 <b>Send your payment screenshot now.</b>\n\n"
        f"📦 Package: <b>{pkg['type']} · {pkg['label']}</b>\n"
        f"💵 Amount: <b>${pkg['price']}</b>\n\n"
        f"⤵️ Just upload the photo as your next message.",
        cancel_payment_kb(),
    )


@router.message(PayState.awaiting_screenshot, F.photo)
async def on_screenshot(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    payment_id = data.get("payment_id")
    if not payment_id:
        await state.clear(); return
    file_id = message.photo[-1].file_id
    db.attach_screenshot(payment_id, file_id)

    # Wipe user's uploaded photo from chat to keep the box clean
    await safe_delete(bot, message.chat.id, message.message_id)

    pkg_text = (
        f"📥 <b>Screenshot received.</b>\n\n"
        f"Tap below so admin can verify your payment."
    )
    await show_screen(bot, message.chat.id, pkg_text,
                      screenshot_check_kb(payment_id))
    await state.clear()


@router.message(PayState.awaiting_screenshot)
async def on_wrong_input(message: Message, bot: Bot):
    await safe_delete(bot, message.chat.id, message.message_id)


@router.callback_query(F.data.startswith("submit_ss:"))
async def cb_submit_ss(call: CallbackQuery, bot: Bot):
    payment_id = int(call.data.split(":", 1)[1])
    pay = db.get_payment(payment_id)
    if not pay or not pay.get("screenshot_file_id"):
        await call.answer("Screenshot not found.", show_alert=True); return

    admin_id = db.get_admin_id()
    user = call.from_user
    uname = f"@{user.username}" if user.username else "(no username)"

    caption = (
        f"📥 <b>NEW PAYMENT — REVIEW</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {uname}\n"
        f"🆔 Chat ID: <code>{user.id}</code>\n"
        f"📦 Package: <b>{pay['package_label']}</b>\n"
        f"💵 Amount: <b>${pay['amount']}</b>\n"
        f"🗓️ Days: <b>{pay['days'] if pay['days'] else 'LIFETIME'}</b>\n"
        f"🆔 Payment #<b>{pay['id']}</b>"
    )
    try:
        await bot.send_photo(
            chat_id=admin_id,
            photo=pay["screenshot_file_id"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=admin_review_kb(payment_id),
        )
    except Exception as e:
        await call.answer(f"Could not notify admin: {e}", show_alert=True)
        return

    await call.answer("Submitted ✅")

    # Wipe everything from the chat box and show only "Pending payment loading"
    await delete_active(bot, call.message.chat.id)
    sent = await bot.send_message(
        chat_id=call.message.chat.id,
        text=("⏳ <b>Pending payment loading…</b>\n\n"
              "Your payment is under admin review. You'll be notified here as "
              "soon as it's <b>approved</b> or <b>rejected</b>."),
        parse_mode="HTML",
    )
    db.set_active_msg(call.message.chat.id, sent.message_id)
    db.set_payment_pending_msg(payment_id, sent.message_id)


# ── Admin approve / reject ────────────────────────────────
@router.callback_query(F.data.startswith("adm:approve:"))
async def cb_admin_approve(call: CallbackQuery, bot: Bot):
    if call.from_user.id != db.get_admin_id():
        await call.answer("Not authorized", show_alert=True); return
    payment_id = int(call.data.split(":")[2])
    pay = db.get_payment(payment_id)
    if not pay:
        await call.answer("Payment not found", show_alert=True); return
    if pay["status"] == "approved":
        await call.answer("Already approved", show_alert=True); return

    days = int(pay["days"])
    access_type = "lifetime" if days == 0 else "temporary"
    db.grant_access(
        user_id=pay["user_id"],
        access_type=access_type,
        days=days,
        package_id=pay["package_id"],
        package_label=pay["package_label"],
    )
    db.update_payment_status(payment_id, "approved")

    # Vanish the admin's review message (the screenshot card)
    await safe_delete(bot, call.message.chat.id, call.message.message_id)
    await call.answer("✅ Approved")

    # Vanish the user's "pending payment loading" message
    if pay.get("pending_msg_id"):
        await safe_delete(bot, pay["user_id"], pay["pending_msg_id"])
    db.clear_active_msg(pay["user_id"])

    # Compute duration text + trade type for the new "Payment Received" screen
    if access_type == "lifetime":
        duration_text = "Lifetime"
    else:
        # e.g. "30 days (expires 2026-05-30 14:22 UTC)"
        from datetime import datetime, timedelta
        exp = datetime.utcnow() + timedelta(days=days)
        duration_text = f"{days} days (expires {exp.strftime('%Y-%m-%d %H:%M')} UTC)"
    trade_type = _trade_type_for(pay["package_id"])

    await send_payment_received_screen(
        bot=bot,
        user_id=pay["user_id"],
        access_type=access_type,
        duration_text=duration_text,
        trade_type=trade_type,
    )


@router.callback_query(F.data.startswith("adm:reject:"))
async def cb_admin_reject(call: CallbackQuery, bot: Bot):
    if call.from_user.id != db.get_admin_id():
        await call.answer("Not authorized", show_alert=True); return
    payment_id = int(call.data.split(":")[2])
    pay = db.get_payment(payment_id)
    if not pay:
        await call.answer("Payment not found", show_alert=True); return

    db.update_payment_status(payment_id, "rejected")
    await safe_delete(bot, call.message.chat.id, call.message.message_id)
    await call.answer("❌ Rejected")

    if pay.get("pending_msg_id"):
        await safe_delete(bot, pay["user_id"], pay["pending_msg_id"])
    db.clear_active_msg(pay["user_id"])

    msg = (
        f"❌ <b>Payment Rejected</b>\n\n"
        f"Your submitted payment for <b>{pay['package_label']}</b> "
        f"was not approved.\n\n"
        f"If you believe this is a mistake, contact {SUPPORT_USERNAME}."
    )
    try:
        sent = await bot.send_message(pay["user_id"], msg, parse_mode="HTML")
        db.set_active_msg(pay["user_id"], sent.message_id)
    except Exception:
        pass


# ── START BOT button on the pinned 'Payment Received' card ────
@router.callback_query(F.data == "paid:start")
async def cb_paid_start(call: CallbackQuery, bot: Bot):
    """When the new member taps START BOT on the pinned welcome card:
    keep the pinned card intact and open the home/workplace screen as a
    fresh new message. The 'show_screen' helper will then keep the chat
    clean from this point onward."""
    await call.answer("Opening your workplace…")
    chat_id = call.message.chat.id
    # Stop tracking the pinned card as the "active" screen — we want a new
    # WORKPLACE screen to appear below the pin, not replace the pin.
    db.clear_active_msg(chat_id)
    # Render home as a fresh message
    from handlers.main_menu import render_home
    await render_home(bot, chat_id, call.from_user, fast=True)
