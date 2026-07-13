from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import (
    SUPPORT_USERNAME, COMMUNITY_BOT, SVIP_BOT, REQUIRED_BOT,
    OTC_PAIRS, LIVE_PAIRS, FOREX_PAIRS, BINARY_TIMEFRAMES,
    FOREX_TIMEFRAMES, TP_LEVELS,
    MTG_PACKAGES, NONMTG_PACKAGES, GOLDZILA_PACKAGES,
    FP_ACCOUNT_SIZES, FP_PROFIT_TARGETS, FP_DAILY_LOSSES, FP_MAX_DRAWDOWNS,
)

PAGE_SIZE = 12


def _ulink(handle: str) -> str:
    return f"https://t.me/{handle.lstrip('@')}"


# ── Required-bot join gate ────────────────────────────────
def join_required_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔗 OPEN {REQUIRED_BOT}", url=_ulink(REQUIRED_BOT))],
        [InlineKeyboardButton(text="✅ I'VE JOINED — VERIFY", callback_data="verify_join")],
    ])


# ── Main menu ─────────────────────────────────────────────
def main_menu_kb(is_admin: bool = False,
                 show_active_fx: bool = False,
                 show_referral: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if show_active_fx:
        rows.append([InlineKeyboardButton(
            text="🟢 YOUR ACTIVE Fx -Signal",
            callback_data="fx:active_view",
        )])
    rows += [
        [InlineKeyboardButton(text="📊 BINARY TRADING",  callback_data="m:binary"),
         InlineKeyboardButton(text="💹 FOREX TRADING",   callback_data="m:forex")],
        [InlineKeyboardButton(text="🏛 FUNDED PASS",     callback_data="m:fp"),
         InlineKeyboardButton(text="💎 ACCESS BUY",      callback_data="m:buy")],
        [InlineKeyboardButton(text="🏆 Leaderboard 🗽",  callback_data="lb:open"),
         InlineKeyboardButton(text="🌍 TIMEZONE",        callback_data="tz:open")],
        [InlineKeyboardButton(text="💬 SUPPORT CHAT",    url=_ulink(SUPPORT_USERNAME)),
         InlineKeyboardButton(text="YouTube",              url="https://youtube.com/@fx_shakibsheikh?si=vLEpHnw5vjmzyNM5")],
    ]
    if show_referral:
        rows.append([InlineKeyboardButton(
            text="🎁 FREE SIGNALS — REFER FRIENDS",
            callback_data="m:referral",
        )])
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡️ ADMINISTRATION ACCESS",
                                          callback_data="adm:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Binary submenu ────────────────────────────────────────
def binary_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 OTC MARKET",  callback_data="m:binary_otc"),
         InlineKeyboardButton(text="📡 LIVE MARKET", callback_data="m:binary_live")],
        [InlineKeyboardButton(text="⬅️ BACK TO MENU", callback_data="m:home")],
    ])


def otc_broker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 POCKET OPTION (OTC)", callback_data="brk:po")],
        [InlineKeyboardButton(text="🟣 QUOTEX (OTC)",        callback_data="brk:qx")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="m:binary"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── Pair pagination (binary) ──────────────────────────────
def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def pairs_kb(market: str, broker: str, page: int) -> InlineKeyboardMarkup:
    """market: 'otc' | 'live'   broker: 'po'|'qx'|'-'"""
    if market == "otc":
        pairs = OTC_PAIRS
        back = "m:binary_otc"
    else:
        pairs = LIVE_PAIRS
        back = "m:binary"

    total_pages = max(1, (len(pairs) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = pairs[start:start + PAGE_SIZE]

    rows = []
    for chunk in _chunk(page_items, 2):
        row = []
        for pair in chunk:
            idx = pairs.index(pair)
            row.append(InlineKeyboardButton(
                text=pair,
                callback_data=f"pair:{market}:{broker}:{idx}",
            ))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️ Prev",
            callback_data=f"pg:{market}:{broker}:{page - 1}",
        ))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}", callback_data="noop",
    ))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            text="Next ▶️",
            callback_data=f"pg:{market}:{broker}:{page + 1}",
        ))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="⬅️ BACK", callback_data=back),
        InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pair_by_index(market: str, idx: int) -> str:
    if market == "otc":
        pairs = OTC_PAIRS
    elif market == "live":
        pairs = LIVE_PAIRS
    else:
        pairs = FOREX_PAIRS
    if 0 <= idx < len(pairs):
        return pairs[idx]
    return ""


# ── Binary timeframe ──────────────────────────────────────
def binary_tf_kb(market: str, broker: str, pair_idx: int) -> InlineKeyboardMarkup:
    rows = []
    # ⚡ 15-second timeframe — OTC only (Pocket Option / Quotex)
    if market == "otc":
        rows.append([InlineKeyboardButton(
            text="⚡ 15 Seconds  🔒 VIP",
            callback_data=f"tf:{market}:{broker}:{pair_idx}:15s",
        )])
    for label, code in BINARY_TIMEFRAMES:
        rows.append([InlineKeyboardButton(
            text=f"⏱️ {label}",
            callback_data=f"tf:{market}:{broker}:{pair_idx}:{code}",
        )])
    rows.append([
        InlineKeyboardButton(text="⬅️ BACK", callback_data=f"back_pairs:{market}:{broker}"),
        InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Binary signal result actions ──────────────────────────
def signal_actions_kb(market: str, broker: str, pair_idx: int, tf: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 AGAIN ANALYZE",
                              callback_data=f"again:{market}:{broker}:{pair_idx}:{tf}")],
        [InlineKeyboardButton(text="🔁 CHANGE PAIR",
                              callback_data=f"back_pairs:{market}:{broker}"),
         InlineKeyboardButton(text="⬅️ MENU",
                              callback_data="m:back_market")],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── Daily limit reached ───────────────────────────────────
def limit_reached_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 BOT ACCESS",   callback_data="m:buy"),
         InlineKeyboardButton(text="📆 COMMUNITY",    url=_ulink(COMMUNITY_BOT))],
        [InlineKeyboardButton(text="💬 SUPPORT",      url=_ulink(SUPPORT_USERNAME)),
         InlineKeyboardButton(text="⭐ SVIP AUTO JOIN", url=_ulink(SVIP_BOT))],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── Buy access — top level (3 buttons) ────────────────────
def buy_top_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 PAID JOIN (See Price List)",
                              callback_data="buy:paid")],
        [InlineKeyboardButton(text="⭐ SVIP & FREE BOT", url=_ulink(SVIP_BOT))],
        [InlineKeyboardButton(text="💬 SUPPORT TEAM",   url=_ulink(SUPPORT_USERNAME))],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="m:home"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── Paid Join — pick trader type ──────────────────────────
def paid_traders_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 BINARY TRADERS", callback_data="buy:binary")],
        [InlineKeyboardButton(text="💹 FOREX TRADERS",  callback_data="buy:forex")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="m:buy"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── Binary packages (MTG / NON-MTG) ───────────────────────
def binary_packages_kb() -> InlineKeyboardMarkup:
    rows = []
    # Section header — MTG list
    rows.append([InlineKeyboardButton(
        text="━━━━ 1 MTG SIGNAL ━━━━",
        callback_data="noop",
    )])
    for p in MTG_PACKAGES:
        rows.append([InlineKeyboardButton(
            text=f"MTG · {p['label']} — ${p['price']}",
            callback_data=f"pkg:{p['id']}",
        )])
    # Section header — NON-MTG list (clear visual break)
    rows.append([InlineKeyboardButton(
        text="━━━━ NON-MTG AI SIGNAL ━━━━",
        callback_data="noop",
    )])
    for p in NONMTG_PACKAGES:
        rows.append([InlineKeyboardButton(
            text=f"NON-MTG · {p['label']} — ${p['price']}",
            callback_data=f"pkg:{p['id']}",
        )])
    rows.append([InlineKeyboardButton(text="💬 SUPPORT",
                                      url=_ulink(SUPPORT_USERNAME))])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="buy:paid"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Forex GOLDZILA packages ───────────────────────────────
def forex_packages_kb() -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(
        text="━━━━ FOREX AI PAID JOIN ━━━━",
        callback_data="noop",
    )])
    for p in GOLDZILA_PACKAGES:
        rows.append([InlineKeyboardButton(
            text=f"{p['label']} — ${p['price']}  (was ${p['was']})",
            callback_data=f"pkg:{p['id']}",
        )])
    rows.append([InlineKeyboardButton(text="💬 SUPPORT",
                                      url=_ulink(SUPPORT_USERNAME))])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="buy:paid"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def package_payment_kb(pkg_id: str, back: str = "m:buy") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 SEND SCREENSHOT", callback_data=f"send_ss:{pkg_id}")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data=back),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def cancel_payment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="m:buy")],
    ])


def screenshot_check_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ TAP HERE — CHECK MY SCREENSHOT",
                              callback_data=f"submit_ss:{payment_id}")],
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="m:buy")],
    ])


def admin_review_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ APPROVED ALHAMDULILLAH",
                              callback_data=f"adm:approve:{payment_id}")],
        [InlineKeyboardButton(text="❌ REJECTED — Don't revive",
                              callback_data=f"adm:reject:{payment_id}")],
    ])


# ── Admin panel ───────────────────────────────────────────
def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ADD USER",
                              callback_data="adm:add_user")],
        [InlineKeyboardButton(text="📋 MEMBERS WITH ACCESS",
                              callback_data="adm:list_access"),
         InlineKeyboardButton(text="⏳ PENDING PAYMENTS",
                              callback_data="adm:pending")],
        [InlineKeyboardButton(text="🚫 REMOVE USER ACCESS",
                              callback_data="adm:remove"),
         InlineKeyboardButton(text="📊 STATS",
                              callback_data="adm:stats")],
        [InlineKeyboardButton(text="📈 WIN RATE DASHBOARD",
                              callback_data="adm:winrate")],
        [InlineKeyboardButton(text="🔁 OWNERSHIP TRANSFER",
                              callback_data="adm:transfer")],
        [InlineKeyboardButton(text="📨 MAILING",
                              callback_data="adm:mail")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="m:home"),
         InlineKeyboardButton(text="❌ CLOSE", callback_data="adm:close")],
    ])


# ── MAILING (admin broadcast) keyboards ───────────────────
def mailing_audience_kb() -> InlineKeyboardMarkup:
    """Three-way audience picker shown after the admin sends/forwards
    the mailing text. Picks who will receive the broadcast."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 BOT ACCESS USER",
                              callback_data="adm:mail:aud:access")],
        [InlineKeyboardButton(text="🆓 NON ACCESS USER",
                              callback_data="adm:mail:aud:non")],
        [InlineKeyboardButton(text="📣 SEND ALL",
                              callback_data="adm:mail:aud:all")],
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="adm:open")],
    ])


def mailing_confirm_kb(audience: str) -> InlineKeyboardMarkup:
    """Final SEND confirmation. `audience` is one of access|non|all."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 SEND",
                              callback_data=f"adm:mail:send:{audience}")],
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="adm:open")],
    ])


# ── ADD USER: duration picker ─────────────────────────────
ADD_USER_DURATIONS = [
    ("⏱️ 1 MIN",   "1m",   "minutes",  1),
    ("⏱️ 2 MIN",   "2m",   "minutes",  2),
    ("⏰ 1 HOUR",  "1h",   "hours",    1),
    ("⏰ 2 HOURS", "2h",   "hours",    2),
    ("📅 1 DAY",   "1d",   "days",     1),
    ("📅 2 DAYS",  "2d",   "days",     2),
    ("🗓️ 1 MONTH", "1mo",  "months",   1),
    ("🗓️ 2 MONTHS","2mo",  "months",   2),
    ("♾️ LIFETIME","life", "lifetime", 0),
]


def add_user_duration_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(ADD_USER_DURATIONS, 2):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"adm:dur:{code}")
            for label, code, _, _ in chunk
        ])
    rows.append([InlineKeyboardButton(text="❌ CANCEL", callback_data="adm:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="adm:open"),
         InlineKeyboardButton(text="❌ CLOSE", callback_data="adm:close")],
    ])


def winrate_dashboard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="adm:winrate_refresh")],
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="adm:open"),
         InlineKeyboardButton(text="❌ CLOSE", callback_data="adm:close")],
    ])


def admin_cancel_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ CANCEL", callback_data="adm:open")],
    ])


# ── Renew prompt ──────────────────────────────────────────
def renew_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 BUY ACCESS", callback_data="m:buy")],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── FOREX 24/7 flow ───────────────────────────────────────
def forex_tf_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(FOREX_TIMEFRAMES, 3):
        rows.append([
            InlineKeyboardButton(text=f"⏱️ {label}", callback_data=f"fxtf:{code}")
            for label, code in chunk
        ])
    rows.append([InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def forex_pairs_text() -> str:
    """Numbered text list of forex pairs."""
    lines = []
    for i, p in enumerate(FOREX_PAIRS, start=1):
        lines.append(f"<b>{i}.</b> {p}")
    return "\n".join(lines)


def forex_pairs_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="m:forex"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def forex_tp_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(TP_LEVELS, 3):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"fxtp:{val}")
            for label, val in chunk
        ])
    rows.append([InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def forex_pip_target_label(max_tp: int) -> str:
    """Human-readable pip target label from stored max_tp value."""
    from config import pip_target_from_max_tp
    pips = pip_target_from_max_tp(max_tp)
    return f"{pips}+ PIPS"


def forex_active_kb(gold_king: bool = False) -> InlineKeyboardMarkup:
    gold_label = ("🥇 GOLD KING : ON ✅" if gold_king
                  else "🥇 GOLD KING : OFF")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=gold_label, callback_data="fx:gold")],
        [InlineKeyboardButton(text="🛑 STOP",  callback_data="fx:stop")],
        [InlineKeyboardButton(text="📋 MENU", callback_data="m:home")],
    ])


def forex_signal_kb(signal_id: int, kind: str = "LIVE",
                    seq: int | None = None) -> InlineKeyboardMarkup:
    """Action buttons under a forex signal.

    LIMIT-ORDER signals (price hasn't been touched yet) get the 🔔 ALERT ME
    button — when tapped, the card hides and the engine pings the user the
    moment market price actually reaches the limit-order entry. Then the
    standard I'M IN button takes over.

    LIVE NOW + HFT signals show the standard 🟢 I'M IN tracker straight away.

    `seq` (optional) is the per-session sequence number (#01, #02, …) shown
    on the button so the user can tell signals apart at a glance.
    """
    label_suffix = f" SIGNAL #{seq:02d}" if seq else ""
    rows = []
    if kind == "LIMIT":
        rows.append([InlineKeyboardButton(
            text=f"🔔 ALERT ME{label_suffix}",
            callback_data=f"fxalert:{signal_id}",
        )])
    else:
        rows.append([InlineKeyboardButton(
            text=f"🟢 I'M IN{label_suffix}",
            callback_data=f"fxin:{signal_id}",
        )])
    rows.append([InlineKeyboardButton(
        text="🎯 NEW SIGNAL — 100% AI SNIPER", callback_data="fx:new",
    )])
    rows.append([
        InlineKeyboardButton(text="🛑 STOP",  callback_data="fx:stop"),
        InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def forex_tp_locked_kb() -> InlineKeyboardMarkup:
    """Big upsell shown when a free user picks TP 2-6 in Forex.
    BACK and WORKPLACE both go to the home screen — same target, two
    labels so the user always sees a familiar exit."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 BUY FULL ACCESS",
                              callback_data="m:buy")],
        [InlineKeyboardButton(text="⬅️ BACK",     callback_data="m:home"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def forex_more_signal_kb() -> InlineKeyboardMarkup:
    """Buttons shown after a forex signal CLOSES (TP / SL hit)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎯 NEW SIGNAL — 100% AI SNIPER", callback_data="fx:new",
        )],
        [InlineKeyboardButton(text="🛑 STOP",      callback_data="fx:stop")],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def fx_active_view_kb(signals: list | None = None) -> InlineKeyboardMarkup:
    """Keyboard shown for the 'YOUR ACTIVE Fx -Signal' overview screen.

    `signals` may be either a list of signal-ids (legacy callers) or a list
    of (signal_id, session_seq) tuples — when seq is provided the button
    label uses the per-session sequence (#01, #02, …) instead of the raw
    database id.
    """
    rows = []
    for item in (signals or []):
        if isinstance(item, (tuple, list)):
            sid, seq = int(item[0]), int(item[1] or 0)
        else:
            sid, seq = int(item), 0
        label_num = f"#{seq:02d}" if seq else f"#{sid}"
        rows.append([InlineKeyboardButton(
            text=f"🟢 I'M IN — Signal {label_num}",
            callback_data=f"fxin:{sid}",
        )])
    rows.append([InlineKeyboardButton(
        text="🎯 NEW SIGNAL — 100% AI SNIPER", callback_data="fx:new",
    )])
    rows.append([
        InlineKeyboardButton(text="🛑 STOP", callback_data="fx:stop"),
    ])
    rows.append([
        InlineKeyboardButton(text="⬅️ BACK", callback_data="m:home"),
        InlineKeyboardButton(text="❌ CLOSE", callback_data="fx:close_view"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Timezone picker ──────────────────────────────────────
# A short, curated list of every continent's most-used IANA timezones.
# Paginated 10 per page so callback_data fits Telegram's 64-byte budget.
WORLD_TIMEZONES = [
    "UTC",
    # Africa
    "Africa/Cairo", "Africa/Casablanca", "Africa/Johannesburg",
    "Africa/Lagos", "Africa/Nairobi",
    # Americas
    "America/Anchorage", "America/Argentina/Buenos_Aires", "America/Bogota",
    "America/Caracas", "America/Chicago", "America/Denver", "America/Halifax",
    "America/Lima", "America/Los_Angeles", "America/Mexico_City",
    "America/New_York", "America/Phoenix", "America/Santiago",
    "America/Sao_Paulo", "America/Toronto", "America/Vancouver",
    # Asia
    "Asia/Almaty", "Asia/Baghdad", "Asia/Bangkok", "Asia/Beirut",
    "Asia/Colombo", "Asia/Dhaka", "Asia/Dubai", "Asia/Ho_Chi_Minh",
    "Asia/Hong_Kong", "Asia/Jakarta", "Asia/Jerusalem", "Asia/Karachi",
    "Asia/Kathmandu", "Asia/Kolkata", "Asia/Kuala_Lumpur", "Asia/Kuwait",
    "Asia/Manila", "Asia/Riyadh", "Asia/Seoul", "Asia/Shanghai",
    "Asia/Singapore", "Asia/Taipei", "Asia/Tashkent", "Asia/Tehran",
    "Asia/Tokyo", "Asia/Yangon", "Asia/Yekaterinburg",
    # Atlantic
    "Atlantic/Azores", "Atlantic/Cape_Verde", "Atlantic/Reykjavik",
    # Australia / Pacific
    "Australia/Adelaide", "Australia/Brisbane", "Australia/Darwin",
    "Australia/Melbourne", "Australia/Perth", "Australia/Sydney",
    "Pacific/Auckland", "Pacific/Fiji", "Pacific/Guam",
    "Pacific/Honolulu", "Pacific/Tahiti",
    # Europe
    "Europe/Amsterdam", "Europe/Athens", "Europe/Berlin", "Europe/Brussels",
    "Europe/Bucharest", "Europe/Budapest", "Europe/Copenhagen",
    "Europe/Dublin", "Europe/Helsinki", "Europe/Istanbul", "Europe/Kyiv",
    "Europe/Lisbon", "Europe/London", "Europe/Madrid", "Europe/Moscow",
    "Europe/Oslo", "Europe/Paris", "Europe/Prague", "Europe/Rome",
    "Europe/Stockholm", "Europe/Vienna", "Europe/Warsaw", "Europe/Zurich",
]

TZ_PAGE_SIZE = 10


def _tz_offset_label(tz_name: str) -> str:
    """Return the current UTC offset string for an IANA tz, e.g. '+6:00'.
    Falls back to an empty string if pytz can't resolve the zone."""
    try:
        import pytz
        from datetime import datetime, timezone as _tz
        offset = pytz.timezone(tz_name).utcoffset(datetime.utcnow())
        if offset is None:
            return ""
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        return f"{sign}{hours}:{minutes:02d}"
    except Exception:
        return ""


def timezone_kb(page: int = 0) -> InlineKeyboardMarkup:
    total = len(WORLD_TIMEZONES)
    total_pages = max(1, (total + TZ_PAGE_SIZE - 1) // TZ_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * TZ_PAGE_SIZE
    items = WORLD_TIMEZONES[start:start + TZ_PAGE_SIZE]

    # Build buttons as pairs — 2 columns side by side
    buttons = []
    for tz in items:
        offset = _tz_offset_label(tz)
        label_text = f"UTC ({offset})" if offset else "UTC"
        buttons.append(InlineKeyboardButton(
            text=label_text,
            callback_data=f"tz:set:{tz}",
        ))
    rows = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️ Prev", callback_data=f"tz:pg:{page - 1}"))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            text="Next ▶️", callback_data=f"tz:pg:{page + 1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="⬅️ BACK",     callback_data="m:home"),
        InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_received_kb() -> InlineKeyboardMarkup:
    """Single 'START BOT' button shown after access is granted."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 START BOT", callback_data="paid:start")],
    ])


def forex_free_exhausted_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 FOREX PAID JOIN",
                              callback_data="buy:forex")],
        [InlineKeyboardButton(text="⭐ AUTO SVIP JOIN", url=_ulink(SVIP_BOT))],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── FUNDED PASS keyboards ─────────────────────────────────
def fp_account_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(FP_ACCOUNT_SIZES, 3):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"fp:acc:{val}")
            for label, val in chunk
        ])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="m:home"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fp_profit_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(FP_PROFIT_TARGETS, 4):
        rows.append([
            InlineKeyboardButton(text=f"🎯 {pct}%", callback_data=f"fp:pt:{pct}")
            for pct in chunk
        ])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="m:fp"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fp_daily_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(FP_DAILY_LOSSES, 4):
        rows.append([
            InlineKeyboardButton(text=f"📉 {pct}%", callback_data=f"fp:dl:{pct}")
            for pct in chunk
        ])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="fp:back_pt"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fp_dd_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(FP_MAX_DRAWDOWNS, 5):
        rows.append([
            InlineKeyboardButton(text=f"🛑 {pct}%", callback_data=f"fp:dd:{pct}")
            for pct in chunk
        ])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="fp:back_dl"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fp_tf_kb() -> InlineKeyboardMarkup:
    rows = []
    for chunk in _chunk(FOREX_TIMEFRAMES, 3):
        rows.append([
            InlineKeyboardButton(text=f"⏱️ {label}", callback_data=f"fp:tf:{code}")
            for label, code in chunk
        ])
    rows.append([InlineKeyboardButton(text="⬅️ BACK", callback_data="fp:back_dd"),
                 InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fp_pairs_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="fp:back_tf"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def fp_active_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛑 STOP CHALLENGE", callback_data="fp:stop")],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def fp_finished_kb(passed: bool) -> InlineKeyboardMarkup:
    rows = []
    if passed:
        rows.append([InlineKeyboardButton(text="💹 USE FOREX TRADERS",
                                          callback_data="m:forex")])
    else:
        rows.append([InlineKeyboardButton(text="🔁 TRY AGAIN",
                                          callback_data="m:fp")])
    rows.append([InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Leaderboard ───────────────────────────────────────────
LEADERBOARD_PAGES = [
    (
        "🏆 <b>LEAGUE OF 5 ZEROS 🥇</b>\n"
        "<i>The transaction amount starts from $100,000</i>\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        "<b>TOP 6 partners for the Year</b>\n"
        "<b>Trader / Volume</b>\n"
        "────────────────────────\n"
        "🥇 <b>Fx_Shakib Sheikh</b> : $250.050.00\n"
        "────────────────────────\n"
        "🥈 <b>TGAnika</b> : $184.310.00\n"
        "────────────────────────\n"
        "🥉 <b>SofTrades</b> : $132.920.42\n"
        "────────────────────────\n"
        "4️⃣ <b>Oawhidshakib</b> : $128.140.12\n"
        "────────────────────────\n"
        "5️⃣ <b>Arif_top_fx</b> : $114.510.00\n"
        "───────────────────────\n"
        "6️⃣ <b>Yhanryee</b> : $110.510.00\n"
        "───────────────────────"
    ),
    (
        "🏆 <b>LEADERBOARD — Page 2</b>\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "────────────────────────\n"
        "<b>Anonymous</b> : $9.980.14\n"
        "────────────────────────\n"
        "<b>Anonymous</b> : $9.710.24\n"
        "────────────────────────\n"
        "<b>atiok3</b> : $9.705.42\n"
        "────────────────────────\n"
        "<b>Anonymous</b> : $9.640.24\n"
        "────────────────────────\n"
        "<b>Anonymous</b> : $9.421.35\n"
        "────────────────────────\n"
        "<b>josh5</b> : $97.980.62\n"
        "────────────────────────\n"
        "<b>bitgoten</b> : $94.860.20\n"
        "────────────────────────\n"
        "<b>gr8boydk</b> : $94.520.42\n"
        "────────────────────────\n"
        "<b>ptsaifu pt</b> : $92.510.24\n"
        "────────────────────────\n"
        "<b>EddieDemon</b> : $90.214.90\n"
        "────────────────────────"
    ),
]


def leaderboard_kb(page: int) -> InlineKeyboardMarkup:
    total = len(LEADERBOARD_PAGES)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"lb:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"lb:page:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text="⬅️ BACK", callback_data="m:home"),
         InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── AUTO TRADING keyboards ─────────────────────────────────

def at_gate_kb() -> InlineKeyboardMarkup:
    """Shown to free / no-access users as the entry gate."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 DEMO MODE (Free Trial)",  callback_data="at:demo_menu")],
        [InlineKeyboardButton(text="💎 UNLOCK FULL ACCESS",       callback_data="m:buy")],
        [InlineKeyboardButton(text="🏠 Back to Menu",             callback_data="m:home")],
    ])


def at_demo_menu_kb() -> InlineKeyboardMarkup:
    """Demo mode — choose instrument."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💹 Demo FOREX Auto Trade",   callback_data="at:demo:forex")],
        [InlineKeyboardButton(text="📊 Demo BINARY Auto Trade",  callback_data="at:demo:binary")],
        [InlineKeyboardButton(text="⬅️ BACK",                   callback_data="at:open"),
         InlineKeyboardButton(text="🏠 Menu",                    callback_data="m:home")],
    ])


def at_demo_run_kb(mode: str) -> InlineKeyboardMarkup:
    """Run a single demo auto trade for mode='forex'|'binary'."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ RUN DEMO AUTO TRADE",     callback_data=f"at:demo_run:{mode}")],
        [InlineKeyboardButton(text="⬅️ BACK",                   callback_data="at:demo_menu"),
         InlineKeyboardButton(text="🏠 Menu",                    callback_data="m:home")],
    ])


def at_demo_locked_kb() -> InlineKeyboardMarkup:
    """Shown when all 3 demo trades are used up."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 GET FULL ACCESS NOW",     callback_data="m:buy")],
        [InlineKeyboardButton(text="💬 CONTACT SUPPORT",         url=_ulink(SUPPORT_USERNAME))],
        [InlineKeyboardButton(text="🏠 Back to Menu",             callback_data="m:home")],
    ])


def at_dashboard_kb() -> InlineKeyboardMarkup:
    """Main premium auto trading dashboard keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 ACCOUNT INFO",            callback_data="at:account")],
        [InlineKeyboardButton(text="⚡ AVAILABLE FEATURES",      callback_data="at:features")],
        [InlineKeyboardButton(text="⚙️ AUTO TRADING CONTROLS",  callback_data="at:controls")],
        [InlineKeyboardButton(text="🛡️ RISK & DRAWDOWN",        callback_data="at:risk")],
        [InlineKeyboardButton(text="🔌 BROKER CONNECTION",       callback_data="at:broker")],
        [InlineKeyboardButton(text="📊 ANALYTICS",               callback_data="at:analytics")],
        [InlineKeyboardButton(text="📅 TODAY TRADE HISTORY",     callback_data="at:history")],
        [InlineKeyboardButton(text="⚠️ WARNINGS",                callback_data="at:warnings")],
        [InlineKeyboardButton(text="🏠 Back to Menu",             callback_data="m:home")],
    ])


def at_back_dashboard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ BACK TO DASHBOARD",      callback_data="at:dashboard")],
        [InlineKeyboardButton(text="🏠 Menu",                    callback_data="m:home")],
    ])


def at_history_menu_kb(is_demo: bool = False) -> InlineKeyboardMarkup:
    """History menu — choose forex or binary tab."""
    if is_demo:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💹 Demo FOREX History",   callback_data="at:hist:demo_forex")],
            [InlineKeyboardButton(text="📊 Demo BINARY History",  callback_data="at:hist:demo_binary")],
            [InlineKeyboardButton(text="⬅️ BACK",                callback_data="at:dashboard"),
             InlineKeyboardButton(text="🏠 Menu",                 callback_data="m:home")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💹 FOREX History",            callback_data="at:hist:forex")],
        [InlineKeyboardButton(text="📊 BINARY History",           callback_data="at:hist:binary")],
        [InlineKeyboardButton(text="⬅️ BACK",                    callback_data="at:dashboard"),
         InlineKeyboardButton(text="🏠 Menu",                     callback_data="m:home")],
    ])


def at_history_back_kb(tab: str = "forex") -> InlineKeyboardMarkup:
    """Back button from a history detail view."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 REFRESH",                  callback_data=f"at:hist:{tab}")],
        [InlineKeyboardButton(text="⬅️ BACK TO HISTORY",         callback_data="at:history"),
         InlineKeyboardButton(text="🏠 Menu",                     callback_data="m:home")],
    ])


def at_controls_kb(engine_state: str = "stopped") -> InlineKeyboardMarkup:
    """Auto Trading Engine Control Panel keyboard."""
    rows = []
    if engine_state == "stopped":
        rows.append([InlineKeyboardButton(text="▶️ START AUTO TRADING",   callback_data="at:ctrl:start")])
    elif engine_state == "running":
        rows.append([InlineKeyboardButton(text="⏸ PAUSE",                 callback_data="at:ctrl:pause"),
                     InlineKeyboardButton(text="⏹ STOP",                  callback_data="at:ctrl:stop")])
    elif engine_state == "paused":
        rows.append([InlineKeyboardButton(text="▶️ RESUME",               callback_data="at:ctrl:resume"),
                     InlineKeyboardButton(text="⏹ STOP",                  callback_data="at:ctrl:stop")])
    elif engine_state == "error":
        rows.append([InlineKeyboardButton(text="🔄 RETRY / RESTART",      callback_data="at:ctrl:start")])
        rows.append([InlineKeyboardButton(text="⏹ STOP (CLEAR ERROR)",    callback_data="at:ctrl:stop")])
    rows.append([InlineKeyboardButton(text="🔄 REFRESH STATUS",           callback_data="at:controls")])
    rows.append([InlineKeyboardButton(text="⬅️ BACK TO DASHBOARD",       callback_data="at:dashboard"),
                 InlineKeyboardButton(text="🏠 Menu",                     callback_data="m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def at_hist_filter_kb(tab: str, current_filter: str = "all") -> InlineKeyboardMarkup:
    """Filter buttons for a history tab (Win / Loss / All)."""
    def _label(key: str, icon: str, text: str) -> InlineKeyboardButton:
        active = " ✓" if current_filter == key else ""
        return InlineKeyboardButton(
            text=f"{icon} {text}{active}",
            callback_data=f"at:hist:{tab}:{key}",
        )
    return InlineKeyboardMarkup(inline_keyboard=[
        [_label("all",  "📋", "ALL"),
         _label("win",  "🟢", "WIN ONLY"),
         _label("loss", "🔴", "LOSS ONLY")],
        [InlineKeyboardButton(text="🔄 REFRESH",            callback_data=f"at:hist:{tab}:all")],
        [InlineKeyboardButton(text="⬅️ BACK TO HISTORY",   callback_data="at:history"),
         InlineKeyboardButton(text="🏠 Menu",               callback_data="m:home")],
    ])


def at_drawdown_confirm_kb() -> InlineKeyboardMarkup:
    """Daily drawdown hit — Continue / Stop permission dialog."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ CONTINUE TRADING (Accept Risk)",  callback_data="at:dd:continue")],
        [InlineKeyboardButton(text="🛑 STOP TRADING (Recommended)",      callback_data="at:dd:stop")],
        [InlineKeyboardButton(text="⬅️ BACK TO DASHBOARD",              callback_data="at:dashboard")],
    ])


