# Job History Agent

An intelligent HR co-pilot for adding and updating worker job history records in Workday via MCP. Built on Azure OpenAI GPT-4o with a Flask web UI and execution trace panel.

---

## Architecture

```
agent.py          ← Main agent loop (Azure OpenAI SDK + tool-use)
mcp_client.py     ← Workday MCP client (JSON-RPC 2.0 + OAuth refresh)
config.py         ← Loads credentials from environment / .env
refresh_token.py  ← OAuth auth code flow utility (run when refresh token expires)
web.py            ← Flask web server (chat UI + trace panel)
templates/        ← HTML UI and Workday logo
.env.example      ← Template — copy to .env and fill in values
requirements.txt  ← Python dependencies
```

### Flow

```
User message
    │
    ▼
GPT-4o (Azure OpenAI)  ←── system prompt + Workday tool schemas
    │  finish_reason = tool_calls
    ▼
WorkdayMCPClient.call_tool()
    │  POST https://us.agent.workday.com/mcp  (JSON-RPC 2.0)
    │  Authorization: Bearer <ASU access token>
    ▼
Tool result fed back to GPT-4o
    │  finish_reason = stop
    ▼
Final response to user
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- Azure OpenAI resource with a GPT-4o deployment
- A registered & activated Job History Agent in your Workday tenant
  (see *ASOR MCP EA Guide* for registration steps)
- ASU OAuth credentials (client_id, client_secret, refresh_token)
- Callback URI registered on the OAuth client: `https://localhost:8888/callback`

> **Workday Setup Status:** Register your agent in Workday and obtain ASU credentials
> before first run. See the *Available Tools* section below for required tool WIDs.

### 2. Install dependencies

```bash
cd job_history_agent
pip3 install -r requirements.txt --break-system-packages
```

### 3. Configure credentials

```bash
cp .env.example .env
# Edit .env with your actual values
```

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Your Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_KEY` | Your Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | GPT-4o deployment name |
| `AZURE_OPENAI_API_VERSION` | API version (e.g. `2024-12-01-preview`) |
| `WORKDAY_TENANT` | Your Workday tenant identifier, e.g. `your_tenant_id` |
| `ASU_CLIENT_ID` | ASU OAuth client ID |
| `ASU_CLIENT_SECRET` | ASU OAuth client secret |
| `ASU_REFRESH_TOKEN` | ASU OAuth refresh token (auto-rotated by mcp_client.py) |

### 4. Get a refresh token (first time or after expiry)

```bash
python3 refresh_token.py
```

### 5. Run the agent

**Web UI:**
```bash
python3 web.py
# Open http://localhost:8080
```

**Interactive CLI:**
```bash
python3 agent.py
```

**Single message:**
```bash
python3 agent.py --message "Add job history for Sarah Chen: Senior Engineer at Acme Corp, Jan 2020 - Mar 2023"
```

---

## Available Tools

| Tool | Description |
|---|---|
| `getWorkers` (+ `searchForWorker`, `getMyInfo`) | Look up workers by name or browse the workforce |
| `manageJobHistory` | Add or update job history entries for a worker |

> Tool WIDs are tenant-specific and are assigned when you register the agent resources
> in your Workday tenant. Obtain them from your Workday administrator or the ASOR API
> response. Once registered and activated, run `python3 agent.py` — it will call
> `tools/list` at startup to confirm the available schemas.

---

## Example Interactions

```
You: Add job history for Sarah Chen — she was a Senior Software Engineer at Acme Corp
     from January 2020 to March 2023.

Agent: I'll look up Sarah Chen first, then add the job history entry.
[calls searchForWorker → manageJobHistory]
Job history entry added for Sarah Chen.
Business Process WID: <wid>
```

```
You: What information do you need to add a job history entry?

Agent: To add a job history entry I need:
  • Worker name (to look up their Workday ID)
  • Job title / position
  • Company or employer name
  • Start date
  • End date (or "current" if still in the role)
  • Description (optional)
```
