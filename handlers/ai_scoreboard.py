"""AI Power Scoreboard — real-time multi-engine scan dashboard.

Shows live % scores from every AI engine for Binary or Forex mode.
Accessed via 🤖 AI POWER SCAN on the main menu.
"""

import asyncio
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

from chat_clean import show_screen

router = Router()

# ── Pair lists shown in the scoreboard picker ──────────────────────────────
BINARY_SCAN_PAIRS = [
    ("EUR/USD", "EURUSD"), ("GBP/USD", "GBPUSD"), ("USD/JPY", "USDJPY"),
    ("AUD/USD", "AUDUSD"), ("GBP/JPY", "GBPJPY"), ("EUR/JPY", "EURJPY"),
]
FOREX_SCAN_PAIRS = [
    ("EUR/USD",  "EURUSD"), ("GBP/USD",  "GBPUSD"), ("USD/JPY",  "USDJPY"),
    ("XAU/USD",  "GOLD"),   ("BTC/USD",  "BTC"),    ("NAS100",   "NAS100"),
]


# ── Keyboard helpers ────────────────────────────────────────────────────────
def _mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 BINARY ENGINES",
                              callback_data="scan:mode:binary"),
         InlineKeyboardButton(text="💹 FOREX ENGINES",
                              callback_data="scan:mode:forex")],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


def _pairs_kb(mode: str) -> InlineKeyboardMarkup:
    pairs = BINARY_SCAN_PAIRS if mode == "binary" else FOREX_SCAN_PAIRS
    rows = []
    for i in range(0, len(pairs), 2):
        row = []
        for label, key in pairs[i:i+2]:
            row.append(InlineKeyboardButton(
                text=label,
                callback_data=f"scan:run:{mode}:{key}"
            ))
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="⬅️ BACK", callback_data="scan:home"),
        InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_kb(mode: str, pair_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 RESCAN",
                              callback_data=f"scan:run:{mode}:{pair_key}"),
         InlineKeyboardButton(text="⬅️ BACK",
                              callback_data=f"scan:mode:{mode}")],
        [InlineKeyboardButton(text="🏢 WORKPLACE", callback_data="m:home")],
    ])


# ── Progress bar renderer ───────────────────────────────────────────────────
def _bar(pct: int, width: int = 18) -> str:
    filled = round(pct / 100 * width)
    return "▓" * filled + "░" * (width - filled)


def _score_emoji(pct: int) -> str:
    if pct >= 80: return "🟢"
    if pct >= 60: return "🟡"
    if pct >= 40: return "🟠"
    return "🔴"


# ── Engine runner (thread-safe wrappers) ────────────────────────────────────
async def _t(fn, *args):
    """Run a sync engine function in a thread pool, return None on error."""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:
        return None


# ── BINARY scan ─────────────────────────────────────────────────────────────
async def _run_binary_scan(pair_display: str, is_otc: bool) -> str:
    from strategy import (
        binary_sniper_analyze, quick_momentum_sniper,
        otc_reversal_sniper, price_action_sniper, one_minute_sniper,
    )
    from god_engine import session_gate, adx_strength
    from elite_signal_engine import (
        binary_last_bar_ok, compute_signal_score, classify_signal
    )

    pair = pair_display.replace(" 〔OTC〕", "").strip()

    # Run all engines concurrently
    (
        r_1min, r_quick, r_otc_rev, r_pa, r_bin,
        r_adx, r_sess
    ) = await asyncio.gather(
        _t(one_minute_sniper,     pair, is_otc),
        _t(quick_momentum_sniper, pair, is_otc),
        _t(otc_reversal_sniper,   pair),
        _t(price_action_sniper,   pair),
        _t(binary_sniper_analyze, pair, is_otc),
        _t(adx_strength,          pair, "5m"),
        _t(session_gate,          pair),
    )

    # Derive direction from majority
    votes, dirs = [], []
    for r in [r_1min, r_quick, r_otc_rev, r_pa, r_bin]:
        if r and isinstance(r, dict):
            d = r.get("direction") or r.get("bias") or r.get("signal")
            if d in ("BUY", "CALL"): dirs.append("BUY")
            elif d in ("SELL", "PUT"): dirs.append("SELL")
    direction = "BUY" if dirs.count("BUY") >= dirs.count("SELL") else "SELL"

    # Candle-flip check
    r_flip = await _t(binary_last_bar_ok, pair, direction)

    # Extract scores --------------------------------------------------------
    def _pct(r, *keys):
        if not r or not isinstance(r, dict): return 0
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 0:
                raw = float(v)
                return min(100, int(raw if raw <= 100 else raw / 10))
        return 0

    def _agree(r):
        if not r or not isinstance(r, dict): return 0
        ag = r.get("agree", 0) or r.get("agreement", 0)
        total = r.get("total", 8)
        if ag and total: return min(100, int(ag / total * 100))
        sc = r.get("score", 0) or r.get("confluence", 0)
        return min(100, int(sc)) if sc else 0

    s_1min   = _pct(r_1min,   "grade", "score", "confluence")
    s_quick  = _pct(r_quick,  "score", "confluence", "grade")
    s_otcrev = _pct(r_otc_rev,"score", "confluence", "grade")
    s_pa     = _pct(r_pa,     "score", "confluence", "grade")
    s_bin    = _pct(r_bin,    "score", "confluence", "grade")
    s_adx    = min(100, int(float(r_adx or 0) * 4)) if r_adx else 0

    # candle-flip: 100 = ok (momentum aligned), 0 = flipped
    flip_ok  = bool(r_flip) if r_flip is not None else True
    s_flip   = 100 if flip_ok else 25

    # Engine votes for elite score
    engine_votes = []
    for r in [r_1min, r_quick, r_otc_rev, r_pa, r_bin]:
        if r and isinstance(r, dict):
            d = r.get("direction") or r.get("bias") or r.get("signal")
            if d in ("BUY","CALL"): engine_votes.append("BUY")
            elif d in ("SELL","PUT"): engine_votes.append("SELL")

    sniper_sc = max(s_1min, s_quick, s_otcrev, s_pa, s_bin)
    sess_name = str(r_sess or "").lower()
    session   = "kill_zone" if "kill" in sess_name else ("active" if "active" in sess_name else "dead")

    elite_sc, elite_grade = compute_signal_score(
        pair, direction, engine_votes,
        sniper_score=sniper_sc, session=session
    )
    cls = classify_signal(elite_sc)

    cls_emoji = {"SNIPER": "🎯 SNIPER ELITE", "STANDARD": "✅ STANDARD", "BLOCKED": "🚫 BLOCKED"}.get(cls, cls)
    dir_arrow = "▲ CALL" if direction == "BUY" else "▼ PUT"
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    mode_label = "OTC" if is_otc else "LIVE"
    flip_label = "✅ Aligned" if flip_ok else "⚠️ Reversed"

    lines = [
        f"🤖 <b>SUPREME PRO AI — BINARY SCAN</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>{pair_display}</b>  ·  {mode_label}  ·  {now}",
        f"",
        f"<b>═══ BINARY ENGINE ANALYSIS ═══</b>",
        f"",
        f"⚡ <b>1-MIN PRECISION SNIPER</b>",
        f"  {_bar(s_1min)} {s_1min}% {_score_emoji(s_1min)}",
        f"",
        f"⚡ <b>QUICK MOMENTUM SNIPER</b>",
        f"  {_bar(s_quick)} {s_quick}% {_score_emoji(s_quick)}",
        f"",
        f"⚡ <b>OTC REVERSAL SNIPER</b>",
        f"  {_bar(s_otcrev)} {s_otcrev}% {_score_emoji(s_otcrev)}",
        f"",
        f"⚡ <b>PRICE ACTION SNIPER</b>",
        f"  {_bar(s_pa)} {s_pa}% {_score_emoji(s_pa)}",
        f"",
        f"⚡ <b>BINARY SNIPER CORE</b>",
        f"  {_bar(s_bin)} {s_bin}% {_score_emoji(s_bin)}",
        f"",
        f"⚡ <b>ADX TREND STRENGTH</b>",
        f"  {_bar(s_adx)} {s_adx}% {_score_emoji(s_adx)}",
        f"",
        f"⚡ <b>CANDLE-FLIP GUARD</b>",
        f"  {_bar(s_flip)} {s_flip}%  {flip_label}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>ELITE SIGNAL SCORE:  {elite_sc}/100</b>",
        f"🎯 <b>CLASSIFICATION:  {cls_emoji}</b>",
        f"💡 <b>AI DIRECTION:  {dir_arrow}</b>",
        f"📡 <b>SESSION:  {session.upper().replace('_',' ')}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


# ── FOREX scan ───────────────────────────────────────────────────────────────
async def _run_forex_scan(pair_display: str) -> str:
    from strategy import analyze_pair, multi_tf_bias
    from god_engine import session_gate, adx_strength, htf_trend
    from liquidity import analyze as liq_analyze
    from trade_entry import analyze as te_analyze, is_valid as te_valid
    from elite_signal_engine import (
        get_htf_levels, compute_signal_score, classify_signal
    )

    pair = pair_display

    # Run all engines concurrently
    (
        r_strat, r_mtf, r_sess, r_adx,
        r_liq_b, r_liq_s, r_te, r_htf
    ) = await asyncio.gather(
        _t(analyze_pair,   pair),
        _t(multi_tf_bias,  pair),
        _t(session_gate,   pair),
        _t(adx_strength,   pair, "1h"),
        _t(liq_analyze,    pair, "BUY"),
        _t(liq_analyze,    pair, "SELL"),
        _t(te_analyze,     pair),
        _t(get_htf_levels, pair),
    )

    # Determine direction
    direction = "BUY"
    if r_strat and isinstance(r_strat, dict):
        direction = r_strat.get("direction", "BUY")
    elif r_mtf and isinstance(r_mtf, dict):
        b = r_mtf.get("bias")
        if b in ("BUY", "SELL"): direction = b

    # HTF trend
    r_htf_trend = await _t(htf_trend, pair, direction)

    # Scores ---------------------------------------------------------------
    def _pct(r, *keys):
        if not r or not isinstance(r, dict): return 0
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 0:
                raw = float(v)
                return min(100, int(raw if raw <= 100 else raw / 10))
        return 0

    # Strategy sniper score (0-100)
    s_strat = _pct(r_strat, "score", "sniper_score", "confluence")

    # Multi-TF bias: count agreeing TFs
    s_mtf = 0
    if r_mtf and isinstance(r_mtf, dict):
        votes = r_mtf.get("votes", {}) or {}
        agree = sum(1 for v in votes.values() if v == direction)
        total = max(len(votes), 1)
        s_mtf = int(agree / total * 100)

    # Session
    sess_name = str(r_sess or "").lower()
    session   = "kill_zone" if "kill" in sess_name else ("active" if "active" in sess_name else "dead")
    s_sess = {"kill_zone": 100, "active": 70, "dead": 10}.get(session, 10)

    # ADX
    s_adx = min(100, int(float(r_adx or 0) * 4)) if r_adx else 0

    # Liquidity SMC
    r_liq = r_liq_b if direction == "BUY" else r_liq_s
    s_liq = _pct(r_liq, "liq_grade", "score", "grade")

    # Trade Entry (BoS/MS)
    te_ok = te_valid(r_te, min_grade=70) if r_te else False
    te_grade = _pct(r_te, "grade", "score") if r_te else 0
    s_te = te_grade if te_grade > 0 else (65 if te_ok else 15)

    # HTF levels present
    htf_has = bool(r_htf and (r_htf.get("prev_week_hi") or r_htf.get("prev_day_hi")))
    s_htf = 100 if htf_has else 0

    # HTF trend aligned
    htf_trend_ok = bool(r_htf_trend)
    s_htftrend = 100 if htf_trend_ok else 20

    # Elite score
    engine_votes = []
    for r in [r_strat, r_te]:
        if r and isinstance(r, dict):
            d = r.get("direction") or r.get("bias")
            if d in ("BUY","SELL"): engine_votes.append(d)
    if r_mtf and s_mtf >= 60: engine_votes.append(direction)
    liq_for_score = r_liq if r_liq else None
    elite_sc, elite_grade = compute_signal_score(
        pair, direction, engine_votes,
        liq=liq_for_score, htf_levels=r_htf,
        sniper_score=s_strat, session=session
    )
    cls = classify_signal(elite_sc)

    cls_emoji = {
        "SNIPER":   "🎯 SNIPER ELITE",
        "STANDARD": "✅ STANDARD",
        "BLOCKED":  "🚫 BLOCKED"
    }.get(cls, cls)
    dir_arrow = "▲ BUY" if direction == "BUY" else "▼ SELL"

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    htf_label  = "✅ Loaded" if htf_has else "⚠️ No data"
    htft_label = "✅ Aligned" if htf_trend_ok else "❌ Opposing"
    te_label   = "✅ Valid" if te_ok else "⚠️ Weak"
    lines = [
        f"🤖 <b>SUPREME PRO AI — FOREX SCAN</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💹 <b>{pair_display}</b>  ·  LIVE  ·  {now}",
        f"",
        f"<b>═══ FOREX ENGINE ANALYSIS ═══</b>",
        f"",
        f"📈 <b>STRATEGY / SNIPER SCORE</b>",
        f"  {_bar(s_strat)} {s_strat}% {_score_emoji(s_strat)}",
        f"",
        f"📊 <b>MULTI-TIMEFRAME BIAS</b>",
        f"  {_bar(s_mtf)} {s_mtf}% {_score_emoji(s_mtf)}",
        f"",
        f"🕐 <b>SESSION QUALITY</b>",
        f"  {_bar(s_sess)} {s_sess}%  {session.upper().replace('_',' ')}",
        f"",
        f"📉 <b>ADX TREND STRENGTH  (1H)</b>",
        f"  {_bar(s_adx)} {s_adx}% {_score_emoji(s_adx)}",
        f"",
        f"💧 <b>LIQUIDITY / SMC GRADE</b>",
        f"  {_bar(s_liq)} {s_liq}% {_score_emoji(s_liq)}",
        f"",
        f"🔷 <b>TRADE ENTRY  (BoS · MS · Sweep)</b>",
        f"  {_bar(s_te)} {s_te}%  {te_label}",
        f"",
        f"📅 <b>HTF LEVEL ANCHORS  (W/M/D)</b>",
        f"  {_bar(s_htf)} {s_htf}%  {htf_label}",
        f"",
        f"🗓 <b>HTF TREND ALIGNMENT</b>",
        f"  {_bar(s_htftrend)} {s_htftrend}%  {htft_label}",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>ELITE SIGNAL SCORE:  {elite_sc}/100</b>",
        f"🎯 <b>CLASSIFICATION:  {cls_emoji}</b>",
        f"💡 <b>AI DIRECTION:  {dir_arrow}</b>",
        f"📡 <b>SESSION:  {session.upper().replace('_',' ')}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


# ── Handlers ─────────────────────────────────────────────────────────────────
SCAN_HOME_TEXT = (
    "🤖 <b>SUPREME PRO AI — POWER SCAN</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Select a trading mode to run a <b>live multi-engine scan</b>.\n\n"
    "Every AI engine fires in parallel and returns a\n"
    "real-time <b>% power score</b> with an elite grade.\n\n"
    "📊 <b>BINARY</b> → 8 engines · candle-flip guard\n"
    "💹 <b>FOREX</b> → 10 engines · HTF level anchors\n"
)


@router.callback_query(F.data == "scan:home")
async def cb_scan_home(call: CallbackQuery):
    await show_screen(call.bot, call.message.chat.id, SCAN_HOME_TEXT, _mode_kb())
    await call.answer()


@router.callback_query(F.data.startswith("scan:mode:"))
async def cb_scan_mode(call: CallbackQuery):
    mode = call.data.split(":")[2]
    label = "📊 BINARY" if mode == "binary" else "💹 FOREX"
    text = (
        f"🤖 <b>SUPREME PRO AI — {label} SCAN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Select a pair to scan:\n"
    )
    await show_screen(call.bot, call.message.chat.id, text, _pairs_kb(mode))
    await call.answer()


@router.callback_query(F.data.startswith("scan:run:"))
async def cb_scan_run(call: CallbackQuery):
    parts   = call.data.split(":")          # scan:run:mode:pair_key
    mode    = parts[2]
    pair_key = ":".join(parts[3:])          # handle colons in pair names

    # Map key back to display name
    pool = BINARY_SCAN_PAIRS if mode == "binary" else FOREX_SCAN_PAIRS
    pair_display = next((lab for lab, k in pool if k == pair_key), pair_key)

    # Show scanning state immediately
    scanning_text = (
        f"🤖 <b>SUPREME PRO AI — SCANNING…</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Running all AI engines on <b>{pair_display}</b>…\n\n"
        f"⏳ Fetching live data & computing scores.\n"
        f"This takes <b>5–15 seconds</b> — please wait."
    )
    await show_screen(call.bot, call.message.chat.id, scanning_text,
                      InlineKeyboardMarkup(inline_keyboard=[]))
    await call.answer()

    # Run the scan in the background
    try:
        if mode == "binary":
            is_otc = False  # default to live for scoreboard
            result = await _run_binary_scan(pair_display, is_otc)
        else:
            result = await _run_forex_scan(pair_display)
    except Exception as e:
        result = (
            f"⚠️ <b>SCAN ERROR</b>\n\n"
            f"Could not complete scan for {pair_display}.\n"
            f"<code>{type(e).__name__}: {e}</code>"
        )

    await show_screen(call.bot, call.message.chat.id,
                      result, _result_kb(mode, pair_key))
