---
name: FINORIX MTF Channel Engine
description: New analysis engine implementing FINORIX AI chart method (regression channels + S/R + MTF); wired as silent vote in signals.py
---

## What was built
`finorix_mtf_engine.py` — full FINORIX AI analysis system matching the chart screenshot:
- Linear regression channels (yellow dashed bands) on last 50 bars
- Dynamic S/R levels via swing-pivot clustering (red resistance / green support)
- MTF consensus: M1 (wt 1) · M5 (wt 2) · M15 (wt 2.5) · H1 (wt 3)
- Trend label: "UP ▲" | "DOWN ▼" | "RANGING ↔"
- Public API: `finorix_mtf_analyse(pair, market_type)` + `finorix_trend_label(pair)`

## Ticker coverage
All asset classes via `_TICKER_MAP` + auto OTC variant stripping (`_OTC` suffix):
- Live Forex (21 pairs + minors + exotics)
- Metals: XAU → PAXG-USD, XAG → SI=F
- Energy: USOIL → CL=F, BRENT → BZ=F
- Crypto: BTC, ETH, BNB, SOL, XRP, ADA, AVAX, LTC, DOT, LINK, BCH, DASH, ETC, TON, MATIC
- Indices: NAS100 → ^NDX, DJ30 → ^DJI, SP500 → ^GSPC
- Stocks: AAPL, TSLA, AMZN, GOOGL, MSFT, META, NFLX, NVDA, BABA, JNJ, PFE, BA, MCD, INTC, V, MA, DIS, IBM, CSCO

## signals.py integration
- Imported as `_finorix_mtf_analyse` right after `_finorix_analyse`
- Fires AFTER the existing FINORIX 12-model engine vote
- Adds 1-2 engine votes (double vote for ELITE/ULTRA/GOD grade)
- +1 confidence when direction agrees + conf ≥ 68
- −2 confidence (max floor 90) when direction contradicts and not elite
- NEVER touches signal text, photo, keyboard

**Why:** Signal text must stay unchanged per user instruction; the MTF result is purely an internal vote that improves direction accuracy.

## OTC reconnect hardened (otc_price_service.py)
QX loop now:
- Tracks `_fail_count` — short wait on first failure, up to 60s cap
- Resets base delay to `_QX_RECONNECT` on auth failure so next attempt is fresh
- Gentler backoff (×1.5 not ×2) for transient errors, 60s hard cap
- PO loop already had immediate SSID refresh on ConnectionError (unchanged)
