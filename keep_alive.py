"""Keep-alive web server for 24/7 uptime on Replit / UptimeRobot.
Runs a minimal Flask app on port 8080 in a background daemon thread so
external ping services (UptimeRobot, Better Uptime, etc.) can hit
GET / and prevent the Replit container from sleeping.
"""
from __future__ import annotations

import logging
import hmac
import os
import threading

from flask import Flask, request

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/internal/broker-feed-readiness")
def broker_feed_readiness():
    """Internal relay gate backed only by recent broker-native ticks."""
    expected = os.environ.get("SESSION_SECRET", "")
    supplied = request.headers.get("X-Internal-Secret", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        return {"error": "unauthorized"}, 401
    try:
        from otc_price_service import is_broker_feed_ready
        return {
            "qx": is_broker_feed_ready("qx"),
            "po": is_broker_feed_ready("po"),
        }, 200
    except Exception:
        logger.exception("Broker feed readiness check failed")
        return {"qx": False, "po": False}, 503


def keep_alive() -> None:
    """Start the keep-alive server in a background daemon thread."""
    def _run():
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.ERROR)
        app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="keep-alive")
    t.start()
    logger.info("🌐 Keep-alive server started on port 8080")
