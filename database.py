import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

DB_PATH = "trading_bot.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            verified     INTEGER DEFAULT 0,
            tz           TEXT,
            joined_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS access (
            user_id      INTEGER PRIMARY KEY,
            access_type  TEXT NOT NULL,         -- 'temporary' | 'lifetime'
            package_id   TEXT,
            package_label TEXT,
            granted_at   TEXT DEFAULT (datetime('now')),
            expires_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS payments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            username      TEXT,
            package_id    TEXT NOT NULL,
            package_label TEXT NOT NULL,
            amount        INTEGER NOT NULL,
            days          INTEGER NOT NULL,
            screenshot_file_id TEXT,
            status        TEXT DEFAULT 'awaiting_screenshot',
            submitted_at  TEXT,
            reviewed_at   TEXT,
            pending_msg_id INTEGER,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS signal_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            day       TEXT NOT NULL,
            pair      TEXT,
            tf        TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS forex_setup (
            user_id        INTEGER PRIMARY KEY,
            tf             TEXT,
            pairs          TEXT,         -- comma-separated indices
            max_tp         INTEGER,
            status         TEXT DEFAULT 'inactive',  -- inactive | active | stopped | exhausted
            sent_today     INTEGER DEFAULT 0,
            day            TEXT,
            last_signal_at TEXT,
            updated_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS forex_signal (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            chat_id     INTEGER NOT NULL,
            message_id  INTEGER,
            pair        TEXT,
            direction   TEXT,
            entry       REAL,
            tp_prices   TEXT,           -- comma-separated
            sl_price    REAL,
            max_tp      INTEGER,
            tps_hit     INTEGER DEFAULT 0,
            outcome     TEXT,           -- NULL | 'tp' | 'sl' | 'partial'
            im_in       INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'open',   -- open | closed
            created_at  TEXT DEFAULT (datetime('now')),
            closed_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS funded_pass (
            user_id          INTEGER PRIMARY KEY,
            account_size     INTEGER NOT NULL,
            profit_pct       REAL NOT NULL,
            daily_loss_pct   REAL NOT NULL,
            max_dd_pct       REAL NOT NULL,
            tf               TEXT,
            pair             TEXT,
            equity_pct       REAL DEFAULT 0,    -- running cumulative P/L %
            daily_pct        REAL DEFAULT 0,    -- today's P/L %
            day              TEXT,
            status           TEXT DEFAULT 'active',  -- active | passed | failed
            last_signal_at   TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        );

        -- ── Self-Improve Engine: per-signal outcome tracker ─────────────
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            pair            TEXT    NOT NULL,
            market          TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            timeframe       TEXT,
            engine          TEXT,
            confidence      INTEGER DEFAULT 0,
            weighted_score  REAL    DEFAULT 0,
            entry_price     REAL,
            expiry_minutes  INTEGER DEFAULT 5,
            atr_pct         REAL    DEFAULT 0,
            vol_mode        TEXT    DEFAULT 'normal',
            timestamp       INTEGER NOT NULL,
            outcome         TEXT    DEFAULT NULL,
            outcome_price   REAL    DEFAULT NULL,
            outcome_ts      INTEGER DEFAULT NULL,
            auto_checked    INTEGER DEFAULT 0
        );

        -- ── Self-Improve Engine: per-pair/engine win-rate counters ──────
        CREATE TABLE IF NOT EXISTS ai_learning (
            pair            TEXT    NOT NULL,
            engine          TEXT    NOT NULL,
            win_count       INTEGER DEFAULT 0,
            loss_count      INTEGER DEFAULT 0,
            total_signals   INTEGER DEFAULT 0,
            updated_at      TEXT    DEFAULT (datetime('now')),
            PRIMARY KEY (pair, engine)
        );

        -- ── Self-Improve Engine: retune audit log ────────────────────────
        CREATE TABLE IF NOT EXISTS ai_retune_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at           TEXT    DEFAULT (datetime('now')),
            pairs_analyzed   INTEGER DEFAULT 0,
            engines_adjusted INTEGER DEFAULT 0,
            summary          TEXT
        );

        -- Tracks every Telegram message the admin's MAILING broadcast posted
        -- so we can auto-delete it from each user's chat after 72 hours.
        CREATE TABLE IF NOT EXISTS mailing_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            message_id   INTEGER NOT NULL,
            sent_at      TEXT DEFAULT (datetime('now')),
            deleted      INTEGER DEFAULT 0
        );

        -- ── Binary Trading Daily Alert System ────────────────────────────
        -- Tracks per-user daily streak state for smart loss/win alerts.
        -- All counters reset at UTC midnight on first interaction.
        CREATE TABLE IF NOT EXISTS binary_daily_alert (
            user_id                   INTEGER PRIMARY KEY,
            date                      TEXT    NOT NULL,
            consecutive_losses        INTEGER DEFAULT 0,
            consecutive_wins          INTEGER DEFAULT 0,
            loss_alert_count          INTEGER DEFAULT 0,
            win_alert_sent            INTEGER DEFAULT 0,
            market_type               TEXT    DEFAULT 'OTC',
            last_action_after_alert1  TEXT
        );

        -- ── Auto Trading Demo State ───────────────────────────────────────
        -- Free users get 3 demo auto trades total (max 1/day), reset after 30 days.
        CREATE TABLE IF NOT EXISTS auto_trading_demo (
            user_id          INTEGER PRIMARY KEY,
            total_used       INTEGER DEFAULT 0,
            today_used       INTEGER DEFAULT 0,
            day              TEXT,
            first_trade_at   TEXT,
            last_trade_at    TEXT
        );

        -- ── Auto Trading Premium Settings ─────────────────────────────────
        -- Per-user premium auto trading configuration and state.
        CREATE TABLE IF NOT EXISTS auto_trading_settings (
            user_id              INTEGER PRIMARY KEY,
            broker_connected     INTEGER DEFAULT 0,
            broker_name          TEXT    DEFAULT '',
            risk_mode            TEXT    DEFAULT 'moderate',
            auto_trading_on      INTEGER DEFAULT 0,
            drawdown_threshold   REAL    DEFAULT 5.0,
            loss_day_count       INTEGER DEFAULT 0,
            loss_window_start    TEXT,
            review_required      INTEGER DEFAULT 0,
            strategy_paused      INTEGER DEFAULT 0,
            updated_at           TEXT    DEFAULT (datetime('now'))
        );

        -- ── Auto Trading Trade History (today) ────────────────────────────
        -- Stores every closed trade for the current day per user.
        -- trade_type: 'forex' | 'binary' | 'demo_forex' | 'demo_binary'
        -- result:     'profit' | 'loss' | 'breakeven' | 'win' | 'refund'
        -- close_reason: 'sl_hit' | 'tp_hit' | 'manual' | 'bot_close'
        --               | 'timeout' | 'pending_verify' | 'broker_close'
        CREATE TABLE IF NOT EXISTS at_trade_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            day             TEXT    NOT NULL,
            trade_no        INTEGER NOT NULL DEFAULT 1,
            trade_type      TEXT    NOT NULL,
            pair            TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            lot_size        REAL    DEFAULT 0,
            amount          REAL    DEFAULT 0,
            entry_price     REAL    DEFAULT 0,
            exit_price      REAL    DEFAULT 0,
            sl_price        REAL    DEFAULT 0,
            tp_price        REAL    DEFAULT 0,
            profit_loss     REAL    DEFAULT 0,
            payout          REAL    DEFAULT 0,
            result          TEXT    DEFAULT 'pending',
            close_reason    TEXT    DEFAULT 'pending_verify',
            open_time       TEXT,
            close_time      TEXT    DEFAULT (datetime('now')),
            is_demo         INTEGER DEFAULT 0,
            is_auto         INTEGER DEFAULT 1,
            status          TEXT    DEFAULT 'closed',
            broker_confirmed INTEGER DEFAULT 0,
            duplicate_check TEXT    UNIQUE
        );

        -- ── Auto Trading Trade Archive (past days) ────────────────────────
        -- Today history is moved here at UTC midnight reset.
        CREATE TABLE IF NOT EXISTS at_trade_archive (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            archive_date    TEXT    NOT NULL,
            day             TEXT    NOT NULL,
            trade_no        INTEGER NOT NULL DEFAULT 1,
            trade_type      TEXT    NOT NULL,
            pair            TEXT    NOT NULL,
            direction       TEXT    NOT NULL,
            lot_size        REAL    DEFAULT 0,
            amount          REAL    DEFAULT 0,
            entry_price     REAL    DEFAULT 0,
            exit_price      REAL    DEFAULT 0,
            sl_price        REAL    DEFAULT 0,
            tp_price        REAL    DEFAULT 0,
            profit_loss     REAL    DEFAULT 0,
            payout          REAL    DEFAULT 0,
            result          TEXT    DEFAULT 'pending',
            close_reason    TEXT    DEFAULT 'pending_verify',
            open_time       TEXT,
            close_time      TEXT,
            is_demo         INTEGER DEFAULT 0,
            is_auto         INTEGER DEFAULT 1,
            status          TEXT    DEFAULT 'closed',
            broker_confirmed INTEGER DEFAULT 0
        );
        """)
        # Migration: ensure 'tz' column exists on legacy users tables.
        try:
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "tz" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN tz TEXT")
            if "last_binary_outcome" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN last_binary_outcome TEXT")
        except Exception as _e:
            print(f"⚠️  users migration skipped: {_e}")

        # Migration: forex_signal table — kind (LIMIT/LIVE/HFT) + alert_armed
        try:
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(forex_signal)").fetchall()]
            if "kind" not in cols:
                conn.execute(
                    "ALTER TABLE forex_signal ADD COLUMN kind TEXT DEFAULT 'LIVE'"
                )
            if "alert_armed" not in cols:
                conn.execute(
                    "ALTER TABLE forex_signal ADD COLUMN alert_armed INTEGER "
                    "DEFAULT 0"
                )
            if "session_seq" not in cols:
                conn.execute(
                    "ALTER TABLE forex_signal ADD COLUMN session_seq INTEGER "
                    "DEFAULT 0"
                )
        except Exception as _e:
            print(f"⚠️  forex_signal migration skipped: {_e}")

        # Migration: forex_setup — more_signal_requested gate (1 signal at a
        # time; the next one only fires after the user taps MORE SIGNAL).
        try:
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(forex_setup)").fetchall()]
            if "more_signal_requested" not in cols:
                conn.execute(
                    "ALTER TABLE forex_setup ADD COLUMN more_signal_requested "
                    "INTEGER DEFAULT 1"
                )
            if "gold_king_mode" not in cols:
                conn.execute(
                    "ALTER TABLE forex_setup ADD COLUMN gold_king_mode "
                    "INTEGER DEFAULT 0"
                )
        except Exception as _e:
            print(f"⚠️  forex_setup migration skipped: {_e}")

        env_admin = os.environ.get("ADMIN_ID", "0")
        if env_admin and env_admin != "0":
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('admin_id', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (env_admin,),
            )

        # Migration: auto_trading_settings — engine_state column
        try:
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(auto_trading_settings)").fetchall()]
            if "engine_state" not in cols:
                conn.execute(
                    "ALTER TABLE auto_trading_settings "
                    "ADD COLUMN engine_state TEXT DEFAULT 'stopped'"
                )
            if "last_signal_time" not in cols:
                conn.execute(
                    "ALTER TABLE auto_trading_settings "
                    "ADD COLUMN last_signal_time TEXT"
                )
            if "last_execution_time" not in cols:
                conn.execute(
                    "ALTER TABLE auto_trading_settings "
                    "ADD COLUMN last_execution_time TEXT"
                )
            if "error_state" not in cols:
                conn.execute(
                    "ALTER TABLE auto_trading_settings "
                    "ADD COLUMN error_state TEXT"
                )
            if "drawdown_hit_today" not in cols:
                conn.execute(
                    "ALTER TABLE auto_trading_settings "
                    "ADD COLUMN drawdown_hit_today INTEGER DEFAULT 0"
                )
        except Exception as _e:
            print(f"⚠️  auto_trading_settings migration skipped: {_e}")

        # Migration: at_trade_history — enhanced fields (V2)
        try:
            cols = [r["name"] for r in
                    conn.execute("PRAGMA table_info(at_trade_history)").fetchall()]
            for col, defn in [
                ("trd_id",          "TEXT DEFAULT ''"),
                ("strategy_name",   "TEXT DEFAULT ''"),
                ("duration_mins",   "REAL DEFAULT 0"),
                ("balance_before",  "REAL DEFAULT 0"),
                ("balance_after",   "REAL DEFAULT 0"),
                ("drawdown_impact", "REAL DEFAULT 0"),
                ("risk_pct",        "REAL DEFAULT 1.0"),
                ("notes",           "TEXT DEFAULT ''"),
            ]:
                if col not in cols:
                    conn.execute(
                        f"ALTER TABLE at_trade_history ADD COLUMN {col} {defn}"
                    )
        except Exception as _e:
            print(f"⚠️  at_trade_history migration skipped: {_e}")

        # ── Binary Trade Entry Tracker tables ────────────────────────────
        try:
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
        except Exception as _e:
            print(f"⚠️  binary_track migration skipped: {_e}")

        # ── Referral System Tables ────────────────────────────────────────
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS referral_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                token       TEXT    NOT NULL UNIQUE,
                created_at  TEXT    DEFAULT (datetime('now')),
                expires_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referral_uses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token       TEXT    NOT NULL,
                referrer_id INTEGER NOT NULL,
                new_user_id INTEGER NOT NULL UNIQUE,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS referral_rewards (
                user_id      INTEGER PRIMARY KEY,
                bonus_binary INTEGER DEFAULT 0,
                bonus_forex  INTEGER DEFAULT 0,
                total_refs   INTEGER DEFAULT 0,
                last_updated TEXT    DEFAULT (datetime('now')),
                expires_at   TEXT
            );
            """)
        except Exception as _e:
            print(f"⚠️  referral migration skipped: {_e}")

    print("✅ Database initialized.")


# ── Settings ──────────────────────────────────────────────
def get_admin_id() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='admin_id'").fetchone()
        if row:
            return int(row["value"])
    return int(os.environ.get("ADMIN_ID", "0"))


def set_admin_id(new_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('admin_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(new_id),),
        )


# ── Users ─────────────────────────────────────────────────
def upsert_user(user_id: int, username: Optional[str], full_name: Optional[str]):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users(user_id, username, full_name) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username=excluded.username, full_name=excluded.full_name",
            (user_id, username, full_name),
        )


def get_user(user_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def set_user_tz(user_id: int, tz_name: str):
    """Persist the user's IANA timezone (e.g. 'Asia/Dhaka')."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET tz=? WHERE user_id=?", (tz_name, user_id))


def get_user_tz(user_id: int) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT tz FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row and row["tz"]:
            return row["tz"]
    return None


def get_user_by_username(username: str) -> Optional[Dict]:
    """Look up a user by @username (case-insensitive, leading @ stripped)."""
    if not username:
        return None
    uname = username.lstrip("@").strip().lower()
    if not uname:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username)=?",
            (uname,),
        ).fetchone()
        return dict(row) if row else None


def all_user_ids() -> List[int]:
    with get_conn() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]


def set_verified(user_id: int, value: int = 1):
    with get_conn() as conn:
        conn.execute("UPDATE users SET verified=? WHERE user_id=?", (value, user_id))


def is_verified(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and u.get("verified"))


# ── Active message tracking (vanish-on-navigate) ──────────
def set_active_msg(user_id: int, msg_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"active_msg:{user_id}", str(msg_id)),
        )


def get_active_msg(user_id: int) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"active_msg:{user_id}",),
        ).fetchone()
        return int(row["value"]) if row else None


def clear_active_msg(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (f"active_msg:{user_id}",))


# ── Pinned 'Payment Received' card tracking (per user) ────
# We store the pinned message_id so that when the user's access expires the
# expiry watcher can unpin + delete the welcome card automatically.
def set_pinned_payment_msg(user_id: int, msg_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"pinned_payment:{user_id}", str(msg_id)),
        )


def get_pinned_payment_msg(user_id: int) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (f"pinned_payment:{user_id}",),
        ).fetchone()
        return int(row["value"]) if row else None


def clear_pinned_payment_msg(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key=?",
                     (f"pinned_payment:{user_id}",))


# ── Recent forex signal message ids (for WORKPLACE chat-wipe) ─
def list_user_forex_signal_messages(user_id: int, limit: int = 50):
    """Return [(chat_id, message_id, signal_id), ...] of the user's most
    recent forex signal cards that we still have a message_id for."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, message_id FROM forex_signal "
            "WHERE user_id=? AND message_id IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [(r["chat_id"], r["message_id"], r["id"]) for r in rows]


def list_closed_forex_signal_messages(user_id: int, limit: int = 50):
    """Return [(chat_id, message_id, signal_id), ...] for the user's
    CLOSED (TP/SL/partial) forex signal cards we still have a message_id
    for. Used by the engine to wipe stale tracking text from the chat
    before posting a fresh signal."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, message_id FROM forex_signal "
            "WHERE user_id=? AND status='closed' AND message_id IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [(r["chat_id"], r["message_id"], r["id"]) for r in rows]


def clear_forex_signal_message_ids(signal_ids: list[int]):
    """Null-out message_id on signals we've already deleted from chat so we
    don't try to delete them twice."""
    if not signal_ids:
        return
    placeholders = ",".join("?" for _ in signal_ids)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE forex_signal SET message_id=NULL WHERE id IN ({placeholders})",
            signal_ids,
        )


# ── Access ────────────────────────────────────────────────
def grant_access(user_id: int, access_type: str, days: int,
                 package_id: str = "", package_label: str = ""):
    expires_at = None
    if access_type == "temporary" and days > 0:
        expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO access(user_id, access_type, package_id, package_label, expires_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "access_type=excluded.access_type, "
            "package_id=excluded.package_id, "
            "package_label=excluded.package_label, "
            "granted_at=datetime('now'), "
            "expires_at=excluded.expires_at",
            (user_id, access_type, package_id, package_label, expires_at),
        )


def grant_access_delta(user_id: int, access_type: str,
                       delta: Optional[timedelta],
                       package_id: str = "",
                       package_label: str = ""):
    """Grant access by arbitrary timedelta (minutes/hours/days/months).
    For lifetime, pass delta=None and access_type='lifetime'."""
    expires_at = None
    if access_type == "temporary" and delta is not None:
        expires_at = (datetime.utcnow() + delta).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO access(user_id, access_type, package_id, package_label, expires_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "access_type=excluded.access_type, "
            "package_id=excluded.package_id, "
            "package_label=excluded.package_label, "
            "granted_at=datetime('now'), "
            "expires_at=excluded.expires_at",
            (user_id, access_type, package_id, package_label, expires_at),
        )


def revoke_access(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM access WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM payments WHERE user_id=?", (user_id,))


def get_access(user_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM access WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def has_active_access(user_id: int) -> bool:
    a = get_access(user_id)
    if not a:
        return False
    if a["access_type"] == "lifetime":
        return True
    if a["expires_at"]:
        try:
            return datetime.fromisoformat(a["expires_at"]) > datetime.utcnow()
        except Exception:
            return False
    return False


def list_access() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.*, u.username, u.full_name FROM access a "
            "LEFT JOIN users u ON u.user_id=a.user_id "
            "ORDER BY a.granted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_active_access() -> List[Dict]:
    """Return only users who currently have active (non-expired) access."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.*, u.username, u.full_name FROM access a "
            "LEFT JOIN users u ON u.user_id=a.user_id "
            "WHERE a.access_type='lifetime' "
            "   OR (a.access_type='temporary' AND a.expires_at > ?) "
            "ORDER BY a.granted_at DESC",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Payments ──────────────────────────────────────────────
def create_payment(user_id: int, username: Optional[str], pkg: Dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO payments(user_id, username, package_id, package_label, amount, days, status) "
            "VALUES(?,?,?,?,?,?, 'awaiting_screenshot')",
            (user_id, username, pkg["id"], pkg["label"], pkg["price"], pkg["days"]),
        )
        return cur.lastrowid


def attach_screenshot(payment_id: int, file_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET screenshot_file_id=?, status='pending', "
            "submitted_at=datetime('now') WHERE id=?",
            (file_id, payment_id),
        )


def set_payment_pending_msg(payment_id: int, msg_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE payments SET pending_msg_id=? WHERE id=?",
                     (msg_id, payment_id))


def get_payment(payment_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        return dict(row) if row else None


def update_payment_status(payment_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET status=?, reviewed_at=datetime('now') WHERE id=?",
            (status, payment_id),
        )


def list_pending_payments() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE status='pending' ORDER BY submitted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Signal usage (binary daily limit) ─────────────────────
def today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def log_signal(user_id: int, pair: str, tf: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO signal_log(user_id, day, pair, tf) VALUES(?,?,?,?)",
            (user_id, today_str(), pair, tf),
        )


def signals_today(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM signal_log WHERE user_id=? AND day=?",
            (user_id, today_str()),
        ).fetchone()
        return int(row["c"]) if row else 0


# ── Forex setup ───────────────────────────────────────────
def upsert_forex_setup(user_id: int, tf: str, pairs: str, max_tp: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO forex_setup(user_id, tf, pairs, max_tp, status, sent_today, day, more_signal_requested, updated_at) "
            "VALUES(?,?,?,?, 'active', 0, ?, 1, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "tf=excluded.tf, pairs=excluded.pairs, max_tp=excluded.max_tp, "
            "status='active', sent_today=0, day=excluded.day, "
            "more_signal_requested=1, "
            "updated_at=datetime('now')",
            (user_id, tf, pairs, max_tp, today_str()),
        )


def set_more_signal_requested(user_id: int, value: bool):
    """Toggle the 'next signal allowed' gate for a user's forex setup."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE forex_setup SET more_signal_requested=? WHERE user_id=?",
            (1 if value else 0, user_id),
        )


def set_gold_king_mode(user_id: int, value: bool):
    """Toggle 🥇 GOLD KING MODE — when ON, the engine only sends
    XAU/USD (Gold) signals and ignores all other pairs in the user's
    setup."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE forex_setup SET gold_king_mode=? WHERE user_id=?",
            (1 if value else 0, user_id),
        )


def get_forex_setup(user_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM forex_setup WHERE user_id=?",
                           (user_id,)).fetchone()
        return dict(row) if row else None


def set_forex_status(user_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE forex_setup SET status=?, updated_at=datetime('now') "
                     "WHERE user_id=?", (status, user_id))


def list_active_forex_setups() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM forex_setup WHERE status='active'"
        ).fetchall()
        return [dict(r) for r in rows]


def increment_forex_sent(user_id: int):
    with get_conn() as conn:
        # Reset counter at day rollover
        row = conn.execute("SELECT day, sent_today FROM forex_setup WHERE user_id=?",
                           (user_id,)).fetchone()
        if not row:
            return
        if row["day"] != today_str():
            conn.execute("UPDATE forex_setup SET sent_today=1, day=?, "
                         "last_signal_at=datetime('now') WHERE user_id=?",
                         (today_str(), user_id))
        else:
            conn.execute("UPDATE forex_setup SET sent_today=sent_today+1, "
                         "last_signal_at=datetime('now') WHERE user_id=?",
                         (user_id,))


# ── Forex signals ─────────────────────────────────────────
def create_forex_signal(user_id: int, chat_id: int, pair: str, direction: str,
                        entry: float, tp_prices: List[float], sl_price: float,
                        max_tp: int, kind: str = "LIVE",
                        session_seq: int = 0) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO forex_signal(user_id, chat_id, pair, direction, "
            "entry, tp_prices, sl_price, max_tp, kind, session_seq) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, chat_id, pair, direction, entry,
             ",".join(f"{p:.5f}" for p in tp_prices), sl_price, max_tp, kind,
             int(session_seq)),
        )
        return cur.lastrowid


def count_open_forex_signals_for_pair(user_id: int, pair: str) -> int:
    """How many OPEN signals does this user already hold on this pair?
    Used to enforce the 'max 3 per pair' rule — fresh signal blocked
    until SL/TP closes one of the live ones."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM forex_signal "
            "WHERE user_id=? AND pair=? AND status='open'",
            (user_id, pair),
        ).fetchone()
        return int(row["c"]) if row else 0


def count_open_forex_signals_for_pair_kind(
        user_id: int, pair: str, kind: str) -> int:
    """How many OPEN signals of THIS specific kind (LIVE / LIMIT / HFT)
    does the user already hold on this pair? Used to enforce the new
    rule: 'max 1 LIVE + 1 LIMIT + 1 HFT per pair' — a new kind only
    fires once the matching one closes."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM forex_signal "
            "WHERE user_id=? AND pair=? AND kind=? AND status='open'",
            (user_id, pair, kind),
        ).fetchone()
        return int(row["c"]) if row else 0


def open_forex_kinds_for_pair(user_id: int, pair: str) -> list[str]:
    """Return the list of kinds currently OPEN for this user on this pair.
    Lets the engine know which kinds are still 'free slots'."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT kind FROM forex_signal "
            "WHERE user_id=? AND pair=? AND status='open'",
            (user_id, pair),
        ).fetchall()
        return [r["kind"] or "LIVE" for r in rows]


def arm_forex_alert(signal_id: int):
    """Mark a LIMIT-ORDER signal as 'alert armed' — engine will notify the
    user the moment price reaches the limit-order entry."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE forex_signal SET alert_armed=1 WHERE id=?", (signal_id,),
        )


def is_forex_alert_armed(signal_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT alert_armed FROM forex_signal WHERE id=?", (signal_id,),
        ).fetchone()
        return bool(row and row["alert_armed"])


def last_closed_forex_outcome(user_id: int) -> Optional[str]:
    """Outcome of the user's most recently CLOSED forex signal (tp/sl/partial),
    used to flip the next signal into 'recovery / max focus' mode after SL."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT outcome FROM forex_signal "
            "WHERE user_id=? AND status='closed' "
            "ORDER BY closed_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["outcome"] if row else None


def get_last_binary_outcome(user_id: int) -> Optional[str]:
    """Hidden simulated outcome of the user's previous binary signal.
    Used by signals.py to flip the new card into 'recovery focus' mode
    after a loss — same A-Z analysis vibe as forex."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_binary_outcome FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return row["last_binary_outcome"] if row else None


def set_last_binary_outcome(user_id: int, outcome: Optional[str]):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_binary_outcome=? WHERE user_id=?",
            (outcome, user_id),
        )


def set_forex_signal_msg(signal_id: int, message_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE forex_signal SET message_id=? WHERE id=?",
                     (message_id, signal_id))


def get_forex_signal(signal_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM forex_signal WHERE id=?",
                           (signal_id,)).fetchone()
        return dict(row) if row else None


def mark_im_in(signal_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE forex_signal SET im_in=1 WHERE id=?", (signal_id,))


def list_open_forex_signals(user_id: int) -> List[Dict]:
    """All open (not closed) forex signals for a user, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM forex_signal "
            "WHERE user_id=? AND status='open' "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_forex_signal_progress(signal_id: int, tps_hit: int,
                                 outcome: Optional[str], status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE forex_signal SET tps_hit=?, outcome=?, status=?, "
            "closed_at=CASE WHEN ?='closed' THEN datetime('now') ELSE closed_at END "
            "WHERE id=?",
            (tps_hit, outcome, status, status, signal_id),
        )


# ── Stats ─────────────────────────────────────────────────
def stats() -> Dict[str, int]:
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        active_temp = conn.execute(
            "SELECT COUNT(*) AS c FROM access WHERE access_type='temporary' "
            "AND expires_at > datetime('now')"
        ).fetchone()["c"]
        lifetime = conn.execute(
            "SELECT COUNT(*) AS c FROM access WHERE access_type='lifetime'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM payments WHERE status='pending'"
        ).fetchone()["c"]
    return {
        "total_users": total_users,
        "active_temporary": active_temp,
        "lifetime": lifetime,
        "pending_payments": pending,
    }


# ── Funded Pass ───────────────────────────────────────────
def upsert_funded_pass(user_id: int, account_size: int, profit_pct: float,
                       daily_loss_pct: float, max_dd_pct: float):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO funded_pass(user_id, account_size, profit_pct, "
            "daily_loss_pct, max_dd_pct, equity_pct, daily_pct, day, status) "
            "VALUES(?,?,?,?,?, 0, 0, ?, 'active') "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "account_size=excluded.account_size, "
            "profit_pct=excluded.profit_pct, "
            "daily_loss_pct=excluded.daily_loss_pct, "
            "max_dd_pct=excluded.max_dd_pct, "
            "equity_pct=0, daily_pct=0, day=excluded.day, "
            "status='active', tf=NULL, pair=NULL, last_signal_at=NULL, "
            "created_at=datetime('now')",
            (user_id, account_size, profit_pct, daily_loss_pct, max_dd_pct,
             today_str()),
        )


def set_funded_pass_market(user_id: int, tf: str, pair: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE funded_pass SET tf=?, pair=? WHERE user_id=?",
            (tf, pair, user_id),
        )


def get_funded_pass(user_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM funded_pass WHERE user_id=?", (user_id,),
        ).fetchone()
        return dict(row) if row else None


def list_active_funded_passes() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM funded_pass WHERE status='active' AND pair IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]


def apply_funded_pass_pl(user_id: int, pct_delta: float):
    """Add a P/L % to the funded-pass equity. Resets daily_pct on day rollover."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT day, equity_pct, daily_pct FROM funded_pass WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return
        today = today_str()
        if row["day"] != today:
            conn.execute(
                "UPDATE funded_pass SET equity_pct=equity_pct+?, "
                "daily_pct=?, day=?, last_signal_at=datetime('now') "
                "WHERE user_id=?",
                (pct_delta, pct_delta, today, user_id),
            )
        else:
            conn.execute(
                "UPDATE funded_pass SET equity_pct=equity_pct+?, "
                "daily_pct=daily_pct+?, last_signal_at=datetime('now') "
                "WHERE user_id=?",
                (pct_delta, pct_delta, user_id),
            )


def set_funded_pass_status(user_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE funded_pass SET status=? WHERE user_id=?",
            (status, user_id),
        )


def touch_funded_pass(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE funded_pass SET last_signal_at=datetime('now') WHERE user_id=?",
            (user_id,),
        )


# ── MAILING (admin broadcast) ─────────────────────────────
def list_access_user_ids() -> List[int]:
    """Every user that currently has active (non-expired) access."""
    return [int(r["user_id"]) for r in list_active_access()]


def list_non_access_user_ids() -> List[int]:
    """Every user that does NOT currently have active access."""
    access_ids = set(list_access_user_ids())
    return [uid for uid in all_user_ids() if int(uid) not in access_ids]


def log_mailing_message(user_id: int, message_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO mailing_log(user_id, message_id) VALUES(?, ?)",
            (int(user_id), int(message_id)),
        )
        return int(cur.lastrowid)


def list_mailing_to_purge(older_than_hours: int = 72) -> List[Dict]:
    """Mailing messages older than `older_than_hours` that haven't been
    deleted yet — these are the ones to wipe from each user's chat."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, message_id FROM mailing_log "
            "WHERE deleted=0 AND "
            "datetime(sent_at) <= datetime('now', ?)",
            (f"-{int(older_than_hours)} hours",),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_mailing_deleted(row_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE mailing_log SET deleted=1 WHERE id=?", (int(row_id),),
        )


# ══════════════════════════════════════════════════════════════════════════════
# SELF-IMPROVE ENGINE — DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def insert_signal_outcome(
    user_id: int, pair: str, market: str, direction: str, timeframe: str,
    engine: str, confidence: int, weighted_score: float, entry_price,
    expiry_minutes: int, atr_pct: float, vol_mode: str, timestamp: int,
) -> int:
    """Insert a new signal into signal_outcomes; return the new row id."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO signal_outcomes
               (user_id, pair, market, direction, timeframe, engine,
                confidence, weighted_score, entry_price, expiry_minutes,
                atr_pct, vol_mode, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, pair, market, direction, timeframe, engine,
             confidence, weighted_score, entry_price, expiry_minutes,
             atr_pct, vol_mode, timestamp),
        )
        return int(cur.lastrowid)


def mark_signal_outcome(signal_id: int, outcome: str,
                        outcome_price: float = None) -> None:
    """Set the outcome field on a tracked signal row."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE signal_outcomes
               SET outcome=?, outcome_price=?, outcome_ts=strftime('%s','now'),
                   auto_checked=1
               WHERE id=?""",
            (outcome, outcome_price, signal_id),
        )


def get_pending_outcomes(limit: int = 500) -> List[Dict]:
    """Return signals whose outcome is still NULL and expiry has passed."""
    now = int(__import__("time").time())
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, pair, market, direction, engine,
                      entry_price, expiry_minutes, timestamp
               FROM signal_outcomes
               WHERE outcome IS NULL
                 AND auto_checked = 0
                 AND (timestamp + expiry_minutes * 60 + 45) <= ?
               ORDER BY timestamp ASC
               LIMIT ?""",
            (now, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_signal_outcomes_since(cutoff_ts: int) -> List[Dict]:
    """Return all signal_outcomes rows since cutoff_ts (epoch seconds)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pair, engine, outcome, vol_mode, confidence,
                      weighted_score, atr_pct
               FROM signal_outcomes
               WHERE timestamp >= ?
               ORDER BY timestamp ASC""",
            (cutoff_ts,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_ai_engine_learning(pair: str, engine: str, won: bool) -> None:
    """Upsert win/loss counter for (pair, engine) in ai_learning."""
    win_delta  = 1 if won else 0
    loss_delta = 0 if won else 1
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_learning (pair, engine, win_count, loss_count,
                                        total_signals, updated_at)
               VALUES (?, ?, ?, ?, 1, datetime('now'))
               ON CONFLICT(pair, engine) DO UPDATE SET
                   win_count     = win_count     + ?,
                   loss_count    = loss_count    + ?,
                   total_signals = total_signals + 1,
                   updated_at    = datetime('now')""",
            (pair, engine, win_delta, loss_delta, win_delta, loss_delta),
        )


def get_ai_engine_stats(pair: str, engine: str) -> Optional[Dict]:
    """Return the ai_learning row for (pair, engine) or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ai_learning WHERE pair=? AND engine=?",
            (pair, engine),
        ).fetchone()
        return dict(row) if row else None


def insert_retune_log(pairs_analyzed: int, engines_adjusted: int,
                      summary: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_retune_log
               (pairs_analyzed, engines_adjusted, summary)
               VALUES (?,?,?)""",
            (pairs_analyzed, engines_adjusted, summary),
        )


def get_setting(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ══════════════════════════════════════════════════════════════════════════════
# WIN RATE GUARDIAN — DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def winrate_stats(days: int = 2) -> Dict:
    """Return win rate statistics for the last `days` days.

    Returns:
        {
            'total':      int,
            'wins':       int,
            'losses':     int,
            'win_rate':   float,   # 0.0 – 100.0
            'streak':     int,     # consecutive wins from latest signal
            'pair_stats': {pair: {'wins': int, 'total': int}},
        }
    """
    import time as _time
    since_ts = int(_time.time()) - days * 86400
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pair, market, direction, outcome, confidence, timestamp
               FROM signal_outcomes
               WHERE outcome IS NOT NULL
                 AND timestamp >= ?
               ORDER BY timestamp DESC""",
            (since_ts,),
        ).fetchall()

    rows = [dict(r) for r in rows]
    total  = len(rows)
    wins   = sum(1 for r in rows if r["outcome"] == "win")
    losses = sum(1 for r in rows if r["outcome"] == "loss")
    win_rate = round(wins / total * 100.0, 1) if total > 0 else 0.0

    # Consecutive-win streak (most recent first)
    streak = 0
    for r in rows:
        if r["outcome"] == "win":
            streak += 1
        else:
            break

    # Per-pair stats
    pair_stats: Dict[str, Dict] = {}
    for r in rows:
        p = r["pair"]
        if p not in pair_stats:
            pair_stats[p] = {"wins": 0, "total": 0}
        pair_stats[p]["total"] += 1
        if r["outcome"] == "win":
            pair_stats[p]["wins"] += 1

    return {
        "total":      total,
        "wins":       wins,
        "losses":     losses,
        "win_rate":   win_rate,
        "streak":     streak,
        "pair_stats": pair_stats,
    }


def get_boost_level() -> int:
    """Return current AI auto-boost level (0-3)."""
    val = get_setting("winrate_boost_level")
    try:
        return max(0, min(3, int(val or "0")))
    except Exception:
        return 0


def set_boost_level(level: int) -> None:
    """Store AI auto-boost level (0-3)."""
    set_setting("winrate_boost_level", str(max(0, min(3, int(level)))))


# ══════════════════════════════════════════════════════════════════════════════
# BINARY DAILY ALERT — DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_binary_daily_state(user_id: int) -> Optional[Dict]:
    """Return the current daily alert state row for this user, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM binary_daily_alert WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["win_alert_sent"] = bool(d.get("win_alert_sent", 0))
    return d


def save_binary_daily_state(user_id: int, state: dict) -> None:
    """Upsert the daily alert state row for this user."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO binary_daily_alert
                   (user_id, date, consecutive_losses, consecutive_wins,
                    loss_alert_count, win_alert_sent, market_type,
                    last_action_after_alert1)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   date                       = excluded.date,
                   consecutive_losses         = excluded.consecutive_losses,
                   consecutive_wins           = excluded.consecutive_wins,
                   loss_alert_count           = excluded.loss_alert_count,
                   win_alert_sent             = excluded.win_alert_sent,
                   market_type                = excluded.market_type,
                   last_action_after_alert1   = excluded.last_action_after_alert1
            """,
            (
                int(user_id),
                state.get("date", ""),
                int(state.get("consecutive_losses", 0)),
                int(state.get("consecutive_wins", 0)),
                int(state.get("loss_alert_count", 0)),
                1 if state.get("win_alert_sent") else 0,
                state.get("market_type", "OTC"),
                state.get("last_action_after_alert1"),
            ),
        )


def winrate_stats_by_market(days: int = 2) -> Dict:
    """Win rate broken down by market/broker (PO OTC, QX OTC, LIVE) for the
    last `days` days.  Returns a dict keyed by market label."""
    import time as _time
    since_ts = int(_time.time()) - days * 86400
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT market, outcome
               FROM signal_outcomes
               WHERE outcome IS NOT NULL
                 AND timestamp >= ?""",
            (since_ts,),
        ).fetchall()

    rows = [dict(r) for r in rows]
    buckets: Dict[str, Dict] = {}
    for r in rows:
        mkt = r["market"] or "OTHER"
        if mkt not in buckets:
            buckets[mkt] = {"wins": 0, "losses": 0, "total": 0}
        buckets[mkt]["total"] += 1
        if r["outcome"] == "win":
            buckets[mkt]["wins"] += 1
        else:
            buckets[mkt]["losses"] += 1
    for v in buckets.values():
        v["win_rate"] = round(v["wins"] / v["total"] * 100.0, 1) if v["total"] > 0 else 0.0
    return buckets


def purge_old_winrate_data(days: int = 2) -> int:
    """Delete signal_outcomes records older than `days` days.
    Returns the number of rows removed (for logging)."""
    import time as _time
    cutoff = int(_time.time()) - days * 86400
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM signal_outcomes WHERE timestamp < ?",
            (cutoff,),
        )
        deleted = cur.rowcount
    return deleted


# ── Auto Trading Demo ──────────────────────────────────────

_AT_DEMO_MAX_TOTAL = 3
_AT_DEMO_MAX_PER_DAY = 1
_AT_DEMO_RESET_DAYS = 30


def get_at_demo_state(user_id: int) -> Dict:
    """Return the demo state dict for user, creating row if missing."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM auto_trading_demo WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO auto_trading_demo(user_id, day) VALUES(?,?)",
                (user_id, today),
            )
            return {"user_id": user_id, "total_used": 0, "today_used": 0,
                    "day": today, "first_trade_at": None, "last_trade_at": None}
        d = dict(row)

        # Reset today counter if it's a new day
        if d.get("day") != today:
            conn.execute(
                "UPDATE auto_trading_demo SET today_used=0, day=? WHERE user_id=?",
                (today, user_id),
            )
            d["today_used"] = 0
            d["day"] = today

        # Reset everything after 30 days from first trade
        if d.get("first_trade_at"):
            first_dt = datetime.strptime(d["first_trade_at"][:10], "%Y-%m-%d")
            if (datetime.utcnow() - first_dt).days >= _AT_DEMO_RESET_DAYS:
                conn.execute(
                    "UPDATE auto_trading_demo SET total_used=0, today_used=0, "
                    "first_trade_at=NULL, last_trade_at=NULL, day=? WHERE user_id=?",
                    (today, user_id),
                )
                d["total_used"] = 0
                d["today_used"] = 0
                d["first_trade_at"] = None
                d["last_trade_at"] = None
        return d


def can_at_demo_trade(user_id: int) -> tuple:
    """Returns (can_trade: bool, reason: str)."""
    state = get_at_demo_state(user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state["total_used"] >= _AT_DEMO_MAX_TOTAL:
        return False, "total_exhausted"
    if state.get("day") == today and state["today_used"] >= _AT_DEMO_MAX_PER_DAY:
        return False, "daily_limit"
    return True, "ok"


def record_at_demo_trade(user_id: int):
    """Increment demo trade counters."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    state = get_at_demo_state(user_id)
    first = state.get("first_trade_at") or now_str
    with get_conn() as conn:
        conn.execute(
            """UPDATE auto_trading_demo
               SET total_used=total_used+1,
                   today_used=today_used+1,
                   first_trade_at=COALESCE(first_trade_at, ?),
                   last_trade_at=?,
                   day=?
               WHERE user_id=?""",
            (first, now_str, today, user_id),
        )


# ── Auto Trading Premium Settings ─────────────────────────

def get_at_settings(user_id: int) -> Dict:
    """Return premium auto trading settings, creating defaults if missing."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM auto_trading_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO auto_trading_settings(user_id) VALUES(?)",
                (user_id,),
            )
            return {
                "user_id": user_id, "broker_connected": 0, "broker_name": "",
                "risk_mode": "moderate", "auto_trading_on": 0,
                "drawdown_threshold": 5.0, "loss_day_count": 0,
                "loss_window_start": None, "review_required": 0,
                "strategy_paused": 0,
            }
        return dict(row)


def update_at_settings(user_id: int, **kwargs):
    """Update one or more fields in auto_trading_settings."""
    if not kwargs:
        return
    kwargs["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE auto_trading_settings SET {cols} WHERE user_id=?", vals
        )


# ── Auto Trading Trade History ─────────────────────────────

def _at_today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _at_next_trade_no(user_id: int, day: str, trade_type: str) -> int:
    """Return the next sequential trade number for the user today."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(trade_no) FROM at_trade_history "
            "WHERE user_id=? AND day=? AND trade_type=?",
            (user_id, day, trade_type),
        ).fetchone()
    val = row[0] if (row and row[0]) else 0
    return val + 1


def _at_generate_trd_id(user_id: int) -> str:
    """Generate a unique #TRD-XXXXX id for a new trade."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM at_trade_history WHERE user_id=?", (user_id,)
        ).fetchone()
        n = (row[0] if row else 0) + 10001
    return f"#TRD-{n:05d}"


def at_add_trade(
    user_id: int,
    trade_type: str,
    pair: str,
    direction: str,
    lot_size: float = 0.0,
    amount: float = 0.0,
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    profit_loss: float = 0.0,
    payout: float = 0.0,
    result: str = "pending",
    close_reason: str = "pending_verify",
    open_time: Optional[str] = None,
    is_demo: bool = False,
    is_auto: bool = True,
    broker_confirmed: bool = False,
    strategy_name: str = "",
    duration_mins: float = 0.0,
    balance_before: float = 0.0,
    balance_after: float = 0.0,
    drawdown_impact: float = 0.0,
    risk_pct: float = 1.0,
    notes: str = "",
) -> int:
    """Insert a closed trade into today's history. Returns new row id, or -1 on duplicate."""
    today = _at_today()
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    open_t = open_time or now_str
    trade_no = _at_next_trade_no(user_id, today, trade_type)
    trd_id   = _at_generate_trd_id(user_id)
    dup_key  = f"{user_id}:{today}:{trade_type}:{pair}:{direction}:{open_t}"
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO at_trade_history
                   (user_id, day, trade_no, trade_type, pair, direction,
                    lot_size, amount, entry_price, exit_price, sl_price, tp_price,
                    profit_loss, payout, result, close_reason, open_time, close_time,
                    is_demo, is_auto, status, broker_confirmed, duplicate_check,
                    trd_id, strategy_name, duration_mins, balance_before, balance_after,
                    drawdown_impact, risk_pct, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, today, trade_no, trade_type, pair, direction,
                 lot_size, amount, entry_price, exit_price, sl_price, tp_price,
                 profit_loss, payout, result, close_reason, open_t, now_str,
                 1 if is_demo else 0, 1 if is_auto else 0, "closed",
                 1 if broker_confirmed else 0, dup_key,
                 trd_id, strategy_name, duration_mins, balance_before, balance_after,
                 drawdown_impact, risk_pct, notes),
            )
            return cur.lastrowid
    except Exception:
        return -1


def at_get_today_history(user_id: int, trade_type: Optional[str] = None,
                         is_demo: Optional[bool] = None) -> List[Dict]:
    """Return today's trade history for a user, optionally filtered."""
    today = _at_today()
    conditions = ["user_id=?", "day=?"]
    params: list = [user_id, today]
    if trade_type:
        conditions.append("trade_type=?")
        params.append(trade_type)
    if is_demo is not None:
        conditions.append("is_demo=?")
        params.append(1 if is_demo else 0)
    sql = (
        "SELECT * FROM at_trade_history WHERE "
        + " AND ".join(conditions)
        + " ORDER BY id ASC"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def at_get_today_summary(user_id: int, trade_type: str,
                         is_demo: bool = False) -> Dict:
    """Return summary stats for today: total, profit_count, loss_count, net_pl, win_rate."""
    trades = at_get_today_history(user_id, trade_type=trade_type, is_demo=is_demo)
    total = len(trades)
    profit = sum(1 for t in trades if t["result"] in ("profit", "win"))
    loss   = sum(1 for t in trades if t["result"] in ("loss",))
    be     = sum(1 for t in trades if t["result"] in ("breakeven", "refund"))
    net_pl = sum(float(t["profit_loss"] or 0) for t in trades)
    payout = sum(float(t["payout"] or 0) for t in trades)
    win_rate = round(profit / total * 100, 1) if total > 0 else 0.0
    return {
        "total": total, "profit": profit, "loss": loss,
        "breakeven": be, "net_pl": net_pl, "payout": payout,
        "win_rate": win_rate,
    }


def at_archive_today_for_user(user_id: int) -> int:
    """Move all of yesterday's history to archive. Returns rows moved."""
    today = _at_today()
    archive_date = today
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM at_trade_history WHERE user_id=? AND day<?",
            (user_id, today),
        ).fetchall()
        if not rows:
            return 0
        for r in rows:
            d = dict(r)
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO at_trade_archive
                       (user_id, archive_date, day, trade_no, trade_type, pair,
                        direction, lot_size, amount, entry_price, exit_price,
                        sl_price, tp_price, profit_loss, payout, result,
                        close_reason, open_time, close_time, is_demo, is_auto,
                        status, broker_confirmed)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (d["user_id"], archive_date, d["day"], d["trade_no"],
                     d["trade_type"], d["pair"], d["direction"], d["lot_size"],
                     d["amount"], d["entry_price"], d["exit_price"], d["sl_price"],
                     d["tp_price"], d["profit_loss"], d["payout"], d["result"],
                     d["close_reason"], d["open_time"], d["close_time"],
                     d["is_demo"], d["is_auto"], d["status"], d["broker_confirmed"]),
                )
            except Exception:
                pass
        conn.execute(
            "DELETE FROM at_trade_history WHERE user_id=? AND day<?",
            (user_id, today),
        )
    return len(rows)


def at_archive_all_stale() -> int:
    """Archive stale (past-day) rows for ALL users. Called by midnight scheduler."""
    today = _at_today()
    with get_conn() as conn:
        users = conn.execute(
            "SELECT DISTINCT user_id FROM at_trade_history WHERE day<?",
            (today,),
        ).fetchall()
    total = 0
    for row in users:
        total += at_archive_today_for_user(row["user_id"])
    return total


def at_get_all_users_today(trade_type: Optional[str] = None) -> List[Dict]:
    """Admin: return all users' today history, optionally filtered by type."""
    today = _at_today()
    if trade_type:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM at_trade_history WHERE day=? AND trade_type=? ORDER BY user_id, id",
                (today, trade_type),
            ).fetchall()
    else:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM at_trade_history WHERE day=? ORDER BY user_id, id",
                (today,),
            ).fetchall()
    return [dict(r) for r in rows]


def at_get_today_history_filtered(
    user_id: int,
    trade_type: Optional[str] = None,
    result_filter: Optional[str] = None,
    is_demo: Optional[bool] = None,
) -> List[Dict]:
    """Return today's history with optional result filter (win/loss/all)."""
    today = _at_today()
    conditions = ["user_id=?", "day=?"]
    params: list = [user_id, today]
    if trade_type:
        conditions.append("trade_type=?")
        params.append(trade_type)
    if is_demo is not None:
        conditions.append("is_demo=?")
        params.append(1 if is_demo else 0)
    if result_filter == "win":
        conditions.append("result IN ('profit','win')")
    elif result_filter == "loss":
        conditions.append("result IN ('loss')")
    where = " AND ".join(conditions)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM at_trade_history WHERE {where} ORDER BY id",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ── Auto Trading Engine State ──────────────────────────────

def at_get_engine_state(user_id: int) -> str:
    """Return current engine state: stopped | running | paused | error."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT engine_state FROM auto_trading_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if row and row["engine_state"]:
        return row["engine_state"]
    return "stopped"


def at_set_engine_state(user_id: int, state: str,
                        last_signal_time: Optional[str] = None,
                        last_execution_time: Optional[str] = None,
                        error_state: Optional[str] = None):
    """Upsert engine state for a user."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO auto_trading_settings (user_id, engine_state, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 engine_state=excluded.engine_state,
                 updated_at=excluded.updated_at""",
            (user_id, state, now),
        )
        if last_signal_time:
            conn.execute(
                "UPDATE auto_trading_settings SET last_signal_time=? WHERE user_id=?",
                (last_signal_time, user_id),
            )
        if last_execution_time:
            conn.execute(
                "UPDATE auto_trading_settings SET last_execution_time=? WHERE user_id=?",
                (last_execution_time, user_id),
            )
        if error_state is not None:
            conn.execute(
                "UPDATE auto_trading_settings SET error_state=? WHERE user_id=?",
                (error_state, user_id),
            )


def at_get_full_settings(user_id: int) -> Dict:
    """Return complete auto_trading_settings row as dict (with defaults)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM auto_trading_settings WHERE user_id=?", (user_id,)
        ).fetchone()
    defaults = {
        "user_id": user_id,
        "broker_connected": 0,
        "broker_name": "",
        "risk_mode": "moderate",
        "auto_trading_on": 0,
        "drawdown_threshold": 5.0,
        "loss_day_count": 0,
        "loss_window_start": None,
        "review_required": 0,
        "strategy_paused": 0,
        "engine_state": "stopped",
        "last_signal_time": None,
        "last_execution_time": None,
        "error_state": None,
        "drawdown_hit_today": 0,
        "updated_at": None,
    }
    if row:
        d = defaults.copy()
        d.update({k: row[k] for k in row.keys() if k in d})
        return d
    return defaults


# ── Drawdown Protection ────────────────────────────────────

def at_set_drawdown_hit(user_id: int, hit: bool = True):
    """Mark that today's drawdown threshold has been triggered."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO auto_trading_settings (user_id, drawdown_hit_today, updated_at)
               VALUES (?,?,datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 drawdown_hit_today=excluded.drawdown_hit_today,
                 updated_at=excluded.updated_at""",
            (user_id, 1 if hit else 0),
        )


def at_drawdown_is_hit(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT drawdown_hit_today FROM auto_trading_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return bool(row and row["drawdown_hit_today"])


def at_count_loss_days(user_id: int, window: int = 10) -> int:
    """Count how many distinct days in the last `window` trading days had net loss."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT day, SUM(profit_loss) as net
               FROM at_trade_history
               WHERE user_id=? AND is_demo=0
               GROUP BY day
               ORDER BY day DESC
               LIMIT ?""",
            (user_id, window),
        ).fetchall()
    return sum(1 for r in rows if float(r["net"] or 0) < 0)


# ── Referral System ────────────────────────────────────────

def user_exists(user_id: int) -> bool:
    """True if the user is already in the users table (used before upsert)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    return row is not None


def create_referral_link(user_id: int, token: str, expires_at: str):
    """Insert a new referral link (replaces old ones for same user via token uniqueness)."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO referral_links(user_id, token, expires_at) "
            "VALUES(?,?,?)",
            (user_id, token, expires_at),
        )


def get_valid_referral_link(user_id: int) -> Optional[Dict]:
    """Return the most recent non-expired referral link for a user, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM referral_links "
            "WHERE user_id=? AND expires_at > datetime('now') "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_link_by_token(token: str) -> Optional[Dict]:
    """Return a referral link row by token (includes expired ones for info)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM referral_links WHERE token=?", (token,)
        ).fetchone()
    return dict(row) if row else None


def has_been_referred(new_user_id: int) -> bool:
    """True if this user has already been counted as a referral for anyone."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM referral_uses WHERE new_user_id=?", (new_user_id,)
        ).fetchone()
    return row is not None


def record_referral_use(token: str, referrer_id: int, new_user_id: int):
    """Record that new_user_id joined via referrer's token, then update rewards."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO referral_uses(token, referrer_id, new_user_id) "
                "VALUES(?,?,?)",
                (token, referrer_id, new_user_id),
            )
        except Exception:
            return  # already counted (UNIQUE constraint)
    _update_referral_rewards(referrer_id)


def get_referral_count(user_id: int) -> int:
    """Total successful (distinct new-user) referrals for a user."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM referral_uses WHERE referrer_id=?",
            (user_id,),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def _update_referral_rewards(user_id: int):
    """Recalculate bonus signals based on total referral count and save."""
    total = get_referral_count(user_id)
    milestones = total // 5
    bonus_binary = milestones * 3   # +3 binary signals per milestone
    bonus_forex  = milestones * 1   # +1 forex signal per milestone
    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO referral_rewards(user_id, bonus_binary, bonus_forex, "
            "total_refs, last_updated, expires_at) VALUES(?,?,?,?,datetime('now'),?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "bonus_binary=excluded.bonus_binary, "
            "bonus_forex=excluded.bonus_forex, "
            "total_refs=excluded.total_refs, "
            "last_updated=datetime('now'), "
            "expires_at=excluded.expires_at",
            (user_id, bonus_binary, bonus_forex, total, expires_at),
        )


def get_referral_bonus(user_id: int) -> Dict:
    """Return active bonus signals dict. Returns zeros if expired or no record."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT bonus_binary, bonus_forex, expires_at FROM referral_rewards "
            "WHERE user_id=? AND expires_at > datetime('now')",
            (user_id,),
        ).fetchone()
    if row:
        return {
            "bonus_binary": int(row["bonus_binary"]),
            "bonus_forex":  int(row["bonus_forex"]),
            "expires_at":   row["expires_at"],
        }
    return {"bonus_binary": 0, "bonus_forex": 0, "expires_at": None}
