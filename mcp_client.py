"""
Workday MCP Client
Handles JSON-RPC 2.0 communication with the Workday MCP endpoint,
including OAuth 2.0 token management with automatic refresh.
"""

import logging
import os
import re
import time
import warnings

# Suppress LibreSSL warning on older Macs — must come before requests import
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

import requests
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Path to .env file — same directory as this script
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _update_env_value(key: str, value: str) -> None:
    """Overwrite a single key's value in the .env file in-place."""
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH, "r") as f:
        contents = f.read()
    # Replace existing key=value line (handles quoted and unquoted values)
    pattern = rf"^({re.escape(key)}=).*$"
    updated = re.sub(pattern, rf"\g<1>{value}", contents, flags=re.MULTILINE)
    if updated == contents:
        # Key not found — append it
        updated = contents.rstrip() + f"\n{key}={value}\n"
    with open(_ENV_PATH, "w") as f:
        f.write(updated)
    logger.info(".env updated: %s rotated", key)

# ── OAuth endpoints ─────────────────────────────────────────────────────────
AUTH_TOKEN_URL = "https://us.agent.workday.com/auth/oauth2/{tenant}/token"
MCP_URL = "https://us.agent.workday.com/mcp"


class WorkdayMCPClient:
    """
    Stateful MCP client that:
      - Holds ASU OAuth credentials (client_id / client_secret / refresh_token)
      - Auto-refreshes the access token before it expires (uses expires_in from token response)
      - Sends JSON-RPC 2.0 calls to the Workday MCP endpoint
    """

    def __init__(
        self,
        tenant: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ):
        self.tenant = tenant
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._rpc_id = 0

        # Fetch a token immediately so the agent is ready to call tools
        self._refresh_access_token()

    # ── Token management ────────────────────────────────────────────────────

    def _refresh_access_token(self) -> None:
        """Exchange refresh_token for a new access_token. Retries once on failure."""
        url = AUTH_TOKEN_URL.format(tenant=self.tenant)
        last_exc: Optional[Exception] = None

        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self.refresh_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=15,
                )
                resp.raise_for_status()
                payload = resp.json()

                self._access_token = payload["access_token"]
                # Workday access tokens expire in 600 s; refresh 60 s early
                expires_in = int(payload.get("expires_in", 600))
                self._token_expires_at = time.time() + expires_in - 60

                # Workday rotates refresh tokens — update in memory AND persist to .env
                if "refresh_token" in payload:
                    self.refresh_token = payload["refresh_token"]
                    _update_env_value("ASU_REFRESH_TOKEN", self.refresh_token)

                logger.info("Access token refreshed, valid for ~%ss", expires_in)
                return

            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning("Token refresh failed (attempt 1), retrying in 2s: %s", exc)
                    time.sleep(2)

        raise RuntimeError(f"Token refresh failed after 2 attempts: {last_exc}") from last_exc

    def _get_token(self) -> str:
        if time.time() >= self._token_expires_at:
            self._refresh_access_token()
        return self._access_token  # type: ignore[return-value]

    # ── JSON-RPC helpers ─────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _rpc(self, method: str, params: Optional[dict] = None) -> Any:
        """Send a single JSON-RPC 2.0 request and return the result."""
        body: dict = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params:
            body["params"] = params

        resp = requests.post(
            MCP_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(
                f"MCP error {data['error']['code']}: {data['error']['message']}"
            )
        return data.get("result")

    # ── Public MCP methods ───────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """Return all tools available to this agent."""
        result = self._rpc("tools/list")
        return result.get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        Invoke a Workday MCP tool.

        Args:
            tool_name:  Exact tool name (e.g. "manageJobHistory")
            arguments:  Dict matching the tool's input schema

        Returns:
            The tool result (structure varies by tool)
        """
        logger.info("Calling tool: %s", tool_name)
        return self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
