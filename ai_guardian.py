"""SUPREME PRO AI — Silent AI Guardian System
=============================================
Four autonomous agents working silently under the bot at all times:

  Agent-1 (WinRateAgent)   — monitors 2-day winrate every 5 min.
                              If < 85% → auto-boost AI thresholds.
                              If > 92% → relax thresholds.
                              Never prints anything in signal text.

  Agent-2 (SSIDGuard)      — probes PO SSID health every 45 seconds.
                              Detects expiry 3-4 minutes early → auto-refresh.
                              On any auth failure → immediate refresh + reconnect.

  Agent-3 (PriceFundAgent) — monitors every OTC pair price every 20 seconds.
                              Detects stale/delayed/missing broker prices.
                              Auto force-reconnects QX + PO streams to fix delays.
                              3-layer recovery: restart tasks → SSID refresh → notify.
                              Never shows anything in signal cards.

  ClaudeAdvisor            — uses Anthropic Claude API (if key set) to analyse
                              per-pair/per-engine failure patterns every 15 min
                              and silently apply micro-threshold corrections to DB.

All agents are 100% silent — zero text ever added to signal cards.
Only the admin receives private status pings when the AI acts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import database as db

log = logging.getLogger("ai_guardian")

# ── Thresholds (mirror winrate_guardian constants) ────────────────────────────
WR_TARGET   = 85.0
WR_RELAX    = 92.0
MIN_SAMPLES = 4
CHECK_EVERY = 300        # 5 minutes
CLAUDE_EVERY = 900       # 15 minutes

# ── SSID probe settings ───────────────────────────────────────────────────────
SSID_PROBE_INTERVAL = 45         # probe every 45 seconds
SSID_EXPIRY_BUFFER  = 240        # refresh if < 4 minutes remain (seconds)
SSID_PROBE_URL      = "https://api-l.po.market/socket.io/?EIO=4&transport=websocket"
_SSID_LAST_OK: dict  = {"ts": 0.0, "consecutive_fails": 0}

# ── Anthropic Claude ──────────────────────────────────────────────────────────
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL   = "claude-opus-4-5"


# ══════════════════════════════════════════════════════════════════════════════
# AGENT-1: WIN RATE MONITOR
# ══════════════════════════════════════════════════════════════════════════════

async def _winrate_agent(bot):
    """Silently monitors winrate. Auto-adjusts boost level. No signal text impact."""
    log.info("[Agent-1/WinRate] Started — checking every %ds", CHECK_EVERY)
    await asyncio.sleep(60)   # warm-up

    while True:
        try:
            await _winrate_check(bot)
        except Exception as exc:
            log.debug("[Agent-1/WinRate] check error: %s", exc)
        await asyncio.sleep(CHECK_EVERY)


async def _winrate_check(bot):
    stats = db.winrate_stats(days=2)
    total    = stats.get("total", 0)
    win_rate = stats.get("win_rate", 100.0)

    if total < MIN_SAMPLES:
        return

    current = db.get_boost_level()

    if win_rate < WR_TARGET and current < 3:
        new_level = current + 1
        db.set_boost_level(new_level)
        log.info("[Agent-1/WinRate] WR %.1f%% < %.0f%% → boost %d→%d (silent)",
                 win_rate, WR_TARGET, current, new_level)
        await _silent_admin_notify(
            bot,
            f"🤖 <b>AI Agent-1 AUTO-BOOST</b>\n"
            f"Win Rate: <b>{win_rate:.1f}%</b> (target ≥85%)\n"
            f"Boost Level: {current} → <b>{new_level}</b>\n"
            f"Signals: {stats.get('wins',0)}W / {stats.get('losses',0)}L / {total} total\n"
            f"<i>Thresholds tightened. Monitoring continues.</i>",
        )
    elif win_rate >= WR_RELAX and current > 0:
        new_level = current - 1
        db.set_boost_level(new_level)
        log.info("[Agent-1/WinRate] WR %.1f%% ≥ %.0f%% → relax %d→%d (silent)",
                 win_rate, WR_RELAX, current, new_level)
        await _silent_admin_notify(
            bot,
            f"✅ <b>AI Agent-1 THRESHOLD RELAXED</b>\n"
            f"Win Rate: <b>{win_rate:.1f}%</b> (excellent)\n"
            f"Boost Level: {current} → <b>{new_level}</b>\n"
            f"<i>Performance confirmed. Thresholds eased.</i>",
        )


# ══════════════════════════════════════════════════════════════════════════════
# AGENT-2: SSID EXPIRY GUARD
# ══════════════════════════════════════════════════════════════════════════════

async def _ssid_guard(bot):
    """Probes PO SSID health every 45 s. Refreshes 4 min before expiry."""
    log.info("[Agent-2/SSIDGuard] Started — probing every %ds", SSID_PROBE_INTERVAL)
    await asyncio.sleep(30)   # warm-up

    while True:
        try:
            await _ssid_probe(bot)
        except Exception as exc:
            log.debug("[Agent-2/SSIDGuard] probe error: %s", exc)
        await asyncio.sleep(SSID_PROBE_INTERVAL)


async def _ssid_probe(bot):
    """Probe the SSID via lightweight HTTP check. Refresh if stale/bad."""
    try:
        import httpx
        ssid = os.environ.get("PO_SSID", "")
        if not ssid:
            return

        # Lightweight HEAD probe to PO API using the SSID cookie
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api-l.po.market/socket.io/?EIO=4&transport=polling",
                headers={"Cookie": f"token={ssid}; ssid={ssid}"},
            )
        status = r.status_code

        # 401/403 = auth failure → immediate refresh
        if status in (401, 403):
            _SSID_LAST_OK["consecutive_fails"] += 1
            log.warning("[Agent-2/SSIDGuard] SSID auth failed (HTTP %d) → refreshing now", status)
            await _do_ssid_refresh(bot, reason=f"Auth failure HTTP {status}")
            return

        # Success path — reset fail counter, update last-ok timestamp
        _SSID_LAST_OK["consecutive_fails"] = 0
        _SSID_LAST_OK["ts"] = time.time()

        # Check if we're approaching the token's actual expiry boundary
        # po_auth._SSID_STAMP now tracks both fetched_at and ttl (7d or 30d)
        try:
            from po_auth import _SSID_STAMP, _DEFAULT_TTL, _REFRESH_RATIO
            fetched_at = _SSID_STAMP.get("fetched_at", 0)
            if fetched_at:
                ttl           = int(_SSID_STAMP.get("ttl", _DEFAULT_TTL))
                age           = time.time() - fetched_at
                refresh_after = ttl * _REFRESH_RATIO        # 85% of lifetime
                hard_deadline = ttl - SSID_EXPIRY_BUFFER    # 4 min before full expiry
                remaining     = ttl - age
                # Trigger proactive refresh if past 85% or within 4 min of expiry
                if 0 < remaining <= SSID_EXPIRY_BUFFER or age >= refresh_after:
                    log.info(
                        "[Agent-2/SSIDGuard] SSID approaching expiry "
                        "(age=%.1fh / ttl=%dh, %.0fs remaining) → silent refresh",
                        age / 3600, ttl // 3600, remaining,
                    )
                    await _do_ssid_refresh(bot, reason=f"Expiry refresh ({remaining:.0f}s remaining)")
        except Exception:
            pass

    except Exception as exc:
        # Network error counts as a soft fail
        _SSID_LAST_OK["consecutive_fails"] += 1
        fails = _SSID_LAST_OK["consecutive_fails"]
        log.debug("[Agent-2/SSIDGuard] probe exception (fail #%d): %s", fails, exc)

        # After 5 consecutive failures → force refresh
        if fails >= 5:
            log.warning("[Agent-2/SSIDGuard] %d consecutive probe failures → forcing SSID refresh", fails)
            await _do_ssid_refresh(bot, reason=f"{fails} consecutive probe failures")


async def _do_ssid_refresh(bot, reason: str = ""):
    """Run SSID refresh in executor (blocking login call). 100% silent — no bot messages ever."""
    loop = asyncio.get_event_loop()
    try:
        from po_auth import refresh_ssid_now
        ok = await loop.run_in_executor(None, refresh_ssid_now)
        _SSID_LAST_OK["consecutive_fails"] = 0
        _SSID_LAST_OK["ts"] = time.time()
        if ok:
            log.info("[Agent-2/SSIDGuard] ✅ SSID silently refreshed (%s)", reason)
        else:
            log.warning("[Agent-2/SSIDGuard] ❌ SSID refresh failed (%s) — retrying in 60s", reason)
    except Exception as exc:
        log.warning("[Agent-2/SSIDGuard] refresh error: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT-3: PRICE FUND AGENT — OTC + Live price delay auto-fix
# ══════════════════════════════════════════════════════════════════════════════

_FUND_CHECK_INTERVAL   = 20    # seconds between price health checks
_FUND_STALE_THRESHOLD  = 90    # seconds — broker price older than this = stale
_FUND_STALE_ALERT_PCT  = 0.60  # 60%+ pairs stale → trigger recovery
_FUND_RECONNECT_COOL   = 180   # seconds between reconnect attempts (anti-spam)
_FUND_NOTIFY_COOL      = 600   # seconds between admin notifications (10 min)

# Mutable state (single dict avoids global keyword issues)
_FUND_STATE: dict = {
    "last_reconnect": 0.0,
    "last_notify":    0.0,
    "consecutive_stale_rounds": 0,
    "total_reconnects": 0,
    "total_fixed": 0,
}

# Task handles so we can cancel + restart individual streams
_STREAM_TASKS: dict = {}   # populated when tasks are created


def _register_stream_task(name: str, task: "asyncio.Task"):
    """Called by otc_price_service to register its tasks so Agent-3 can restart them."""
    _STREAM_TASKS[name] = task


async def _price_fund_agent(bot):
    """Agent-3 main loop — monitors OTC/live price freshness every 20 seconds."""
    log.info("[Agent-3/PriceFund] Started — monitoring OTC price health every %ds", _FUND_CHECK_INTERVAL)
    await asyncio.sleep(45)   # let streams connect first

    while True:
        try:
            await _price_fund_check(bot)
        except Exception as exc:
            log.debug("[Agent-3/PriceFund] check error: %s", exc)
        await asyncio.sleep(_FUND_CHECK_INTERVAL)


async def _price_fund_check(bot):
    """Analyse price buffer. If broker prices stale → force-reconnect streams."""
    try:
        from otc_price_service import _PRICES, _LOCK, _QX_OTC_PAIRS, _PO_OTC_PAIRS
    except ImportError:
        return

    now = time.time()
    all_pairs = set(p.lower() for p in _QX_OTC_PAIRS + _PO_OTC_PAIRS)
    total = len(all_pairs)
    if total == 0:
        return

    # Tally freshness per pair
    broker_fresh  = 0  # has broker (qx/po) price newer than threshold
    broker_stale  = 0  # has broker price but it's old
    yf_only       = 0  # only yfinance price, no broker data ever
    no_data       = 0  # no price at all

    with _LOCK:
        snapshot = dict(_PRICES)

    for key in all_pairs:
        entry = snapshot.get(key)
        if not entry:
            no_data += 1
            continue
        source = entry.get("source", "yf")
        age    = now - entry.get("time", 0)
        if source in ("qx", "po"):
            if age < _FUND_STALE_THRESHOLD:
                broker_fresh += 1
            else:
                broker_stale += 1
        else:
            yf_only += 1

    stale_count  = broker_stale + yf_only + no_data
    stale_ratio  = stale_count / total
    has_problem  = stale_ratio >= _FUND_STALE_ALERT_PCT

    if not has_problem:
        # Price feeds healthy — reset stale counter
        if _FUND_STATE["consecutive_stale_rounds"] > 0:
            log.info(
                "[Agent-3/PriceFund] ✅ Prices recovered — "
                "broker_fresh=%d / %d pairs (%.0f%%)",
                broker_fresh, total, 100 * broker_fresh / total,
            )
            _FUND_STATE["total_fixed"] += 1
        _FUND_STATE["consecutive_stale_rounds"] = 0
        return

    _FUND_STATE["consecutive_stale_rounds"] += 1
    rounds = _FUND_STATE["consecutive_stale_rounds"]

    log.warning(
        "[Agent-3/PriceFund] ⚠ Price delay detected — "
        "fresh=%d  stale=%d  yf_only=%d  no_data=%d / %d pairs (%.0f%% stale) round#%d",
        broker_fresh, broker_stale, yf_only, no_data, total,
        100 * stale_ratio, rounds,
    )

    # ── Layer 1: Force-reconnect streams (after 1st stale round) ─────────────
    cool_ok = (now - _FUND_STATE["last_reconnect"]) > _FUND_RECONNECT_COOL
    if rounds >= 1 and cool_ok:
        await _fund_force_reconnect(bot, broker_fresh, stale_count, total)
        _FUND_STATE["last_reconnect"] = now
        _FUND_STATE["total_reconnects"] += 1

    # ── Layer 2: Also refresh PO SSID in case auth is the cause ──────────────
    if rounds >= 2:
        try:
            from po_auth import refresh_ssid_now
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, refresh_ssid_now)
            if ok:
                log.info("[Agent-3/PriceFund] PO SSID refreshed as part of recovery")
        except Exception as exc:
            log.debug("[Agent-3/PriceFund] SSID refresh error: %s", exc)

    # ── Layer 3: Admin notification (rate-limited to every 10 min) ───────────
    notify_ok = (now - _FUND_STATE["last_notify"]) > _FUND_NOTIFY_COOL
    if rounds >= 3 and notify_ok:
        _FUND_STATE["last_notify"] = now
        await _silent_admin_notify(
            bot,
            f"⚡ <b>Agent-3 Price Recovery Active</b>\n"
            f"Stale pairs: <b>{stale_count}/{total}</b> ({100*stale_ratio:.0f}%)\n"
            f"Broker fresh: {broker_fresh} | YF-only: {yf_only} | No data: {no_data}\n"
            f"Reconnect #{_FUND_STATE['total_reconnects']} triggered\n"
            f"<i>Streams force-restarted. Fixing…</i>",
        )


async def _fund_force_reconnect(bot, broker_fresh: int, stale_count: int, total: int):
    """Cancel and restart QX + PO asyncio stream tasks to force a fresh connection."""
    log.info("[Agent-3/PriceFund] 🔄 Force-reconnecting OTC streams …")

    restarted = []

    # Restart any registered stream tasks
    for task_name in ("otc-qx-stream", "otc-po-stream"):
        old_task = _STREAM_TASKS.get(task_name)
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(old_task), timeout=2.0)
            except Exception:
                pass
            restarted.append(task_name)
            log.info("[Agent-3/PriceFund] Cancelled task: %s", task_name)

    # Find any matching tasks in the running loop (catches tasks we didn't register)
    for task in list(asyncio.all_tasks()):
        name = task.get_name()
        if name in ("otc-qx-stream", "otc-po-stream") and name not in restarted:
            if not task.done():
                task.cancel()
                restarted.append(name)
                log.info("[Agent-3/PriceFund] Cancelled task (loop scan): %s", name)

    # Restart fresh stream loops from otc_price_service
    try:
        from otc_price_service import _run_qx_loop, _run_po_loop
        if "otc-qx-stream" in restarted or not any(
            t.get_name() == "otc-qx-stream" and not t.done()
            for t in asyncio.all_tasks()
        ):
            new_qx = asyncio.create_task(_run_qx_loop(), name="otc-qx-stream")
            _STREAM_TASKS["otc-qx-stream"] = new_qx
            log.info("[Agent-3/PriceFund] ▶ QX stream task restarted")

        if "otc-po-stream" in restarted or not any(
            t.get_name() == "otc-po-stream" and not t.done()
            for t in asyncio.all_tasks()
        ):
            new_po = asyncio.create_task(_run_po_loop(), name="otc-po-stream")
            _STREAM_TASKS["otc-po-stream"] = new_po
            log.info("[Agent-3/PriceFund] ▶ PO stream task restarted")

        # Also restart the PO candle sync and yf fallback if they died
        for task_name, coro_fn in (
            ("otc-po-candle-sync", "otc_price_service._sync_from_po_candles"),
        ):
            alive = any(
                t.get_name() == task_name and not t.done()
                for t in asyncio.all_tasks()
            )
            if not alive:
                from otc_price_service import _sync_from_po_candles
                asyncio.create_task(_sync_from_po_candles(), name=task_name)
                log.info("[Agent-3/PriceFund] ▶ %s restarted", task_name)

    except Exception as exc:
        log.warning("[Agent-3/PriceFund] stream restart error: %s", exc)

    log.info(
        "[Agent-3/PriceFund] Reconnect done. broker_fresh=%d/%d pairs before restart.",
        broker_fresh, total,
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE AI ADVISOR — Premium signal quality analysis
# ══════════════════════════════════════════════════════════════════════════════

async def _claude_advisor(bot):
    """Uses Claude API to silently analyse signal quality and apply corrections."""
    if not CLAUDE_API_KEY:
        log.info("[ClaudeAdvisor] No ANTHROPIC_API_KEY — running rule-based fallback advisor")
        await _rule_based_advisor(bot)
        return

    log.info("[ClaudeAdvisor] Claude AI started — analysing every %ds", CLAUDE_EVERY)
    await asyncio.sleep(120)  # warm-up

    while True:
        try:
            await _claude_analysis(bot)
        except Exception as exc:
            log.debug("[ClaudeAdvisor] analysis error: %s", exc)
        await asyncio.sleep(CLAUDE_EVERY)


async def _claude_analysis(bot):
    """Gather per-pair/engine stats, ask Claude to identify weak spots, apply fixes."""
    loop = asyncio.get_event_loop()

    # Collect stats
    stats_2d = db.winrate_stats(days=2)
    total    = stats_2d.get("total", 0)
    if total < 8:
        return   # not enough data

    # Try per-engine breakdown if available
    engine_stats = {}
    try:
        engine_stats = db.winrate_by_engine(days=2)
    except Exception:
        pass

    prompt = (
        "You are a binary trading signal AI analyst. "
        "Based on the following 2-day performance data, identify the 1-2 weakest "
        "signal engines or pairs, and suggest a specific boost_level (0-3) "
        "and pa_threshold_delta (float -1.0 to +3.0) to improve win rate. "
        "Respond ONLY with valid JSON: "
        "{\"boost_level\": int, \"pa_delta\": float, \"reasoning\": \"string\"}\n\n"
        f"Overall 2-day stats: {json.dumps(stats_2d)}\n"
        f"Per-engine stats: {json.dumps(engine_stats)}"
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        response = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
        )
        raw = response.content[0].text.strip()
        # Extract JSON
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return
        advice = json.loads(m.group())
        boost  = int(advice.get("boost_level", db.get_boost_level()))
        pa_d   = float(advice.get("pa_delta", 0.0))
        reason = str(advice.get("reasoning", "Claude analysis"))

        # Apply boost if different from current
        cur_boost = db.get_boost_level()
        if boost != cur_boost:
            db.set_boost_level(boost)
            log.info("[ClaudeAdvisor] boost %d→%d  pa_delta=%.1f  reason=%s",
                     cur_boost, boost, pa_d, reason)
            await _silent_admin_notify(
                bot,
                f"🧠 <b>Claude AI Advisor</b>\n"
                f"Boost: {cur_boost} → <b>{boost}</b>\n"
                f"PA delta: {pa_d:+.1f}\n"
                f"Reason: <i>{reason[:200]}</i>",
            )
    except ImportError:
        pass   # anthropic not installed — rule-based will handle
    except Exception as exc:
        log.debug("[ClaudeAdvisor] Claude call error: %s", exc)


async def _rule_based_advisor(bot):
    """Fallback rule-based advisor when Claude API is unavailable."""
    log.info("[RuleAdvisor] Rule-based advisor started — checking every %ds", CLAUDE_EVERY)
    await asyncio.sleep(180)

    while True:
        try:
            stats = db.winrate_stats(days=2)
            total = stats.get("total", 0)
            wr    = stats.get("win_rate", 100.0)

            if total >= MIN_SAMPLES:
                cur = db.get_boost_level()
                # Fine-grained rule: if 80–85% → boost-1, <80% → boost-2 min
                if wr < 80.0 and cur < 2:
                    db.set_boost_level(2)
                    log.info("[RuleAdvisor] WR %.1f%% < 80%% → boost set to 2", wr)
                elif wr < WR_TARGET and cur < 1:
                    db.set_boost_level(1)
                    log.info("[RuleAdvisor] WR %.1f%% < 85%% → boost set to 1", wr)
                elif wr >= WR_RELAX and cur > 0:
                    db.set_boost_level(cur - 1)
                    log.info("[RuleAdvisor] WR %.1f%% ≥ 92%% → relaxed boost to %d", wr, cur - 1)
        except Exception as exc:
            log.debug("[RuleAdvisor] error: %s", exc)
        await asyncio.sleep(CLAUDE_EVERY)


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL LOCK VERIFIER — blocks any update text from entering signal output
# ══════════════════════════════════════════════════════════════════════════════

_BANNED_PHRASES = [
    "update", "fixed", "bug fix", "patch", "version", "changelog",
    "auto-improve", "self-improve", "boost level", "ai adjusted",
    "winrate guardian", "agent-1", "agent-2", "claude",
    "threshold", "system notice", "system update",
]

def verify_signal_text(text: str) -> str:
    """Strip any AI/agent status lines from signal text. Admin-locked."""
    if not text:
        return text
    lines = text.split("\n")
    clean = []
    for line in lines:
        lower = line.lower()
        if any(ph in lower for ph in _BANNED_PHRASES):
            log.warning("[SignalLock] Blocked forbidden phrase in signal text: %r", line[:60])
            continue
        clean.append(line)
    return "\n".join(clean)


# ══════════════════════════════════════════════════════════════════════════════
# UTIL — silent admin notification (auto-deletes after 30s)
# ══════════════════════════════════════════════════════════════════════════════

async def _silent_admin_notify(bot, text: str):
    """Send a private admin-only status message and auto-delete it after 5 s."""
    try:
        admin_id = db.get_admin_id()
        if not admin_id:
            return
        msg = await bot.send_message(int(admin_id), text, parse_mode="HTML")
        await asyncio.sleep(5)
        try:
            await bot.delete_message(int(admin_id), msg.message_id)
        except Exception:
            pass
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — called from bot.py
# ══════════════════════════════════════════════════════════════════════════════

async def run_ai_guardian(bot):
    """Start all four silent agents as background asyncio tasks."""
    log.info("[AIGuardian] Launching 4 agents: WinRateAgent, SSIDGuard, PriceFundAgent, ClaudeAdvisor")
    asyncio.create_task(_winrate_agent(bot),      name="ai_winrate_agent")
    asyncio.create_task(_ssid_guard(bot),         name="ai_ssid_guard")
    asyncio.create_task(_price_fund_agent(bot),   name="ai_price_fund_agent")
    asyncio.create_task(_claude_advisor(bot),     name="ai_claude_advisor")
    log.info("[AIGuardian] All agents started — running silently in background")
