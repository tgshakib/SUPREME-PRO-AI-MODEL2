"""Main menu, /start, join-required gate, navigation home."""
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import database as db
import os
from chat_clean import show_screen, show_photo_screen, delete_active, wipe_user_signals
from keyboards import (
    main_menu_kb, join_required_kb, binary_menu_kb, otc_broker_kb,
    pairs_kb, renew_kb, timezone_kb, WORLD_TIMEZONES,
    leaderboard_kb, LEADERBOARD_PAGES,
)
from config import (
    BOT_NAME, REQUIRED_BOT, REQUIRED_BOT_ID, DAILY_FREE_LIMIT,
)
from tz_utils import detect_tz, get_user_tz, format_for_user

router = Router()


def _is_admin(user_id: int) -> bool:
    return db.is_admin(user_id)


def _welcome_text(user, has_access: bool, is_admin: bool = False) -> str:
    """Two welcomes:
       • Admin → 'Assalamu Walaikum BOSS / CEO - the TOP G'
       • Everyone else → 'Assalamu Walaikum @username / Welcome ALL IN ONE
         SUPREME PRO AI BOT'  (falls back to first name if no @username)."""
    if is_admin:
        greeting = (
            "☪️ <b>Assalamu Walaikum BOSS</b> 👋\n"
            "Welcome <b>CEO - the TOP G</b>"
        )
    else:
        if user and getattr(user, "username", None):
            who = f"@{user.username}"
        elif user and getattr(user, "full_name", None):
            who = user.full_name
        else:
            who = "Trader"
        greeting = (
            f"☪️ <b>Assalamu Walaikum {who}</b> 👋\n"
            f"Welcome <b>ALL IN ONE SUPREME PRO AI BOT</b>"
        )
    return (
        f"{greeting}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 <b>Your Smart AI Signal for Global Traders</b>\n"
        "💰 <b><i>Earn $199,760 More a Year</i></b>\n"
        "「 <b>AI TRADING  SUPPORT</b> 」\n\n"
        "💹 <b>FOREX TRADING</b>\n"
        "🏦 <b>PROP FIRM</b> <i>(Funded Account Trading)</i>\n"
        "📊 <b>BINARY TRADING</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Choose an option below 👇"
    )


def _has_active_fx(chat_id: int) -> bool:
    """True when the user has any LIVE forex-style engine running:
    the 24/7 forex bot OR an active FUNDED PASS challenge. Both stream
    forex-style signals into the same `forex_signal` table, so the
    🟢 YOUR ACTIVE Fx-SIGNALS button correctly views either source."""
    if not (db.has_active_access(chat_id) or _is_admin(chat_id)):
        return False
    setup = db.get_forex_setup(chat_id)
    if setup and setup.get("status") == "active":
        return True
    fp = db.get_funded_pass(chat_id)
    if fp and fp.get("status") == "active":
        return True
    return False


JOIN_REQUIRED_TEXT = (
    f"🔒 <b>One quick step before you start.</b>\n\n"
    f"To activate <b>{BOT_NAME}</b> you must first start our community bot:\n"
    f"👉 {REQUIRED_BOT}\n\n"
    f"1️⃣ Tap the button below to open it.\n"
    f"2️⃣ Press <b>Start</b> in that bot.\n"
    f"3️⃣ Come back here and tap <b>I'VE JOINED</b>.\n\n"
    f"⚠️ If you block that bot, this signal bot will stop working "
    f"until you re-start it."
)


async def _check_required_bot_alive(bot, user_id: int) -> bool:
    """Best-effort check: try sending a typing chat-action via the required-bot
    channel. Telegram doesn't expose other-bot membership, so we trust the
    user's verify-tap. If they later 'block' our community bot, we can't
    detect it directly — return True here. The verify flag is the gate."""
    return True


async def render_home(bot, chat_id: int, user=None, *, fast: bool = False):
    is_adm = _is_admin(chat_id)
    is_configured_owner = db.is_configured_admin(chat_id)
    needs_admin_recovery = db.needs_admin_recovery(chat_id)

    # Verification gate
    if not db.is_verified(chat_id) and not (is_adm or is_configured_owner):
        await show_screen(bot, chat_id, JOIN_REQUIRED_TEXT, join_required_kb())
        return
    # Expired temporary access → renew prompt (admin always bypasses this)
    a = db.get_access(chat_id)
    if not is_adm and a and a["access_type"] == "temporary" and not db.has_active_access(chat_id):
        text = (
            "⌛ <b>Your access has expired.</b>\n\n"
            "Renew your access to continue receiving unlimited "
            "premium signals.\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎁 <b>Share This bot with Your 10 Trader Friend — after your "
            "Friends Active and Buy Access you will Get a Bonus!</b>\n"
            "<b>🔥 Don't miss this Opportunity!</b>"
        )
        await show_screen(bot, chat_id, text, renew_kb())
        return
    has = db.has_active_access(chat_id)
    show_ref = not has and not (is_adm or is_configured_owner)
    welcome_photo = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "welcome.jpg",
    )
    # Home should consistently show its welcome image.  ``fast`` controls the
    # navigation path, not whether the user sees the home visual.
    if os.path.exists(welcome_photo):
        await show_photo_screen(
            bot, chat_id,
            photo_path=welcome_photo,
            caption=_welcome_text(user, has, is_admin=is_adm),
            reply_markup=main_menu_kb(
                is_admin=is_adm,
                show_active_fx=_has_active_fx(chat_id),
                show_referral=show_ref,
                show_admin_recovery=needs_admin_recovery,
            ),
        )
    else:
        await show_screen(
            bot, chat_id,
            _welcome_text(user, has, is_admin=is_adm),
            main_menu_kb(
                is_admin=is_adm,
                show_active_fx=_has_active_fx(chat_id),
                show_referral=show_ref,
                show_admin_recovery=needs_admin_recovery,
            ),
        )


# ── /start ────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    # Check if this user is brand-new BEFORE upserting (for referral tracking)
    is_new_user = not db.user_exists(user.id)
    db.upsert_user(user.id, user.username, user.full_name)

    # Handle referral deep-link: /start ref_TOKEN
    text_parts = (message.text or "").split(" ", 1)
    ref_arg = text_parts[1].strip() if len(text_parts) > 1 else ""
    if ref_arg.startswith("ref_") and is_new_user:
        token = ref_arg[4:]  # strip "ref_" prefix
        link_row = db.get_link_by_token(token)
        if link_row:
            referrer_id = link_row["user_id"]
            # Don't let users refer themselves; don't count already-referred users
            if (referrer_id != user.id
                    and not db.has_been_referred(user.id)
                    and link_row.get("expires_at", "") > __import__("datetime").datetime.utcnow().isoformat()):
                db.record_referral_use(token, referrer_id, user.id)
                # Notify the referrer
                try:
                    count = db.get_referral_count(referrer_id)
                    bonus = db.get_referral_bonus(referrer_id)
                    milestone_text = ""
                    if count > 0 and count % 5 == 0:
                        milestone_text = (
                            f"\n🏅 <b>Milestone {count // 5} reached!</b>\n"
                            f"📊 +{bonus['bonus_binary']} bonus binary signals/day\n"
                            f"💹 +{bonus['bonus_forex']} bonus forex signal/day\n"
                            f"<i>(Active for 7 days)</i>"
                        )
                    await message.bot.send_message(
                        referrer_id,
                        f"🎉 <b>New referral!</b> A friend joined via your link.\n"
                        f"👥 Total referrals: <b>{count}</b>"
                        f"{milestone_text}",
                    )
                except Exception:
                    pass

    await render_home(message.bot, message.chat.id, user, fast=True)
    try:
        await message.delete()
    except Exception:
        pass


@router.message(Command("help"))
@router.message(Command("support"))
async def cmd_admin_contact(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    from config import OWNER_USERNAME
    text = (
        "📩 <b>Need help or have questions?</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Please reach out to our admin:\n\n"
        f"👤 <b>{OWNER_USERNAME}</b>\n\n"
        "We're here to help you with:\n"
        "• 💳 Payment & access issues\n"
        "• 📊 Signal questions\n"
        "• ⚙️ Bot settings & support\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Tap the username above to open a chat.</i>"
    )
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Contact Admin", url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton(text="🏠 Back to Menu", callback_data="m:home")],
    ])
    from chat_clean import show_screen
    await show_screen(message.bot, message.chat.id, text, kb)


@router.message(Command("admin"))
async def cmd_admin_panel(message: Message, state: FSMContext):
    """Open the real administration panel for the configured owner.

    `/admin` previously shared the support-contact handler, making the owner
    command appear broken despite valid administration configuration.
    """
    try:
        await message.delete()
    except Exception:
        pass
    if not _is_admin(message.from_user.id):
        await cmd_admin_contact(message)
        return
    from handlers.admin import _panel_text
    from keyboards import admin_panel_kb
    await state.clear()
    await show_screen(
        message.bot,
        message.chat.id,
        _panel_text(),
        admin_panel_kb(),
    )


@router.message(Command("home"))
@router.message(Command("menu"))
async def cmd_home(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    await render_home(message.bot, message.chat.id, message.from_user, fast=True)


@router.callback_query(F.data == "verify_join")
async def cb_verify_join(call: CallbackQuery):
    db.set_verified(call.from_user.id, 1)
    await call.answer("✅ Verified — welcome aboard!")
    await render_home(call.bot, call.message.chat.id, call.from_user, fast=True)


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "m:home")
async def cb_home(call: CallbackQuery):
    """WORKPLACE / home button.
    Clears the user's recent forex signal cards from the chat (best-effort —
    Telegram only allows the bot to delete its own messages, and only those
    sent within the last ~48h) so the chat returns to a clean home view."""
    await call.answer()
    # Render first so BACK / WORKPLACE is immediate. Deleting old tracking
    # cards is best-effort housekeeping and must not delay navigation.
    await render_home(call.bot, call.message.chat.id, call.from_user, fast=True)
    try:
        import asyncio
        asyncio.create_task(wipe_user_signals(call.bot, call.from_user.id))
    except Exception:
        pass


# ── Timezone picker (inline keyboard from home) ─────────────────────────
def _tz_screen_text(user_id: int) -> str:
    current = get_user_tz(user_id)
    local_now = format_for_user(user_id, "%Y-%m-%d %H:%M:%S %Z")
    return (
        "🌍 <b>TIMEZONE CHANGE</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Current: <b>{current}</b>\n"
        f"🕐 Local time: <b>{local_now}</b>\n\n"
        "Pick your city below — every signal time, expiry and "
        "AI-analysis clock will be shown in that timezone.\n\n"
        "<i>Tip: you can also send your GPS via /timezone for auto-detect.</i>"
    )


@router.callback_query(F.data == "tz:open")
async def cb_tz_open(call: CallbackQuery):
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        _tz_screen_text(call.from_user.id),
        timezone_kb(0),
    )


@router.callback_query(F.data.startswith("tz:pg:"))
async def cb_tz_page(call: CallbackQuery):
    try:
        page = int(call.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        _tz_screen_text(call.from_user.id),
        timezone_kb(page),
    )


@router.callback_query(F.data.startswith("tz:set:"))
async def cb_tz_set(call: CallbackQuery):
    tz_name = call.data[len("tz:set:"):]
    if tz_name not in WORLD_TIMEZONES:
        await call.answer("Unknown timezone", show_alert=True); return
    db.set_user_tz(call.from_user.id, tz_name)
    local_now = format_for_user(call.from_user.id, "%H:%M %Z")
    await call.answer(f"✅ {tz_name}  ({local_now})", show_alert=False)
    await show_screen(
        call.bot, call.message.chat.id,
        _tz_screen_text(call.from_user.id),
        timezone_kb(0),
    )


_MARKET_PHOTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "market.jpg",
)
_MARKET_TEXT = (
    "<b>SELECT ▸ MARKET TYPE</b>\n"
    "━━━━━━━━━━━━━━━━━━━\n"
    "Pick the market you want to trade 👇"
)


async def _show_binary_menu(bot, chat_id: int):
    if os.path.exists(_MARKET_PHOTO):
        await show_photo_screen(
            bot, chat_id,
            photo_path=_MARKET_PHOTO,
            caption=_MARKET_TEXT,
            reply_markup=binary_menu_kb(),
        )
    else:
        await show_screen(bot, chat_id, _MARKET_TEXT, binary_menu_kb())


@router.callback_query(F.data == "m:back_market")
async def cb_back_market(call: CallbackQuery):
    await call.answer()
    await _show_binary_menu(call.bot, call.message.chat.id)


@router.callback_query(F.data == "m:binary")
async def cb_binary(call: CallbackQuery):
    if not db.is_verified(call.from_user.id):
        await call.answer()
        await render_home(call.bot, call.message.chat.id, call.from_user); return
    await call.answer()
    await _show_binary_menu(call.bot, call.message.chat.id)


@router.callback_query(F.data == "m:binary_otc")
async def cb_binary_otc(call: CallbackQuery):
    await call.answer()
    _otc_broker_photo = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "otc_broker.png",
    )
    _otc_broker_text = (
        "🌐 <b>OTC MARKET</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Pick your broker 👇"
    )
    if os.path.exists(_otc_broker_photo):
        await show_photo_screen(
            call.bot, call.message.chat.id,
            photo_path=_otc_broker_photo,
            caption=_otc_broker_text,
            reply_markup=otc_broker_kb(),
        )
    else:
        await show_screen(call.bot, call.message.chat.id, _otc_broker_text, otc_broker_kb())


@router.callback_query(F.data == "m:binary_live")
async def cb_binary_live(call: CallbackQuery):
    from datetime import datetime
    await call.answer()
    # Sat & Sun: real LIVE binary pairs are closed (Pocket Option / Quotex
    # only stream OTC pairs on weekends).
    if datetime.utcnow().weekday() in (5, 6):
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
    _pair_photo = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "pair.jpg",
    )
    _live_text = (
        "<b>LIVE SELECT ▸ CURRENCY PAIR</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Choose a pair to analyze 👇"
    )
    if os.path.exists(_pair_photo):
        await show_photo_screen(
            call.bot, call.message.chat.id,
            photo_path=_pair_photo,
            caption=_live_text,
            reply_markup=pairs_kb(market="live", broker="-", page=0),
        )
    else:
        await show_screen(call.bot, call.message.chat.id, _live_text,
                          pairs_kb(market="live", broker="-", page=0))


@router.callback_query(F.data.startswith("brk:"))
async def cb_broker(call: CallbackQuery):
    broker = call.data.split(":", 1)[1]
    name = "POCKET OPTION (OTC)" if broker == "po" else "QUOTEX (OTC)"
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🌐 <b>{name}</b>\n━━━━━━━━━━━━━━━━━━━\n"
        "Pick a pair to analyze 👇",
        pairs_kb(market="otc", broker=broker, page=0),
    )


# ── /timezone — per-user timezone via shared GPS location ────────────────
def _share_location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Share My Location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tap the button below to share your location",
    )


@router.message(Command("timezone"))
async def cmd_timezone(msg: Message):
    """Ask the user to share their location so we can save their IANA TZ.
    Also accepts `/timezone <Region/City>` to set a TZ manually."""
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) == 2 and "/" in parts[1]:
        # Manual set, e.g. /timezone Asia/Dhaka
        tz_name = parts[1].strip()
        try:
            import pytz
            pytz.timezone(tz_name)  # validate
        except Exception:
            await msg.answer(
                "❌ Unknown timezone.\n"
                "Use the form <code>Region/City</code>, e.g. "
                "<code>/timezone Asia/Dhaka</code>.",
                parse_mode="HTML",
            )
            return
        db.set_user_tz(msg.from_user.id, tz_name)
        local_now = format_for_user(msg.from_user.id, "%Y-%m-%d %H:%M:%S %Z")
        await msg.answer(
            f"✅ Timezone saved: <b>{tz_name}</b>\n"
            f"🕐 Your local time now: <b>{local_now}</b>",
            parse_mode="HTML",
        )
        return

    current = get_user_tz(msg.from_user.id)
    await msg.answer(
        "🌍 <b>Set your timezone</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"Current: <b>{current}</b>\n\n"
        "Tap the button below to share your location and the bot will "
        "auto-detect your timezone.\n\n"
        "<i>Your location is used once and never stored.</i>",
        parse_mode="HTML",
        reply_markup=_share_location_kb(),
    )


@router.message(F.location)
async def on_location(msg: Message):
    """User shared GPS → detect IANA TZ, save it, confirm local time."""
    loc = msg.location
    if not loc:
        return
    tz_name = detect_tz(loc.latitude, loc.longitude)
    if not tz_name:
        await msg.answer(
            "❌ Sorry, couldn't detect your timezone from that location.\n"
            "Try setting it manually, e.g. <code>/timezone Asia/Dhaka</code>.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    db.set_user_tz(msg.from_user.id, tz_name)
    local_now = format_for_user(msg.from_user.id, "%Y-%m-%d %H:%M:%S %Z")
    await msg.answer(
        f"✅ Timezone detected: <b>{tz_name}</b>\n"
        f"🕐 Your local time now: <b>{local_now}</b>\n\n"
        "All signals will show times in your local timezone.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Leaderboard ───────────────────────────────────────────
_LB_PHOTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "leaderboard.jpg",
)


async def _show_lb_page(bot, chat_id: int, page: int):
    text = LEADERBOARD_PAGES[page]
    kb = leaderboard_kb(page)
    if page == 0 and os.path.exists(_LB_PHOTO):
        await show_photo_screen(bot, chat_id, photo_path=_LB_PHOTO,
                                caption=text, reply_markup=kb)
    else:
        await show_screen(bot, chat_id, text, kb)


@router.callback_query(F.data == "lb:open")
async def cb_lb_open(call: CallbackQuery):
    await call.answer()
    await _show_lb_page(call.bot, call.message.chat.id, 0)


@router.callback_query(F.data.startswith("lb:page:"))
async def cb_lb_page(call: CallbackQuery):
    await call.answer()
    try:
        page = int(call.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    page = max(0, min(page, len(LEADERBOARD_PAGES) - 1))
    await _show_lb_page(call.bot, call.message.chat.id, page)


