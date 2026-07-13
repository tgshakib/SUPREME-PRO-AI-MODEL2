"""debug_report.py — Ultra God Level Engine: Module 9
Verbose debug logging for every signal decision.
Writes to stdout + rotating log file (ultra_engine.log).
Also supports demo/backtest mode with sample candle data.

Usage
─────
    from debug_report import log_decision, demo_backtest

    log_decision(pair="EURUSD", direction="BUY",
                 accepted=True, confidence=87,
                 modules={...}, reason="All checks passed")

    demo_backtest(pair="XAUUSD")
"""
from __future__ import annotations

import os
import time
import json
from datetime import datetime, timezone
from typing import Any

LOG_FILE  = "ultra_engine.log"
MAX_BYTES = 1_000_000   # 1 MB before rotation
DEBUG_ENV = os.getenv("ULTRA_DEBUG", "0") == "1"


# ── Internal helpers ────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _rotate_if_needed() -> None:
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_BYTES:
            old = LOG_FILE + ".1"
            if os.path.exists(old):
                os.remove(old)
            os.rename(LOG_FILE, old)
    except Exception:
        pass


def _write(line: str) -> None:
    _rotate_if_needed()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Public API ──────────────────────────────────────────────────────────────

def log_decision(
    pair:       str,
    direction:  str | None,
    accepted:   bool,
    confidence: int,
    modules:    dict[str, Any] | None = None,
    reason:     str = "",
) -> None:
    """Log a signal accept/reject decision.

    Always writes to stdout if ULTRA_DEBUG=1.
    Always writes to ultra_engine.log (rotating).
    """
    verdict = "✅ ACCEPTED" if accepted else "❌ REJECTED"
    line = (
        f"[{_ts()}] {pair} {direction or 'NONE'} | "
        f"{verdict} | conf={confidence} | {reason}"
    )

    if DEBUG_ENV:
        print(f"[ultra_engine] {line}")
        if modules:
            for mod, data in modules.items():
                print(f"  • {mod}: {data.get('reason', str(data))}")

    _write(line)
    if modules and DEBUG_ENV:
        _write("  modules: " + json.dumps(
            {k: v.get("reason", "") for k, v in modules.items()},
            ensure_ascii=False,
        ))


def log_raw(message: str) -> None:
    """Write a raw message to the log."""
    line = f"[{_ts()}] {message}"
    if DEBUG_ENV:
        print(f"[ultra_engine] {line}")
    _write(line)


# ── Demo / Backtest mode ─────────────────────────────────────────────────────

_DEMO_CANDLES = {
    "XAUUSD": [
        {"close": 2310.5, "ema20": 2308.0, "ema50": 2305.0, "rsi": 57.3,
         "buy_v": 14, "sell_v": 4, "strength": 0.62, "bias": "BUY", "ok": True},
        {"close": 2305.0, "ema20": 2307.0, "ema50": 2306.0, "rsi": 47.1,
         "buy_v": 8,  "sell_v": 9, "strength": 0.30, "bias": "NEUTRAL", "ok": True},
        {"close": 2295.0, "ema20": 2300.0, "ema50": 2303.0, "rsi": 33.5,
         "buy_v": 3,  "sell_v": 17, "strength": 0.70, "bias": "SELL", "ok": True},
    ],
    "EURUSD": [
        {"close": 1.08520, "ema20": 1.08480, "ema50": 1.08420,
         "rsi": 61.0, "buy_v": 13, "sell_v": 5,
         "strength": 0.55, "bias": "BUY", "ok": True},
    ],
    "BTCUSD": [
        {"close": 67800.0, "ema20": 67600.0, "ema50": 67200.0,
         "rsi": 64.5, "buy_v": 15, "sell_v": 3,
         "strength": 0.71, "bias": "BUY", "ok": True},
    ],
}


def demo_backtest(pair: str = "XAUUSD") -> None:
    """Run the ultra engine on sample candles and print decisions."""
    print(f"\n{'─'*60}")
    print(f"ULTRA GOD ENGINE — DEMO BACKTEST: {pair}")
    print(f"{'─'*60}")

    candles = _DEMO_CANDLES.get(pair, _DEMO_CANDLES["XAUUSD"])

    for i, candle in enumerate(candles):
        print(f"\n  Candle {i+1}: close={candle['close']} RSI={candle['rsi']}")

        try:
            from ultra_god_engine import ultra_analyze
            result = ultra_analyze(pair, direction=None, is_otc=False,
                                   _override_data=candle)
            verdict = "✅ ACCEPT" if result["accept"] else "❌ REJECT"
            print(f"  → {verdict} | conf={result['confidence']} | "
                  f"dir={result['direction']} | grade={result['grade']}")
            print(f"  → {result['reason']}")
        except Exception as e:
            print(f"  → ERROR: {e}")

    print(f"\n{'─'*60}\n")
