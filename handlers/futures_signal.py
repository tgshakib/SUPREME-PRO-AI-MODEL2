"""Isolated Telegram UI for Future Signal • TG."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from chat_clean import show_screen
from futures_signal_bot.config import (
    MARKET_ASSETS, MARKETS, MAX_SELECTED_ASSETS, MAX_SIGNALS_PER_ASSET,
)
from futures_signal_bot.engine import generate_message

router = Router()


@dataclass
class FutureSession:
    market: str | None = None
    assets: list[str] = field(default_factory=list)
    direction: str = "BOTH"


_sessions: dict[int, FutureSession] = {}


def _session(user_id: int, reset: bool = False) -> FutureSession:
    if reset or user_id not in _sessions:
        _sessions[user_id] = FutureSession()
    return _sessions[user_id]


def _market_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=MARKETS["real"], callback_data="fut:market:real")],
        [InlineKeyboardButton(text=MARKETS["quotex"], callback_data="fut:market:quotex"),
         InlineKeyboardButton(text=MARKETS["po"], callback_data="fut:market:po")],
        [InlineKeyboardButton(text=MARKETS["iq"], callback_data="fut:market:iq"),
         InlineKeyboardButton(text=MARKETS["olymp"], callback_data="fut:market:olymp")],
        [InlineKeyboardButton(text="⬅️ BACK TO HOME", callback_data="fut:home")],
    ])


def _asset_kb(session: FutureSession) -> InlineKeyboardMarkup:
    assets = MARKET_ASSETS.get(session.market or "", ())
    rows = []
    for index in range(0, len(assets), 2):
        row = []
        for pair_index, asset in enumerate(assets[index:index + 2], start=index):
            mark = "✅ " if asset in session.assets else ""
            row.append(InlineKeyboardButton(
                text=f"{mark}{asset}", callback_data=f"fut:asset:{pair_index}",
            ))
        rows.append(row)
    rows.extend([
        [InlineKeyboardButton(text="✅ DONE", callback_data="fut:assets:done")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="fut:back:market"),
         InlineKeyboardButton(text="🏢 HOME", callback_data="fut:home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _direction_kb(direction: str) -> InlineKeyboardMarkup:
    def label(value: str) -> str:
        return f"{'✅ ' if direction == value else ''}{value}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label("BOTH"), callback_data="fut:direction:BOTH"),
         InlineKeyboardButton(text=label("CALL"), callback_data="fut:direction:CALL"),
         InlineKeyboardButton(text=label("PUT"), callback_data="fut:direction:PUT")],
        [InlineKeyboardButton(text="5 SIGNALS", callback_data="fut:count:5"),
         InlineKeyboardButton(text="10 SIGNALS", callback_data="fut:count:10"),
         InlineKeyboardButton(text="15 SIGNALS", callback_data="fut:count:15")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="fut:back:assets"),
         InlineKeyboardButton(text="🏢 HOME", callback_data="fut:home")],
    ])


def _home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔮 GENERATE ANOTHER", callback_data="fut:open")],
        [InlineKeyboardButton(text="🏢 BACK TO HOME", callback_data="fut:home")],
    ])


async def _show_market(callback: CallbackQuery, reset: bool = False) -> None:
    _session(callback.from_user.id, reset=reset)
    await show_screen(
        callback.bot, callback.from_user.id,
        "🔮 <b>FUTURE SIGNAL • TG</b>\n\n📊 <b>Select Market Type:</b>",
        _market_kb(),
    )


@router.message(Command("futuresignal"))
async def command_future_signal(message: Message) -> None:
    _session(message.from_user.id, reset=True)
    await show_screen(
        message.bot, message.from_user.id,
        "🔮 <b>FUTURE SIGNAL • TG</b>\n\n📊 <b>Select Market Type:</b>",
        _market_kb(),
    )


@router.callback_query(F.data == "fut:open")
async def open_future_signal(callback: CallbackQuery) -> None:
    await callback.answer()
    await _show_market(callback, reset=True)


@router.callback_query(F.data.startswith("fut:market:"))
async def select_market(callback: CallbackQuery) -> None:
    market = callback.data.rsplit(":", 1)[-1]
    if market not in MARKETS:
        await callback.answer("Unknown market.", show_alert=True)
        return
    session = _session(callback.from_user.id)
    session.market, session.assets = market, []
    await callback.answer()
    await show_screen(
        callback.bot, callback.from_user.id,
        f"🔮 <b>FUTURE SIGNAL • TG</b>\n\n<b>{MARKETS[market]}</b>\n"
        f"Select 1–{MAX_SELECTED_ASSETS} pairs, then tap <b>DONE</b>.",
        _asset_kb(session),
    )


@router.callback_query(F.data.startswith("fut:asset:"))
async def toggle_asset(callback: CallbackQuery) -> None:
    session = _session(callback.from_user.id)
    assets = MARKET_ASSETS.get(session.market or "", ())
    try:
        asset = assets[int(callback.data.rsplit(":", 1)[-1])]
    except (ValueError, IndexError):
        await callback.answer("That pair is no longer available.", show_alert=True)
        return
    if asset in session.assets:
        session.assets.remove(asset)
    elif len(session.assets) >= MAX_SELECTED_ASSETS:
        await callback.answer(f"Select up to {MAX_SELECTED_ASSETS} pairs.", show_alert=True)
        return
    else:
        session.assets.append(asset)
    await callback.answer()
    await show_screen(
        callback.bot, callback.from_user.id,
        f"🔮 <b>FUTURE SIGNAL • TG</b>\n\n<b>{MARKETS[session.market]}</b>\n"
        f"Selected: <b>{len(session.assets)}</b> / {MAX_SELECTED_ASSETS}",
        _asset_kb(session),
    )


@router.callback_query(F.data == "fut:assets:done")
async def finish_assets(callback: CallbackQuery) -> None:
    session = _session(callback.from_user.id)
    if not session.assets:
        await callback.answer("Select at least one pair first.", show_alert=True)
        return
    await callback.answer()
    await show_screen(
        callback.bot, callback.from_user.id,
        "🔮 <b>FUTURE SIGNAL • TG</b>\n\n"
        f"<b>Pairs:</b> {escape(', '.join(session.assets))}\n"
        "<b>Choose direction and signal count:</b>",
        _direction_kb(session.direction),
    )


@router.callback_query(F.data.startswith("fut:direction:"))
async def select_direction(callback: CallbackQuery) -> None:
    direction = callback.data.rsplit(":", 1)[-1]
    if direction not in {"BOTH", "CALL", "PUT"}:
        return
    session = _session(callback.from_user.id)
    session.direction = direction
    await callback.answer()
    await show_screen(
        callback.bot, callback.from_user.id,
        "🔮 <b>FUTURE SIGNAL • TG</b>\n\n"
        f"<b>Direction:</b> {direction}\n<b>Choose signal count:</b>",
        _direction_kb(direction),
    )


@router.callback_query(F.data.startswith("fut:count:"))
async def generate_future_signals(callback: CallbackQuery) -> None:
    session = _session(callback.from_user.id)
    try:
        count = min(MAX_SIGNALS_PER_ASSET, max(1, int(callback.data.rsplit(":", 1)[-1])))
    except ValueError:
        await callback.answer("Invalid signal count.", show_alert=True)
        return
    if not session.market or not session.assets:
        await callback.answer("Please choose a market and pair again.", show_alert=True)
        await _show_market(callback, reset=True)
        return
    await callback.answer("Generating Future Signal • TG…")
    await show_screen(
        callback.bot, callback.from_user.id,
        "🔮 <b>FUTURE SIGNAL • TG</b>\n\n<i>Analysing local market data…</i>",
        _home_kb(),
    )
    text = await asyncio.to_thread(
        generate_message, list(session.assets), session.market, session.direction, count,
    )
    # Keep one Telegram card under the platform's text limit; the offered
    # maximum produces comfortably smaller messages.
    await show_screen(callback.bot, callback.from_user.id, text, _home_kb())


@router.callback_query(F.data == "fut:back:market")
async def back_to_market(callback: CallbackQuery) -> None:
    await callback.answer()
    await _show_market(callback)


@router.callback_query(F.data == "fut:back:assets")
async def back_to_assets(callback: CallbackQuery) -> None:
    session = _session(callback.from_user.id)
    await callback.answer()
    await show_screen(
        callback.bot, callback.from_user.id,
        f"🔮 <b>FUTURE SIGNAL • TG</b>\n\n<b>{MARKETS.get(session.market or '', 'Market')}</b>\n"
        f"Selected: <b>{len(session.assets)}</b> / {MAX_SELECTED_ASSETS}",
        _asset_kb(session),
    )


@router.callback_query(F.data == "fut:home")
async def back_home(callback: CallbackQuery) -> None:
    await callback.answer()
    from handlers.main_menu import render_home
    await render_home(callback.bot, callback.from_user.id, callback.from_user, fast=True)