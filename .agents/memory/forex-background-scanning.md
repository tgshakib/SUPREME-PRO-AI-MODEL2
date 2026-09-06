---
name: Persistent Forex background scanning
description: User-approved behavior for active Forex signal subscriptions and silent analysis.
---

An active Forex setup must keep searching in the background until the user stops it. It must not require repeated NEW SIGNAL taps.

**Why:** The user wants Forex to behave like Funded Pass: activation is a persistent signal-feed switch, not a one-shot request.

**How to apply:** Keep analysis and unqualified attempts out of Telegram chat. On activation or manual re-scan, allow up to about one minute of full-depth qualification attempts; afterward the persistent loop continues silently. Send only qualified cards with calculated entry, SL, TP ladder, and analysis details, keep one open Forex trade at a time, and resume automatically after it closes.

Forex STOP must affect only Forex and must not stop an independent Funded Pass subscription.

**Why:** Forex and Funded Pass are independent user-controlled systems.

**How to apply:** Persist Forex inactive status and let the background loop stop selecting it; do not couple the stop action to Funded Pass state.