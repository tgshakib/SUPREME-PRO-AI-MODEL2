"""24/7 Forex flow:
   m:forex → choose TF → choose pairs (text input, comma list) →
   choose Max TP → bot active 24/7. Background engine sends signals.
   Each signal carries an I'M IN button for follow-up updates.

   Weekend awareness: real forex / metals / oil / indices don't trade on
   Sat & Sun. Crypto pairs (BTC, ETH, SOL, *USDT) trade 24/7. The pair
   selector politely rejects closed pairs and lists which ones are open.
"""
import asyncio
import os
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import database as db
from chat_clean import (
    show_screen, show_photo_screen, safe_delete, delete_active, wipe_user_signals,
)

_PAIR_PHOTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "pair.jpg",
)
_TIME_PHOTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "time.jpg",
)
from keyboards import (
    forex_tf_kb, forex_pairs_input_kb, forex_pairs_text,
    forex_tp_kb, forex_active_kb, fx_active_view_kb, forex_tp_locked_kb,
    instance_signal_result_kb,
)
from live_prices import decimals as live_decimals, pip_size as live_pip_size
from config import (
    FOREX_PAIRS, FOREX_TIMEFRAMES, TP_LEVELS, FREE_FOREX_DAILY_LIMIT,
)
from forex_engine import (
    run_im_in_simulation, run_alert_armed_simulation, reset_session_seq,
)


# Pairs that trade 24/7 (crypto). Everything else closes on weekends.
def _is_pair_24_7(pair: str) -> bool:
    p = pair.upper()
    return ("BTC" in p) or ("ETH" in p) or ("SOL" in p) or ("USDT" in p)


def _is_weekend() -> bool:
    return datetime.utcnow().weekday() in (5, 6)


def _is_admin(uid: int) -> bool:
    return int(uid) == int(db.get_admin_id())


def _is_premium(uid: int) -> bool:
    """Paid (temporary or lifetime) OR admin — bypass free-trial caps."""
    return db.has_active_access(uid) or _is_admin(uid)


def is_pair_open_now(pair: str) -> bool:
    if _is_pair_24_7(pair):
        return True
    return not _is_weekend()

router = Router()


class ForexState(StatesGroup):
    awaiting_pairs = State()


def _tf_label(code: str) -> str:
    for label, c in FOREX_TIMEFRAMES:
        if c == code:
            return label
    return code


# ── Step 1: Forex menu opens TF picker ────────────────────
@router.callback_query(F.data == "m:forex")
async def cb_forex(call: CallbackQuery, state: FSMContext):
    if not db.is_verified(call.from_user.id):
        await call.answer("Verify first via /start", show_alert=True); return
    await state.clear()
    await call.answer()
    _fx_tf_caption = (
        "💹 <b>FOREX TRADING — 24/7 ALERT</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<b>Step 1/3</b> — <b>SELECT ▸ TRADING TIME</b>"
    )
    if os.path.exists(_TIME_PHOTO):
        await show_photo_screen(
            call.bot, call.message.chat.id,
            photo_path=_TIME_PHOTO,
            caption=_fx_tf_caption,
            reply_markup=forex_tf_kb(),
        )
    else:
        await show_screen(call.bot, call.message.chat.id, _fx_tf_caption,
                          forex_tf_kb())


# ── Step 2: TF chosen → show numbered pair list, ask input ─
@router.callback_query(F.data.startswith("fxtf:"))
async def cb_fx_tf(call: CallbackQuery, state: FSMContext):
    tf = call.data.split(":", 1)[1]
    await state.set_state(ForexState.awaiting_pairs)
    await state.update_data(tf=tf)
    await call.answer()
    weekend_note = ""
    if _is_weekend():
        weekend_note = (
            "\n━━━━━━━━━━━━━━━━━━━\n"
            "🛑 <b>Weekend mode:</b> real forex / metals / oil / indices "
            "are <b>closed</b>.\n"
            "✅ Crypto pairs (<b>BTC, ETH, SOL, *USDT</b>) are open 24/7 "
            "— pick those."
        )
    # Free users get a stricter call-out in the header (1 pair / 1 signal /
    # TP 1 max). Paid + admin keep the standard 'max 10' guidance.
    is_premium = (db.has_active_access(call.from_user.id)
                  or _is_admin(call.from_user.id))
    if is_premium:
        pick_line = (
            "Reply with the <b>number(s)</b> from the list, separated "
            "by commas.\n"
            "Example: <code>1</code>  or  <code>1,3,14,20</code>  "
            "(<b>max 10</b> markets)"
        )
    else:
        pick_line = (
            "Reply with <b>ONE number</b> from the list (free trial = "
            "<b>1 pair · 1 signal · TP 1 max</b> per day).\n"
            "Example: <code>1</code>"
        )
    text = (
        f"💹 <b>FOREX TRADING — 24/7 ALERT</b>\n"
        f"⏱️ Timeframe: <b>{_tf_label(tf)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Step 2/3</b> — Pick the market(s) you want the bot to "
        f"watch.\n"
        f"{pick_line}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{forex_pairs_text()}"
        f"{weekend_note}"
    )
    if os.path.exists(_PAIR_PHOTO):
        await show_photo_screen(
            call.bot, call.message.chat.id,
            photo_path=_PAIR_PHOTO,
            caption=text,
            reply_markup=forex_pairs_input_kb(),
        )
    else:
        await show_screen(call.bot, call.message.chat.id, text,
                          forex_pairs_input_kb())


@router.message(ForexState.awaiting_pairs)
async def msg_fx_pairs(message: Message, state: FSMContext, bot: Bot):
    await safe_delete(bot, message.chat.id, message.message_id)
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()]
    nums = []
    for p in parts:
        if not p.isdigit():
            continue
        n = int(p)
        if 1 <= n <= len(FOREX_PAIRS):
            nums.append(n - 1)  # 0-based index
    nums = list(dict.fromkeys(nums))  # de-dupe, preserve order

    if not nums:
        await show_screen(
            bot, message.chat.id,
            "❌ No valid market numbers found.\n"
            f"Send numbers from <b>1</b> to <b>{len(FOREX_PAIRS)}</b>.\n"
            "Example: <code>1,5,10</code>",
            forex_pairs_input_kb(),
        )
        return
    # Free trial cap = 1 pair. Paid + admin can pick up to 10.
    if not _is_premium(message.from_user.id):
        nums = nums[:1]
    elif len(nums) > 10:
        nums = nums[:10]

    # Weekend: filter out closed real-market pairs (forex / metals / oil /
    # indices). Crypto pairs stay open 24/7.
    open_nums = [i for i in nums if is_pair_open_now(FOREX_PAIRS[i])]
    closed_nums = [i for i in nums if i not in open_nums]
    if not open_nums:
        closed_list = ", ".join(FOREX_PAIRS[i] for i in closed_nums)
        await show_screen(
            bot, message.chat.id,
            "🛑 <b>Weekend — those pairs are closed.</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"Closed: <b>{closed_list}</b>\n\n"
            "Real forex / metals / oil / indices don't trade on "
            "<b>Sat &amp; Sun</b>.\n"
            "👉 Pick an <b>open</b> pair instead — crypto pairs (BTC, ETH, "
            "SOL, *USDT) run 24/7.",
            forex_pairs_input_kb(),
        )
        return
    nums = open_nums
    closed_note = ""
    if closed_nums:
        skipped = ", ".join(FOREX_PAIRS[i] for i in closed_nums)
        closed_note = (
            f"\n⚠️ <b>Skipped (weekend closed):</b> {skipped}"
        )

    data = await state.get_data()
    tf = data.get("tf", "")
    selected = ", ".join(FOREX_PAIRS[i] for i in nums)

    await state.update_data(pairs=",".join(str(n) for n in nums))
    text = (
        f"💹 <b>FOREX TRADING — 24/7 ALERT</b>\n"
        f"⏱️ Timeframe: <b>{_tf_label(tf)}</b>\n"
        f"📊 Markets: <b>{selected}</b>{closed_note}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Step 3/3</b> — Choose your <b>MAX TAKE PROFIT (TP)</b>:"
    )
    await show_screen(bot, message.chat.id, text, forex_tp_kb())


# ── Step 3: TP chosen → activate ──────────────────────────
@router.callback_query(F.data.startswith("fxtp:"))
async def cb_fx_tp(call: CallbackQuery, state: FSMContext):
    max_tp = int(call.data.split(":", 1)[1])
    data = await state.get_data()
    tf = data.get("tf")
    pairs = data.get("pairs")
    if not tf or not pairs:
        await call.answer("Setup expired — start again.", show_alert=True)
        await state.clear()
        return

    # Free user trying to pick >30 pips → upsell screen (not auto-capped).
    # They can still come BACK and choose 30+ PIPS to keep using the free trial.
    if not _is_premium(call.from_user.id) and max_tp > 30:
        from config import pip_target_from_max_tp
        pips = pip_target_from_max_tp(max_tp)
        await call.answer()
        await show_screen(
            call.bot, call.message.chat.id,
            f"🚫 <b>{pips}+ PIPS IS A PAID-ONLY TARGET</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🚀 <b>BUY BOT ACCESS TO UNLOCK ALL THIS BENEFIT &amp; "
            "USE THE BOT AT FULL POWER:</b>\n\n"
            "✅ <b>40 / 60 / 80 / 100 / 120 / 150 / 200 / 300 / 500 / 900+ PIPS</b> targets\n"
            "✅ <b>BIG MOVE · RESERVE · ULTRA · MEGA · MONSTER</b> sniper signals\n"
            "✅ Watch up to <b>10 markets</b> at once\n"
            "✅ <b>Unlimited 24/7</b> Forex signals (no daily cap)\n"
            "✅ Full <b>SUPREME PRO</b> setups — session + footprint confirmed\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎯 Tap <b>BUY FULL ACCESS</b> below — or go back to "
            "<b>WORKPLACE</b> and stay on the free trial (30+ PIPS).",
            forex_tp_locked_kb(),
        )
        return

    db.upsert_forex_setup(call.from_user.id, tf, pairs, max_tp)
    # Fresh active session → reset I'M IN counter so the next signal is #01.
    reset_session_seq(call.from_user.id)
    await state.clear()
    await call.answer("Bot activated ✅")

    from config import pip_target_from_max_tp
    pip_tgt = pip_target_from_max_tp(max_tp)
    sel_pairs = ", ".join(
        FOREX_PAIRS[int(i)] for i in pairs.split(",")
    )
    free_note = ""
    if not _is_premium(call.from_user.id):
        free_note = (
            "\n\n⚠️ <b>Free trial:</b> 1 pair · 1 signal · 20+ PIPS max per day. "
            "After your daily signal, the bot stops and you'll need to set "
            "TF/pairs/target again. Buy access for 24/7 unlimited signals."
        )

    text = (
        f"🟢 <b>NOW BOT ACTIVE FOR YOU 24/7</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Timeframe: <b>{_tf_label(tf)}</b>\n"
        f"📊 Markets: <b>{sel_pairs}</b>\n"
        f"🎯 Pip Target: <b>{pip_tgt}+ PIPS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📡 Bot scans session + chart for <b>SNIPER / BIG MOVE</b> setups.\n"
        f"   Signal fires when a <b>high-quality confirmed entry</b> is detected.\n"
        f"✅ Signal includes: Entry · TP ladder · SL · Session · Footprint\n"
        f"   Pip range shown on each TP — big moves labelled clearly.\n"
        f"🛑 Tap <b>STOP</b> to deactivate &amp; clear the panel.{free_note}"
    )
    setup_now = db.get_forex_setup(call.from_user.id) or {}
    gold_on = bool(int(setup_now.get("gold_king_mode") or 0))
    await show_screen(call.bot, call.message.chat.id, text,
                      forex_active_kb(gold_king=gold_on))


# ── STOP ──────────────────────────────────────────────────
@router.callback_query(F.data == "fx:stop")
async def cb_fx_stop(call: CallbackQuery, bot: Bot):
    """STOP the 24/7 engine, close any open signals, wipe every signal
    card from the chat AND make the STOP panel itself vanish — the user
    is left on a clean home screen, exactly as requested."""
    user_id = call.from_user.id
    db.set_forex_status(user_id, "stopped")
    # Also stop any active Funded Pass challenge so both streams halt together
    try:
        fp = db.get_funded_pass(user_id)
        if fp and fp.get("status") == "active":
            db.set_funded_pass_status(user_id, "stopped")
    except Exception:
        pass
    # Reset the per-session I'M IN counter — next active session restarts at #01.
    reset_session_seq(user_id)
    # Close out every still-open signal so they stop being tracked
    try:
        for s in db.list_open_forex_signals(user_id):
            db.update_forex_signal_progress(
                int(s["id"]), int(s.get("tps_hit") or 0),
                "stopped", "closed",
            )
    except Exception:
        pass
    await call.answer("Stopped & cleared 🛑", show_alert=False)
    # Best-effort wipe of every recent forex signal card from the chat
    try:
        await wipe_user_signals(bot, user_id)
    except Exception:
        pass
    # Make the STOP panel vanish completely — no lingering keyboard
    try:
        await delete_active(bot, call.message.chat.id)
    except Exception:
        pass
    # Also delete the message the STOP button was attached to (covers the
    # case where it's a forex signal card that isn't tracked as 'active').
    try:
        await safe_delete(bot, call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    # Drop the user back on the WORKPLACE / home screen.
    try:
        from handlers.main_menu import render_home
        await render_home(bot, call.message.chat.id, call.from_user)
    except Exception:
        pass


# ── 🥇 GOLD KING MODE — toggle Gold-only signals ──────────
@router.callback_query(F.data == "fx:gold")
async def cb_fx_gold(call: CallbackQuery):
    """Toggle 🥇 GOLD KING MODE. When ON, the engine ignores every
    other pair and only sends XAU/USD (Gold) signals — for traders who
    want a pure Gold feed."""
    user_id = call.from_user.id
    setup = db.get_forex_setup(user_id)
    if not setup:
        await call.answer("Activate the 24/7 engine first.", show_alert=True)
        return
    new_val = not bool(int(setup.get("gold_king_mode") or 0))
    db.set_gold_king_mode(user_id, new_val)
    if new_val:
        await call.answer("🥇 GOLD KING ON — Gold-only signals", show_alert=False)
    else:
        await call.answer("🥇 GOLD KING OFF — all your pairs back", show_alert=False)
    # Update the keyboard in place so the button label flips immediately.
    try:
        await call.message.edit_reply_markup(
            reply_markup=forex_active_kb(gold_king=new_val)
        )
    except Exception:
        pass


# ── NEW SIGNAL — re-arm the engine for the next entry ────
@router.callback_query(F.data.in_({"fx:new", "fx:more"}))
async def cb_fx_new(call: CallbackQuery, bot: Bot):
    """User tapped NEW SIGNAL. Wipes every previous signal card from the
    chat, then forces the 24/7 engine to immediately scan for the next
    high-accuracy sniper entry on the user's selected pairs.
    Also kept on the `fx:more` callback so older signal cards still work.
    """
    user_id = call.from_user.id
    setup = db.get_forex_setup(user_id)
    if not setup or setup.get("status") != "active":
        await call.answer(
            "Activate the 24/7 engine first.", show_alert=True
        )
        return
    # Free trial: 1 signal per day cap (+ any referral bonus) still applies
    if (not _is_premium(user_id)
            and setup.get("day") == db.today_str()):
        import database as _db2
        bonus = _db2.get_referral_bonus(user_id)
        effective_forex_limit = FREE_FOREX_DAILY_LIMIT + bonus["bonus_forex"]
        if (setup.get("sent_today") or 0) >= effective_forex_limit:
            bonus_note = (
                f" (+{bonus['bonus_forex']} referral bonus)" if bonus["bonus_forex"] else ""
            )
            await call.answer(
                f"Free trial = {effective_forex_limit} signal/day{bonus_note}. "
                "Buy access for unlimited.",
                show_alert=True,
            )
            return
    await call.answer(
        "🎯 Searching for a fresh A+ sniper entry…", show_alert=False,
    )
    # 1) Close any still-open signals so the engine isn't blocked by them
    try:
        for s in db.list_open_forex_signals(user_id):
            db.update_forex_signal_progress(
                int(s["id"]), int(s.get("tps_hit") or 0),
                "replaced", "closed",
            )
    except Exception:
        pass
    # 2) Wipe every previous signal card from the chat (open + closed)
    try:
        await wipe_user_signals(bot, user_id)
    except Exception:
        pass
    # 3) Re-open the one-at-a-time gate so the next loop tick fires.
    db.set_more_signal_requested(user_id, True)
    # 4) Force an immediate scan instead of waiting for the next loop tick
    try:
        from forex_engine import trigger_immediate_scan
        asyncio.create_task(trigger_immediate_scan(bot, user_id))
    except Exception:
        pass


# ── I'M IN — opt in to live updates ───────────────────────
@router.callback_query(F.data.startswith("fxin:"))
async def cb_fx_im_in(call: CallbackQuery, bot: Bot):
    sig_id = int(call.data.split(":", 1)[1])
    sig = db.get_forex_signal(sig_id)
    if not sig:
        await call.answer("Signal not found", show_alert=True); return
    if sig.get("im_in"):
        await call.answer("Already tracking this signal", show_alert=True); return
    if sig["status"] != "open":
        await call.answer("Signal already closed", show_alert=True); return

    db.mark_im_in(sig_id)
    await call.answer("✅ In position — tracking your trade.", show_alert=False)

    # ── Collapse the signal card into a compact "I'M IN" tracker ──
    # The full signal panel hides; only the pair + side + status stays
    # visible so the chat is clean. Full TP/SL detail lives under the
    # 🟢 YOUR ACTIVE Fx-Signal button below.
    pair      = sig.get("pair", "")
    direction = sig.get("direction", "")
    side_icon = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    dec       = live_decimals(pair)
    entry_val = sig.get("entry")
    entry_str = f"  ·  Entry <code>{float(entry_val):.{dec}f}</code>" if entry_val else ""
    compact = (
        f"✅ <b>I'M IN  ·  {pair}  ·  {side_icon}</b>{entry_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👀 <b>TRACKING YOUR TRADE</b>\n"
        f"<i>Tap <b>🟢 Active Signal</b> below to see full TP / SL and live status.</i>"
    )
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    sig_kind = (sig.get("kind") or "LIVE").upper()
    if sig_kind == "INSTANCE":
        compact_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Signal History",  callback_data="fx:instant_history")],
            [InlineKeyboardButton(text="🟢 Active Signal",   callback_data="fx:active_view"),
             InlineKeyboardButton(text="⚡ New Instance",    callback_data="fx:instant")],
            [InlineKeyboardButton(text="🛑 STOP",            callback_data="fx:stop"),
             InlineKeyboardButton(text="🏢 WORKPLACE",        callback_data="m:home")],
        ])
    else:
        compact_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Active Signal",   callback_data="fx:active_view"),
             InlineKeyboardButton(text="⚡ New Instance",    callback_data="fx:instant")],
            [InlineKeyboardButton(text="🛑 STOP",            callback_data="fx:stop"),
             InlineKeyboardButton(text="🏢 WORKPLACE",        callback_data="m:home")],
        ])
    try:
        await call.message.edit_text(compact, parse_mode="HTML",
                                     reply_markup=compact_kb)
    except Exception:
        try:
            await call.message.delete()
        except Exception:
            pass

    asyncio.create_task(run_im_in_simulation(bot, sig_id))


# ── ALERT ME — opt in to LIMIT-ORDER fill notification ────
@router.callback_query(F.data.startswith("fxalert:"))
async def cb_fx_alert_me(call: CallbackQuery, bot: Bot):
    """Only fired by LIMIT-ORDER signals. Hides the original card and arms
    a watcher — when market reaches the limit price, bot re-pings the user
    with the same signal flipped to LIVE NOW + I'M IN button."""
    sig_id = int(call.data.split(":", 1)[1])
    sig = db.get_forex_signal(sig_id)
    if not sig:
        await call.answer("Signal not found", show_alert=True); return
    if sig["status"] != "open":
        await call.answer("Signal already closed", show_alert=True); return
    if db.is_forex_alert_armed(sig_id):
        await call.answer("Alert already armed", show_alert=True); return

    db.arm_forex_alert(sig_id)
    await call.answer(
        "🔔 Alert armed — I'll ping you when price taps the zone.",
        show_alert=False,
    )
    asyncio.create_task(run_alert_armed_simulation(bot, sig_id))


_SUPS = ["¹", "²", "³", "⁴", "⁵", "⁶", "⁷", "⁸"]


def _format_active_sig_block(s: dict) -> str:
    """Build the FULL 'I'M IN' detail block for ONE open signal — pair,
    side, entry, every TP price, SL price + tracking status. Shown under
    the 🟢 YOUR ACTIVE Fx-Signal button so the user sees everything that
    matters at a glance."""
    pair = s["pair"]
    direction = s["direction"]
    side = "BUY 🔼" if direction == "BUY" else "SELL 🔽"
    head_emoji = "🟢" if direction == "BUY" else "🔴"
    dec = live_decimals(pair)
    entry = s.get("entry")
    try:
        tp_prices = [float(x) for x in (s.get("tp_prices") or "").split(",") if x]
    except Exception:
        tp_prices = []
    sl_price = float(s.get("sl_price") or 0.0)
    tps_hit = int(s.get("tps_hit") or 0)
    max_tp = int(s.get("max_tp") or len(tp_prices))
    kind = s.get("kind") or "LIVE"
    kind_tag = "📍 LIMIT" if kind == "LIMIT" else "🟢 LIVE NOW"
    tracker = "👀 <b>TRACKING (I'M IN)</b>" if s.get("im_in") else "🟡 <b>FRESH SIGNAL</b>"

    out = [f"{head_emoji} <b>{pair}</b>  ·  <b>{side}</b>  ·  {kind_tag}"]
    if entry is not None:
        try:
            out.append(f"⚡ <b>ENTRY</b>   <code>{float(entry):.{dec}f}</code>")
        except Exception:
            pass
    _TP_STEPS = [60, 90, 130, 160, 190, 250]
    for i, p in enumerate(tp_prices):
        sup = _SUPS[i] if i < len(_SUPS) else f"({i+1})"
        pips_off = _TP_STEPS[i] if i < len(_TP_STEPS) else (i + 1) * 30
        check = "  ✅" if i < tps_hit else ""
        out.append(
            f"🎯 <b>TP{sup}</b>  <code>{p:.{dec}f}</code>"
            f"   <i>(+{pips_off} pips)</i>{check}"
        )
    out.append(f"🛡️ <b>SL</b>    <code>{sl_price:.{dec}f}</code>   <i>(-25 pips)</i>")
    out.append(f"<i>{tracker}  ·  TPs hit {tps_hit}/{max_tp}</i>")
    return "\n".join(out)


# ── Active FX View — opened from the home '🟢 YOUR ACTIVE Fx-Signal' button ─
@router.callback_query(F.data == "fx:active_view")
async def cb_fx_active_view(call: CallbackQuery, bot: Bot):
    """Show the user's currently OPEN forex signals as a single fresh
    consolidated card with FULL I'M IN detail (entry, every TP price, SL
    price, tracking state). Replaces the previous tracking message every
    time it's opened, so the chat only ever holds the freshest snapshot.
    """
    await call.answer()
    open_sigs = db.list_open_forex_signals(call.from_user.id)
    if not open_sigs:
        await show_screen(
            call.bot, call.message.chat.id,
            "ℹ️ <b>No active Fx-Signals right now.</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "When the 24/7 engine fires a fresh signal and you tap "
            "<b>I'M IN</b>, it will show up here with full TP &amp; SL "
            "prices until SL or TP closes it.",
            fx_active_view_kb(),
        )
        return

    blocks = [
        "🟢 <b>YOUR ACTIVE Fx-SIGNALS</b>  ·  fresh tracking",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    for s in open_sigs[:5]:
        blocks.append(_format_active_sig_block(s))
        blocks.append("━━━━━━━━━━━━━━━━━━━")
    blocks.append(
        "ℹ️ <i>Each new signal removes the previous tracking text.</i>\n"
        "🛑 <b>STOP</b> = clear all old &amp; new tracking from chat."
    )
    sig_items = [
        (int(s["id"]), int(s.get("session_seq") or 0))
        for s in open_sigs[:5] if not s.get("im_in")
    ]
    await show_screen(
        call.bot, call.message.chat.id, "\n".join(blocks),
        fx_active_view_kb(sig_items),
    )


@router.callback_query(F.data == "fx:close_view")
async def cb_fx_close_view(call: CallbackQuery, bot: Bot):
    """Close the active-view panel and go back to WORKPLACE."""
    await call.answer()
    from handlers.main_menu import render_home
    await delete_active(bot, call.message.chat.id)
    await render_home(bot, call.message.chat.id, call.from_user)


# ── ⚡ Instance SIGNAL — fast pure-PA scan, fires in 5-8 s ───────────────────
@router.callback_query(F.data == "fx:instant")
async def cb_fx_instant(call: CallbackQuery, bot: Bot):
    """User tapped ⚡ Instance SIGNAL.

    Runs a fast (5-8 s) pure price-action scan across session-ranked pairs,
    picks the best current setup and sends a formatted signal card immediately.
    No waiting for the 24/7 engine loop — fires right now.
    Free users: subject to daily forex limit.  Premium: unlimited.
    """
    import os
    from instant_signal_engine import instant_scan, format_instant_signal
    from aiogram.types import FSInputFile

    user_id = call.from_user.id

    if not db.is_verified(user_id):
        await call.answer("Verify first via /start", show_alert=True)
        return

    # ── Free trial limit (same cap as NEW SIGNAL) ─────────────────────────
    if not _is_premium(user_id):
        setup = db.get_forex_setup(user_id)
        if setup and setup.get("day") == db.today_str():
            import database as _db2
            bonus = _db2.get_referral_bonus(user_id)
            effective_limit = FREE_FOREX_DAILY_LIMIT + bonus["bonus_forex"]
            if (setup.get("sent_today") or 0) >= effective_limit:
                bonus_note = (
                    f" (+{bonus['bonus_forex']} referral bonus)"
                    if bonus["bonus_forex"] else ""
                )
                await call.answer(
                    f"Free trial = {effective_limit} signal/day{bonus_note}. "
                    "Buy access for unlimited.",
                    show_alert=True,
                )
                return

    await call.answer("⚡ Scanning market… signal ready in 5-8 sec", show_alert=False)

    # ── Scanning placeholder message ──────────────────────────────────────
    scan_msg = None
    try:
        scan_msg = await call.message.answer(
            "⚡ <b>SUPREME AI FX Analysing ...</b>", parse_mode="HTML"
        )
    except Exception:
        pass

    # ── Run the analysis (await gives event loop time while fetch runs) ───
    await asyncio.sleep(1.0)   # let the scanning message show first

    user_pairs = _user_pair_list(user_id)
    loop = asyncio.get_event_loop()
    try:
        # Strict=True → scan ONLY the user's selected pair(s), no fallback fill
        sig = await loop.run_in_executor(None, lambda: instant_scan(
            user_pairs, strict=bool(user_pairs)
        ))
    except Exception as e:
        if scan_msg:
            try:
                await scan_msg.edit_text(
                    "⚠️ <b>Scan failed</b> — market data temporarily unavailable.\n"
                    "Please try again in a moment.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    # ── Add realistic analysis delay (5-8 s total) ────────────────────────
    await asyncio.sleep(4.5)

    # ── Format signal text ────────────────────────────────────────────────
    caption = format_instant_signal(sig, user_id=user_id)

    # ── Insert signal into DB so I'M IN / tracker work like a normal signal
    sig_id = None
    try:
        sig_id = db.create_forex_signal(
            user_id   = user_id,
            chat_id   = call.message.chat.id,
            pair      = sig["pair"],
            direction = sig["direction"],
            entry     = sig["entry"],
            tp_prices = [t["price"] for t in sig["tps"]],
            sl_price  = sig["sl"],
            max_tp    = len(sig["tps"]),
            kind      = "INSTANCE",
        )
    except Exception:
        pass

    kb = instance_signal_result_kb(sig_id) if sig_id else _instant_signal_kb()

    # ── Choose banner photo ───────────────────────────────────────────────
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banner_buy  = os.path.join(_base, "assets", "forex_buy.jpg")
    banner_sell = os.path.join(_base, "assets", "forex_sell.jpg")
    photo_path  = banner_buy if sig["direction"] == "BUY" else banner_sell

    # Delete the scanning placeholder
    if scan_msg:
        try:
            await scan_msg.delete()
        except Exception:
            pass

    # ── Send signal card ──────────────────────────────────────────────────
    sent_msg = None
    try:
        if os.path.exists(photo_path):
            sent_msg = await bot.send_photo(
                chat_id=call.message.chat.id,
                photo=FSInputFile(photo_path),
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            sent_msg = await call.message.answer(
                caption, parse_mode="HTML", reply_markup=kb,
            )
    except Exception as e:
        try:
            sent_msg = await call.message.answer(caption, parse_mode="HTML")
        except Exception:
            pass

    # Track the message_id so signal card can be found for I'M IN wipe
    if sig_id and sent_msg:
        try:
            db.set_forex_signal_msg(sig_id, sent_msg.message_id)
        except Exception:
            pass


def _user_pair_list(user_id: int) -> list[str]:
    """Return the pair names the user configured for their forex setup.

    Pairs are stored as a comma-separated string of indices into FOREX_PAIRS
    (e.g. "0,5,10").  Returns a list of pair name strings like ["EUR/USD"].
    """
    try:
        setup = db.get_forex_setup(user_id)
        if setup and setup.get("pairs"):
            raw = str(setup["pairs"])
            indices = [int(i) for i in raw.split(",") if i.strip().isdigit()]
            return [FOREX_PAIRS[i] for i in indices if 0 <= i < len(FOREX_PAIRS)]
    except Exception:
        pass
    return []


def _instant_signal_kb():
    """Fallback keyboard when Instance Signal DB insert fails (no sig_id)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ New Instance",   callback_data="fx:instant"),
            InlineKeyboardButton(text="🎯 AI Sniper",      callback_data="fx:new"),
        ],
        [
            InlineKeyboardButton(text="🟢 Active Signals", callback_data="fx:active_view"),
            InlineKeyboardButton(text="⬅️ BACK",           callback_data="m:home"),
        ],
    ])


# ── Signal History ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "fx:signal_history")
async def cb_fx_signal_history(call: CallbackQuery, bot: Bot):
    """Show the last 10 forex signals (open + closed) for this user."""
    await call.answer()
    user_id = call.from_user.id
    signals = db.list_recent_forex_signals(user_id, limit=10)

    if not signals:
        await show_screen(
            call.bot, call.message.chat.id,
            "📊 <b>No signal history yet.</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Your forex signal history will appear here after you receive signals.",
            _history_kb(),
        )
        return

    _STATUS_ICON = {
        "open":   "🟡",
        "closed": "✅",
    }
    _OUTCOME_ICON = {
        "tp":      "✅ TP HIT",
        "partial": "🔶 PARTIAL",
        "sl":      "❌ SL HIT",
        "expired": "⏰ EXPIRED",
    }
    blocks = [
        "📊 <b>SIGNAL HISTORY</b>  ·  last 10 signals",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    for s in signals:
        pair      = s.get("pair", "?")
        direction = s.get("direction", "?")
        status    = s.get("status", "open")
        outcome   = s.get("outcome")
        tps_hit   = int(s.get("tps_hit") or 0)
        max_tp    = int(s.get("max_tp") or 1)
        kind      = s.get("kind") or "LIVE"
        side_icon = "🟢" if direction == "BUY" else "🔴"
        st_icon   = _STATUS_ICON.get(status, "⚪")
        out_str   = _OUTCOME_ICON.get(outcome, "") if outcome else ""
        tp_prog   = f"TP {tps_hit}/{max_tp}" if max_tp else ""
        kind_tag  = "📍 LIMIT" if kind == "LIMIT" else "🟢 LIVE"
        line = f"{st_icon} {side_icon} <b>{pair}</b>  ·  {direction}  ·  {kind_tag}"
        if tp_prog:
            line += f"  ·  {tp_prog}"
        if out_str:
            line += f"  {out_str}"
        blocks.append(line)

    blocks.append("━━━━━━━━━━━━━━━━━━━")
    await show_screen(
        call.bot, call.message.chat.id,
        "\n".join(blocks),
        _history_kb(),
    )


def _history_kb():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Active Signals",  callback_data="fx:active_view"),
         InlineKeyboardButton(text="⚡ New Instance",    callback_data="fx:instant")],
        [InlineKeyboardButton(text="🎯 AI Sniper",       callback_data="fx:new"),
         InlineKeyboardButton(text="🏢 WORKPLACE",       callback_data="m:home")],
    ])


# ── ⚡ Instance Signal History — current signal data (Pair, SL, TP) ─────────
@router.callback_query(F.data == "fx:instant_history")
async def cb_fx_instant_history(call: CallbackQuery, bot: Bot):
    """Show the most recent Instance Signal's Pair, SL, TP data.
    Only accessible after tapping 🟢 I'M IN on an Instance Signal.
    All data comes directly from DB — no false or placeholder values.
    Stop and Back both wipe this view the same as any FX signal view."""
    await call.answer()
    user_id = call.from_user.id

    signals = db.list_recent_forex_signals(user_id, limit=20)
    instance_sigs = [s for s in signals
                     if (s.get("kind") or "LIVE").upper() == "INSTANCE"]

    if not instance_sigs:
        await show_screen(
            call.bot, call.message.chat.id,
            "⚡ <b>No Instance Signal data yet.</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Tap <b>⚡ Instance Signal</b> to generate a fresh signal.",
            _instant_history_kb(),
        )
        return

    sig      = instance_sigs[0]
    pair      = sig.get("pair", "?")
    direction = sig.get("direction", "?")
    status    = sig.get("status", "open")
    tps_hit   = int(sig.get("tps_hit") or 0)
    entry     = sig.get("entry")
    sl_price  = float(sig.get("sl_price") or 0.0)

    try:
        tp_prices = [float(x) for x in (sig.get("tp_prices") or "").split(",") if x]
    except Exception:
        tp_prices = []

    try:
        dec = live_decimals(pair)
    except Exception:
        dec = 5

    head_emoji  = "🟢" if direction == "BUY" else "🔴"
    side_word   = "BUY 🔼" if direction == "BUY" else "SELL 🔽"
    status_icon = "🟡 OPEN" if status == "open" else "✅ CLOSED"

    blocks = [
        "⚡ <b>INSTANCE SIGNAL HISTORY</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"{head_emoji} <b>{pair}</b>  ·  <b>{side_word}</b>",
        "",
    ]
    if entry is not None:
        try:
            blocks.append(f"⚡ <b>ENTRY</b>   <code>{float(entry):.{dec}f}</code>")
        except Exception:
            pass

    _TP_PIPS = [60, 90, 130, 160, 190, 250]
    for i, p in enumerate(tp_prices):
        pips = _TP_PIPS[i] if i < len(_TP_PIPS) else (i + 1) * 30
        hit  = "  ✅" if i < tps_hit else ""
        blocks.append(
            f"🎯 <b>TP{i + 1}</b>  <code>{p:.{dec}f}</code>"
            f"   <i>(+{pips} pips)</i>{hit}"
        )

    if sl_price > 0:
        blocks.append(f"🛡️ <b>SL</b>    <code>{sl_price:.{dec}f}</code>")

    blocks += [
        "━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>Status:</b> {status_icon}  ·  TPs hit: {tps_hit}/{len(tp_prices)}",
    ]

    await show_screen(
        call.bot, call.message.chat.id,
        "\n".join(blocks),
        _instant_history_kb(),
    )


def _instant_history_kb():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ New Instance",    callback_data="fx:instant"),
         InlineKeyboardButton(text="🟢 Active Signals",  callback_data="fx:active_view")],
        [InlineKeyboardButton(text="🛑 STOP",            callback_data="fx:stop"),
         InlineKeyboardButton(text="🏢 WORKPLACE",        callback_data="m:home")],
    ])
