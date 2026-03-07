"""
refresh_token.py — Workday ASU OAuth Token Refresh Utility

Automates the full OAuth 2.0 authorization_code flow for the Agent System User (ASU):
  1. Generates a temporary self-signed TLS certificate for localhost
  2. Opens the Workday authorize URL in your browser
  3. Starts a temporary local HTTPS server on https://localhost:8888 to catch the redirect
  4. Exchanges the auth code for a fresh access_token + refresh_token
  5. Writes the new ASU_REFRESH_TOKEN directly into your .env file

NOTE: Your browser will show a security warning when redirected to https://localhost:8888.
  - Chrome: click "Advanced" then "Proceed to localhost (unsafe)"
  - Safari: click "Show Details" then "visit this website"
  This is expected — the cert is self-signed and only used locally.

Requires: pip install cryptography (in addition to existing requirements)

Run this whenever your refresh token has expired and the agent can no longer start.

Usage:
    python3 refresh_token.py
"""

import os
import re
import ssl
import sys
import datetime
import ipaddress
import tempfile
import webbrowser
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import subprocess
import requests
from dotenv import load_dotenv

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("[ERROR] Missing dependency: cryptography")
    print("Install it with: pip3 install cryptography --break-system-packages")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────────────

REDIRECT_URI   = "https://localhost:8888/callback"
CALLBACK_PORT  = 8888
ENV_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

AUTHORIZE_URL  = "https://us.agent.workday.com/auth/authorize/{tenant}"
TOKEN_URL      = "https://us.agent.workday.com/auth/oauth2/{tenant}/token"

# ── Load credentials from .env ───────────────────────────────────────────────

load_dotenv(ENV_PATH)

TENANT        = os.getenv("WORKDAY_TENANT", "")
CLIENT_ID     = os.getenv("ASU_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ASU_CLIENT_SECRET", "")

missing = [k for k, v in {
    "WORKDAY_TENANT":    TENANT,
    "ASU_CLIENT_ID":     CLIENT_ID,
    "ASU_CLIENT_SECRET": CLIENT_SECRET,
}.items() if not v]

if missing:
    print(f"[ERROR] Missing required .env values: {', '.join(missing)}")
    sys.exit(1)

# ── Helper: update a single key in .env ──────────────────────────────────────

def _update_env_value(key: str, value: str) -> None:
    """Overwrite a single key=value line in .env in-place."""
    if not os.path.exists(ENV_PATH):
        print(f"[WARN] .env not found at {ENV_PATH} — cannot persist token.")
        return
    with open(ENV_PATH, "r") as f:
        contents = f.read()
    pattern = rf"^({re.escape(key)}=).*$"
    updated = re.sub(pattern, rf"\g<1>{value}", contents, flags=re.MULTILINE)
    if updated == contents:
        updated = contents.rstrip("\n") + f"\n{key}={value}\n"
    with open(ENV_PATH, "w") as f:
        f.write(updated)
    print(f"[refresh_token] .env updated: {key} written")

# ── Step 1: Generate self-signed TLS certificate for localhost ───────────────

print("\n─────────────────────────────────────────────────────")
print("  Workday ASU Token Refresh")
print("─────────────────────────────────────────────────────")
print("\nGenerating temporary self-signed certificate for localhost...")

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(private_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    )
    .sign(private_key, hashes.SHA256())
)

# Write cert and key to temp files (ssl module needs file paths)
tmp_cert = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
tmp_key  = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")

tmp_cert.write(cert.public_bytes(serialization.Encoding.PEM))
tmp_cert.close()

tmp_key.write(private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
))
tmp_key.close()

print("Certificate generated.")

# ── Step 2: Build authorize URL and open browser ─────────────────────────────

params = urllib.parse.urlencode({
    "client_id":     CLIENT_ID,
    "response_type": "code",
    "redirect_uri":  REDIRECT_URI,
    "state":         "refresh_flow",
})

auth_url = AUTHORIZE_URL.format(tenant=TENANT) + "?" + params

print(f"\nOpening browser to Workday authorize URL...")
print(f"If it doesn't open automatically, paste this into your browser:\n\n  {auth_url}\n")
print("⚠️  After approving, your browser will warn about the self-signed certificate.")
print("    Chrome: click 'Advanced' → 'Proceed to localhost (unsafe)'")
print("    Safari: click 'Show Details' → 'visit this website'\n")

subprocess.run(["open", "-a", "Google Chrome", auth_url])

# ── Step 3: HTTPS server to catch the redirect ───────────────────────────────

auth_code = None

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        query  = urllib.parse.parse_qs(parsed.query)

        if "code" in query:
            auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;padding:2rem;">
                <h2>&#10003; Authorization successful</h2>
                <p>Auth code received. You can close this tab and return to your terminal.</p>
                </body></html>
            """)
        else:
            error = query.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"""
                <html><body style="font-family:sans-serif;padding:2rem;">
                <h2>&#10007; Authorization failed</h2>
                <p>Error: {error}</p>
                </body></html>
            """.encode())

    def log_message(self, format, *args):
        pass  # Silence default request logging

print(f"Waiting for Workday to redirect to {REDIRECT_URI} ...")
print("(Complete the login/consent in your browser)\n")

try:
    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=tmp_cert.name, keyfile=tmp_key.name)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.timeout = 5  # seconds per handle_request attempt

    # Keep looping until we capture the code or hit the 2-minute timeout
    import time
    deadline = time.time() + 120
    while not auth_code and time.time() < deadline:
        try:
            server.handle_request()
        except Exception:
            pass  # Ignore SSL probe connections and other transient errors

    server.server_close()
finally:
    # Always clean up temp cert files
    os.unlink(tmp_cert.name)
    os.unlink(tmp_key.name)

if not auth_code:
    print("[ERROR] Did not receive an auth code within 2 minutes. Check browser for errors.")
    sys.exit(1)

print("[refresh_token] Auth code received.")

# ── Step 4: Exchange auth code for tokens ────────────────────────────────────

print("[refresh_token] Exchanging auth code for tokens...")

resp = requests.post(
    TOKEN_URL.format(tenant=TENANT),
    data={
        "grant_type":    "authorization_code",
        "code":          auth_code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    },
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    timeout=15,
)

if not resp.ok:
    print(f"[ERROR] Token exchange failed: {resp.status_code} {resp.text}")
    sys.exit(1)

payload = resp.json()

access_token  = payload.get("access_token", "")
refresh_token = payload.get("refresh_token", "")
expires_in    = payload.get("expires_in", "?")

if not refresh_token:
    print("[ERROR] No refresh_token in response. Full response:")
    print(payload)
    sys.exit(1)

# ── Step 5: Persist new refresh token to .env ────────────────────────────────

_update_env_value("ASU_REFRESH_TOKEN", refresh_token)

print("\n─────────────────────────────────────────────────────")
print("  Token refresh complete!")
print("─────────────────────────────────────────────────────")
print(f"  Access token valid for: {expires_in}s")
print(f"  Refresh token:          {refresh_token[:12]}...{refresh_token[-6:]}")
print(f"  Saved to:               {ENV_PATH}")
print("\n── Access token (for Bruno / API testing) ───────────")
print(f"  {access_token}")
print("\nYou can now run agent.py normally.\n")
