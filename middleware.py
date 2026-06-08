"""Anti-spam / dedup middleware for aiogram 3.
=================================================
Fixes the duplicate-response problem where rapid button taps cause 10+
identical responses. Applied globally to ALL callback_query updates.

What it does (in order)
-----------------------
1. GLOBAL CALLBACK DEDUP  — each Telegram callback_query has a unique
   `.id`. If we've already handled this exact ID, skip silently.
   The seen-IDs set is flushed every 60 seconds to prevent memory leak.

2. PER-USER 5-SECOND COOLDOWN  — if the same user triggers the same
   callback_data within 5 seconds, answer() the query (clears spinner)
   and drop it silently. No error toast shown to the user.

3. PER-USER ASYNC LOCK  — only one request processes at a time per user.
   If the user somehow fires a second request while the first is still
   running (e.g. during the 5-7 s analysis sleep), the second is
   answered and silently dropped.

All three guards are non-intrusive — the user never sees an error
message. The "loading" spinner is cleared by the answer() call.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Update


# ── Shared state (module-level singletons) ─────────────────────────────────

# Set of already-handled callback query IDs.
# Telegram can re-deliver the same update on slow acks.
_seen_cb_ids: set[str] = set()
_seen_lock   = asyncio.Lock()

# {user_id: (callback_data, timestamp)} — last accepted click per user
_last_click: dict[int, tuple[str, float]] = {}
_click_lock = asyncio.Lock()

# {user_id: asyncio.Lock()} — one active request per user
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

# How long (seconds) to block repeated clicks on the same button
# 1.5s is enough to kill accidental double-taps while still feeling instant
COOLDOWN_SEC = 1.5

# Cleanup background task handle
_cleanup_task: asyncio.Task | None = None


# ── Background cleanup ─────────────────────────────────────────────────────

async def _cleanup_loop():
    """Flush the seen-IDs set every 60 s to prevent unbounded growth."""
    global _seen_cb_ids
    while True:
        await asyncio.sleep(60)
        async with _seen_lock:
            _seen_cb_ids = set()
        # Also purge stale cooldown entries (older than 60 s)
        now = time.monotonic()
        async with _click_lock:
            stale = [uid for uid, (_, ts) in _last_click.items()
                     if now - ts > 60]
            for uid in stale:
                del _last_click[uid]


def start_cleanup(loop: asyncio.AbstractEventLoop | None = None):
    """Call once from bot startup to start the background cleaner."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.ensure_future(_cleanup_loop())


# ── Middleware class ───────────────────────────────────────────────────────

class AntiSpamMiddleware(BaseMiddleware):
    """Applies all three guards to every callback_query update."""

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        cb_id   = event.id
        user_id = event.from_user.id
        cb_data = event.data or ""

        # ── 1. Global dedup ────────────────────────────────────────────
        async with _seen_lock:
            if cb_id in _seen_cb_ids:
                # Already handled — answer to clear spinner and drop
                try:
                    await event.answer()
                except Exception:
                    pass
                return
            _seen_cb_ids.add(cb_id)

        # ── 2. Per-user same-button cooldown ───────────────────────────
        now = time.monotonic()
        async with _click_lock:
            prev_data, prev_ts = _last_click.get(user_id, ("", 0.0))
            if cb_data == prev_data and (now - prev_ts) < COOLDOWN_SEC:
                try:
                    await event.answer()
                except Exception:
                    pass
                return
            _last_click[user_id] = (cb_data, now)

        # ── 3. Per-user async lock (one request at a time) ────────────
        lock = _user_locks[user_id]
        if lock.locked():
            # Another request still running for this user — drop silently
            try:
                await event.answer()
            except Exception:
                pass
            return

        async with lock:
            return await handler(event, data)


# ── Update-level middleware (dedup at the Update level too) ────────────────

class UpdateDedupMiddleware(BaseMiddleware):
    """Secondary guard: dedup at the raw Update level.
    Catches the rare case where Telegram re-sends the same Update ID
    before the callback-level dedup has a chance to run."""

    _seen_update_ids: set[int] = set()
    _lock = asyncio.Lock()

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        uid = event.update_id
        async with self._lock:
            if uid in self._seen_update_ids:
                return
            self._seen_update_ids.add(uid)
            # Keep the set bounded
            if len(self._seen_update_ids) > 5000:
                self._seen_update_ids = set(
                    list(self._seen_update_ids)[-2500:]
                )
        return await handler(event, data)
