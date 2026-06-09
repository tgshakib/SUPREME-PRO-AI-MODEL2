"""FUNDED PASS — prop-firm challenge mode.

Flow:
    🏛 FUNDED PASS  →  Account size  →  Profit target %  →  Daily loss %  →
                      Max drawdown %  →  Timeframe  →  Pair  →  ACTIVE.

While active, the bot fires LIVE / LIMIT-ORDER signals on the chosen pair.
Each closed signal moves the user's running equity %:
    win  →  +(profit_target / target_signals_to_pass) %
    loss →  -2% (the user's spec: every funded-pass SL costs 2%)

Hits:
    • equity % ≥ profit_target  →  CONGRATS, switch to FOREX TRADERS button
    • equity % ≤ -max_dd        →  "Part of trading. Better luck next time…"
    • daily %  ≤ -daily_loss    →  pause for the day, resume tomorrow
"""
import asyncio
import random
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db
from chat_clean import show_screen, safe_delete
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from keyboards import (
    fp_account_kb, fp_profit_kb, fp_daily_kb, fp_dd_kb, fp_tf_kb,
    fp_pairs_input_kb, fp_active_kb, fp_finished_kb,
    forex_pairs_text,
)
from config import (
    FOREX_PAIRS, FOREX_TIMEFRAMES, FP_ACCOUNT_SIZES,
)
from forex_engine import (
    _generate_levels, _signal_text, _shift_for_limit, _ai_analysis_block,
    last_smart as _fx_last_smart, _SIGNAL_SMART as _FX_SIGNAL_SMART,
    _KIND_TITLE, _KIND_TAGLINE, _is_pair_24_7, _is_weekend,
)
from keyboards import forex_signal_kb
from live_prices import (
    pip_size as live_pip_size, decimals as live_decimals, get_live_price,
)
from tz_utils import short_time_for_user

router = Router()


class FpState(StatesGroup):
    awaiting_pair = State()


def _is_admin(uid: int) -> bool:
    return int(uid) == int(db.get_admin_id())


def _account_label(size: int) -> str:
    for label, val in FP_ACCOUNT_SIZES:
        if val == size:
            return label
    return f"${size:,}"


def _tf_label(code: str) -> str:
    for label, c in FOREX_TIMEFRAMES:
        if c == code:
            return label
    return code or ""


def _summary_line(fp: dict) -> str:
    return (
        f"💼 Account: <b>{_account_label(int(fp['account_size']))}</b>  ·  "
        f"🎯 Target: <b>{fp['profit_pct']:.0f}%</b>  ·  "
        f"📉 Daily: <b>{fp['daily_loss_pct']:.0f}%</b>  ·  "
        f"🛑 DD: <b>{fp['max_dd_pct']:.0f}%</b>"
    )


# ── Step 0: open FUNDED PASS menu ─────────────────────────
@router.callback_query(F.data == "m:fp")
async def cb_fp_open(call: CallbackQuery, state: FSMContext):
    if not db.is_verified(call.from_user.id):
        await call.answer("Verify first via /start", show_alert=True); return
    if not _is_admin(call.from_user.id):
        acc = db.get_access(call.from_user.id) if db.has_active_access(call.from_user.id) else None
        allowed = False
        if acc:
            if acc.get("access_type") == "lifetime":
                allowed = True
            elif acc.get("access_type") == "temporary" and str(acc.get("package_id", "")).startswith("gz_"):
                allowed = True
        if not allowed:
            await call.answer()
            await show_screen(
                call.bot, call.message.chat.id,
                "🚨 <b>⚠️ ATTENTION ⚠️</b> 🚨\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "🔒 <b>You currently do <u>NOT</u> have access.</b>\n\n"
                "💎 To unlock <b>FUNDED PASS</b>, tap the "
                "<b>BUY FOREX TRADERS ACCESS</b> button below "
                "and pick your plan.\n\n"
                "✨ <i>One tap — straight to the price list.</i>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "🙏 <b>Thank you!</b>",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="💹 BUY FOREX TRADERS ACCESS",
                        callback_data="buy:forex")],
                    [InlineKeyboardButton(text="⬅️ BACK TO MENU",
                                          callback_data="m:home")],
                ]),
            )
            return
    await state.clear()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        "🏛 <b>FUNDED PASS — PROP-FIRM CHALLENGE</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🚀 Practice the rules of a real funded account before you take the "
        "live one. The bot streams sniper LIVE / LIMIT-ORDER signals "
        "tuned to your <b>profit target, daily loss &amp; drawdown</b> caps.\n\n"
        "<b>Step 1/5</b> — pick your <b>account size</b> 👇",
        fp_account_kb(),
    )


# ── Step 1: account size → profit target ──────────────────
@router.callback_query(F.data.startswith("fp:acc:"))
async def cb_fp_account(call: CallbackQuery, state: FSMContext):
    size = int(call.data.split(":")[2])
    await state.update_data(size=size)
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 Account: <b>{_account_label(size)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 2/5</b> — pick your <b>PROFIT TARGET</b> (%):",
        fp_profit_kb(),
    )


# ── Step 2: profit target → daily loss ────────────────────
@router.callback_query(F.data.startswith("fp:pt:"))
async def cb_fp_profit(call: CallbackQuery, state: FSMContext):
    pt = float(call.data.split(":")[2])
    await state.update_data(profit_pct=pt)
    data = await state.get_data()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 Account: <b>{_account_label(int(data['size']))}</b>  ·  "
        f"🎯 Target: <b>{pt:.0f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 3/5</b> — pick your <b>MAX DAILY LOSS</b> (%):",
        fp_daily_kb(),
    )


@router.callback_query(F.data == "fp:back_pt")
async def cb_fp_back_pt(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 Account: <b>{_account_label(int(data.get('size', 0)))}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 2/5</b> — pick your <b>PROFIT TARGET</b> (%):",
        fp_profit_kb(),
    )


# ── Step 3: daily loss → max drawdown ─────────────────────
@router.callback_query(F.data.startswith("fp:dl:"))
async def cb_fp_daily(call: CallbackQuery, state: FSMContext):
    dl = float(call.data.split(":")[2])
    await state.update_data(daily_pct=dl)
    data = await state.get_data()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 {_account_label(int(data['size']))}  ·  "
        f"🎯 {data['profit_pct']:.0f}%  ·  📉 {dl:.0f}%/day\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 4/5</b> — pick your <b>MAX OVERALL DRAWDOWN</b> (%):",
        fp_dd_kb(),
    )


@router.callback_query(F.data == "fp:back_dl")
async def cb_fp_back_dl(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 {_account_label(int(data.get('size', 0)))}  ·  "
        f"🎯 {data.get('profit_pct', 0):.0f}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 3/5</b> — pick your <b>MAX DAILY LOSS</b> (%):",
        fp_daily_kb(),
    )


# ── Step 4: drawdown → timeframe ──────────────────────────
@router.callback_query(F.data.startswith("fp:dd:"))
async def cb_fp_dd(call: CallbackQuery, state: FSMContext):
    dd = float(call.data.split(":")[2])
    await state.update_data(dd_pct=dd)
    data = await state.get_data()
    # Persist the rules now (pair/tf set on next step)
    db.upsert_funded_pass(
        user_id=call.from_user.id,
        account_size=int(data["size"]),
        profit_pct=float(data["profit_pct"]),
        daily_loss_pct=float(data["daily_pct"]),
        max_dd_pct=dd,
    )
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 {_account_label(int(data['size']))}  ·  "
        f"🎯 {data['profit_pct']:.0f}%  ·  📉 {data['daily_pct']:.0f}%/day  ·  "
        f"🛑 {dd:.0f}% DD\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 5/5</b> — choose a <b>TIMEFRAME</b>:",
        fp_tf_kb(),
    )


@router.callback_query(F.data == "fp:back_dd")
async def cb_fp_back_dd(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 {_account_label(int(data.get('size', 0)))}  ·  "
        f"🎯 {data.get('profit_pct', 0):.0f}%  ·  "
        f"📉 {data.get('daily_pct', 0):.0f}%/day\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 4/5</b> — pick your <b>MAX OVERALL DRAWDOWN</b> (%):",
        fp_dd_kb(),
    )


# ── Step 5: TF → numbered pair input ──────────────────────
@router.callback_query(F.data.startswith("fp:tf:"))
async def cb_fp_tf(call: CallbackQuery, state: FSMContext):
    tf = call.data.split(":")[2]
    await state.update_data(tf=tf)
    await state.set_state(FpState.awaiting_pair)
    await call.answer()
    weekend_note = ""
    if _is_weekend():
        weekend_note = (
            "\n━━━━━━━━━━━━━━━━━━━\n"
            "🛑 <b>Weekend mode:</b> real forex / metals / oil / indices "
            "are <b>closed</b>. Pick a crypto pair (BTC, ETH, SOL, *USDT)."
        )
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"⏱️ Timeframe: <b>{_tf_label(tf)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Step 5/5 — pick ONE pair</b> (reply with the number):\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{forex_pairs_text()}"
        f"{weekend_note}",
        fp_pairs_input_kb(),
    )


@router.callback_query(F.data == "fp:back_tf")
async def cb_fp_back_tf(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await call.answer()
    await show_screen(
        call.bot, call.message.chat.id,
        f"🏛 <b>FUNDED PASS</b>\n"
        f"💼 {_account_label(int(data.get('size', 0)))}  ·  "
        f"🎯 {data.get('profit_pct', 0):.0f}%  ·  "
        f"📉 {data.get('daily_pct', 0):.0f}%/day  ·  "
        f"🛑 {data.get('dd_pct', 0):.0f}% DD\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Step 5/5</b> — choose a <b>TIMEFRAME</b>:",
        fp_tf_kb(),
    )


@router.message(FpState.awaiting_pair)
async def msg_fp_pair(message: Message, state: FSMContext, bot: Bot):
    await safe_delete(bot, message.chat.id, message.message_id)
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await show_screen(
            bot, message.chat.id,
            "❌ Please reply with a single number from the list above.",
            fp_pairs_input_kb(),
        )
        return
    n = int(raw)
    if not (1 <= n <= len(FOREX_PAIRS)):
        await show_screen(
            bot, message.chat.id,
            f"❌ Pick a number between <b>1</b> and <b>{len(FOREX_PAIRS)}</b>.",
            fp_pairs_input_kb(),
        )
        return
    pair = FOREX_PAIRS[n - 1]

    # Weekend gate
    if _is_weekend() and not _is_pair_24_7(pair):
        await show_screen(
            bot, message.chat.id,
            f"🛑 <b>Weekend — {pair} is closed.</b>\n"
            "Pick a crypto pair (BTC, ETH, SOL, *USDT) instead.",
            fp_pairs_input_kb(),
        )
        return

    data = await state.get_data()
    tf = data.get("tf", "")
    db.set_funded_pass_market(message.from_user.id, tf, pair)
    await state.clear()

    fp = db.get_funded_pass(message.from_user.id)
    text = (
        "🏛 <b>FUNDED PASS — CHALLENGE ACTIVE</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"{_summary_line(fp)}\n"
        f"📊 Pair: <b>{pair}</b>  ·  ⏱️ TF: <b>{_tf_label(tf)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📡 Bot will stream <b>LIVE NOW</b> + <b>LIMIT-ORDER</b> signals "
        f"sized to your <b>2% SL rule</b>.\n"
        f"🎯 Hit <b>{int(fp['profit_pct'])}%</b> total profit and you "
        f"<b>PASS</b> the challenge.\n"
        f"🛑 Lose <b>{int(fp['max_dd_pct'])}%</b> total or "
        f"<b>{int(fp['daily_loss_pct'])}%</b> in one day and the "
        f"challenge ends.\n\n"
        f"🛑 Tap <b>STOP CHALLENGE</b> any time."
    )
    await show_screen(bot, message.chat.id, text, fp_active_kb())


# ── STOP ──────────────────────────────────────────────────
@router.callback_query(F.data == "fp:stop")
async def cb_fp_stop(call: CallbackQuery):
    db.set_funded_pass_status(call.from_user.id, "failed")
    await call.answer("Challenge stopped 🛑")
    await show_screen(
        call.bot, call.message.chat.id,
        "🛑 <b>Funded Pass challenge stopped.</b>\n"
        "Tap <b>FUNDED PASS</b> from the home menu to start a new one.",
        fp_finished_kb(passed=False),
    )


# ── ENGINE ────────────────────────────────────────────────
FP_THROTTLE_SEC = (180, 360)        # 3-6 min between signals
FP_FIRST_DELAY_SEC = (45, 90)


def _signals_to_pass(profit_pct: float) -> int:
    """How many winning signals it takes (on average) to PASS the challenge.
    Each TP win returns roughly profit_pct / N. We target ~5 wins for a
    realistic, satisfying pace."""
    return 5


def _per_win_pct(fp: dict) -> float:
    return float(fp["profit_pct"]) / max(1, _signals_to_pass(fp["profit_pct"]))


async def _send_fp_signal(bot: Bot, fp: dict):
    user_id = fp["user_id"]
    pair = fp["pair"]
    tf_label = _tf_label(fp["tf"] or "")

    # Generate levels using the live chart bias just like normal forex
    direction, entry, tps, sl, dec, _pat = _generate_levels(pair, max_tp=1)
    pip = live_pip_size(pair)

    # Funded-pass SL clamp — standard forex only: 15–25 pips hard cap.
    # Metals / crypto / indices retain their ATR-scaled SL from _generate_levels.
    _fp_is_exotic = any(x in pair.upper() for x in (
        "XAU", "XAG", "BTC", "ETH", "SOL", "BNB", "LTC", "XRP",
        "NDX", "DJI", "NAS", "SPX", "US30", "US500",
    ))
    if not _fp_is_exotic:
        _sl_dist = abs(entry - sl)
        _sl_dist = max(15 * pip, min(_sl_dist, 25 * pip))
        sl = (entry - _sl_dist) if direction == "BUY" else (entry + _sl_dist)

    # Funded-pass: 96% LIVE NOW (sniper entry — zero pip slippage),
    # 4% LIMIT (zone entry). This ensures near-zero pip drop on entry.
    kind = "LIMIT" if random.random() < 0.04 else "LIVE"
    if kind == "LIMIT":
        entry = _shift_for_limit(direction, entry, pip)
        tps = [entry + 30 * pip] if direction == "BUY" else [entry - 30 * pip]
        sl = entry - 25 * pip if direction == "BUY" else entry + 25 * pip

    sig_id = db.create_forex_signal(
        user_id=user_id, chat_id=user_id, pair=pair, direction=direction,
        entry=entry, tp_prices=tps, sl_price=sl, max_tp=1, kind=kind,
    )
    smart_pkt = _fx_last_smart(pair)
    if smart_pkt is not None:
        _FX_SIGNAL_SMART[sig_id] = smart_pkt
    text = _signal_text(
        pair, direction, tps, sl, 1, dec, 0, None,
        entry=entry, signal_time=short_time_for_user(user_id),
        tf_label=tf_label, pip=pip, kind=kind, smart=smart_pkt,
    )
    text = (
        "🏛 <b>FUNDED PASS SIGNAL</b>  ·  2% RISK\n"
        + text
    )
    try:
        msg = await bot.send_message(
            chat_id=user_id, text=text, parse_mode="HTML",
            reply_markup=forex_signal_kb(sig_id, kind=kind),
        )
        db.set_forex_signal_msg(sig_id, msg.message_id)
        db.touch_funded_pass(user_id)
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        print(f"[funded_pass] cannot DM {user_id}: {e}")
        return

    # Track outcome in background — apply +TP% / -2% to running equity
    asyncio.create_task(_track_and_apply(bot, sig_id, fp))


async def _track_and_apply(bot: Bot, signal_id: int, fp: dict):
    """Wait for the signal to close, then apply +per_win or -2% to equity.
    Triggers the PASS / FAIL endgame messages when caps are reached."""
    user_id = fp["user_id"]
    win_pct = _per_win_pct(fp)
    # Poll the signal row until it closes (real-price tracker handles edits)
    while True:
        await asyncio.sleep(15)
        sig = db.get_forex_signal(signal_id)
        if not sig:
            return
        if sig.get("status") == "closed":
            outcome = sig.get("outcome") or "sl"
            break

    if outcome == "tp":
        delta = +win_pct
    elif outcome == "partial":
        delta = +win_pct * 0.5
    else:
        delta = -2.0  # the user's spec: each SL = -2% of account

    db.apply_funded_pass_pl(user_id, delta)
    fp_now = db.get_funded_pass(user_id)
    if not fp_now or fp_now.get("status") != "active":
        return

    eq = float(fp_now.get("equity_pct") or 0)
    daily = float(fp_now.get("daily_pct") or 0)
    target = float(fp_now["profit_pct"])
    max_dd = float(fp_now["max_dd_pct"])
    daily_cap = float(fp_now["daily_loss_pct"])

    # PASS
    if eq >= target:
        db.set_funded_pass_status(user_id, "passed")
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "🎉 <b>CONGRATULATIONS — FUNDED PASS CLEARED!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 Account: <b>{_account_label(int(fp_now['account_size']))}</b>\n"
                    f"📈 Final equity: <b>+{eq:.2f}%</b>  "
                    f"(target {int(target)}%)\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🚀 You've proven the rules. Now switch to "
                    f"<b>FOREX TRADERS</b> and trade live with the bot's "
                    f"full SUPREME PRO setups."
                ),
                parse_mode="HTML",
                reply_markup=fp_finished_kb(passed=True),
            )
        except Exception:
            pass
        return

    # FAIL — overall drawdown
    if eq <= -max_dd:
        db.set_funded_pass_status(user_id, "failed")
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "🛑 <b>FUNDED PASS — CHALLENGE ENDED</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"📉 Final equity: <b>{eq:.2f}%</b>  "
                    f"(max DD {int(max_dd)}%)\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    "🤝 <i>Part of trading. Better luck next time — market "
                    "conditions and volatility shifted against the plan. "
                    "We tried our best with the analysis. "
                    "Tap TRY AGAIN to take a fresh challenge.</i>"
                ),
                parse_mode="HTML",
                reply_markup=fp_finished_kb(passed=False),
            )
        except Exception:
            pass
        return

    # FAIL — daily loss cap
    if daily <= -daily_cap:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⚠️ <b>DAILY LOSS LIMIT REACHED</b>\n"
                    f"📉 Today: <b>{daily:.2f}%</b>  "
                    f"(cap {int(daily_cap)}%)\n"
                    "🌙 No more signals today — challenge resumes tomorrow."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


async def run_funded_pass_loop(bot: Bot):
    """Background scan: drives funded-pass signal generation."""
    while True:
        try:
            for fp in db.list_active_funded_passes():
                user_id = fp["user_id"]
                # Daily-loss pause
                if fp.get("day") == db.today_str():
                    if float(fp.get("daily_pct") or 0) <= -float(fp["daily_loss_pct"]):
                        continue

                last = fp.get("last_signal_at")
                now = datetime.utcnow()
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last)
                    except Exception:
                        last_dt = now
                    if (now - last_dt).total_seconds() < random.randint(*FP_THROTTLE_SEC):
                        continue
                else:
                    try:
                        created = datetime.fromisoformat(fp["created_at"])
                    except Exception:
                        created = now
                    if (now - created).total_seconds() < random.randint(*FP_FIRST_DELAY_SEC):
                        continue

                # Cap: max 2 OPEN signals on the funded-pass pair at once
                if db.count_open_forex_signals_for_pair(user_id, fp["pair"]) >= 2:
                    continue

                await _send_fp_signal(bot, fp)
        except Exception as e:
            print(f"[funded_pass] loop error: {e}")
        await asyncio.sleep(30)
