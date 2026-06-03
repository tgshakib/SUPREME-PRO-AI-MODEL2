"""Quotex Auto-SSID Manager
============================
Mirrors the Pocket Option auth manager (po_auth.py) for Quotex.

IMPORTANT — Cloudflare reality:
  Quotex (qxbroker.com) sits behind Cloudflare Turnstile which blocks ALL
  headless HTTP logins.  The only way to get a fresh Quotex SSID token is
  through a real browser session.  quotexpy uses Chrome/Selenium for exactly
  this reason.  Since Chrome is not available on Replit servers, we use a
  different approach:

  1.  On first start — the token is read from the QUOTEX_SSID env var (set
      by the admin once via Replit Secrets).
  2.  The token is saved locally (.qx_ssid_cache) and seeded into quotexpy's
      sessions.pkl so quotexpy never calls Chrome.
  3.  Keepalive pings reset the server-side inactivity timer every 90 min.
  4.  When the token is near expiry (85% of TTL) the manager tries a
      Cloudflare-bypass route: the /api/v1/cabinets/digest endpoint accepts
      authenticated cookie strings (token=SSID) and returns a fresh token.
  5.  On rejection (WS sends "authorization/reject") the guardian sets
      QX_SSID_REJECTED=True and this manager logs a clear renewal notice.

How to set / renew the token:
  • Open qxbroker.com in your browser and log in.
  • F12 → Console → type: window.settings.token  → press Enter → copy value.
  • Set in Replit Secrets: QUOTEX_SSID = <paste value here>
  • The bot picks it up automatically on next restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

QX_EMAIL    = os.environ.get("QUOTEX_EMAIL",    "hosnaranupur@gmail.com")
QX_PASSWORD = os.environ.get("QUOTEX_PASSWORD", "hosnaranupur@")

_SSID_FILE         = os.path.join(os.path.dirname(__file__), ".qx_ssid_cache")
_LOGIN_RETRY_DELAY = 60   # seconds between retries

# ── Token lifetime ────────────────────────────────────────────────────────────
# Quotex tokens issued from window.settings.token last approximately 30 days
# when remember_me is active.  We use a conservative 7-day default and probe
# the actual expiry via the digest endpoint when possible.
_SSID_TTL_DEFAULT  = 7  * 24 * 3600    # 7 days (safe conservative)
_SSID_TTL_30D      = 30 * 24 * 3600    # 30 days (extended)
_REFRESH_RATIO     = 0.85              # refresh at 85% of lifetime
_EARLY_REFRESH_BUFFER = 240            # also refresh if < 4 min remain

_SSID_STAMP: dict = {}   # {"ssid": str, "fetched_at": float, "ttl": int}

# Public flag — set True by the stream when WS sends "authorization/reject"
QX_SSID_REJECTED: bool = False

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Origin": "https://qxbroker.com",
    "Referer": "https://qxbroker.com/en/trade",
}

# ── Token validation ──────────────────────────────────────────────────────────
def _is_real_qx_token(token: str) -> bool:
    """Return True only if this looks like a real Quotex API token.

    Rejects:
      • Laravel encrypted session cookies — base64 JSON blobs starting with
        eyJpdiI6 ({"iv":"...}) or ending with %3D / %3D%3D (URL-encoded =)
      • Very short strings (< 20 chars)
      • Cloudflare tokens (__cf_bm, _cfuvid prefix patterns)

    Accepts:
      • Long alphanumeric tokens (the real window.settings.token format)
      • UUID-style tokens
    """
    if not token or len(token) < 20:
        return False
    # Laravel encrypted session always starts with eyJpdiI6 when base64-decoded
    # In raw form the cookie value starts with eyJpdiI6 (base64 of {"iv":)
    if token.startswith("eyJpdiI6"):
        return False
    # URL-encoded Laravel cookies end with %3D
    if "%3D" in token and len(token) > 100:
        return False
    # Cloudflare tokens
    if token.startswith("__cf") or token.startswith("_cf"):
        return False
    return True


# ── quotexpy sessions.pkl ─────────────────────────────────────────────────────
def _qxpy_sessions_path() -> Optional[str]:
    try:
        from quotexpy.utils import sessions_file_path
        return sessions_file_path
    except Exception:
        return None


def _seed_qxpy_session(ssid: str, cookies: str = ""):
    """Write the SSID into quotexpy's sessions.pkl so Chrome is never called."""
    path = _qxpy_sessions_path()
    if not path:
        return
    try:
        data: dict = {}
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
            except Exception:
                data = {}
        data[QX_EMAIL] = [{
            "ssid":       ssid,
            "cookies":    cookies or f"token={ssid}",
            "user_agent": _REQUEST_HEADERS["User-Agent"],
        }]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(data, f)
        logger.debug("[qx_auth] quotexpy sessions.pkl seeded — Chrome bypass active")
    except Exception as exc:
        logger.debug(f"[qx_auth] Could not seed sessions.pkl: {exc}")


# ── Digest-based token refresh (works if session cookies are valid) ────────────
def _try_digest_refresh(current_ssid: str) -> Optional[str]:
    """Try GET /api/v1/cabinets/digest with the current SSID as cookie.

    Returns a fresh token string if the server accepts it, None otherwise.
    This endpoint works when the Cloudflare session is still valid.
    """
    try:
        import requests
        import urllib3
        urllib3.disable_warnings()

        headers = {
            **_REQUEST_HEADERS,
            "Cookie": f"token={current_ssid}; session={current_ssid}",
            "Accept": "application/json",
        }
        for url in [
            "https://qxbroker.com/api/v1/cabinets/digest",
            "https://qxbroker.com/api/v1/cabinet/digest",
        ]:
            try:
                r = requests.get(url, headers=headers, timeout=12, verify=False)
                if r.status_code == 200:
                    try:
                        d = r.json()
                        # {"data": {"token": "NEW_TOKEN"}} or {"token": "..."}
                        tok = (
                            (d.get("data") or {}).get("token")
                            or d.get("token")
                        )
                        if tok and isinstance(tok, str) and len(tok) > 20:
                            logger.info(f"[qx_auth] ✅ Token refreshed via digest {url}")
                            return tok
                    except Exception:
                        pass
                elif r.status_code == 401:
                    logger.debug(f"[qx_auth] Digest {url} → 401 (token expired)")
                    return None
            except Exception as exc:
                logger.debug(f"[qx_auth] Digest {url} error: {exc}")
    except ImportError:
        pass
    return None


# ── Cache / persistence ───────────────────────────────────────────────────────
def _detect_token_ttl(token: str) -> int:
    """Heuristic TTL detection: long tokens → 30d, otherwise 7d."""
    if not token:
        return _SSID_TTL_DEFAULT
    if len(token.strip()) > 80:
        return _SSID_TTL_30D
    return _SSID_TTL_DEFAULT


def _save_ssid(ssid: str, ttl: Optional[int] = None, cookies: str = ""):
    """Persist SSID to cache file, env var, and quotexpy sessions.pkl."""
    os.environ["QUOTEX_SSID"] = ssid
    real_ttl = ttl if (ttl and 3600 < ttl <= _SSID_TTL_30D) else _detect_token_ttl(ssid)
    now = time.time()
    try:
        with open(_SSID_FILE, "w") as f:
            json.dump({"ssid": ssid, "fetched_at": now, "ttl": real_ttl, "cookies": cookies}, f)
    except Exception as exc:
        logger.debug(f"[qx_auth] Could not save cache: {exc}")
    _SSID_STAMP.update({"ssid": ssid, "fetched_at": now, "ttl": real_ttl})
    _seed_qxpy_session(ssid, cookies)
    _notify_services(ssid)
    token_type = "30d" if real_ttl >= _SSID_TTL_30D else f"{real_ttl//3600}h"
    logger.info(
        f"[qx_auth] ✅ QX SSID saved — type={token_type}  "
        f"auto-refresh in {real_ttl * _REFRESH_RATIO / 3600:.1f}h"
    )


def _load_cached_ssid() -> Optional[str]:
    """Load SSID from cache file or env var.

    Validates that the token is a real Quotex API token — rejects Laravel
    encrypted session cookies (eyJpdiI6...) that Cloudflare hands out.
    """
    # 1. Cache file
    try:
        if os.path.exists(_SSID_FILE):
            with open(_SSID_FILE, "r") as f:
                d = json.load(f)
            ssid       = d.get("ssid", "")
            fetched_at = d.get("fetched_at", 0)
            ttl        = int(d.get("ttl", _SSID_TTL_DEFAULT))
            age        = time.time() - fetched_at
            if ssid and _is_real_qx_token(ssid) and age < ttl * _REFRESH_RATIO:
                if not _SSID_STAMP.get("fetched_at"):
                    _SSID_STAMP.update({"ssid": ssid, "fetched_at": fetched_at, "ttl": ttl})
                return ssid
            elif ssid and not _is_real_qx_token(ssid):
                logger.warning(
                    "[qx_auth] ⚠️  Cached SSID looks like a Laravel session cookie — ignoring. "
                    "Set QUOTEX_SSID in Replit Secrets with the real token from window.settings.token"
                )
                try:
                    os.remove(_SSID_FILE)
                except Exception:
                    pass
    except Exception:
        pass
    # 2. Env var (manual secret)
    env_ssid = os.environ.get("QUOTEX_SSID", "").strip()
    if env_ssid:
        if not _is_real_qx_token(env_ssid):
            logger.warning(
                "[qx_auth] ⚠️  QUOTEX_SSID env var looks like a Laravel session cookie — ignoring. "
                "Copy the real token from window.settings.token in your browser's DevTools."
            )
            return None
        if not _SSID_STAMP.get("fetched_at"):
            ttl = _detect_token_ttl(env_ssid)
            _SSID_STAMP.update({"ssid": env_ssid, "fetched_at": 0, "ttl": ttl})
        return env_ssid
    return None


def _notify_services(ssid: str):
    """Push the new SSID to active QX stream so it reconnects immediately."""
    global QX_SSID_REJECTED
    try:
        import otc_price_service as _svc
        _svc.QX_SSID = ssid
        logger.info("[qx_auth] Updated otc_price_service.QX_SSID")
    except Exception:
        pass
    QX_SSID_REJECTED = False


# ── Keepalive ─────────────────────────────────────────────────────────────────
_KEEPALIVE_INTERVAL = 90 * 60   # 90 minutes

def _do_keepalive_ping(ssid: str) -> bool:
    """Ping qxbroker.com with the SSID cookie to reset the inactivity timer."""
    try:
        import requests, urllib3
        urllib3.disable_warnings()
        headers = {
            **_REQUEST_HEADERS,
            "Cookie": f"token={ssid}; session={ssid}",
        }
        for url in [
            "https://qxbroker.com/api/v1/cabinets/digest",
            "https://qxbroker.com/",
        ]:
            try:
                r = requests.get(url, headers=headers, timeout=12, verify=False)
                if r.status_code < 500:
                    logger.debug(f"[qx_auth] QX keepalive {url} → {r.status_code}")
                    return True
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"[qx_auth] Keepalive error: {exc}")
    return False


async def _keepalive_loop():
    logger.info(f"[qx_auth] 🔄 QX SSID keepalive started — pinging every {_KEEPALIVE_INTERVAL//60}min")
    await asyncio.sleep(120)   # warm-up
    loop = asyncio.get_event_loop()
    while True:
        ssid = _SSID_STAMP.get("ssid") or os.environ.get("QUOTEX_SSID", "")
        if ssid:
            try:
                await loop.run_in_executor(None, _do_keepalive_ping, ssid)
            except Exception:
                pass
        await asyncio.sleep(_KEEPALIVE_INTERVAL)


# ── Renewal helpers ───────────────────────────────────────────────────────────
def refresh_ssid_now() -> bool:
    """Try to refresh the SSID via digest endpoint.

    Returns True if a new token was obtained, False otherwise.
    Logs a clear renewal instruction if HTTP refresh fails (Cloudflare).
    """
    current = _SSID_STAMP.get("ssid") or os.environ.get("QUOTEX_SSID", "")

    # Try digest-based renewal if we have a current token
    if current:
        new_token = _try_digest_refresh(current)
        if new_token:
            _save_ssid(new_token)
            return True

    # Cloudflare blocks all headless HTTP logins — cannot auto-renew
    logger.warning(
        "[qx_auth] ⚠️ QX SSID renewal via HTTP failed (Cloudflare protected).\n"
        "          To renew manually:\n"
        "          1. Open qxbroker.com in your browser and log in.\n"
        "          2. Press F12 → Console → type: window.settings.token → Enter.\n"
        "          3. Copy the value and set QUOTEX_SSID in Replit Secrets.\n"
        "          4. Restart the bot — the new token is picked up automatically."
    )
    return False


# ── Public API ────────────────────────────────────────────────────────────────
def get_current_ssid() -> str:
    """Return the active QX SSID. Loads from cache/env, seeds sessions.pkl."""
    cached = _load_cached_ssid()
    if cached:
        # Always seed sessions.pkl so quotexpy skips Chrome
        _seed_qxpy_session(cached)
        os.environ["QUOTEX_SSID"] = cached
        return cached
    return ""


# ── Background manager ────────────────────────────────────────────────────────
async def run_qx_auth_manager():
    """Background task: keeps the QX SSID alive indefinitely.

    Strategy:
      1. Read SSID from cache / QUOTEX_SSID env var.
      2. Seed quotexpy sessions.pkl → Chrome never needed.
      3. Keepalive ping every 90 min (resets server inactivity).
      4. Proactive refresh at 85% of TTL via digest endpoint.
      5. On WS rejection (QX_SSID_REJECTED=True) → try digest, then log renewal.
    """
    global QX_SSID_REJECTED
    logger.info("[qx_auth] Starting Quotex auth manager …")
    logger.info(f"[qx_auth] Using account: {QX_EMAIL}")

    asyncio.create_task(_keepalive_loop(), name="qx_ssid_keepalive")

    existing = get_current_ssid()
    if existing:
        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _SSID_TTL_DEFAULT)
        age           = time.time() - fetched_at
        refresh_after = ttl * _REFRESH_RATIO

        if fetched_at == 0:
            # Came from env var (manual set) — save it properly now
            logger.info("[qx_auth] QX SSID loaded from QUOTEX_SSID env var — saving …")
            _save_ssid(existing)
        elif age >= refresh_after:
            logger.info(f"[qx_auth] QX SSID {age/3600:.1f}h old ≥ threshold — refreshing …")
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
        else:
            logger.info(
                f"[qx_auth] QX SSID loaded — valid  "
                f"(next refresh in {(refresh_after - age)/3600:.1f}h)"
            )
    else:
        logger.warning(
            "[qx_auth] ⚠️ No QUOTEX_SSID found.\n"
            "          QX OTC stream disabled until token is provided.\n"
            "          To enable: set QUOTEX_SSID in Replit Secrets\n"
            "          (qxbroker.com → F12 → Console → window.settings.token)"
        )

    # Maintenance loop
    while True:
        await asyncio.sleep(60)

        fetched_at    = _SSID_STAMP.get("fetched_at", 0)
        ttl           = _SSID_STAMP.get("ttl", _SSID_TTL_DEFAULT)
        age           = time.time() - fetched_at
        refresh_after = ttl * _REFRESH_RATIO
        hard_deadline = ttl - _EARLY_REFRESH_BUFFER

        # WS rejection detected by stream
        if QX_SSID_REJECTED:
            logger.info("[qx_auth] QX SSID rejected by WS — attempting refresh …")
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if ok:
                QX_SSID_REJECTED = False
            else:
                await asyncio.sleep(300)   # wait 5 min before next attempt
            continue

        # Check if env var was updated externally (admin set new QUOTEX_SSID)
        env_ssid = os.environ.get("QUOTEX_SSID", "").strip()
        current_ssid = _SSID_STAMP.get("ssid", "")
        if env_ssid and env_ssid != current_ssid and _is_real_qx_token(env_ssid):
            logger.info("[qx_auth] QUOTEX_SSID env var updated — applying new token …")
            _save_ssid(env_ssid)
            continue
        elif env_ssid and not _is_real_qx_token(env_ssid):
            logger.warning(
                "[qx_auth] ⚠️  QUOTEX_SSID looks like a Laravel/Cloudflare cookie — skipping. "
                "Use the real token from: qxbroker.com → F12 → Console → window.settings.token"
            )

        if not current_ssid:
            # Still no token — check env again
            env_ssid = os.environ.get("QUOTEX_SSID", "").strip()
            if env_ssid and _is_real_qx_token(env_ssid):
                _save_ssid(env_ssid)
            continue

        if age >= ttl:
            logger.info("[qx_auth] QX SSID fully expired — refreshing …")
            ok = await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
            if not ok:
                await asyncio.sleep(_LOGIN_RETRY_DELAY)

        elif age >= refresh_after:
            remaining = ttl - age
            logger.info(
                f"[qx_auth] ⚡ QX SSID at {age/3600:.1f}h/{ttl/3600:.0f}h — "
                f"proactive refresh ({remaining/3600:.1f}h before expiry)"
            )
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)

        elif age >= hard_deadline:
            remaining = ttl - age
            logger.info(f"[qx_auth] ⚡ QX SSID expiring in {remaining:.0f}s — emergency refresh")
            await asyncio.get_event_loop().run_in_executor(None, refresh_ssid_now)
