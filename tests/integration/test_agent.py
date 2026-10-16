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

"""Integration tests for live MCP connectivity and Agent execution."""

import google.auth.exceptions
import pytest
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.mcp_tools import (
    call_serviceimmediately_mcp_tool,
    call_workweek_mcp_tool,
    fetch_verified_employee_id,
    get_employee_profile,
    get_ticket_details,
)


@pytest.mark.asyncio
async def test_live_mcp_workweek_connectivity() -> None:
    """Tests live read connectivity to the WorkWeek MCP server."""
    emp_id = await fetch_verified_employee_id()
    assert emp_id == "EMP-894"

    profile_res = await get_employee_profile(emp_id)
    assert profile_res["status"] == "success"
    profile = profile_res["profile"]
    assert profile["employee_id"] == "EMP-894"
    assert "first_name" in profile
    assert "last_name" in profile

    balances = await call_workweek_mcp_tool("get_employee_balances", {"employee_id": emp_id})
    assert "Leave Balances" in balances
    assert "Vacation" in balances
    assert "Sick" in balances


@pytest.mark.asyncio
async def test_live_mcp_serviceimmediately_connectivity() -> None:
    """Tests live read connectivity to the ServiceImmediately MCP server."""
    ticket_res = await get_ticket_details("INC0005099")
    assert ticket_res["status"] == "success"
    ticket = ticket_res["ticket"]
    assert ticket["ticket_id"] == "INC0005099"
    assert "short_description" in ticket
    assert "status" in ticket

    tickets_list = await call_serviceimmediately_mcp_tool("list_tickets", {"employee_id": "EMP-894"})
    assert "INC0005099" in tickets_list


def test_agent_stream_execution() -> None:
    """Tests that root_agent runs end-to-end and streams back policy information."""
    session_service = InMemorySessionService()
    session = session_service.create_session_sync(user_id="EMP-894", app_name="app")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="app")

    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text="What is the bereavement leave policy regarding pet loss?")],
    )

    try:
        events = list(
            runner.run(
                new_message=message,
                user_id="EMP-894",
                session_id=session.id,
                run_config=RunConfig(streaming_mode=StreamingMode.SSE),
            )
        )
    except google.auth.exceptions.RefreshError:
        pytest.skip("Vertex AI credentials require reauthentication via gcloud auth application-default login")

    assert len(events) > 0

    has_text_content = False
    for event in events:
        if (
            event.content
            and event.content.parts
            and any(part.text for part in event.content.parts)
        ):
            has_text_content = True
            break
    assert has_text_content, "Expected at least one event with text content"
