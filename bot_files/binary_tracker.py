"""BINARY TRADE ENTRY TRACKER — SUPREME PRO AI BOT
===================================================
Tracks precise execute-time, non-martingale (fresh candle) and martingale
outcomes for every binary signal.

HOW IT WORKS
────────────
Signal fires at e.g. 12:30:30  (or :40 or :45 within 12:30)
  → User enters at 12:31:00     NON-MARTINGALE  (next fresh 1-minute candle)
  → If loss → enter at 12:32:00 MARTINGALE      (the following candle)

Tracking rules:
  • Non-MG WIN                            → record WIN
  • Non-MG LOSS + MG WIN                  → record MARTINGALE WIN
  • Non-MG LOSS + MG LOSS                 → record LOSS (back-to-back)
  • Alert message includes exact candle times

Back-to-back loss/win counter (Live + OTC separate):
  • 2 consecutive losses → ALERT "⚠️ 2 losses in a row"
  • 3+ consecutive losses → ALERT "🚨 CRITICAL: 3+ losses — STOP TRADING NOW"
  • 5+ consecutive wins   → ALERT "🎉 5 WIN STREAK — keep momentum!"

Public API
──────────
    record_signal_entry(user_id, pair, direction, execute_time_str, market) -> int (trade_id)
    record_non_mg_result(trade_id, result: 'win'|'loss') -> None
    record_mg_result(trade_id, result: 'win'|'loss') -> None
    get_trade_status(trade_id) -> dict
    get_streak_alert(user_id, market) -> str | None
    format_entry_time_instruction(execute_time_str, tf_minutes) -> str
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import database as db


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _next_candle_time(execute_time_str: str, tf_minutes: int = 1) -> tuple[str, str]:
    """
    Given a signal execute time like '12:30:30', '12:30:40', '12:30:45'
    or '12:30', compute:
      • non_mg_entry  = start of the next candle  (e.g. '12:31:00')
      • mg_entry      = start of the candle after (e.g. '12:32:00')

    tf_minutes: candle size in minutes (default 1 for binary)
    """
    try:
        parts = execute_time_str.replace("UTC", "").strip().split(":")
        hour   = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0

        base = datetime(2000, 1, 1, hour, minute, second)
        non_mg_dt = base.replace(second=0) + timedelta(minutes=tf_minutes)
        mg_dt     = non_mg_dt + timedelta(minutes=tf_minutes)

        non_mg_str = non_mg_dt.strftime("%H:%M")
        mg_str     = mg_dt.strftime("%H:%M")
        return non_mg_str, mg_str
    except Exception:
        return "next 1-min candle", "+2 candle"


def format_entry_time_instruction(execute_time_str: str, tf_minutes: int = 1) -> str:
    """
    Returns a formatted instruction string for signal cards:
    e.g. '⏱️ Execute: 12:30:45  →  Enter: 12:31:00 (new candle)'
    """
    try:
        non_mg, mg = _next_candle_time(execute_time_str, tf_minutes)
        return (
            f"⏱️ <b>EXECUTE:</b> <code>{execute_time_str}</code>\n"
            f"🕐 <b>NON-MG ENTRY:</b> <code>{non_mg}:00</code> <i>(new fresh candle)</i>\n"
            f"🔁 <b>MG ENTRY:</b> <code>{mg}:00</code> <i>(if needed)</i>"
        )
    except Exception:
        return f"⏱️ Execute now → Enter on next fresh candle"


def record_signal_entry(
    user_id: int,
    pair: str,
    direction: str,
    execute_time_str: str,
    market: str = "OTC",
    tf_minutes: int = 1,
) -> int:
    """
    Record a new binary signal entry. Returns the trade_id (row id).
    """
    non_mg_time, mg_time = _next_candle_time(execute_time_str, tf_minutes)
    try:
        with db.get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO binary_trade_track
                   (user_id, pair, direction, market, execute_time,
                    non_mg_time, mg_time, status, created_at)
                   VALUES (?,?,?,?,?,?,?,'pending',?)""",
                (user_id, pair, direction, market, execute_time_str,
                 non_mg_time, mg_time,
                 datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            )
            return cur.lastrowid or -1
    except Exception as e:
        print(f"[binary_tracker] record_signal_entry error: {e}")
        return -1


def record_non_mg_result(trade_id: int, result: str) -> None:
    """result: 'win' or 'loss'"""
    if trade_id <= 0:
        return
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM binary_trade_track WHERE id=?", (trade_id,)
            ).fetchone()
            if not row:
                return
            if result == "win":
                final = "WIN"
                status = "closed"
            else:
                final = None
                status = "mg_pending"
            conn.execute(
                "UPDATE binary_trade_track SET non_mg_result=?, final_result=?, "
                "status=? WHERE id=?",
                (result, final, status, trade_id),
            )
            if result == "win":
                _update_streak(int(row["user_id"]), str(row["market"]), "win")
    except Exception as e:
        print(f"[binary_tracker] record_non_mg_result error: {e}")


def record_mg_result(trade_id: int, result: str) -> None:
    """result: 'win' or 'loss'. Only call after non_mg was a loss."""
    if trade_id <= 0:
        return
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM binary_trade_track WHERE id=?", (trade_id,)
            ).fetchone()
            if not row:
                return
            final = "MG_WIN" if result == "win" else "LOSS"
            conn.execute(
                "UPDATE binary_trade_track SET mg_result=?, final_result=?, "
                "status='closed' WHERE id=?",
                (result, final, trade_id),
            )
            streak_outcome = "win" if result == "win" else "loss"
            _update_streak(int(row["user_id"]), str(row["market"]), streak_outcome)
    except Exception as e:
        print(f"[binary_tracker] record_mg_result error: {e}")


def get_trade_status(trade_id: int) -> Optional[dict]:
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM binary_trade_track WHERE id=?", (trade_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _update_streak(user_id: int, market: str, outcome: str) -> None:
    """Update the consecutive win/loss counter for this user+market."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM binary_streak WHERE user_id=? AND market=?",
                (user_id, market),
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO binary_streak (user_id, market, date, "
                    "consec_wins, consec_losses, total_wins, total_losses) "
                    "VALUES (?,?,?,0,0,0,0)",
                    (user_id, market, today),
                )
                row = conn.execute(
                    "SELECT * FROM binary_streak WHERE user_id=? AND market=?",
                    (user_id, market),
                ).fetchone()

            cw = int(row["consec_wins"])
            cl = int(row["consec_losses"])
            tw = int(row["total_wins"])
            tl = int(row["total_losses"])
            date = str(row["date"])
            if date != today:
                cw = 0; cl = 0

            if outcome == "win":
                cw += 1; cl = 0; tw += 1
            else:
                cl += 1; cw = 0; tl += 1

            conn.execute(
                "UPDATE binary_streak SET consec_wins=?, consec_losses=?, "
                "total_wins=?, total_losses=?, date=? "
                "WHERE user_id=? AND market=?",
                (cw, cl, tw, tl, today, user_id, market),
            )
    except Exception as e:
        print(f"[binary_tracker] _update_streak error: {e}")


def get_streak_alert(user_id: int, market: str = "OTC") -> Optional[str]:
    """
    Returns an alert message string if the user has a notable streak,
    or None if nothing to alert.
    """
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM binary_streak WHERE user_id=? AND market=?",
                (user_id, market),
            ).fetchone()
            if not row:
                return None
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if str(row["date"]) != today:
                return None
            cl = int(row["consec_losses"])
            cw = int(row["consec_wins"])
            tw = int(row["total_wins"])
            tl = int(row["total_losses"])
            total = tw + tl
            wr_pct = int(100 * tw / total) if total > 0 else 0

            if cl >= 3:
                return (
                    f"🚨 <b>CRITICAL ALERT — {cl} CONSECUTIVE LOSSES</b>\n"
                    f"⛔ <b>STOP TRADING IMMEDIATELY</b>\n"
                    f"📊 Today: {tw}W / {tl}L ({wr_pct}% win rate)\n"
                    f"💡 <i>Wait for the next high-confidence signal. "
                    f"Emotional trading causes more losses.</i>"
                )
            elif cl == 2:
                return (
                    f"⚠️ <b>WARNING — 2 Losses in a Row ({market})</b>\n"
                    f"📊 Today: {tw}W / {tl}L ({wr_pct}% win rate)\n"
                    f"🧠 <i>Take a breath. Wait for a SUPREME or ELITE grade signal only.</i>"
                )
            elif cw >= 5:
                return (
                    f"🎉 <b>{cw} WIN STREAK! Keep the momentum!</b>\n"
                    f"📊 Today: {tw}W / {tl}L ({wr_pct}% win rate)\n"
                    f"⚡ <i>Engine is in peak form — stay disciplined, same stake size.</i>"
                )
            elif cw >= 3:
                return (
                    f"✅ <b>{cw} consecutive wins — great session!</b>\n"
                    f"📊 Today: {tw}W / {tl}L ({wr_pct}% win rate)"
                )
        return None
    except Exception as e:
        print(f"[binary_tracker] get_streak_alert error: {e}")
        return None


def get_daily_summary(user_id: int, market: str = "OTC") -> Optional[dict]:
    """Returns today's win/loss summary for a user."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM binary_streak WHERE user_id=? AND market=?",
                (user_id, market),
            ).fetchone()
            if not row or str(row["date"]) != today:
                return {"wins": 0, "losses": 0, "win_rate": 0, "streak": 0}
            tw = int(row["total_wins"])
            tl = int(row["total_losses"])
            total = tw + tl
            return {
                "wins":     tw,
                "losses":   tl,
                "win_rate": int(100 * tw / total) if total > 0 else 0,
                "streak":   int(row["consec_wins"]) - int(row["consec_losses"]),
            }
    except Exception:
        return None


def ensure_tables() -> None:
    """Create binary tracking tables if they don't exist yet. Called from init_db."""
    try:
        with db.get_conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS binary_trade_track (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                pair            TEXT    NOT NULL,
                direction       TEXT    NOT NULL,
                market          TEXT    DEFAULT 'OTC',
                execute_time    TEXT,
                non_mg_time     TEXT,
                mg_time         TEXT,
                non_mg_result   TEXT,
                mg_result       TEXT,
                final_result    TEXT,
                status          TEXT    DEFAULT 'pending',
                created_at      TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS binary_streak (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                market          TEXT    NOT NULL DEFAULT 'OTC',
                date            TEXT    NOT NULL,
                consec_wins     INTEGER DEFAULT 0,
                consec_losses   INTEGER DEFAULT 0,
                total_wins      INTEGER DEFAULT 0,
                total_losses    INTEGER DEFAULT 0,
                UNIQUE(user_id, market)
            );
            """)
    except Exception as e:
        print(f"[binary_tracker] ensure_tables error: {e}")
