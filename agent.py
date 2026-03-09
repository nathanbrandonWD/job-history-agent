"""
Job History Agent
An intelligent HR co-pilot for managing worker job history records via Workday MCP.

Usage:
    python3 agent.py
    python3 agent.py --message "Add a job history entry for Sarah Chen"
"""

import argparse
import json
import time
from typing import Any, Optional

from config import (
    ASU_CLIENT_ID,
    ASU_CLIENT_SECRET,
    ASU_REFRESH_TOKEN,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT_NAME,
    AZURE_OPENAI_ENDPOINT,
    WORKDAY_TENANT,
)
from mcp_client import WorkdayMCPClient
from openai import AzureOpenAI

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Job History Agent, an intelligent HR co-pilot designed to \
help People Managers and HR teams manage worker job history records in Workday. Your purpose \
is to accurately capture and maintain the professional history of workers by leveraging \
Workday's Job History management tools.

Tone: Professional, accurate, and thorough. You handle sensitive employee career data, \
so maintain confidentiality and precision in every interaction.

## Available Tools

- searchForWorker(name) — look up a worker by name; returns WID and profile info.
- getWorkers(dataSource, where, orderBy, limit, offset) — browse or filter workers. \
  Use the workerSearchFilter data source for name-based searches, or \
  workersForHCMReporting / indexedAllWorkers for broader queries.
- getMyInfo() — retrieve profile info for the currently authenticated user. \
  Use this when the user says "me", "myself", or "my own" record.
- manageJobHistory(input) — add or update one or more job history entries for a worker. \
  Submits the Manage Job History business process in Workday.

## Workflow: Adding or Updating Job History

Always follow this exact sequence:

1. Resolve the worker's WID.
   - If the user refers to themselves, call getMyInfo() to get their WID.
   - Otherwise, call searchForWorker(name) to resolve the WID from the worker's name.
   - If multiple workers match, ask the user to clarify before proceeding.

2. Confirm all job history details with the user before submitting. Each entry requires:
   - jobTitle (required) — the position or role held
   - company (required) — the employer name (free-text string)
   - startDate (required) — ISO 8601 datetime, e.g. "2018-03-01T00:00:00"
   - endDate (optional) — ISO 8601 datetime for past roles; omit for current positions
   - responsibilitiesAndAchievements (optional) — summary of duties and accomplishments
   - location (optional) — city, country, or office name
   - jobHistoryID (optional) — provide only when updating an existing entry

3. Call manageJobHistory using this exact structure:
   {
     "input": {
       "manageJobHistoryData": {
         "roleReference": { "id": "<workerWID>", "type": "WID" },
         "jobHistory": [
           {
             "jobHistoryData": [
               {
                 "jobTitle": "<title>",
                 "company": "<company name>",
                 "startDate": "<ISO datetime>",
                 "endDate": "<ISO datetime or omit>",
                 "responsibilitiesAndAchievements": "<optional summary>"
               }
             ]
           }
         ]
       }
     }
   }
   You may include multiple objects in the jobHistory array to submit several entries at once.

4. On success, report the business process WID to the user.

If any step fails, stop immediately and report the error — do not attempt to proceed.

## Guidelines

- Always confirm the worker's name and all job history details with the user before submitting.
- Always resolve a worker's WID via searchForWorker or getMyInfo — never assume or fabricate IDs.
- If a user provides a date without a year, ask for clarification before proceeding.
- When the business process completes, always include the business process WID in your response \
  (e.g. "Business Process WID: <id>").
- Keep responses concise and accurate.
- Never fabricate Workday IDs or worker data.

## Known Limitations

The following are not supported — tell the user to use the Workday UI directly:
- Deleting job history entries
- Viewing existing job history records"""


# ── Trace helpers ────────────────────────────────────────────────────────────


def _truncate(s: str, max_len: int = 300) -> str:
    s = str(s)
    return s if len(s) <= max_len else s[:max_len] + "..."


def _messages_summary(messages: list, n: int = 5) -> list:
    """Last n non-system messages as a compact summary for the trace panel."""
    non_system = [m for m in messages if m.get("role") != "system"]
    result = []
    for m in non_system[-n:]:
        role = m.get("role", "?")
        content = m.get("content", "") or ""
        result.append({"role": role, "content": _truncate(str(content))})
    return result


def _tool_status(result_str: str) -> str:
    lower = result_str.lower()
    if "error calling" in lower or "mcp error" in lower or "exception" in lower:
        return "error"
    return "success"


# ── Tool schema conversion ───────────────────────────────────────────────────


def mcp_tools_to_openai(mcp_tools: list[dict]) -> list[dict]:
    """
    Convert the MCP tools/list response into Azure OpenAI's function-calling schema.

    MCP inputSchema is already JSON Schema, so we pass it through as-is under
    the 'parameters' key inside each function definition.
    """
    openai_tools = []
    for t in mcp_tools:
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get(
                        "inputSchema", {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return openai_tools


# ── Agent loop ───────────────────────────────────────────────────────────────


class JobHistoryAgent:
    def __init__(self):
        self.mcp = WorkdayMCPClient(
            tenant=WORKDAY_TENANT,
            client_id=ASU_CLIENT_ID,
            client_secret=ASU_CLIENT_SECRET,
            refresh_token=ASU_REFRESH_TOKEN,
        )
        self.llm = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
        )

        # Fetch available tools once at startup
        print("[agent] Fetching available MCP tools...")
        self.mcp_tools_raw = self.mcp.list_tools()
        self.tools = mcp_tools_to_openai(self.mcp_tools_raw)
        print(
            f"[agent] {len(self.tools)} tools available: "
            f"{', '.join(t['function']['name'] for t in self.tools)}"
        )

    def _execute_tool_call(self, tool_name: str, tool_input: dict) -> Any:
        """Call a Workday MCP tool and return its result as a string."""
        try:
            result = self.mcp.call_tool(tool_name, tool_input)
            result_str = (
                json.dumps(result, indent=2) if not isinstance(result, str) else result
            )
            print(f"[agent] Tool result ({tool_name}): {result_str[:2000]}")
            return result_str
        except Exception as exc:
            error_msg = f"Error calling {tool_name}: {exc}"
            print(f"[agent] {error_msg}")
            return error_msg

    def chat(
        self, user_message: str, conversation_history: Optional[list] = None
    ) -> tuple[str, list[str], dict]:
        """
        Run a single turn of the agentic loop.

        Args:
            user_message: The HR professional's natural language request.
            conversation_history: Optional prior messages for multi-turn context.

        Returns:
            Tuple of (response_text, tools_used, trace).
            trace contains timing spans for each LLM call and tool call.
        """
        request_start = time.time()
        spans: list[dict] = []
        tools_used: list[str] = []
        iteration = 0

        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        while True:
            iteration += 1
            llm_t0 = time.time()
            llm_start_offset = llm_t0 - request_start

            response = self.llm.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
                max_tokens=4096,
            )
            llm_duration_ms = int((time.time() - llm_t0) * 1000)

            choice = response.choices[0]
            assistant_message = choice.message
            messages.append(assistant_message.to_dict())  # type: ignore[arg-type]

            # Build LLM span
            if assistant_message.tool_calls:
                llm_output: dict = {
                    "action": "tool_calls",
                    "tool_calls": [
                        {
                            "name": tc.function.name,
                            "arguments": _truncate(tc.function.arguments, 300),
                        }
                        for tc in assistant_message.tool_calls
                    ],
                }
            else:
                llm_output = {
                    "action": "final_response",
                    "content": _truncate(assistant_message.content or "", 500),
                }
            llm_span: dict = {
                "type": "llm_call",
                "name": "Azure OpenAI",
                "iteration": iteration,
                "start_time": llm_start_offset,
                "duration_ms": llm_duration_ms,
                "status": "success",
                "input": _messages_summary(messages[:-1]),
                "output": llm_output,
            }
            if response.usage:
                llm_span["usage"] = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            spans.append(llm_span)

            # ── If the model is done (no tool calls), return the final text ──
            if choice.finish_reason == "stop":
                total_ms = int((time.time() - request_start) * 1000)
                trace = {
                    "total_duration_ms": total_ms,
                    "llm_calls": sum(1 for s in spans if s["type"] == "llm_call"),
                    "tool_calls": sum(1 for s in spans if s["type"] == "tool_call"),
                    "spans": spans,
                }
                return assistant_message.content or "", tools_used, trace

            # ── If the model wants to call tools, execute them all ──
            if choice.finish_reason == "tool_calls" and assistant_message.tool_calls:
                for tc in assistant_message.tool_calls:
                    tool_name = tc.function.name
                    tool_input = json.loads(tc.function.arguments)
                    tools_used.append(tool_name)

                    print(
                        f"[agent] Tool call: {tool_name}("
                        f"{tc.function.arguments[:120]}...)"
                    )

                    tool_t0 = time.time()
                    tool_start_offset = tool_t0 - request_start
                    result_content = self._execute_tool_call(tool_name, tool_input)
                    tool_duration_ms = int((time.time() - tool_t0) * 1000)

                    spans.append({
                        "type": "tool_call",
                        "name": tool_name,
                        "iteration": iteration,
                        "start_time": tool_start_offset,
                        "duration_ms": tool_duration_ms,
                        "input": tool_input,
                        "output": _truncate(result_content, 3000),
                        "status": _tool_status(result_content),
                    })

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_content,
                        }
                    )

                # Loop back so the model can reason over the tool results
                continue

            # Unexpected finish_reason — break to avoid infinite loop
            print(f"[agent] Unexpected finish_reason: {choice.finish_reason}")
            break

        total_ms = int((time.time() - request_start) * 1000)
        trace = {"total_duration_ms": total_ms, "llm_calls": 0, "tool_calls": 0, "spans": spans}
        return "[Agent loop ended unexpectedly]", tools_used, trace

    def run_interactive(self) -> None:
        """Simple interactive CLI session for testing."""
        print("\n" + "=" * 60)
        print("  Job History Agent — Workday HR Co-Pilot")
        print("=" * 60)
        print("Type your request, or 'exit' to quit.\n")

        history: list[dict] = []

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if user_input.lower() in ("exit", "quit", "bye"):
                print("Goodbye.")
                break

            if not user_input:
                continue

            print("\nAgent: ", end="", flush=True)
            reply, _, _ = self.chat(user_input, history)
            print(reply)
            print()

            # Maintain a rolling conversation window (last 20 turns)
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": reply})
            if len(history) > 40:
                history = history[-40:]


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job History Agent")
    parser.add_argument(
        "--message",
        "-m",
        type=str,
        help="Run a single message non-interactively",
    )
    args = parser.parse_args()

    agent = JobHistoryAgent()

    if args.message:
        print(f"\nUser: {args.message}\n")
        reply, _, _ = agent.chat(args.message)
        print(f"Agent: {reply}")
    else:
        agent.run_interactive()
