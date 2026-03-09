"""
Configuration for the Job History Agent.

Best practice: set these values via environment variables rather than
hard-coding them here. Copy .env.example to .env and fill in your values,
then load with python-dotenv (pip install python-dotenv).

Alternatively, replace the os.getenv() calls below with your actual values
for quick local testing.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on real env vars

# ── Azure OpenAI ──────────────────────────────────────────────────────────────
# Endpoint:        Azure Portal → your OpenAI resource → Keys and Endpoint
# API key:         Same location, copy Key 1 or Key 2
# Deployment name: Azure OpenAI Studio → Deployments (e.g. "gpt-4o-deployment")
# API version:     Use the latest stable version shown below
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT_NAME: str = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")
AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-06")

# ── Workday tenant ────────────────────────────────────────────────────────────
# e.g. "wday_wcpdev4"
WORKDAY_TENANT: str = os.getenv("WORKDAY_TENANT", "")

# ── ASU (Agent System User) OAuth credentials ─────────────────────────────────
# These are the credentials shown ONCE when you activated the agent in Workday.
# Store them securely — never commit them to source control.
ASU_CLIENT_ID: str = os.getenv("ASU_CLIENT_ID", "")
ASU_CLIENT_SECRET: str = os.getenv("ASU_CLIENT_SECRET", "")
ASU_REFRESH_TOKEN: str = os.getenv("ASU_REFRESH_TOKEN", "")

# ── Validation ────────────────────────────────────────────────────────────────
_required = {
    "AZURE_OPENAI_ENDPOINT": AZURE_OPENAI_ENDPOINT,
    "AZURE_OPENAI_API_KEY": AZURE_OPENAI_API_KEY,
    "AZURE_OPENAI_DEPLOYMENT_NAME": AZURE_OPENAI_DEPLOYMENT_NAME,
    "WORKDAY_TENANT": WORKDAY_TENANT,
    "ASU_CLIENT_ID": ASU_CLIENT_ID,
    "ASU_CLIENT_SECRET": ASU_CLIENT_SECRET,
    "ASU_REFRESH_TOKEN": ASU_REFRESH_TOKEN,
}

missing = [k for k, v in _required.items() if not v]
if missing:
    raise EnvironmentError(
        f"Missing required config values: {', '.join(missing)}\n"
        "Set them in a .env file or as environment variables."
    )
