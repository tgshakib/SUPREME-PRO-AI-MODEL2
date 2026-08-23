---
name: QX authenticated-session analysis
description: Rules for keeping Quotex OTC analysis tied to the account-authenticated price stream.
---

QX OTC signals must consume ticks and candles built only from the currently
authenticated QX session. Do not use the generic QX socket, Pocket Option,
averaged data, or public-market feeds as a fallback for selected-QX analysis.

**Why:** Quotex synthetic prices are account/session-bound. A socket can remain
connected while its price path no longer matches the logged-in terminal; a
substitute data source would hide that failure and produce unsupported trades.

**How to apply:** Rotate the authenticated QX stream within the configured
15–30 minute window. Withhold QX ticks, broker prices, and derived candles
during re-authentication, after session expiry/disconnect, or after a manual
price-drift flag. Audit entries may retain an opaque local session id and start
time, never a token or SSID. The owner spot-check command relies on the most
recent selected QX pair, so use it immediately after analysing that pair.