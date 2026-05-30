"""Auto Trading dashboard handler.

Free users  → demo gate (3 total trades, 1/day, reset after 30 days)
             → demo-only trade history
Premium     → full dashboard: account info, features, risk, broker,
               analytics, TODAY TRADE HISTORY, warnings
"""
import asyncio
import random
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery

import database as db
from chat_clean import show_screen

from keyboards import (
    at_gate_kb,
    at_demo_menu_kb,
    at_demo_run_kb,
    at_demo_locked_kb,
    at_dashboard_kb,
    at_back_dashboard_kb,
    at_history_menu_kb,
    at_history_back_kb,
    at_controls_kb,
    at_hist_filter_kb,
    at_drawdown_confirm_kb,
)

router = Router()

_DEMO_PAIRS_FOREX  = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
                       "USD/CAD", "EUR/GBP", "GBP/JPY", "XAU/USD"]
_DEMO_PAIRS_BINARY = ["EUR/USD (OTC)", "GBP/JPY (OTC)", "AUD/CAD (OTC)",
                       "USD/CHF (OTC)", "EUR/GBP (OTC)", "Bitcoin (OTC)"]


def _is_admin(uid: int) -> bool:
    return int(uid) == int(db.get_admin_id())


def _has_access(uid: int) -> bool:
    return _is_admin(uid) or db.has_active_access(uid)


# ── Helpers ────────────────────────────────────────────────

def _close_reason_label(r: str) -> str:
    return {
        "sl_hit":        "SL HIT",
        "tp_hit":        "TP HIT",
        "manual":        "MANUAL CLOSE",
        "bot_close":     "BOT CLOSE",
        "timeout":       "TIMEOUT",
        "broker_close":  "BROKER CLOSE",
        "pending_verify": "PENDING VERIFY",
    }.get(r, r.upper())


def _result_badge(r: str) -> str:
    return {
        "profit":    "🟢 PROFIT",
        "win":       "🟢 WIN",
        "loss":      "🔴 LOSS",
        "breakeven": "🟡 BREAK-EVEN",
        "refund":    "🟡 REFUND",
        "pending":   "⏳ PENDING",
    }.get(r, f"❓ {r.upper()}")


def _dir_label(d: str) -> str:
    d = d.upper()
    if d in ("BUY", "CALL"):
        return "▲ BUY" if d == "BUY" else "▲ CALL"
    return "▼ SELL" if d == "SELL" else "▼ PUT"


# ── Entry point ────────────────────────────────────────────

@router.callback_query(F.data == "at:open")
async def at_open(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if _has_access(uid):
        await _show_premium_dashboard(cq.bot, uid)
    else:
        text = (
            "🤖 <b>AUTO TRADING</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Welcome to the <b>FX Shakib Sheikh Auto Trading</b> system.\n\n"
            "🔒 <b>Full access is locked for free users.</b>\n\n"
            "✅ <b>What you get with full access:</b>\n"
            "  • Live auto trade execution\n"
            "  • Premium AI signals (Forex & Binary)\n"
            "  • Smart money management\n"
            "  • Daily drawdown protection\n"
            "  • Loss protection engine\n"
            "  • Broker connection module\n"
            "  • Analytics & self-improvement\n"
            "  • Today trade history\n\n"
            "🎮 <b>Try it free:</b> 3 demo trades available\n"
            "  (Max 1 per day • Resets after 30 days)\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <i>Auto trading involves risk. Demo results do not\n"
            "guarantee live performance.</i>"
        )
        await show_screen(cq.bot, uid, text, at_gate_kb())


# ── Demo flow ──────────────────────────────────────────────

@router.callback_query(F.data == "at:demo_menu")
async def at_demo_menu(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    state = db.get_at_demo_state(uid)
    can, reason = db.can_at_demo_trade(uid)
    remaining = max(0, db._AT_DEMO_MAX_TOTAL - state["total_used"])

    if not can:
        await _show_demo_locked(cq.bot, uid, reason=reason)
        return

    text = (
        "🎮 <b>DEMO AUTO TRADING</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 Demo trades remaining: <b>{remaining} / 3</b>\n"
        f"📅 Used today: <b>{state['today_used']} / 1</b>\n\n"
        "Choose your market to simulate an auto trade:\n\n"
        "💹 <b>FOREX Demo</b> — Currency pairs, auto entry simulation\n"
        "📊 <b>BINARY Demo</b> — OTC / Live binary signal simulation\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Demo trades use simulated data only.\n"
        "No real funds are involved.</i>"
    )
    await show_screen(cq.bot, uid, text, at_demo_menu_kb())


@router.callback_query(F.data.startswith("at:demo:"))
async def at_demo_pick(cq: CallbackQuery):
    uid = cq.from_user.id
    mode = cq.data.split(":")[-1]
    await cq.answer()
    can, reason = db.can_at_demo_trade(uid)
    if not can:
        await _show_demo_locked(cq.bot, uid, reason=reason)
        return
    label = "FOREX" if mode == "forex" else "BINARY"
    pair = random.choice(_DEMO_PAIRS_FOREX if mode == "forex" else _DEMO_PAIRS_BINARY)
    text = (
        f"🎮 <b>DEMO {label} AUTO TRADE</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Selected pair: <b>{pair}</b>\n"
        f"🔧 Mode: <b>Demo / Simulated</b>\n\n"
        "The system will:\n"
        "  1️⃣ Scan market structure\n"
        "  2️⃣ Detect entry signal\n"
        "  3️⃣ Apply risk rules\n"
        "  4️⃣ Simulate auto entry\n\n"
        "Tap <b>RUN DEMO AUTO TRADE</b> to simulate.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Simulated result only — no real trade executed.</i>"
    )
    await show_screen(cq.bot, uid, text, at_demo_run_kb(mode))


@router.callback_query(F.data.startswith("at:demo_run:"))
async def at_demo_run(cq: CallbackQuery):
    uid = cq.from_user.id
    mode = cq.data.split(":")[-1]
    await cq.answer()
    can, reason = db.can_at_demo_trade(uid)
    if not can:
        await _show_demo_locked(cq.bot, uid, reason=reason)
        return

    label = "FOREX" if mode == "forex" else "BINARY"
    pair  = random.choice(_DEMO_PAIRS_FOREX if mode == "forex" else _DEMO_PAIRS_BINARY)

    await show_screen(
        cq.bot, uid,
        f"⏳ <b>Scanning {pair} …</b>\nAnalyzing market structure, please wait…",
        None,
    )
    open_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    await asyncio.sleep(random.uniform(2.5, 4.0))

    direction  = random.choice(["BUY", "SELL"]) if mode == "forex" else random.choice(["CALL", "PUT"])
    confidence = random.randint(72, 94)
    now_str    = datetime.utcnow().strftime("%H:%M UTC")

    if mode == "forex":
        entry  = round(random.uniform(1.05, 1.35), 5)
        sl_pip = random.randint(20, 55)
        tp_pip = random.randint(40, 110)
        lot    = round(random.choice([0.01, 0.02, 0.03, 0.05, 0.08, 0.10]), 2)
        pip    = 0.0001
        sl_p   = round(entry - sl_pip * pip if direction == "BUY" else entry + sl_pip * pip, 5)
        tp_p   = round(entry + tp_pip * pip if direction == "BUY" else entry - tp_pip * pip, 5)
        outcome_raw = random.choices(["profit", "loss"], weights=[68, 32])[0]
        pl = round(lot * tp_pip * 10 if outcome_raw == "profit" else -(lot * sl_pip * 10), 2)
        exit_p = tp_p if outcome_raw == "profit" else sl_p
        close_reason = "tp_hit" if outcome_raw == "profit" else "sl_hit"
        outcome_badge = _result_badge(outcome_raw)
        result_block = (
            f"📌 Pair: <b>{pair}</b>\n"
            f"📈 Direction: <b>{'▲ BUY' if direction == 'BUY' else '▼ SELL'}</b>\n"
            f"📦 Lot Size: <b>{lot}</b>\n"
            f"🎯 Confidence: <b>{confidence}%</b>\n"
            f"⚡ Entry: <b>{entry:.5f}</b>   Exit: <b>{exit_p:.5f}</b>\n"
            f"🛡️ SL: <b>{sl_p:.5f}</b>   TP: <b>{tp_p:.5f}</b>\n"
            f"💰 P/L: <b>{'+ $' if pl >= 0 else '- $'}{abs(pl):.2f}</b>\n"
            f"🚪 Close: <b>{_close_reason_label(close_reason)}</b>\n"
            f"⏱️ Time: <b>{now_str}</b>\n"
            f"🏁 Result: <b>{outcome_badge}</b>"
        )
        duration = round(random.uniform(3.0, 45.0), 1)
        db.at_add_trade(
            user_id=uid, trade_type="demo_forex", pair=pair, direction=direction,
            lot_size=lot, entry_price=entry, exit_price=exit_p,
            sl_price=sl_p, tp_price=tp_p, profit_loss=pl,
            result=outcome_raw, close_reason=close_reason,
            open_time=open_time, is_demo=True, is_auto=True, broker_confirmed=False,
            strategy_name="SMC-Demo", duration_mins=duration,
            balance_before=1000.0, balance_after=round(1000.0 + pl, 2),
            drawdown_impact=round(abs(pl) / 1000 * 100, 2) if pl < 0 else 0.0,
            risk_pct=1.0,
        )
    else:
        amount = random.choice([10, 15, 20, 25])
        payout = round(amount * 0.85, 2)
        expiry = random.choice(["1 min", "2 min", "3 min", "5 min"])
        outcome_raw = random.choices(["win", "loss"], weights=[68, 32])[0]
        close_reason = "tp_hit" if outcome_raw == "win" else "sl_hit"
        outcome_badge = _result_badge(outcome_raw)
        result_block = (
            f"📌 Asset: <b>{pair}</b>\n"
            f"📈 Direction: <b>{'▲ CALL' if direction == 'CALL' else '▼ PUT'}</b>\n"
            f"💵 Amount: <b>${amount}</b>\n"
            f"⏱️ Expiry: <b>{expiry}</b>\n"
            f"🎯 Confidence: <b>{confidence}%</b>\n"
            f"💰 Payout: <b>{'+ $' + str(payout) if outcome_raw == 'win' else '- $' + str(amount)}</b>\n"
            f"🕐 Time: <b>{now_str}</b>\n"
            f"🏁 Result: <b>{outcome_badge}</b>"
        )
        bin_pl = payout if outcome_raw == "win" else -amount
        db.at_add_trade(
            user_id=uid, trade_type="demo_binary", pair=pair, direction=direction,
            amount=amount, payout=(payout if outcome_raw == "win" else 0),
            profit_loss=bin_pl,
            result=outcome_raw, close_reason=close_reason,
            open_time=open_time, is_demo=True, is_auto=True, broker_confirmed=False,
            strategy_name="BIN-Demo", duration_mins=round(random.uniform(1.0, 5.0), 1),
            balance_before=500.0, balance_after=round(500.0 + bin_pl, 2),
            drawdown_impact=round(abs(bin_pl) / 500 * 100, 2) if bin_pl < 0 else 0.0,
            risk_pct=round(amount / 500 * 100, 1),
        )

    db.record_at_demo_trade(uid)
    state     = db.get_at_demo_state(uid)
    remaining = max(0, db._AT_DEMO_MAX_TOTAL - state["total_used"])

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from config import SUPPORT_USERNAME
    hist_tab = "demo_forex" if mode == "forex" else "demo_binary"
    if remaining > 0:
        after_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 RUN ANOTHER DEMO",         callback_data="at:demo_menu")],
            [InlineKeyboardButton(text="📅 VIEW DEMO HISTORY",        callback_data=f"at:hist:{hist_tab}")],
            [InlineKeyboardButton(text="💎 UNLOCK FULL ACCESS",       callback_data="m:buy")],
            [InlineKeyboardButton(text="🏠 Back to Menu",              callback_data="m:home")],
        ])
    else:
        after_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 VIEW DEMO HISTORY",        callback_data=f"at:hist:{hist_tab}")],
            [InlineKeyboardButton(text="💎 GET FULL ACCESS NOW",      callback_data="m:buy")],
            [InlineKeyboardButton(text="💬 CONTACT SUPPORT",
                                  url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
            [InlineKeyboardButton(text="🏠 Back to Menu",              callback_data="m:home")],
        ])

    text = (
        f"🤖 <b>DEMO {label} AUTO TRADE — RESULT</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"{result_block}\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 Demo trades left: <b>{remaining} / 3</b>\n\n"
        "⚠️ <i>Simulated trade only. No real funds used.\n"
        "Demo results do not reflect live performance.</i>"
    )
    if remaining == 0:
        text += "\n\n🔒 <b>All demo trades used.</b> Upgrade to continue."
    await show_screen(cq.bot, uid, text, after_kb)


async def _show_demo_locked(bot, uid: int, reason: str):
    if reason == "daily_limit":
        msg = (
            "⏰ <b>Daily demo limit reached.</b>\n\n"
            "You can run <b>1 demo auto trade per day</b>.\n"
            "Come back tomorrow, or unlock full access."
        )
    else:
        msg = (
            "🔒 <b>All demo trades exhausted.</b>\n\n"
            "You've used all <b>3 demo auto trades</b>.\n"
            "Demo resets after <b>30 days</b>.\n\n"
            "Upgrade to <b>full access</b> for unlimited live trading."
        )
    text = (
        "🤖 <b>AUTO TRADING — DEMO LOCKED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"{msg}\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "💎 <b>Full Access includes:</b>\n"
        "  • Unlimited live auto trades\n"
        "  • AI signal engine (Forex + Binary)\n"
        "  • Smart risk management\n"
        "  • Drawdown protection\n"
        "  • Today trade history"
    )
    await show_screen(bot, uid, text, at_demo_locked_kb())


# ── Premium dashboard ──────────────────────────────────────

async def _show_premium_dashboard(bot, uid: int):
    s   = db.get_at_settings(uid)
    acc = db.get_access(uid)

    auto_status  = "🟢 ON"  if s.get("auto_trading_on") else "🔴 OFF"
    broker_status = "🟢 Connected" if s.get("broker_connected") else "🔴 Not connected"
    risk_label   = {"low": "🟢 Low", "moderate": "🟡 Moderate",
                    "high": "🔴 High"}.get(s.get("risk_mode", "moderate"), "🟡 Moderate")

    flags = []
    if s.get("review_required"):
        flags.append("🔍 <b>REVIEW REQUIRED</b> — confirm before resuming")
    if s.get("strategy_paused"):
        flags.append("⏸️ <b>STRATEGY PAUSED</b> — loss protection active")
    flag_block = ("\n".join(flags) + "\n\n") if flags else ""

    acc_type   = "Lifetime" if (acc and acc.get("access_type") == "lifetime") else "Monthly"
    expiry_str = ""
    if acc and acc.get("expires_at"):
        expiry_str = f"\n⏳ Expires: <b>{acc['expires_at'][:10]}</b>"

    # Quick today summary
    fx_sum  = db.at_get_today_summary(uid, "forex")
    bin_sum = db.at_get_today_summary(uid, "binary")
    today_block = ""
    if fx_sum["total"] > 0 or bin_sum["total"] > 0:
        today_block = (
            "─────────────────────\n"
            "📅 <b>TODAY SNAPSHOT</b>\n"
        )
        if fx_sum["total"] > 0:
            today_block += (
                f"  💹 Forex: {fx_sum['total']} trades · "
                f"{fx_sum['profit']}✅ {fx_sum['loss']}❌ · "
                f"Net <b>{'+'if fx_sum['net_pl']>=0 else ''}"
                f"${fx_sum['net_pl']:.2f}</b>\n"
            )
        if bin_sum["total"] > 0:
            today_block += (
                f"  📊 Binary: {bin_sum['total']} trades · "
                f"WR <b>{bin_sum['win_rate']}%</b> "
                f"({bin_sum['profit']}W/{bin_sum['loss']}L)\n"
            )
        today_block += "\n"

    text = (
        "🤖 <b>AUTO TRADING DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"{flag_block}"
        f"⚙️ Auto Trading: <b>{auto_status}</b>\n"
        f"🔌 Broker:       <b>{broker_status}</b>\n"
        f"🛡️ Risk Mode:    <b>{risk_label}</b>\n"
        f"💳 Access:       <b>{acc_type}</b>{expiry_str}\n\n"
        f"{today_block}"
        "Select a section below 👇\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Auto trading involves significant risk.\n"
        "No profit is guaranteed.</i>"
    )
    await show_screen(bot, uid, text, at_dashboard_kb())


@router.callback_query(F.data == "at:dashboard")
async def at_dashboard(cq: CallbackQuery):
    await cq.answer()
    if not _has_access(cq.from_user.id):
        await at_open(cq)
        return
    await _show_premium_dashboard(cq.bot, cq.from_user.id)


# ── Account Info ───────────────────────────────────────────

@router.callback_query(F.data == "at:account")
async def at_account(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    s    = db.get_at_settings(uid)
    acc  = db.get_access(uid)
    user = db.get_user(uid)
    username   = (f"@{user['username']}" if user and user.get("username")
                  else (user.get("full_name", "Trader") if user else "Trader"))
    acc_type   = "Lifetime" if (acc and acc.get("access_type") == "lifetime") else "Monthly"
    pkg_label  = (acc.get("package_label") or "—") if acc else "—"
    expiry_str = acc["expires_at"][:10] if (acc and acc.get("expires_at")) else "N/A (Lifetime)"
    auto_status  = "🟢 ON" if s.get("auto_trading_on") else "🔴 OFF"
    broker_status = "🟢 Connected" if s.get("broker_connected") else "🔴 Not connected"
    broker_name  = s.get("broker_name") or "—"
    risk_label   = {"low": "🟢 Low", "moderate": "🟡 Moderate",
                    "high": "🔴 High"}.get(s.get("risk_mode", "moderate"), "🟡 Moderate")
    text = (
        "📋 <b>ACCOUNT INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Username:         <b>{username}</b>\n"
        f"🔑 Account Type:     <b>Live</b>\n"
        f"💳 Subscription:     <b>{acc_type}</b>\n"
        f"📦 Package:          <b>{pkg_label}</b>\n"
        f"⏳ Expiry:           <b>{expiry_str}</b>\n\n"
        "─────────────────────\n"
        f"✅ Forex Access:     <b>{'ON' if acc else 'OFF'}</b>\n"
        f"✅ Binary Access:    <b>{'ON' if acc else 'OFF'}</b>\n\n"
        "─────────────────────\n"
        f"⚙️ Auto Trading:     <b>{auto_status}</b>\n"
        f"🔌 Broker:           <b>{broker_status}</b>\n"
        f"🏦 Broker Name:      <b>{broker_name}</b>\n"
        f"🛡️ Risk Mode:        <b>{risk_label}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Contact support to update broker details.</i>"
    )
    await show_screen(cq.bot, uid, text, at_back_dashboard_kb())


# ── Available Features ─────────────────────────────────────

@router.callback_query(F.data == "at:features")
async def at_features(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    text = (
        "⚡ <b>AVAILABLE FEATURES</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>Live Trading Mode</b>\n"
        "   Real-time signal execution on live market\n\n"
        "✅ <b>Auto Trading Engine</b>\n"
        "   Automated entry/exit based on AI signals\n\n"
        "✅ <b>Premium AI Signals</b>\n"
        "   Forex + Binary — high-confluence setups only\n\n"
        "✅ <b>Smart Money Management</b>\n"
        "   Position sizing, risk-per-trade limits\n\n"
        "✅ <b>Daily Drawdown Protection</b>\n"
        "   Auto-stop when daily loss threshold is hit\n\n"
        "✅ <b>Loss Protection Engine</b>\n"
        "   7-of-10 rule: reduces risk on losing streak\n\n"
        "✅ <b>Today Trade History</b>\n"
        "   Full trade log per session with summary bar\n\n"
        "✅ <b>Analytics & Self-Improvement</b>\n"
        "   Strategy scoring updates based on trade history\n\n"
        "✅ <b>Broker Connection Module</b>\n"
        "   Connect/disconnect broker, retry on error\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Some features require broker connection to activate.</i>"
    )
    await show_screen(cq.bot, uid, text, at_back_dashboard_kb())


# ── Risk & Drawdown ────────────────────────────────────────

@router.callback_query(F.data == "at:risk")
async def at_risk(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    s          = db.get_at_settings(uid)
    risk_label = {"low": "🟢 Low", "moderate": "🟡 Moderate",
                  "high": "🔴 High"}.get(s.get("risk_mode", "moderate"), "🟡 Moderate")
    dd_thresh  = s.get("drawdown_threshold", 5.0)
    loss_days  = s.get("loss_day_count", 0)
    review     = s.get("review_required", 0)
    paused     = s.get("strategy_paused", 0)
    loss_bar   = "🟥" * min(loss_days, 10) + "⬜" * (10 - min(loss_days, 10))
    status_flags = []
    if review:
        status_flags.append("🔍 <b>REVIEW REQUIRED</b> — manual confirm needed")
    if paused:
        status_flags.append("⏸️ <b>STRATEGY PAUSED</b> — aggressive mode disabled")
    if not status_flags:
        status_flags.append("🟢 All systems normal")
    text = (
        "🛡️ <b>RISK & DRAWDOWN PROTECTION</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚖️ Risk Mode:             <b>{risk_label}</b>\n"
        f"📉 Daily Drawdown Limit:  <b>{dd_thresh:.1f}%</b>\n\n"
        "─────────────────────\n"
        "<b>📊 LOSS PROTECTION ENGINE</b>\n"
        f"   Loss days (10-day window): <b>{loss_days} / 10</b>\n"
        f"   {loss_bar}\n\n"
        "   Rule: If <b>7 of 10 trading days</b> are loss days:\n"
        "   • Risk mode reduced\n"
        "   • Aggressive strategy paused\n"
        "   • Analysis re-checked\n"
        "   • Manual review required\n\n"
        "─────────────────────\n"
        "<b>📌 DRAWDOWN STOP RULE</b>\n"
        f"   If daily loss hits <b>{dd_thresh:.1f}%</b>, trading stops.\n"
        "   Manual permission required to continue.\n\n"
        "─────────────────────\n"
        "<b>⚠️ CURRENT STATUS</b>\n"
        + "\n".join(f"   {f}" for f in status_flags) + "\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Contact support to adjust risk thresholds.</i>"
    )
    await show_screen(cq.bot, uid, text, at_back_dashboard_kb())


# ── Broker Connection ──────────────────────────────────────

@router.callback_query(F.data == "at:broker")
async def at_broker(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    s           = db.get_at_settings(uid)
    connected   = s.get("broker_connected", 0)
    broker_name = s.get("broker_name") or "Not configured"
    status_icon = "🟢" if connected else "🔴"
    status_text = "Connected" if connected else "Disconnected"
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    broker_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔄 RETRY CONNECTION" if not connected else "🔌 DISCONNECT",
            callback_data="at:broker_toggle",
        )],
        [InlineKeyboardButton(text="⬅️ BACK TO DASHBOARD", callback_data="at:dashboard")],
        [InlineKeyboardButton(text="🏠 Menu",               callback_data="m:home")],
    ])
    text = (
        "🔌 <b>BROKER CONNECTION MODULE</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"{status_icon} Status:        <b>{status_text}</b>\n"
        f"🏦 Broker:        <b>{broker_name}</b>\n\n"
        "─────────────────────\n"
        "<b>Supported brokers (modular):</b>\n"
        "  • Pocket Option\n"
        "  • Quotex\n"
        "  • MetaTrader 4 / 5\n"
        "  • Custom broker API\n\n"
        "─────────────────────\n"
        "<b>Connection rules:</b>\n"
        "  • Auto-retry on connection drop\n"
        "  • Error logs captured\n"
        "  • No unsafe auto-execution\n"
        "  • Manual approval required for live orders\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Contact support to set up your broker connection.\n"
        "Never share your broker password with anyone.</i>"
    )
    await show_screen(cq.bot, uid, text, broker_kb)


@router.callback_query(F.data == "at:broker_toggle")
async def at_broker_toggle(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer("Updating broker status…")
    if not _has_access(uid):
        return
    s         = db.get_at_settings(uid)
    new_state = 0 if s.get("broker_connected") else 1
    db.update_at_settings(uid, broker_connected=new_state)
    await at_broker(cq)


# ── Analytics ─────────────────────────────────────────────

@router.callback_query(F.data == "at:analytics")
async def at_analytics(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    try:
        recent = db.winrate_stats_by_market(days=2)
        rows   = []
        for mkt, v in recent.items():
            rows.append(
                f"  📌 {mkt}: <b>{v['win_rate']}%</b> "
                f"({v['wins']}W / {v['losses']}L / {v['total']} total)"
            )
        wr_block = "\n".join(rows) if rows else "  No recent trade data."
    except Exception:
        wr_block = "  Analytics data unavailable."
    text = (
        "📊 <b>ANALYTICS & SELF-IMPROVEMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>📈 Recent Win Rate (last 48 hrs):</b>\n"
        f"{wr_block}\n\n"
        "─────────────────────\n"
        "<b>🔄 How the system self-improves:</b>\n"
        "  • Tracks each signal outcome (win/loss)\n"
        "  • Re-scores strategy confidence per pair\n"
        "  • Reduces weight on underperforming setups\n"
        "  • Flags pairs with repeated losses for review\n"
        "  • Recommends adjusted entry filters\n\n"
        "─────────────────────\n"
        "⚠️ <b>Important notice:</b>\n"
        "<i>This is analytics-based tuning, not AI self-learning.\n"
        "Past performance does not guarantee future results.</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━"
    )
    await show_screen(cq.bot, uid, text, at_back_dashboard_kb())


# ── Warnings ───────────────────────────────────────────────

@router.callback_query(F.data == "at:warnings")
async def at_warnings(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    text = (
        "⚠️ <b>RISK WARNINGS & DISCLAIMERS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "📢 <b>Please read carefully before using Auto Trading:</b>\n\n"
        "⚠️ Win rate depends on market conditions.\n\n"
        "⚠️ No guaranteed profit.\n"
        "   All trading involves risk of capital loss.\n\n"
        "⚠️ Risk only money you can afford to lose.\n\n"
        "⚠️ Live trading has higher risk than demo.\n\n"
        "⚠️ Loss protection rule:\n"
        "   If losses occur in at least 7 of 10 trading days,\n"
        "   the system may reduce risk, pause strategy, re-check\n"
        "   analysis, or request manual review.\n\n"
        "⚠️ No guarantees of account safety.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>By using Auto Trading you confirm you have read\n"
        "and accepted these risk warnings.</i>"
    )
    await show_screen(cq.bot, uid, text, at_back_dashboard_kb())


# ══════════════════════════════════════════════════════════
# TODAY TRADE HISTORY
# ══════════════════════════════════════════════════════════

@router.callback_query(F.data == "at:history")
async def at_history(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    is_free = not _has_access(uid)

    if is_free:
        # Free users: demo history only
        demo_fx  = db.at_get_today_history(uid, trade_type="demo_forex",  is_demo=True)
        demo_bin = db.at_get_today_history(uid, trade_type="demo_binary", is_demo=True)
        total    = len(demo_fx) + len(demo_bin)
        text = (
            "📅 <b>TODAY TRADE HISTORY</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🎮 <b>Demo Mode — Your trades today</b>\n\n"
            f"💹 Demo Forex trades:  <b>{len(demo_fx)}</b>\n"
            f"📊 Demo Binary trades: <b>{len(demo_bin)}</b>\n"
            f"📌 Total:              <b>{total}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🔒 <i>Upgrade to access live trade history.</i>"
        )
        await show_screen(cq.bot, uid, text, at_history_menu_kb(is_demo=True))
        return

    # Premium: show summary + tabs
    fx_sum  = db.at_get_today_summary(uid, "forex")
    bin_sum = db.at_get_today_summary(uid, "binary")

    def _pl_str(v):
        return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"

    text = (
        "📅 <b>TODAY TRADE HISTORY</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "💹 <b>FOREX SUMMARY</b>\n"
        f"   Total trades:  <b>{fx_sum['total']}</b>\n"
        f"   Profit trades: <b>{fx_sum['profit']} 🟢</b>\n"
        f"   Loss trades:   <b>{fx_sum['loss']} 🔴</b>\n"
        f"   Break-even:    <b>{fx_sum['breakeven']} 🟡</b>\n"
        f"   Net P/L:       <b>{_pl_str(fx_sum['net_pl'])}</b>\n\n"
        "─────────────────────\n"
        "📊 <b>BINARY SUMMARY</b>\n"
        f"   Total trades:  <b>{bin_sum['total']}</b>\n"
        f"   Wins:          <b>{bin_sum['profit']} 🟢</b>\n"
        f"   Losses:        <b>{bin_sum['loss']} 🔴</b>\n"
        f"   Refunds:       <b>{bin_sum['breakeven']} 🟡</b>\n"
        f"   Win Rate:      <b>{bin_sum['win_rate']}%</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Tap a tab below to see full trade list.\n"
        "History resets at 00:00 UTC — archived automatically.</i>"
    )
    await show_screen(cq.bot, uid, text, at_history_menu_kb(is_demo=False))


# ── Forex history detail ───────────────────────────────────

@router.callback_query(F.data == "at:hist:forex")
async def at_hist_forex(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    trades  = db.at_get_today_history_filtered(uid, trade_type="forex", is_demo=False)
    summary = db.at_get_today_summary(uid, "forex")
    await show_screen(cq.bot, uid,
                      _build_forex_history_text(trades, summary, demo=False),
                      at_hist_filter_kb("forex", "all"))


@router.callback_query(F.data == "at:hist:demo_forex")
async def at_hist_demo_forex(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    trades  = db.at_get_today_history(uid, trade_type="demo_forex", is_demo=True)
    summary = db.at_get_today_summary(uid, "demo_forex", is_demo=True)
    await show_screen(cq.bot, uid,
                      _build_forex_history_text(trades, summary, demo=True),
                      at_history_back_kb("demo_forex"))


def _build_forex_history_text(trades: list, summary: dict, demo: bool) -> str:
    label = "DEMO FOREX" if demo else "FOREX"
    lines = [
        f"📈 <b>{label} TRADE HISTORY</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if not trades:
        lines += ["", "📭 <i>No trades recorded today.</i>"]
    else:
        for t in trades:
            pair   = t["pair"].replace("/", "")
            lot    = f"{float(t['lot_size'] or 0):.2f}"
            pl     = float(t["profit_loss"] or 0)
            pl_str = f"+${pl:.2f}" if pl > 0 else (f"-${abs(pl):.2f}" if pl < 0 else "$0.00")
            badge  = _result_badge(t["result"])
            direc  = "▲" if t["direction"].upper() in ("BUY", "CALL") else "▼"
            lines.append(
                f"<code>{pair:<9}</code> | <code>{lot}</code> | "
                f"<b>{direc} {pl_str}</b>  {badge}"
            )

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"📊 Total: <b>{summary['total']}</b>  "
        f"🟢 {summary['profit']}  🔴 {summary['loss']}  🟡 {summary['breakeven']}",
        f"💰 Net P/L: <b>{'+'if summary['net_pl']>=0 else ''}"
        f"${summary['net_pl']:.2f}</b>",
    ]

    if trades:
        lines += ["", "<b>── TRADE DETAILS ──</b>"]
        for t in trades:
            open_t  = (t.get("open_time")  or "")[:16]
            close_t = (t.get("close_time") or "")[:16]
            entry   = float(t.get("entry_price") or 0)
            exit_p  = float(t.get("exit_price")  or 0)
            sl_p    = float(t.get("sl_price")     or 0)
            tp_p    = float(t.get("tp_price")     or 0)
            direc   = "▲ BUY" if t["direction"].upper() == "BUY" else "▼ SELL"
            lines += [
                "",
                f"<b>#{t['trade_no']}  {t['pair']}</b>  {_result_badge(t['result'])}",
                f"  Dir: {direc}   Lot: {float(t['lot_size'] or 0):.2f}",
                f"  Entry: <code>{entry:.5f}</code>  Exit: <code>{exit_p:.5f}</code>",
                f"  SL: <code>{sl_p:.5f}</code>  TP: <code>{tp_p:.5f}</code>",
                f"  Close: <b>{_close_reason_label(t['close_reason'])}</b>",
                f"  🕐 {open_t} → {close_t}",
                f"  ID: <code>{t['id']}</code>",
            ]

    lines.append("\n<i>History resets at 00:00 UTC daily.</i>")
    return "\n".join(lines)


# ── Binary history detail ──────────────────────────────────

@router.callback_query(F.data == "at:hist:binary")
async def at_hist_binary(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not _has_access(uid):
        await at_open(cq)
        return
    trades  = db.at_get_today_history_filtered(uid, trade_type="binary", is_demo=False)
    summary = db.at_get_today_summary(uid, "binary")
    await show_screen(cq.bot, uid,
                      _build_binary_history_text(trades, summary, demo=False),
                      at_hist_filter_kb("binary", "all"))


@router.callback_query(F.data == "at:hist:demo_binary")
async def at_hist_demo_binary(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    trades  = db.at_get_today_history(uid, trade_type="demo_binary", is_demo=True)
    summary = db.at_get_today_summary(uid, "demo_binary", is_demo=True)
    await show_screen(cq.bot, uid,
                      _build_binary_history_text(trades, summary, demo=True),
                      at_history_back_kb("demo_binary"))


def _build_binary_history_text(trades: list, summary: dict, demo: bool) -> str:
    label = "DEMO BINARY" if demo else "BINARY"
    lines = [
        f"📊 <b>{label} TRADE HISTORY</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not trades:
        lines += ["", "📭 <i>No trades recorded today.</i>"]
    else:
        # Header row
        lines += [
            "",
            "<code>No | Asset            | Dir  | Amt  | Result</code>",
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        ]
        for t in trades:
            no     = str(t["trade_no"]).rjust(2)
            asset  = t["pair"][:16].ljust(16)
            direc  = "CALL" if t["direction"].upper() in ("BUY", "CALL") else "PUT "
            amt    = f"${float(t['amount'] or 0):.0f}".rjust(5)
            badge  = _result_badge(t["result"])
            lines.append(f"<code>{no} | {asset} | {direc} | {amt} | </code>{badge}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Total: <b>{summary['total']}</b>   "
        f"🟢 Win: <b>{summary['profit']}</b>   "
        f"🔴 Loss: <b>{summary['loss']}</b>   "
        f"Win Rate: <b>{summary['win_rate']}%</b>",
    ]

    if trades:
        lines += ["", "<b>── TRADE DETAILS ──</b>"]
        for t in trades:
            open_t  = (t.get("open_time")  or "")[:16]
            close_t = (t.get("close_time") or "")[:16]
            direc   = "▲ CALL" if t["direction"].upper() in ("BUY", "CALL") else "▼ PUT"
            payout  = float(t.get("payout") or 0)
            amount  = float(t.get("amount") or 0)
            lines += [
                "",
                f"<b>#{t['trade_no']}  {t['pair']}</b>  {_result_badge(t['result'])}",
                f"  Dir: {direc}   Amount: <b>${amount:.0f}</b>",
                f"  Payout: <b>${payout:.2f}</b>",
                f"  Close: <b>{_close_reason_label(t['close_reason'])}</b>",
                f"  🕐 {open_t} → {close_t}",
                f"  ID: <code>{t['id']}</code>",
            ]

    lines.append("\n<i>History resets at 00:00 UTC daily.</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# AUTO TRADING ENGINE CONTROL PANEL
# ══════════════════════════════════════════════════════════

_ENGINE_STATE_LABELS = {
    "stopped":  "⏹ STOPPED",
    "running":  "▶️ RUNNING",
    "paused":   "⏸ PAUSED",
    "error":    "⚠️ ERROR",
}

_RISK_MODE_LABELS = {
    "conservative": "🟢 Conservative",
    "moderate":     "🟡 Moderate",
    "aggressive":   "🔴 Aggressive",
}


def _fmt_engine_panel(uid: int) -> str:
    s = db.at_get_full_settings(uid)
    state   = s.get("engine_state") or "stopped"
    state_l = _ENGINE_STATE_LABELS.get(state, state.upper())
    broker  = "🟢 Connected" if s.get("broker_connected") else "🔴 Disconnected"
    risk    = _RISK_MODE_LABELS.get(s.get("risk_mode", "moderate"), "Moderate")
    strat_p = "⏸ Paused" if s.get("strategy_paused") else "▶️ Active"
    last_sig = s.get("last_signal_time") or "—"
    last_ex  = s.get("last_execution_time") or "—"
    err      = s.get("error_state") or "None"
    review   = "⚠️ YES — Manual approval required" if s.get("review_required") else "✅ No"
    dd_hit   = "⚠️ HIT TODAY" if s.get("drawdown_hit_today") else "✅ Normal"
    loss_d   = s.get("loss_day_count") or 0

    return (
        "⚙️ <b>AUTO TRADING ENGINE CONTROL PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔋 <b>Engine State:</b>  {state_l}\n"
        f"📡 <b>Broker:</b>        {broker}\n"
        f"🧠 <b>Strategy:</b>      {strat_p}\n"
        f"⚖️ <b>Risk Mode:</b>     {risk}\n"
        f"📉 <b>Drawdown:</b>      {dd_hit}\n"
        f"📅 <b>Loss Days (10d):</b> {loss_d}/10\n"
        f"🔍 <b>Review Required:</b> {review}\n"
        "\n"
        f"📨 <b>Last Signal:</b>    {last_sig}\n"
        f"⚡ <b>Last Execution:</b> {last_ex}\n"
        f"🐛 <b>Error State:</b>   {err}\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Use the controls below to start, stop, pause, or resume\n"
        "the auto trading engine. Changes take effect immediately.</i>"
    )


@router.callback_query(F.data == "at:controls")
async def at_controls(cq: CallbackQuery):
    uid = cq.from_user.id
    await cq.answer()
    if not db.has_premium_access(uid) and not _is_admin(uid):
        await show_screen(cq.bot, uid, "🔒 Premium access required.", at_gate_kb())
        return
    state = db.at_get_engine_state(uid)
    await show_screen(cq.bot, uid, _fmt_engine_panel(uid), at_controls_kb(state))


@router.callback_query(F.data.startswith("at:ctrl:"))
async def at_ctrl_action(cq: CallbackQuery):
    uid    = cq.from_user.id
    action = cq.data.split(":")[-1]   # start | stop | pause | resume
    await cq.answer()
    if not db.has_premium_access(uid) and not _is_admin(uid):
        await show_screen(cq.bot, uid, "🔒 Premium access required.", at_gate_kb())
        return

    state_before = db.at_get_engine_state(uid)
    settings     = db.at_get_full_settings(uid)
    now_str      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Broker must be connected to start / resume
    if action in ("start", "resume") and not settings.get("broker_connected"):
        err_text = (
            "⚙️ <b>AUTO TRADING CONTROLS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🔌 <b>No broker connected.</b>\n\n"
            "You must connect a broker account first before\n"
            "starting the auto trading engine.\n\n"
            "Go to 🔌 BROKER CONNECTION to set it up."
        )
        await show_screen(cq.bot, uid, err_text, at_controls_kb(state_before))
        return

    # Drawdown hit — require permission before starting
    if action in ("start", "resume") and db.at_drawdown_is_hit(uid):
        await show_screen(cq.bot, uid, _fmt_drawdown_dialog(uid), at_drawdown_confirm_kb())
        return

    # Review required — block start/resume
    if action in ("start", "resume") and settings.get("review_required"):
        err_text = (
            "⚙️ <b>AUTO TRADING CONTROLS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🔍 <b>Manual Review Required.</b>\n\n"
            "The system detected excessive losses over the past\n"
            "10 trading days. A strategy review must be completed\n"
            "before auto trading can resume.\n\n"
            "Please contact support to clear the review flag."
        )
        await show_screen(cq.bot, uid, err_text, at_controls_kb(state_before))
        return

    # Apply state transition
    transitions = {
        "start":  ("stopped", "running"),
        "stop":   (None,      "stopped"),
        "pause":  ("running", "paused"),
        "resume": ("paused",  "running"),
    }
    _, new_state = transitions.get(action, (None, state_before))
    clear_error  = action == "stop"
    db.at_set_engine_state(
        uid, new_state,
        last_execution_time=now_str if action in ("start", "resume") else None,
        error_state="" if clear_error else None,
    )
    if action == "stop":
        db.at_set_drawdown_hit(uid, False)

    state_after = db.at_get_engine_state(uid)
    await show_screen(cq.bot, uid, _fmt_engine_panel(uid), at_controls_kb(state_after))


# ══════════════════════════════════════════════════════════
# DAILY DRAWDOWN PERMISSION DIALOG
# ══════════════════════════════════════════════════════════

def _fmt_drawdown_dialog(uid: int) -> str:
    s = db.at_get_full_settings(uid)
    threshold = s.get("drawdown_threshold") or 5.0
    loss_d    = s.get("loss_day_count") or 0
    return (
        "🛑 <b>DAILY DRAWDOWN LIMIT REACHED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ Your account has hit the <b>{threshold:.1f}% daily drawdown</b>\n"
        "threshold set in your risk settings.\n\n"
        f"📅 <b>Loss days (last 10):</b> {loss_d}/10\n\n"
        "The system has paused auto trading to protect your\n"
        "account. You may choose to:\n\n"
        "✅ <b>CONTINUE</b> — Accept the risk and resume trading.\n"
        "   (You take full responsibility for further losses.)\n\n"
        "🛑 <b>STOP</b> — Keep trading paused until tomorrow.\n"
        "   (Recommended — protect your capital.)\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<i>Win rate depends on market conditions.\n"
        "No guaranteed profit. Risk only what you can afford.</i>"
    )


@router.callback_query(F.data.startswith("at:dd:"))
async def at_drawdown_decision(cq: CallbackQuery):
    uid    = cq.from_user.id
    choice = cq.data.split(":")[-1]   # continue | stop
    await cq.answer()

    if choice == "continue":
        db.at_set_drawdown_hit(uid, False)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        db.at_set_engine_state(uid, "running", last_execution_time=now_str)
        confirm_text = (
            "✅ <b>TRADING RESUMED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "You have chosen to continue trading.\n"
            "Drawdown flag cleared for this session.\n\n"
            "⚠️ <i>Please monitor your account closely.\n"
            "Trade responsibly and manage your risk.</i>"
        )
        state = db.at_get_engine_state(uid)
        await show_screen(cq.bot, uid, confirm_text, at_controls_kb(state))
    else:
        db.at_set_engine_state(uid, "stopped")
        db.at_set_drawdown_hit(uid, False)
        confirm_text = (
            "🛑 <b>TRADING STOPPED</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Auto trading has been stopped.\n"
            "Drawdown protection is active.\n\n"
            "✅ Your capital is protected for today.\n"
            "You can start again tomorrow or when ready.\n\n"
            "<i>Tap ⚙️ AUTO TRADING CONTROLS to restart.</i>"
        )
        await show_screen(cq.bot, uid, confirm_text, at_back_dashboard_kb())


# ══════════════════════════════════════════════════════════
# TRADE HISTORY — FILTERED VIEWS
# ══════════════════════════════════════════════════════════

# Handles: at:hist:forex:all / at:hist:forex:win / at:hist:forex:loss
#          at:hist:binary:all / at:hist:binary:win / at:hist:binary:loss
@router.callback_query(F.data.regexp(r"^at:hist:(forex|binary):(all|win|loss)$"))
async def at_hist_filtered(cq: CallbackQuery):
    uid   = cq.from_user.id
    parts = cq.data.split(":")
    tab   = parts[2]        # forex | binary
    filt  = parts[3]        # all | win | loss
    await cq.answer()

    is_premium = db.has_premium_access(uid) or _is_admin(uid)
    if not is_premium:
        await show_screen(cq.bot, uid, "🔒 Premium access required.", at_gate_kb())
        return

    trade_type = "forex" if tab == "forex" else "binary"
    trades = db.at_get_today_history_filtered(
        uid, trade_type=trade_type, result_filter=None if filt == "all" else filt,
        is_demo=False,
    )

    label_map = {"all": "📋 ALL", "win": "🟢 WIN ONLY", "loss": "🔴 LOSS ONLY"}
    filt_label = label_map.get(filt, filt.upper())
    type_label = "💹 FOREX" if tab == "forex" else "📊 BINARY"

    if not trades:
        empty_text = (
            f"{type_label} HISTORY  ›  {filt_label}\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "📭 No trades found for this filter today.\n\n"
            "<i>Trades appear here as they are executed.</i>"
        )
        await show_screen(cq.bot, uid, empty_text, at_hist_filter_kb(tab, filt))
        return

    text = _build_filtered_history_text(trades, tab, filt_label)
    await show_screen(cq.bot, uid, text, at_hist_filter_kb(tab, filt))


def _build_filtered_history_text(trades: list, tab: str, filt_label: str) -> str:
    type_icon = "💹" if tab == "forex" else "📊"
    type_name = "FOREX" if tab == "forex" else "BINARY"

    wins   = sum(1 for t in trades if t.get("result") in ("profit", "win"))
    losses = sum(1 for t in trades if t.get("result") == "loss")
    net_pl = sum(float(t.get("profit_loss") or 0) for t in trades)

    lines = [
        f"{type_icon} <b>{type_name} HISTORY  ›  {filt_label}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"📊 {len(trades)} trade(s)  |  🟢 {wins} Win  🔴 {losses} Loss",
        f"💰 Net P/L: <b>{'+ $' if net_pl >= 0 else '- $'}{abs(net_pl):.2f}</b>",
        "",
    ]

    for t in trades:
        trd_id   = t.get("trd_id") or f"#{t['trade_no']:02d}"
        pair     = t.get("pair", "—")
        direc    = t.get("direction", "—")
        result   = t.get("result", "pending")
        strategy = t.get("strategy_name") or "—"
        duration = t.get("duration_mins") or 0
        risk_pct = t.get("risk_pct") or 0
        pl       = float(t.get("profit_loss") or 0)
        badge    = _result_badge(result)

        open_t  = (t.get("open_time") or "")[:16].replace("T", " ")
        close_t = (t.get("close_time") or "")[:16].replace("T", " ")

        if tab == "forex":
            lot    = t.get("lot_size") or 0
            entry  = t.get("entry_price") or 0
            exit_p = t.get("exit_price") or 0
            sl     = t.get("sl_price") or 0
            tp     = t.get("tp_price") or 0
            lines += [
                f"<b>{trd_id}  {pair}</b>  {badge}",
                f"  {'▲ BUY' if str(direc).upper() in ('BUY','CALL') else '▼ SELL'}  "
                f"Lot: <b>{lot}</b>  Risk: <b>{risk_pct}%</b>",
                f"  Strategy: <b>{strategy}</b>  Duration: <b>{duration} min</b>",
                f"  Entry: <b>{entry:.5f}</b>  Exit: <b>{exit_p:.5f}</b>",
                f"  SL: <b>{sl:.5f}</b>  TP: <b>{tp:.5f}</b>",
                f"  P/L: <b>{'+ $' if pl >= 0 else '- $'}{abs(pl):.2f}</b>  "
                f"Close: <b>{_close_reason_label(t.get('close_reason',''))}</b>",
                f"  🕐 {open_t} → {close_t}",
                "",
            ]
        else:
            amount = t.get("amount") or 0
            payout = t.get("payout") or 0
            lines += [
                f"<b>{trd_id}  {pair}</b>  {badge}",
                f"  {'▲ CALL' if str(direc).upper() in ('BUY','CALL') else '▼ PUT'}  "
                f"Amount: <b>${amount:.0f}</b>  Payout: <b>${payout:.2f}</b>",
                f"  Strategy: <b>{strategy}</b>  Duration: <b>{duration} min</b>",
                f"  Risk: <b>{risk_pct}%</b>  "
                f"Close: <b>{_close_reason_label(t.get('close_reason',''))}</b>",
                f"  🕐 {open_t} → {close_t}",
                "",
            ]

    lines.append("<i>History resets at 00:00 UTC daily.</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# MIDNIGHT ARCHIVE SCHEDULER
# ══════════════════════════════════════════════════════════

async def run_midnight_archive():
    """Background task: at UTC midnight, archive all stale today-history rows."""
    import logging
    log = logging.getLogger(__name__)
    while True:
        now  = datetime.now(timezone.utc)
        secs_until_midnight = (
            (24 - now.hour - 1) * 3600
            + (60 - now.minute - 1) * 60
            + (60 - now.second)
        )
        await asyncio.sleep(secs_until_midnight + 5)
        try:
            moved = db.at_archive_all_stale()
            log.info("[AutoTrading] Midnight archive: moved %d trade rows to archive.", moved)
        except Exception as exc:
            log.warning("[AutoTrading] Midnight archive error: %s", exc)
