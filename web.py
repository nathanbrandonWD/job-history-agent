"""
Web UI for the Job History Agent.
Flask server exposing /chat, /clear, and the static UI at /.

Usage:
    python3 web.py
Then open http://localhost:8080 in your browser.
"""

import json
from pathlib import Path

from flask import Flask, request as flask_request, send_file

from agent import JobHistoryAgent

_ROOT_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _ROOT_DIR / "templates"
_LOGO_PATH = _TEMPLATE_DIR / "workday-logo.svg"

web_app = Flask(__name__, static_folder=None)

# Lazy-initialized agent (shared across all requests)
_agent: JobHistoryAgent | None = None

# Per-session conversation history keyed by remote IP
_sessions: dict[str, list[dict]] = {}


def _get_agent() -> JobHistoryAgent:
    global _agent
    if _agent is None:
        _agent = JobHistoryAgent()
    return _agent


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

    session_id = flask_request.remote_addr or "default"

    try:
        agent = _get_agent()
        history = _sessions.get(session_id, [])

        response, tools_used, trace = agent.chat(message, history)

        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ]
        # Keep rolling window of last 40 messages (20 turns)
        if len(history) > 40:
            history = history[-40:]
        _sessions[session_id] = history

        return (
            json.dumps({"response": response, "tools_used": tools_used, "trace": trace}),
            200,
            {"Content-Type": "application/json"},
        )
    except Exception as exc:
        return (
            json.dumps({"error": str(exc)}),
            500,
            {"Content-Type": "application/json"},
        )


@web_app.route("/clear", methods=["POST"])
def clear():
    session_id = flask_request.remote_addr or "default"
    _sessions.pop(session_id, None)
    return json.dumps({"status": "cleared"}), 200, {"Content-Type": "application/json"}


def run_web():
    print("\n" + "=" * 60)
    print("  Job History Agent — Web Interface")
    print("=" * 60)
    print("\nOpen http://localhost:8080 in your browser")
    print("Press Ctrl+C to stop the server")
    print("=" * 60 + "\n")
    web_app.run(host="0.0.0.0", port=8080, debug=False)


if __name__ == "__main__":
    run_web()
