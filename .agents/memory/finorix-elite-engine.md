---
name: Finorix Elite Engine
description: Architecture and voting contract for finorix_elite_engine.py (V4 Supreme)
---

# Finorix Elite Engine — V4 Supreme

## What it does
`finorix_elite_engine.py` — 5-module silent analysis engine wired into signals.py.
Zero side-effects: never touches signal text, keyboards, or any other module.

## 5 Modules
1. **BigToSmallCascade** — H1→M15→M5→M1 trend agreement (weighted TF vote, 4=ELITE)
2. **EliteSREngine** — swing pivots + psychological levels + EMA-50/200 + EQH/EQL + OB + FVG midpoints
3. **HiddenReversalDetector** — RSI hidden div + MACD hidden div + wick exhaustion + ATR compression + stochastic extremes
4. **TrendStrengthClassifier** — ADX-14 + 5-EMA stack (8/13/21/50/89) + normalized slope
5. **ZoneConfluenceScorer** — counts how many zone types stack at current price (0–6)

## Return dict keys
- `grade`: "HIDDEN" > "ELITE" > "STRONG" > "MODERATE" > "WEAK"
- `zone_confluence`: int 0–6
- `trend_phase`: "REVERSAL" | "CONTINUATION" | "RANGING"
- `trend_strength`: "ELITE" | "STRONG" | "MODERATE" | "WEAK"
- `hidden_zone`: bool (EQH/EQL, OB, FVG, or hidden divergence at current price)
- `cascade_score`: int 0–4 (TFs agreeing)

## Voting contract in signals.py
- HIDDEN grade → triple vote (3 appended to `_engine_votes`)
- ELITE/GOD/ULTRA grade → double vote
- zone_confluence ≥ 4 → extra vote
- REVERSAL phase + direction match + confidence ≥ 70 → +2 confidence boost
- CONTINUATION phase match → +1 confidence boost
- hidden_zone + direction match → +1 confidence boost
- veto=True → -4 confidence (capped at 88 minimum)

## Adaptive timing (handlers/signal.py)
- Normal market (<0.015%/3s move): 3+3 = 6s total
- Slightly elevated: 3+4 = 7s
- Elevated volatility (>0.03%/3s): 3+7 = 10s
- Very high volatility (>0.06%/3s): 3+9 = 12s

**Why:** User spec: 6-7s normal, 10-12s max for high volatility / confused market.

## Key constraint
Signal text contract is ABSOLUTE — no engine may ever modify signal text output.
All modules are silent voters only.
