# Ultra God Level Analysis Engine — Setup Guide

## Overview

A modular, ultra-strict signal quality engine that runs **behind the scenes**.
It never changes any signal text, menus, or bot output format — it only gates
whether a setup is high-enough confidence to count as a real signal vote.

## Architecture

```
ultra_god_engine.py          ← Main orchestrator (call this)
├── regime_filter.py         ← Module 1: Trend/range detection
├── htf_alignment.py         ← Module 2: 1h/4h/1d multi-TF alignment
├── liquidity_zones.py       ← Module 3: S/R zones, breakout/retest/fakeout
├── momentum_gate.py         ← Module 4: RSI + momentum confirmation
├── volatility_adapter.py    ← Module 5: ATR filter + candle body strength
├── entry_precision.py       ← Module 6: Entry quality (not late/chasing)
├── confidence_engine.py     ← Module 7: Final 0-100 scorer (threshold: 80)
├── risk_guard.py            ← Module 8: Cooldown, no-martingale, spread gate
└── debug_report.py          ← Module 9: Verbose logging + demo backtest
```

## Confidence Scoring (Total: 100)

| Module | Max Points |
|---|---|
| HTF Alignment (1h/4h/1d agree) | 20 |
| Liquidity Zone quality | 20 |
| Momentum (RSI + TV votes) | 15 |
| Volatility fit (ATR) | 15 |
| Candle body strength | 10 |
| Entry precision | 10 |
| Market regime quality | 10 |

**Signal fires only if score ≥ 80.**

## Signal Rules

| Rule | Behaviour |
|---|---|
| Market ranging badly | Skip |
| Fakeout zone detected | Skip |
| ATR dead / spike | Skip |
| Late entry (price extended) | Skip |
| Confidence < 80 | Reject |
| Loss cooldown active | Reject |
| Martingale (same dir after loss < 10 min) | Reject |
| Weekend LIVE | Reject |
| Duplicate setup < 5 min | Reject |

## Setup

1. **No extra installs needed** — uses `tradingview-ta` already installed.

2. **Enable debug logging** (optional):
   ```
   # In Replit Secrets, set:
   ULTRA_DEBUG = 1
   ```
   Logs appear in console and in `ultra_engine.log`.

3. **Run the test suite:**
   ```bash
   python test_ultra_engine.py
   ```

4. **Run the demo backtest:**
   ```python
   from debug_report import demo_backtest
   demo_backtest("XAUUSD")
   demo_backtest("EURUSD")
   ```

## Integration

The engine is wired into `signals.py` as a silent vote:
- When `ultra_analyze()` returns `accept=True`, it adds 1-2 votes to the
  signal consensus pool (direction matching → +2; non-matching → neutral).
- It **never blocks** an existing signal that's already been confirmed by
  the main engines. It only adds extra confidence.
- When `accept=False` and confidence < 65, it removes 1 vote from the pool.

## Environment Variables

See `.env.ultra.example` for all available settings.

## Debug Log Format

```
[2026-07-13 07:45:00 UTC] EURUSD BUY | ✅ ACCEPTED | conf=87 | Confidence=87/100 ...
[2026-07-13 07:45:01 UTC] XAUUSD SELL | ❌ REJECTED | conf=61 | Volatility blocked ...
```

Log file: `ultra_engine.log` (rotates at 1 MB → `ultra_engine.log.1`)
