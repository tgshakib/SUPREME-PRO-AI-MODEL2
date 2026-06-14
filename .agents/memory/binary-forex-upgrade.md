---
name: Binary & Forex Strength Upgrade
description: Tighter quality gates for binary signals and new forex quick engine with 100-pip minimum TP
---

## Binary Signal Improvements

**Root cause**: Without yfinance/pandas installed, `binary_master_filter.py` always falls into the `df is None` branch and returns STANDARD for everything — candle gates never run.

**Fix — no-data fallback**: Instead of always returning STANDARD, the fallback now evaluates engine-consensus quality:
- < 2 engine votes → WEAK (−12 adj)
- 2+ opposing engines → BLOCKED (−25, approved=False)
- 1 opposing engine → WEAK (−8 adj)
- 5+ agree, 0 oppose → HIGH (+6 adj)
- 3+ agree, 0 oppose → STANDARD (−2 adj)
- else → STANDARD (−5 adj)

**Fix — OTC tiers** (when candle data IS available):
- 0 gates → BLOCKED (hard block, approved=False)
- 1 gate → WEAK (−8 adj)
- 2 gates → STANDARD (+2 adj)
- 3 gates → HIGH (+8 adj)
- 4+ gates → ELITE (+15 adj)

**Fix — LIVE tiers** (when candle data IS available):
- 0-1 gates → BLOCKED (hard block, approved=False)
- 2 gates → STANDARD (+2 adj)
- 3 gates → HIGH (+8 adj)
- 4 gates → ELITE (+15 adj)

**Other tightening**:
- Doji threshold: 0.22 → 0.28 body/range ratio (stricter indecision filter)
- Engine oppose ratio block: 0.60 → 0.45 (blocks sooner on disagreement)
- Engine partial opposition: 0.40 → 0.30 (earlier confidence penalty)
- Doji confidence penalty: −8 → −12
- `_otc_min` in signals.py: default 4 → 6 (OTC reversal engine needs 6 agree votes)

## Forex Quick Engine (`forex_quick_engine.py`)

New module that runs in < 1 second at the start of every forex signal request.

**Votes aggregated**:
- SMART AI (Sweep▸BoS▸MS): weight 4 — highest trust
- Market Bias: weight 2-3 (3 if strength ≥ 0.80)
- FX Expert (EMA Fib Ribbon + MACD + Stoch): weight 2-3
- Sniper (EMA9/21 + RSI): weight 1
- Finorix Elite: weight 2-3

**Grade calculation**: 72 + dominance×28, then bonuses for SMART AI agreement (+8), strong bias (+5), unanimous (+4).

**Minimum grade to fire**: 80. Opposite side must be < 35% of total weight.

**Setup types detected**: HUNT, FAKEOUT, REAL_MOVE, CONSENSUS.

**Integrated into `forex_engine.py`**:
- Runs at start of `_generate_levels_raw` (before SMART AI)
- If grade ≥ 80: direction locked, min TP floor applied
- Falls back as direction-setter if SMART AI / sniper / bias all failed
- 30-second result cache per pair

## Forex SL/TP Changes

- `SL_MIN_PIPS_FOREX`: 20 → 10 pips (tighter SL = better RR)
- `SL_MAX_PIPS_FOREX`: 30 → 20 pips (hard cap reduced)
- `_SNIPER_SL_FOREX` table: [10,14,20] → [8,10,14]
- `_SNIPER_TP_FOREX` table: [60,100,200,500] → [100,160,280,500] (min 100-pip TP1)
- Minimum TP floor enforced in `_generate_levels` as post-processing step

**Why**: User specification — all forex signals must have minimum 100 pip TP1. Tight SL (10-20 pips) + 100+ pip TP = 1:5 to 1:10 RR.
