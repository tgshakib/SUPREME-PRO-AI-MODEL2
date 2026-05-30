"""Win Rate Guardian — 2-day rolling win rate monitor with auto-boost.

Every 10 minutes this background task:
  1. Reads 2-day signal outcomes from the database
  2. If win rate < 91% → raises boost level (max 3) → stricter AI thresholds
  3. If win rate > 95% → lowers boost level (toward 0) → relaxed thresholds
  4. Notifies admin when boost level changes
  5. Every 2 days: purges old signal_outcomes and resets the cycle (fresh data)

BOOST LEVELS
───────────
  0 = NORMAL   — base thresholds (win rate OK ≥ 91%)
  1 = BOOST-1  — PA gate +1.0, OTC votes +1, confidence cap -5%
  2 = BOOST-2  — PA gate +2.0, OTC votes +2, confidence cap -10%
  3 = MAX POWER— PA gate +3.5, OTC votes +3, elite-only signals
"""

import asyncio
import logging
import time
from datetime import datetime

import database as db

log = logging.getLogger(__name__)

WIN_RATE_TARGET  = 85.0    # below this → tighten AI (user threshold: 85%)
WIN_RATE_RELAX   = 92.0    # above this → relax AI
MIN_SIGNALS      = 4       # need at least 4 completed signals before adjusting
CHECK_INTERVAL   = 600     # every 10 minutes
CYCLE_DAYS       = 2       # rolling window: purge data older than this many days
_LAST_PURGE_KEY  = "winrate_last_purge_ts"

BOOST_LABELS = {
    0: "🟢 NORMAL MODE",
    1: "🟡 BOOST-1  (Moderate filter upgrade)",
    2: "🟠 BOOST-2  (Strong filter upgrade)",
    3: "🔴 MAX POWER (Elite-only signals)",
}

# Threshold adjustments applied per boost level
BOOST_ADJUSTMENTS = {
    0: {"pa_delta": 0.0, "otc_delta": 0, "conf_cap": 99, "extra_votes": 0},
    1: {"pa_delta": 1.0, "otc_delta": 1, "conf_cap": 94, "extra_votes": 1},
    2: {"pa_delta": 2.0, "otc_delta": 2, "conf_cap": 89, "extra_votes": 2},
    3: {"pa_delta": 3.5, "otc_delta": 3, "conf_cap": 84, "extra_votes": 3},
}


def get_current_boost() -> int:
    """Return current boost level (0-3)."""
    return db.get_boost_level()


def get_boost_adjustments() -> dict:
    """Return the threshold deltas for the current boost level."""
    return BOOST_ADJUSTMENTS[get_current_boost()]


def winrate_summary_text() -> str:
    """Return a short one-line summary of current win-rate + boost status."""
    stats_2d = db.winrate_stats(days=2)
    stats_1d = db.winrate_stats(days=1)
    level = get_current_boost()
    label = BOOST_LABELS[level]
    wr2   = stats_2d["win_rate"]
    wr1   = stats_1d["win_rate"]
    tot2  = stats_2d["total"]
    return (
        f"📊 Today: {wr1:.0f}% ({stats_1d['wins']}W/{stats_1d['losses']}L)  "
        f"│  2-Day: {wr2:.0f}% ({tot2} signals)  │  AI: {label}"
    )


def _should_purge() -> bool:
    """Return True if 2 days have elapsed since the last data purge."""
    try:
        last_str = db.get_setting(_LAST_PURGE_KEY)
        if not last_str:
            return True
        elapsed = time.time() - float(last_str)
        return elapsed >= CYCLE_DAYS * 86400
    except Exception:
        return True


async def _run_cycle_purge(bot):
    """Delete signal data older than CYCLE_DAYS and notify admin."""
    try:
        deleted = db.purge_old_winrate_data(days=CYCLE_DAYS)
        db.set_setting(_LAST_PURGE_KEY, str(time.time()))
        log.info("[WinRateGuardian] 2-day cycle purge: removed %d old records. Fresh collection started.", deleted)
        admin_id = db.get_admin_id()
        if admin_id:
            try:
                msg = await bot.send_message(
                    int(admin_id),
                    "🔄 <b>WIN RATE CYCLE RESET</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Removed <b>{deleted}</b> old signal records.\n"
                    "📊 Fresh 2-day data collection has started.\n"
                    "<i>AI engine recalibrating from new data.</i>",
                    parse_mode="HTML",
                )
                await asyncio.sleep(15)
                try:
                    await bot.delete_message(int(admin_id), msg.message_id)
                except Exception:
                    pass
            except Exception:
                pass
    except Exception as exc:
        log.warning("[WinRateGuardian] Cycle purge failed: %s", exc)


async def run_winrate_guardian(bot):
    """Long-running background task — starts with a 30 s warm-up delay."""
    log.info("[WinRateGuardian] Started")
    await asyncio.sleep(30)
    while True:
        try:
            if _should_purge():
                await _run_cycle_purge(bot)
            await _check_and_adjust(bot)
        except Exception as exc:
            log.warning("[WinRateGuardian] Error during check: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL)


async def _check_and_adjust(bot):
    stats = db.winrate_stats(days=2)
    total    = stats["total"]
    win_rate = stats["win_rate"]

    if total < MIN_SIGNALS:
        log.debug("[WinRateGuardian] Only %d completed signals — skipping", total)
        return

    current = db.get_boost_level()
    new     = current

    if win_rate < WIN_RATE_TARGET and current < 3:
        new = current + 1
        log.info("[WinRateGuardian] Win rate %.1f%% < %.0f%% → boost %d → %d",
                 win_rate, WIN_RATE_TARGET, current, new)
    elif win_rate >= WIN_RATE_RELAX and current > 0:
        new = current - 1
        log.info("[WinRateGuardian] Win rate %.1f%% ≥ %.0f%% → relax %d → %d",
                 win_rate, WIN_RATE_RELAX, current, new)

    if new != current:
        db.set_boost_level(new)
        await _notify_admin(bot, win_rate, total, stats["wins"],
                            stats["losses"], current, new)


async def _notify_admin(bot, win_rate, total, wins, losses, old_lvl, new_lvl):
    admin_id = db.get_admin_id()
    if not admin_id:
        return
    try:
        direction = "UPGRADED ⬆️" if new_lvl > old_lvl else "RELAXED ⬇️"
        bar = _progress_bar(win_rate, 100)
        text = (
            f"🤖 <b>AI AUTO-BOOST {direction}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📉 2-Day Win Rate: <b>{win_rate:.1f}%</b>\n"
            f"{bar}\n"
            f"✅ Wins: {wins}  ❌ Losses: {losses}  Total: {total}\n\n"
            f"🎯 Target: <b>91%+</b>\n\n"
            f"Old mode: {BOOST_LABELS[old_lvl]}\n"
            f"New mode: <b>{BOOST_LABELS[new_lvl]}</b>\n\n"
            f"<i>AI thresholds auto-adjusted — signal quality improving.</i>"
        )
        await bot.send_message(int(admin_id), text)
    except Exception as exc:
        log.warning("[WinRateGuardian] Admin notify failed: %s", exc)


def _progress_bar(value: float, total: float, width: int = 10) -> str:
    """Return a Unicode block progress bar string."""
    pct  = max(0.0, min(1.0, value / max(total, 1)))
    filled = int(round(pct * width))
    bar  = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {value:.1f}%"
