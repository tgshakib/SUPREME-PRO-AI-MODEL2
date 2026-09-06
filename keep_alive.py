"""Keep-alive web server for 24/7 uptime on Replit / UptimeRobot.
Runs a minimal Flask app on port 8080 in a background daemon thread so
external ping services (UptimeRobot, Better Uptime, etc.) can hit
GET / and prevent the Replit container from sleeping.
"""
from __future__ import annotations

import logging
import threading

from flask import Flask

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def keep_alive() -> None:
    """Start the keep-alive server in a background daemon thread."""
    def _run():
        import logging as _log
        _log.getLogger("werkzeug").setLevel(_log.ERROR)
        app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="keep-alive")
    t.start()
    logger.info("🌐 Keep-alive server started on port 8080")
