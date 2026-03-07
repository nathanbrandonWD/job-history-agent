# Job History Agent — Cursor Context

This file gives Cursor full context on the project so it can assist accurately.

---

## What This Is

An AI-powered HR co-pilot for adding and updating worker job history records in Workday. It connects to Workday via the Model Context Protocol (MCP) and uses Azure OpenAI (GPT-4o) for language model inference.

**v1 (current):** Job History management only — add and update job history entries for workers.

---

## Tech Stack

| Component | Detail |
|---|---|
| LLM | Azure OpenAI GPT-4o via `openai` SDK (`AzureOpenAI` client) |
| Workday integration | Workday MCP — JSON-RPC 2.0 over HTTPS at `https://us.agent.workday.com/mcp` |
| Auth | OAuth 2.0 with rotating refresh tokens (ASU = Agent System User) |
| Web UI | Flask + single-page HTML with chat + execution trace panel |
| Config | `.env` via `python-dotenv` |

**Important:** Uses **Azure OpenAI** — `agent.py` imports `from openai import AzureOpenAI`. Do not rewrite to Anthropic SDK or LangChain.

---

## File Overview

| File | Purpose |
|---|---|
| `agent.py` | Main agent loop, system prompt, Azure OpenAI function-calling loop, trace generation |
| `web.py` | Flask web server — routes `/`, `/chat`, `/clear`, `/logo.svg` |
| `mcp_client.py` | Workday MCP JSON-RPC client + OAuth token refresh + .env persistence |
| `config.py` | Loads and validates env vars; raises `EnvironmentError` if any missing |
| `refresh_token.py` | One-shot OAuth auth code flow — run when refresh token expires |
| `templates/index.html` | Web UI — chat panel + collapsible execution trace panel |
| `templates/workday-logo.svg` | Workday logo asset |
| `.env` | Secrets — never commit |
| `requirements.txt` | `openai`, `requests`, `python-dotenv`, `cryptography`, `flask` |

---

## Workday Setup Status

### ⚠️ Agent definition NOT yet registered in Workday

The following must be completed before the agent can make MCP calls:

1. Register the agent in Workday via ASOR (`https://us.agent.workday.com/asor/v1/agentDefinition`)
2. Add the following tools to the agent definition:
   - `getWorkers` — WID `94914bb1185d10000de91f2013e70032` (inherits `searchForWorker` and `getMyInfo`)
   - `manageJobHistory` — WID `939b097c68df100015371634996f0000`
3. Activate the agent and note the ASU client_id, client_secret
4. Run `python3 refresh_token.py` to get the initial refresh token
5. Fill in `.env` with all credentials

---

## Known Tool

### manageJobHistory

| Field | Value |
|---|---|
| **WID** | `939b097c68df100015371634996f0000` |
| **Description** | Adds or updates job histories for a worker. Submits the Manage Job History business process. Can submit one or more job history entries for the specified worker. |
| **Schema status** | ⚠️ Placeholder — exact field names/nesting TBD once `tools/list` is called at startup after registration |

**Expected input shape (to be confirmed):**
```json
{
  "worker": { "id": { "id": "<workerWID>", "type": "WID" } },
  "jobHistoryEntries": [
    {
      "jobTitle": "Senior Engineer",
      "company": "Acme Corp",
      "startDate": "2020-01-15",
      "endDate": "2023-03-31",
      "description": "Optional summary"
    }
  ]
}
```

> Update the system prompt in `agent.py` and this section once the real schema is confirmed from `tools/list` output.

---

## Tool Inheritance

`searchForWorker` and `getMyInfo` inherit automatically from `getWorkers` — no separate agent definition entry needed.

---

## Critical Implementation Patterns (inherited from Team Success Agent)

### IdentifierInput — Two Forms

**Nested** — used for entity references like `worker`:
```json
{ "id": { "id": "<WID>", "type": "WID" } }
```

**Flat** — used for event/transaction IDs:
```json
{ "id": "<WID>", "type": "WID" }
```

Rule of thumb: `worker`, `organization` → nested. Event/transaction IDs → flat.

---

## Token Management

- Access token TTL: **3600s (60 min)**
- Refresh token TTL: **24 hours**
- Refresh tokens **rotate on every exchange**
- `mcp_client.py` auto-persists the new refresh token to `.env`
- If `ASU_REFRESH_TOKEN` is stale → run `python3 refresh_token.py`

---

## Environment Variables

```
AZURE_OPENAI_ENDPOINT          # base domain only, no path
AZURE_OPENAI_API_KEY
AZURE_OPENAI_DEPLOYMENT_NAME   # e.g. gpt-4o-deployment
AZURE_OPENAI_API_VERSION       # e.g. 2024-12-01-preview
WORKDAY_TENANT                 # e.g. wday_wcpdev4
ASU_CLIENT_ID                  # from Workday agent activation
ASU_CLIENT_SECRET
ASU_REFRESH_TOKEN              # auto-rotated by mcp_client.py
```

---

## Workday Endpoints

```
MCP:        https://us.agent.workday.com/mcp
Token:      https://us.agent.workday.com/auth/oauth2/<tenant>/token
Authorize:  https://us.agent.workday.com/auth/authorize/<tenant>
ASOR:       https://us.agent.workday.com/asor/v1/agentDefinition
```

---

## Known Gotchas

- `pip` not available on this Mac — use `pip3 install ... --break-system-packages`
- Run with `python3 agent.py` or `python3 web.py`, not `python`
- The `chat()` method returns `(content, tools_used, trace)` — always unpack all three
- `_update_env_value()` in `mcp_client.py` uses regex to update `.env` in-place
- `handle_request()` in `refresh_token.py` loops with a 5s timeout to handle Chrome's TLS probe
- Redirect URI registered on the OAuth client: `https://localhost:8888/callback`
- Web UI runs on port `8080` — separate from the OAuth callback port `8888`
