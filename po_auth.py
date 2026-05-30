"""Pocket Option Auto-Login & SSID Manager
==========================================
Automatically logs into Pocket Option using email + password,
extracts the SSID session cookie, and keeps it refreshed.

• On startup: tries existing SSID → if stale/rejected logs in fresh.
• Background: checks every 6 days (before 7-day minimum expiry).
• On auth failure in WebSocket: triggers immediate re-login.
• Updates PO_SSID env var and notifies pocket_option_ws + otc_price_service
  so all connections auto-reconnect with the fresh SSID.

Login method: requests.Session() — handles cookies, redirects, and
all Set-Cookie variants that urllib misses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

PO_EMAIL    = os.environ.get("PO_EMAIL",    "tgshakib012@gmail.com")
PO_PASSWORD = os.environ.get("PO_PASSWORD", "tgshakib012@g")

_SSID_FILE        = os.path.join(os.path.dirname(__file__), ".po_ssid_cache")
_LOGIN_RETRY_DELAY = 30              # seconds between login retries on failure

# ── SSID token lifetime options ───────────────────────────────────────────────
# PO issues two token lifetimes depending on login method:
#   • Standard session (UUID format)  → 7-day TTL   (604 800 s)
#   • Extended / OAuth token          → 30-day TTL  (2 592 000 s)
# We refresh at 85% of actual TTL so the SSID never actually expires.
_SSID_TTL_7D  = 7  * 24 * 3600    # 604 800 s
_SSID_TTL_30D = 30 * 24 * 3600    # 2 592 000 s
_REFRESH_RATIO = 0.85              # refresh when 85% of lifetime has elapsed
_EARLY_REFRESH_BUFFER = 240        # also refresh if < 4 min remain
_DEFAULT_TTL  = _SSID_TTL_7D       # conservative default

_SSID_STAMP: dict = {}   # {"ssid": str, "fetched_at": float, "ttl": int}

# ── PO login endpoints (tried in order) ──────────────────────────────────────
_PO_LOGIN_ENDPOINTS = [
    # Cabinet API — returns JSON with token in body + Set-Cookie
    ("json", "https://po.trade/api/v1/cabinet/login",
     {"email": None, "password": None}),
    ("json", "https://pocketoption.com/api/v1/cabinet/login",
     {"email": None, "password": None}),
    ("json", "https://api.po.market/api/v1/cabinet/login",
     {"email": None, "password": None}),
    # Older auth endpoints
    ("json", "https://po.trade/api/v1/user/login",
     {"email": None, "password": None}),
    ("json", "https://pocketoption.com/api/user/login",
     {"email": None, "password": None}),
    # Form-encoded login (some PO deployments use form POST)
    ("form", "https://po.trade/login",
     {"email": None, "password": None}),
]

# Cookie names that PO uses for session tokens (checked in priority order)
_SSID_COOKIE_NAMES = [
    "ssid", "token", "PHPSESSID", "auth_token",
    "session_id", "sessionId", "session", "sid",
    "_ga_token", "access_token",
]

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://po.trade",
    "Referer": "https://po.trade/",
}


def _extract_from_cookies(session) -> Optional[str]:
    """Extract SSID token from a requests.Session cookie jar."""
    try:
        import requests
        jar = session.cookies
        for name in _SSID_COOKIE_NAMES:
            v = jar.get(name)
            if v and len(str(v)) > 8:
                return str(v)
        # Check all cookies for long-value tokens
        for cookie in jar:
            if len(cookie.value or "") > 30:
                return cookie.value
    except Exception:
        pass
    return None


def _extract_from_body(text: str) -> Optional[str]:
    """Extract token from JSON response body or HTML script tags."""
    # JSON body extraction
    try:
        d = json.loads(text)
        # Flat keys
        for key in ("ssid", "token", "session", "sid", "sessionId",
                    "session_id", "access_token", "auth_token"):
            v = d.get(key)
            if v and isinstance(v, str) and len(v) > 10:
                return v
        # Nested under "data"
        data = d.get("data") or d.get("result") or d.get("payload") or {}
        if isinstance(data, dict):
            for key in ("ssid", "token", "session", "sid", "sessionId",
                        "session_id", "access_token"):
                v = data.get(key)
                if v and isinstance(v, str) and len(v) > 10:
                    return v
    except Exception:
        pass
    # HTML/JS token pattern (e.g. window.__token__ = "...")
    for pattern in [
        r'"(?:ssid|token|session|auth_token)"\s*:\s*"([^"]{16,})"',
        r"'(?:ssid|token|session|auth_token)'\s*:\s*'([^']{16,})'",
        r'ssid["\s:=]+([A-Za-z0-9._~+/-]{20,})',
    ]:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def _do_login() -> Optional[str]:
    """Login to Pocket Option using requests.Session for proper cookie handling.

    Uses a single persistent session so cookies from redirects are captured.
    Tries every known login endpoint and both JSON and form payloads.
    """
    try:
        import requests
    except ImportError:
        logger.error("[po_auth] requests library not installed — cannot auto-login")
        return None

    session = requests.Session()
    session.headers.update(_REQUEST_HEADERS)

    for method, url, payload_template in _PO_LOGIN_ENDPOINTS:
        payload = {k: (PO_EMAIL if v is None and "email" in k else
                       PO_PASSWORD if v is None else v)
                   for k, v in payload_template.items()}
        # Fill actual credentials
        for k in list(payload.keys()):
            if "email" in k or k == "login":
                payload[k] = PO_EMAIL
            elif "pass" in k or "password" in k:
                payload[k] = PO_PASSWORD

        logger.info(f"[po_auth] Trying {method.upper()} {url} …")
        try:
            if method == "json":
                r = session.post(url, json=payload, timeout=20,
                                 allow_redirects=True)
            else:
                r = session.post(url, data=payload, timeout=20,
                                 allow_redirects=True)

            logger.debug(f"[po_auth] {url} → HTTP {r.status_code}")

            # 1. Check cookies set by this response + accumulated in session
            ssid = _extract_from_cookies(session)
            if ssid:
                logger.info(f"[po_auth] ✅ SSID from cookies via {url}")
                return ssid

            # 2. Check response body
            ssid = _extract_from_body(r.text)
            if ssid:
                logger.info(f"[po_auth] ✅ SSID from body via {url}")
                return ssid

        except Exception as exc:
            logger.debug(f"[po_auth] {url} error: {exc}")
            continue

    # Last resort: GET the PO homepage with the session — may set cookie
    for url in ("https://po.trade/", "https://pocketoption.com/"):
        try:
            session.get(url, timeout=10, allow_redirects=True)
            ssid = _extract_from_cookies(session)
            if ssid:
                logger.info(f"[po_auth] ✅ SSID from homepage GET {url}")
                return ssid
        except Exception:
            pass

    logger.warning("[po_auth] ⚠️ All login attempts failed — SSID unavailable")
    return None


def _load_cached_ssid() -> Optional[str]:
    """Load SSID from the local cache file, or from environment.

    Reads fetched_at AND ttl so the TTL-aware age check in
    run_po_auth_manager() works correctly across restarts.
    """
    # Try cache file first — most reliable source of age + TTL information
    try:
        if os.path.exists(_SSID_FILE):
            with open(_SSID_FILE, "r") as f:
                d = json.load(f)
            ssid       = d.get("ssid", "")
            fetched_at = d.get("fetched_at", 0)
            ttl        = int(d.get("ttl", _DEFAULT_TTL))
            age        = time.time() - fetched_at
            refresh_after = ttl * _REFRESH_RATIO   # 85% of lifetime
            if ssid and age < refresh_after:
                if not _SSID_STAMP.get("fetched_at"):
                    _SSID_STAMP["ssid"]       = ssid
                    _SSID_STAMP["fetched_at"] = fetched_at
                    _SSID_STAMP["ttl"]        = ttl
                return ssid
    except Exception:
        pass
    # Fall back to environment variable — treat as stale (fetched_at=0) so the
    # auth manager immediately attempts a fresh login.
    env_ssid = os.environ.get("PO_SSID", "").strip()
    if env_ssid:
        if not _SSID_STAMP.get("fetched_at"):
            _SSID_STAMP["ssid"]       = env_ssid
            _SSID_STAMP["fetched_at"] = 0   # immediately stale → force refresh
            _SSID_STAMP["ttl"]        = _detect_ssid_ttl(env_ssid)
        return env_ssid
    return None


def _detect_ssid_ttl(ssid: str) -> int:
    """Detect the expected lifetime of an SSID token.

    Rules (heuristic — conservative side always wins):
    • Starts with 'g.a' (Google OAuth) or is longer than 80 chars → 30-day token
    • Standard UUID format (8-4-4-4-12 hex, 36 chars)             → 7-day token
    • Anything else                                                 → 7-day (safe default)
    """
    if not ssid:
        return _DEFAULT_TTL
    s = ssid.strip()
    if s.startswith("g.a") or len(s) > 80:
        logger.debug("[po_auth] Detected 30-day extended SSID token")
        return _SSID_TTL_30D
    import re as _re
    if _re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s, _re.I):
        logger.debug("[po_auth] Detected 7-day UUID SSID token")
        return _SSID_TTL_7D
    return _DEFAULT_TTL


def _save_ssid(ssid: str):
    """Persist SSID to cache file and update the environment."""
    os.environ["PO_SSID"] = ssid
    ttl = _detect_ssid_ttl(ssid)
    try:
        with open(_SSID_FILE, "w") as f:
            json.dump({"ssid": ssid, "fetched_at": time.time(), "ttl": ttl}, f)
    except Exception as exc:
        logger.debug(f"[po_auth] Could not save SSID cache: {exc}")
    _SSID_STAMP["ssid"] = ssid
    _SSID_STAMP["fetched_at"] = time.time()
    _SSID_STAMP["ttl"] = ttl
    refresh_at = ttl * _REFRESH_RATIO
    logger.info(
        f"[po_auth] SSID saved — type={'30d' if ttl == _SSID_TTL_30D else '7d'}  "
        f"TTL={ttl//3600}h  auto-refresh in {refresh_at/3600:.1f}h"
    )


def _notify_services(ssid: str):
    """Push the new SSID to all WS services so they reconnect immediately."""
    try:
        import otc_price_service as _svc
        _svc.PO_SSID = ssid
        logger.info("[po_auth] Updated otc_price_service.PO_SSID")
    except Exception:
        pass
    try:
        import pocket_option_ws as _pows
        _pows.update_ssid(ssid)
        logger.info("[po_auth] Notified pocket_option_ws of new SSID")
    except Exception:
        pass


def get_current_ssid() -> str:
    """Return the active SSID. Tries cache/env first, login if needed."""
    cached = _load_cached_ssid()
    if cached:
        return cached
    ssid = _do_login()
    if ssid:
        _save_ssid(ssid)
        return ssid
    return os.environ.get("PO_SSID", "")


def refresh_ssid_now() -> bool:
    """Force an immediate SSID refresh via login. Returns True on success."""
    logger.info("[po_auth] Forcing SSID refresh via login …")
    ssid = _do_login()
    if ssid:
        _save_ssid(ssid)
        _notify_services(ssid)
        logger.info("[po_auth] ✅ SSID refreshed and all services notified")
        return True
    logger.warning("[po_auth] ❌ SSID refresh failed — using existing SSID")
    return False


async def run_po_auth_manager():
    """Background task: keeps the PO SSID alive indefinitely.

    Supports both 7-day and 30-day PO session tokens (auto-detected).
    Refresh strategy: proactive at 85% of detected TTL, plus immediate
    refresh when < 4 minutes remain, and on any auth failure.
    """
    logger.info("[po_auth] Starting Pocket Option auth manager …")
    logger.info(f"[po_auth] Using account: {PO_EMAIL}")

    existing = _load_cached_ssid()
    if existing:
        logger.info("[po_auth] Existing SSID found — verifying age …")
        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _DEFAULT_TTL)
        refresh_after = ttl * _REFRESH_RATIO
        age           = time.time() - fetched_at
        token_type    = "30d" if ttl == _SSID_TTL_30D else "7d"
        if age >= refresh_after:
            logger.info(f"[po_auth] SSID ({token_type}) is {age/3600:.1f}h old ≥ "
                        f"{refresh_after/3600:.1f}h threshold — refreshing now")
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        else:
            logger.info(
                f"[po_auth] SSID is {age/3600:.1f}h old — valid "
                f"(type={token_type}  next refresh in {(refresh_after - age)/3600:.1f}h)"
            )
    else:
        logger.info("[po_auth] No SSID found — logging in now …")
        ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        if not ok:
            logger.warning("[po_auth] Initial login failed — will retry in background")

    # Main maintenance loop — checks every 60 s, refreshes when:
    #   • 85% of TTL elapsed (7-day → ~6d  |  30-day → ~25.5d)
    #   • < 4 minutes remain before full TTL expiry
    #   • Auth failure detected by Agent-2 SSIDGuard (external trigger)
    while True:
        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _DEFAULT_TTL)
        age           = time.time() - fetched_at
        refresh_after = ttl * _REFRESH_RATIO      # 85% threshold
        hard_deadline = ttl - _EARLY_REFRESH_BUFFER  # 4 min before expiry
        token_type    = "30d" if ttl == _SSID_TTL_30D else "7d"

        if age >= ttl:
            # Fully expired — must refresh immediately
            logger.info(f"[po_auth] SSID ({token_type}) fully expired — refreshing …")
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                logger.warning(f"[po_auth] Login failed — retrying in {_LOGIN_RETRY_DELAY}s")
                await asyncio.sleep(_LOGIN_RETRY_DELAY)
            else:
                await asyncio.sleep(60)

        elif age >= refresh_after:
            # Past 85% of lifetime — proactive refresh
            remaining = ttl - age
            logger.info(
                f"[po_auth] ⚡ SSID ({token_type}) at {age/3600:.1f}h / "
                f"{ttl/3600:.0f}h — proactive refresh ({remaining/3600:.1f}h before expiry)"
            )
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                logger.warning("[po_auth] Proactive refresh failed — retrying in 30s")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)

        elif age >= hard_deadline:
            # Within 4 minutes of full expiry — emergency early refresh
            remaining = ttl - age
            logger.info(
                f"[po_auth] ⚡ SSID expiring in {remaining:.0f}s — emergency early refresh"
            )
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                logger.warning("[po_auth] Emergency refresh failed — retrying in 30s")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(60)

        else:
            # All good — check again in 60 seconds
            await asyncio.sleep(60)
