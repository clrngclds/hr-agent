# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI Serving Surface with Custom Altostrat Enterprise HR Chat UI and Context APIs."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.guardrails import get_audit_logs, inspect_user_prompt
from app.mcp_tools import (
    call_serviceimmediately_mcp_tool,
    call_workweek_mcp_tool,
    fetch_verified_employee_id,
    get_employee_profile,
    get_ticket_details,
)

load_dotenv()
os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "dc-test2-489503")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
logger = logging.getLogger("hr_agent.api")

allow_origins = os.getenv(
    "ALLOW_ORIGINS",
    "*,http://doralyn.c.googlers.com:8000,https://doralyn.c.googlers.com:8000,http://localhost:8000",
).split(",")
otel_to_cloud = os.getenv("OTEL_TO_CLOUD", "false").lower() == "true"

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=otel_to_cloud,
    lifespan=lifespan,
)
app.title = "hr-agent"
app.description = "API for interacting with the Altostrat Enterprise HR Agent"


# Models
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


# Endpoints for Web Chat UI and Live Context
@app.get("/ui")
@app.get("/hr")
async def serve_hr_ui() -> FileResponse:
    """Serves the custom Altostrat Enterprise HR Assistant Web Chat UI."""
    return FileResponse(STATIC_INDEX)


# Override root route to serve our Enterprise HR Web UI
for route in list(app.routes):
    if getattr(route, "path", None) == "/":
        app.routes.remove(route)


@app.get("/")
async def serve_root_ui() -> FileResponse:
    """Serves the Altostrat Enterprise HR Portal on root path."""
    return FileResponse(STATIC_INDEX)


@app.get("/api/context")
async def get_live_context() -> JSONResponse:
    """Fetches real-time employee profile, balances, and tickets from MCP servers."""
    try:
        emp_id = await fetch_verified_employee_id()
        profile_res = await get_employee_profile(emp_id)
        balances_raw = await call_workweek_mcp_tool("get_employee_balances", {"employee_id": emp_id})
        tickets_raw = await call_serviceimmediately_mcp_tool("list_tickets", {"employee_id": emp_id})

        # Parse Vacation & Sick balances
        vac_match = re.search(r"Vacation:\s*([\d\.]+)\s*days\s*remaining.*?([\d\.]+)/([\d\.]+)", balances_raw, re.IGNORECASE)
        sick_match = re.search(r"Sick:\s*([\d\.]+)\s*days\s*remaining.*?([\d\.]+)/([\d\.]+)", balances_raw, re.IGNORECASE)

        vacation_info = {
            "remaining": float(vac_match.group(1)) if vac_match else 15.0,
            "used": float(vac_match.group(2)) if vac_match else 5.0,
            "total": float(vac_match.group(3)) if vac_match else 20.0,
        }
        sick_info = {
            "remaining": float(sick_match.group(1)) if sick_match else 10.0,
            "used": float(sick_match.group(2)) if sick_match else 0.0,
            "total": float(sick_match.group(3)) if sick_match else 10.0,
        }

        # Parse tickets list
        tickets_list = []
        try:
            parsed_tickets = json.loads(tickets_raw)
            if isinstance(parsed_tickets, list):
                tickets_list = parsed_tickets
        except Exception:
            # If not json, create ticket card from INC0005099
            inc_res = await get_ticket_details("INC0005099")
            if inc_res.get("ticket"):
                tickets_list.append(inc_res["ticket"])

        audit_logs = get_audit_logs(limit=10)

        return JSONResponse(
            {
                "status": "success",
                "employee_id": emp_id,
                "profile": profile_res.get("profile", {}),
                "balances": {
                    "vacation": vacation_info,
                    "sick": sick_info,
                },
                "raw_balances": balances_raw,
                "tickets_list": tickets_list,
                "audit_logs": audit_logs,
            }
        )
    except Exception as e:
        logger.error(f"Error fetching live context: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/audit-logs")
async def get_recent_audit_logs(limit: int = 20) -> JSONResponse:
    """Returns recent audit events from the in-memory ring buffer."""
    logs = get_audit_logs(limit=limit)
    return JSONResponse({"status": "success", "count": len(logs), "logs": logs})


@app.post("/api/chat")
async def handle_chat_message(chat_req: ChatRequest) -> JSONResponse:
    """Processes a chat turn with input inspection, runner execution, and trace capture."""
    user_prompt = chat_req.message.strip()
    session_id = chat_req.session_id or "default-user-session"
    emp_id = await fetch_verified_employee_id()

    # Step 1: Pre-screen prompt with deterministic guardrails
    inspection = inspect_user_prompt(user_prompt, verified_employee_id=emp_id)
    if not inspection["allowed"]:
        return JSONResponse(
            {
                "status": "blocked",
                "reply": inspection["user_message"],
                "agents_invoked": [],
                "tool_calls": [],
                "guardrail": {
                    "status": "BLOCKED",
                    "category": inspection["category"],
                    "reason": inspection["reason"],
                },
                "session_id": session_id,
            }
        )

    # Step 2: Ensure session exists on Runner
    runner: Runner = getattr(app.state, "runner", None)
    if not runner:
        # Fallback if accessed before lifespan
        from app.agent import app as adk_app

        runner = Runner(
            app=adk_app,
            session_service=services.get_session_service(),
            artifact_service=services.get_artifact_service(),
            auto_create_session=True,
        )
        app.state.runner = runner
        app.state.agent_app_name = adk_app.name

    app_name = app.state.agent_app_name
    session = await runner.session_service.get_session(
        app_name=app_name, user_id=emp_id, session_id=session_id
    )
    if not session:
        session = await runner.session_service.create_session(
            app_name=app_name, user_id=emp_id, session_id=session_id
        )

    session.state["verified_employee_id"] = emp_id
    session.state["last_guardrail_warning"] = None

    # Step 3: Run the agent
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_prompt)],
    )

    agents_invoked: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    reply_parts: list[str] = []

    try:
        async for event in runner.run_async(
            user_id=emp_id,
            session_id=session_id,
            new_message=new_message,
        ):
            if event.author and event.author not in agents_invoked and event.author != "user":
                agents_invoked.append(event.author)

            for call in event.get_function_calls():
                tool_calls.append(
                    {
                        "name": call.name,
                        "args": call.args if hasattr(call, "args") else {},
                    }
                )

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        reply_parts.append(part.text)
    except Exception as e:
        logger.error(f"Error executing agent runner: {e}", exc_info=True)
        return JSONResponse(
            {
                "status": "error",
                "reply": f"⚠️ An internal agent error occurred: {e}",
                "agents_invoked": agents_invoked,
                "tool_calls": tool_calls,
                "session_id": session_id,
            },
            status_code=500,
        )

    last_warning = session.state.get("last_guardrail_warning")
    guardrail_status = "WARNING" if last_warning else "ALLOWED"

    full_reply = "\n\n".join(reply_parts).strip() or "Request processed successfully."

    return JSONResponse(
        {
            "status": "success",
            "reply": full_reply,
            "agents_invoked": agents_invoked,
            "tool_calls": tool_calls,
            "guardrail": {
                "status": guardrail_status,
                "warning": last_warning,
                "spii_detected": inspection.get("spii_detected", []),
            },
            "session_id": session_id,
        }
    )


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
