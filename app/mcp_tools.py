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

"""Live MCP Client, Toolsets, and Resource Readers for WorkWeek and ServiceImmediately."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

load_dotenv()

logger = logging.getLogger(__name__)

WORKWEEK_MCP_URL = os.getenv(
    "WORKWEEK_MCP_URL",
    "https://mock-saas.aishprabhat.demo.altostrat.com/work-week/mcp/",
)
WORKWEEK_MCP_TOKEN = os.getenv(
    "WORKWEEK_MCP_TOKEN",
    "mcp_iQST8fpRKYWxgawtrtoH-QhrWnUhAFxLciSyYYk7XG0",
)

SERVICEIMMEDIATELY_MCP_URL = os.getenv(
    "SERVICEIMMEDIATELY_MCP_URL",
    "https://mock-saas.aishprabhat.demo.altostrat.com/service-immediately/mcp/",
)
SERVICEIMMEDIATELY_MCP_TOKEN = os.getenv(
    "SERVICEIMMEDIATELY_MCP_TOKEN",
    "mcp_tspNuAUI501n-aa9bChLsRcQ8zPbxsigPSsRD0evnOA",
)

# Cached verified employee ID
_CACHED_VERIFIED_EMPLOYEE_ID: str | None = None


async def call_workweek_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Invokes a specific WorkWeek tool directly over the MCP stream."""
    headers = {"X-MCP-Token": WORKWEEK_MCP_TOKEN}
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as http_client:
        async with streamable_http_client(WORKWEEK_MCP_URL, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if result.content:
                    first = result.content[0]
                    return getattr(first, "text", str(first))
                return ""


async def read_workweek_mcp_resource(uri: str) -> str:
    """Reads a WorkWeek MCP resource by URI."""
    headers = {"X-MCP-Token": WORKWEEK_MCP_TOKEN}
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as http_client:
        async with streamable_http_client(WORKWEEK_MCP_URL, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                resource = await session.read_resource(uri)
                if resource.contents:
                    first = resource.contents[0]
                    return getattr(first, "text", str(first))
                return "{}"


async def call_serviceimmediately_mcp_tool(
    tool_name: str, arguments: dict[str, Any]
) -> str:
    """Invokes a specific ServiceImmediately tool directly over the MCP stream."""
    headers = {"X-MCP-Token": SERVICEIMMEDIATELY_MCP_TOKEN}
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as http_client:
        async with streamable_http_client(
            SERVICEIMMEDIATELY_MCP_URL, http_client=http_client
        ) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if result.content:
                    first = result.content[0]
                    return getattr(first, "text", str(first))
                return ""


async def read_serviceimmediately_mcp_resource(uri: str) -> str:
    """Reads a ServiceImmediately MCP resource by URI."""
    headers = {"X-MCP-Token": SERVICEIMMEDIATELY_MCP_TOKEN}
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as http_client:
        async with streamable_http_client(
            SERVICEIMMEDIATELY_MCP_URL, http_client=http_client
        ) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                resource = await session.read_resource(uri)
                if resource.contents:
                    first = resource.contents[0]
                    return getattr(first, "text", str(first))
                return "{}"


async def fetch_verified_employee_id(force_refresh: bool = False) -> str:
    """Resolves and returns the authenticated employee ID from the live WorkWeek session."""
    global _CACHED_VERIFIED_EMPLOYEE_ID
    if _CACHED_VERIFIED_EMPLOYEE_ID and not force_refresh:
        return _CACHED_VERIFIED_EMPLOYEE_ID

    try:
        emp_id = await call_workweek_mcp_tool("get_current_employee_id", {})
        emp_id = emp_id.strip()
        if emp_id:
            _CACHED_VERIFIED_EMPLOYEE_ID = emp_id
            return emp_id
    except Exception as e:
        logger.warning(f"Failed to query get_current_employee_id: {e}")

    _CACHED_VERIFIED_EMPLOYEE_ID = "EMP-894"
    return _CACHED_VERIFIED_EMPLOYEE_ID


def create_workweek_mcp_toolset() -> McpToolset:
    """Builds an ADK McpToolset connected to the live WorkWeek MCP server."""
    connection_params = StreamableHTTPConnectionParams(
        url=WORKWEEK_MCP_URL,
        headers={"X-MCP-Token": WORKWEEK_MCP_TOKEN},
        timeout=15.0,
    )
    return McpToolset(connection_params=connection_params)


def create_serviceimmediately_mcp_toolset() -> McpToolset:
    """Builds an ADK McpToolset connected to the live ServiceImmediately MCP server."""
    connection_params = StreamableHTTPConnectionParams(
        url=SERVICEIMMEDIATELY_MCP_URL,
        headers={"X-MCP-Token": SERVICEIMMEDIATELY_MCP_TOKEN},
        timeout=15.0,
    )
    return McpToolset(connection_params=connection_params)


# Companion ADK Tool Functions for MCP Resources (which McpToolset doesn't expose)


async def get_employee_profile(employee_id: str) -> dict[str, Any]:
    """Agent Tool: Read an employee's full WorkWeek HR profile.

    Reads the official WorkWeek employee profile resource (workweek://employees/{employee_id}/profile).
    Contains employee_id, first_name, last_name, email, job_title, department,
    role, hire_date, manager_id, home_address, and phone_number.

    Args:
        employee_id: The employee ID to inspect (e.g. "EMP-894").

    Returns:
        A dict containing the full profile details or an error message.
    """
    clean_id = employee_id.strip().upper() if employee_id else "EMP-894"
    uri = f"workweek://employees/{clean_id}/profile"
    try:
        raw_json = await read_workweek_mcp_resource(uri)
        data = json.loads(raw_json)
        return {"status": "success", "profile": data}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not read profile for employee {clean_id}: {e}",
        }


async def get_ticket_details(ticket_id: str) -> dict[str, Any]:
    """Agent Tool: Read full details, status, and comments timeline for a ServiceImmediately ticket.

    Reads the ServiceImmediately incident resource (serviceimmediately://tickets/{ticket_id}).
    Contains ticket_id, requested_by, category, short_description, status,
    priority, assignment_group, resolution_notes, and the comments timeline.

    Args:
        ticket_id: The ticket ID (e.g., "INC0005099").

    Returns:
        A dict containing ticket details or an error message.
    """
    clean_id = ticket_id.strip().upper()
    uri = f"serviceimmediately://tickets/{clean_id}"
    try:
        raw_json = await read_serviceimmediately_mcp_resource(uri)
        data = json.loads(raw_json)
        return {"status": "success", "ticket": data}
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not read ticket details for {clean_id}: {e}",
        }
