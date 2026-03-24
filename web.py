"""
Web UI for the Job History Agent.
Flask server exposing /chat, /clear, and the static UI at /.

Usage:
    python3 web.py
Then open http://localhost:8080 in your browser.
"""

import json
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, request as flask_request, send_file, session

from agent import JobHistoryAgent

logger = logging.getLogger(__name__)

_ROOT_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _ROOT_DIR / "templates"
_LOGO_PATH = _TEMPLATE_DIR / "workday-logo.svg"

# Session TTL: evict conversations idle for more than 24 hours
_SESSION_TTL_SECONDS = 86_400

web_app = Flask(__name__, static_folder=None)
web_app.secret_key = secrets.token_hex(32)

# Lazy-initialized agent, protected by a lock so concurrent startup requests
# don't race to create multiple instances.
_agent: JobHistoryAgent | None = None
_agent_lock = threading.Lock()

# Per-session conversation history: {session_id: {"history": [...], "last_access": float}}
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _get_agent() -> JobHistoryAgent:
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = JobHistoryAgent()
    return _agent


def _get_session_id() -> str:
    """Return a stable UUID for this browser session, set as a signed cookie."""
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def _evict_stale_sessions() -> None:
    """Remove sessions that haven't been accessed within SESSION_TTL_SECONDS."""
    cutoff = time.time() - _SESSION_TTL_SECONDS
    stale = [sid for sid, data in _sessions.items() if data["last_access"] < cutoff]
    for sid in stale:
        del _sessions[sid]
    if stale:
        logger.info("Evicted %d stale session(s)", len(stale))


@web_app.route("/logo.svg")
def serve_logo():
    return send_file(_LOGO_PATH, mimetype="image/svg+xml")


@web_app.route("/")
def index():
    html = (_TEMPLATE_DIR / "index.html").read_text()
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@web_app.route("/chat", methods=["POST"])
def chat():
    data = flask_request.get_json() or {}
    message = (data.get("message") or "").strip()

    if not message:
        return json.dumps({"error": "Empty message"}), 400, {"Content-Type": "application/json"}

    session_id = _get_session_id()

    try:
        agent = _get_agent()

        with _sessions_lock:
            _evict_stale_sessions()
            session_data = _sessions.get(session_id, {"history": [], "last_access": 0.0})
            history = session_data["history"]

        response, tools_used, trace, updated_history = agent.chat(message, history)

        # Keep rolling window of last 40 messages (~20 turns)
        if len(updated_history) > 40:
            updated_history = updated_history[-40:]

        with _sessions_lock:
            _sessions[session_id] = {"history": updated_history, "last_access": time.time()}

        return (
            json.dumps({"response": response, "tools_used": tools_used, "trace": trace}),
            200,
            {"Content-Type": "application/json"},
        )
    except Exception as exc:
        logger.exception("Error handling chat request for session %s", session_id)
        return (
            json.dumps({"error": str(exc)}),
            500,
            {"Content-Type": "application/json"},
        )


@web_app.route("/clear", methods=["POST"])
def clear():
    session_id = _get_session_id()
    with _sessions_lock:
        _sessions.pop(session_id, None)
    return json.dumps({"status": "cleared"}), 200, {"Content-Type": "application/json"}


def run_web():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    print("\n" + "=" * 60)
    print("  Job History Agent — Web Interface")
    print("=" * 60)
    print("\nOpen http://localhost:8080 in your browser")
    print("Press Ctrl+C to stop the server")
    print("=" * 60 + "\n")
    web_app.run(host="0.0.0.0", port=8080, debug=False)


if __name__ == "__main__":
    run_web()
