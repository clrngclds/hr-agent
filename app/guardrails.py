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

"""Layered Input/Output Guardrails, SPII Redaction, Audit Logging, and Tool Safety Callbacks."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from app.mcp_tools import (
    call_workweek_mcp_tool,
    fetch_verified_employee_id,
    get_ticket_details,
)

logger = logging.getLogger("hr_agent.guardrails")

# In-memory audit log ring buffer (most recent 200 events)
AUDIT_LOG: list[dict[str, Any]] = []
MAX_AUDIT_LOG_SIZE = 200


def record_audit_event(
    event_type: str,
    actor_employee_id: str,
    action: str,
    decision: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Records an audit event to the in-memory ring buffer and structured log."""
    event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event_type": event_type,
        "actor_employee_id": actor_employee_id,
        "action": action,
        "decision": decision,
        "reason": reason,
        "metadata": metadata or {},
    }
    AUDIT_LOG.append(event)
    if len(AUDIT_LOG) > MAX_AUDIT_LOG_SIZE:
        AUDIT_LOG.pop(0)

    logger.info(f"AUDIT_EVENT: {json.dumps(event)}")
    return event


def get_audit_logs(limit: int = 20) -> list[dict[str, Any]]:
    """Returns the most recent audit events."""
    return list(reversed(AUDIT_LOG[-limit:]))


def redact_spii(text: str) -> tuple[str, list[str]]:
    """Redacts Singapore NRIC/FIN, US SSN, and Credit Cards from text.

    Returns:
        tuple of (redacted_text, list_of_detected_spii_types)
    """
    detected: list[str] = []
    redacted = text

    # Singapore NRIC / FIN pattern: [STFGM]\d{7}[A-Z]
    nric_pattern = r"\b[STFGM]\d{7}[A-Z]\b"
    if re.search(nric_pattern, redacted, re.IGNORECASE):
        detected.append("SINGAPORE_NRIC")
        redacted = re.sub(nric_pattern, "[REDACTED-NRIC]", redacted, flags=re.IGNORECASE)

    # US SSN pattern: \d{3}-\d{2}-\d{4}
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    if re.search(ssn_pattern, redacted):
        detected.append("US_SSN")
        redacted = re.sub(ssn_pattern, "[REDACTED-SSN]", redacted)

    # Credit Card pattern (13-16 digits with optional dashes/spaces)
    # Be careful not to match ISO dates or ticket numbers like INC0005099
    cc_pattern = r"\b(?:\d{4}[ -]?){3}\d{4}\b"
    if re.search(cc_pattern, redacted):
        detected.append("CREDIT_CARD")
        redacted = re.sub(cc_pattern, "[REDACTED-CC]", redacted)

    return redacted, detected


# Injection & System Extraction Patterns
PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|prior\s+|previous\s+)?(instructions|prompts|rules)",
    r"disregard\s+(all\s+|prior\s+|previous\s+)?(instructions|rules)",
    r"(reveal|show|print|output|repeat)\s+(your\s+|the\s+)?(system prompt|instructions|developer message|tool schema)",
    r"you\s+are\s+now\s+(dan|unrestricted|jailbroken|in\s+developer\s+mode)",
    r"bypass\s+(security|guardrails|safety|tenant|authentication)",
    r"act\s+as\s+an?\s+unrestricted",
    r"override\s+employee_id",
    r"pretend\s+(my|the)\s+employee_id\s+is",
    r"\[system\s+override\]",
    r"<system>",
]

# Off-topic & Community Guidelines patterns
OFF_TOPIC_PATTERNS = [
    r"who\s+(should\s+i|to)\s+vote\s+for",
    r"political\s+party",
    r"democrat\s+vs\s+republican",
    r"trump\s+vs",
    r"biden\s+vs",
    r"raging\s+debate\s+over\s+politics",
    r"write\s+an?\s+insulting\s+message\s+to\s+my\s+coworker",
    r"humiliate\s+my\s+manager",
    r"recipe(\s+for)?",
    r"how\s+(to|do\s+i|can\s+i)\s+(bake|cook|make)",
    r"(chocolate\s+chip\s+)?cookies?",
    r"write\s+a\s+(poem|haiku|song|story)",
    r"solve\s+(this\s+|the\s+)?(integral|derivative|math|equation)",
    r"stock\s+market\s+prediction",
    r"buy\s+bitcoin",
    r"crypto(\s+trading)?",
    r"movie\s+recommendation",
]

# Regex patterns for legitimate HR / IT requests
LEGITIMATE_HR_REGEXES = [
    r"\bpolicy\b", r"\bleave\b", r"\bticket\b", r"\brelocation\b", r"\blondon\b",
    r"\bchair\b", r"\bmonitor\b", r"\bexpense\b", r"\bmeal\b", r"\bgift\b",
    r"\bgovernment\b", r"\balcohol\b", r"\brelationship\b", r"\bharassment\b",
    r"\bovertime\b", r"\btoil\b", r"\bsick\b", r"\bvacation\b", r"\baddress\b",
    r"\bphone\b", r"\bworkweek\b", r"\bbalance\b", r"\ballowance\b", r"\bpto\b",
    r"\bequipment\b", r"\bserviceimmediately\b", r"\binc\d*\b", r"\bprofile\b",
    r"\bbereavement\b", r"\bmaternity\b", r"\bbonding\b", r"\bchildcare\b",
]


def inspect_user_prompt(
    text: str, verified_employee_id: str = "EMP-894"
) -> dict[str, Any]:
    """Deterministically inspects incoming user prompt against enterprise guardrails.

    Checks:
        1. Prompt Injection / Jailbreaks / System prompt extraction
        2. Off-Topic / Political debate / Insults / Recipes (Handbook 5.5 & 9.2)
        3. Out-of-Scope Payroll / Salary modifications (BRD 2.3)
        4. Cross-Employee IDOR Attempts in Prompt
        5. SPII detection & redaction
        6. Optional Model Armor scan if configured
    """
    cleaned_text = text.strip()
    redacted_text, spii_detected = redact_spii(cleaned_text)
    t_lower = cleaned_text.lower()

    # 1. Prompt Injection / Jailbreak
    for pat in PROMPT_INJECTION_PATTERNS:
        if re.search(pat, t_lower):
            record_audit_event(
                event_type="PROMPT_INSPECTION",
                actor_employee_id=verified_employee_id,
                action="USER_PROMPT",
                decision="BLOCK",
                reason=f"Detected prompt injection / system extraction pattern: {pat}",
            )
            return {
                "allowed": False,
                "category": "PROMPT_INJECTION",
                "reason": "Security Alert: Prompt injection, jailbreak, or system instruction extraction attempt detected.",
                "user_message": "🛡️ **Security Policy Enforcement**: This request was blocked because it contains instructions that attempt to bypass system security controls, alter core instructions, or inspect underlying system configuration.",
                "redacted_text": redacted_text,
                "spii_detected": spii_detected,
            }

    # 2. Out-of-Scope Payroll / Compensation Processing (BRD 2.3)
    payroll_processing_patterns = [
        r"(process|run|execute)\s+(monthly\s+|weekly\s+)?payroll",
        r"(modify|change|increase|decrease|raise|cut)\s+(my\s+|employee\s+|another\s+)?(salary|compensation|base pay|wage)",
        r"(view|check|get|see)\s+(another|other|someone else's)\s+(salary|compensation|paystub)",
    ]
    for pat in payroll_processing_patterns:
        if re.search(pat, t_lower):
            record_audit_event(
                event_type="PROMPT_INSPECTION",
                actor_employee_id=verified_employee_id,
                action="USER_PROMPT",
                decision="BLOCK",
                reason="Payroll/Compensation processing is out of scope for MVP 1",
            )
            return {
                "allowed": False,
                "category": "OUT_OF_SCOPE_PAYROLL",
                "reason": "Payroll and compensation adjustments are outside the operational boundary of the MVP 1 HR Assistant.",
                "user_message": "ℹ️ **Operational Scope Notice (BRD Section 2.3)**: Payroll processing, salary modifications, and compensation adjustments are strictly out of scope for the Altostrat Enterprise HR Assistant MVP 1. For compensation inquiries, please contact your People Partner directly.",
                "redacted_text": redacted_text,
                "spii_detected": spii_detected,
            }

    # 3. Cross-Employee IDOR in Prompt
    # Check if user explicitly requests another employee's records e.g. EMP-001, EMP-101
    emp_matches = re.findall(r"\bEMP-\d+\b", cleaned_text, re.IGNORECASE)
    for emp_match in emp_matches:
        emp_match_upper = emp_match.upper()
        if emp_match_upper != verified_employee_id.upper():
            # Check if this is an explicit access request for another employee
            idor_action_patterns = [
                r"(leave|balance|vacation|sick|profile|address|phone|salary|ticket)",
                r"(for|belonging to|of|regarding)\s+" + re.escape(emp_match),
            ]
            if any(re.search(p, t_lower) for p in idor_action_patterns):
                record_audit_event(
                    event_type="PROMPT_INSPECTION",
                    actor_employee_id=verified_employee_id,
                    action="USER_PROMPT",
                    decision="BLOCK",
                    reason=f"Cross-Employee IDOR attempt targeting {emp_match_upper}",
                )
                extra_payroll_note = ""
                if re.search(r"\b(salary|compensation|base pay|wage|payroll|bonus)\b", t_lower):
                    extra_payroll_note = (
                        "\n\nFurthermore, salary and compensation adjustments are outside the "
                        "operational boundary of this assistant per BRD Section 2.3."
                    )
                return {
                    "allowed": False,
                    "category": "CROSS_EMPLOYEE_IDOR",
                    "reason": f"Access Denied: Attempt to access records for {emp_match_upper} from session authenticated as {verified_employee_id}.",
                    "user_message": (
                        f"🚫 **Access Denied (Anti-IDOR Governance)**: Your authenticated session is locked to **{verified_employee_id}**. "
                        f"Querying or modifying records belonging to other employees (such as **{emp_match_upper}**) is strictly prohibited "
                        f"under Altostrat Singapore Employee Privacy Policies.{extra_payroll_note}"
                    ),
                    "redacted_text": redacted_text,
                    "spii_detected": spii_detected,
                }

    # 4. Off-Topic / Community Guidelines
    # Only flag as off-topic if none of the legitimate HR keywords are present
    has_legitimate_hr = any(re.search(pat, t_lower) for pat in LEGITIMATE_HR_REGEXES)
    if not has_legitimate_hr:
        for pat in OFF_TOPIC_PATTERNS:
            if re.search(pat, t_lower):
                record_audit_event(
                    event_type="PROMPT_INSPECTION",
                    actor_employee_id=verified_employee_id,
                    action="USER_PROMPT",
                    decision="BLOCK",
                    reason=f"Off-topic query matching pattern: {pat}",
                )
                return {
                    "allowed": False,
                    "category": "OFF_TOPIC",
                    "reason": "Off-topic query or Community Guidelines conversational violation (Handbook Section 5.5 & 9.2).",
                    "user_message": "⚠️ **Community Guidelines & Acceptable Use (Handbook Section 5.5 & 9.2)**: This assistant is dedicated strictly to Altostrat HR policies, leave operations, personal profile management, and IT/Facilities service tickets. Engaging in non-work discussions, political debates, or external creative tasks is outside our acceptable workplace use policies.",
                    "redacted_text": redacted_text,
                    "spii_detected": spii_detected,
                }

    # 5. Optional Google Cloud Model Armor scan
    template_id = os.getenv("MODEL_ARMOR_TEMPLATE_ID")
    if template_id:
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud import modelarmor_v1

            project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "dc-test2-489503")
            location_id = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            client = modelarmor_v1.ModelArmorClient(
                client_options=ClientOptions(
                    api_endpoint=f"modelarmor.{location_id}.rep.googleapis.com"
                )
            )
            req = modelarmor_v1.SanitizeUserPromptRequest(
                name=f"projects/{project_id}/locations/{location_id}/templates/{template_id}",
                user_prompt_data=modelarmor_v1.DataItem(text=cleaned_text),
            )
            res = client.sanitize_user_prompt(request=req, timeout=2.0)
            if res.sanitization_result and res.sanitization_result.filter_match_state:
                # If matched unsafe filter
                record_audit_event(
                    event_type="PROMPT_INSPECTION",
                    actor_employee_id=verified_employee_id,
                    action="MODEL_ARMOR",
                    decision="BLOCK",
                    reason="Model Armor flagged user prompt as unsafe",
                )
                return {
                    "allowed": False,
                    "category": "MODEL_ARMOR_VIOLATION",
                    "reason": "Google Cloud Model Armor detected a content safety policy violation.",
                    "user_message": "🛡️ **Model Armor Safety Alert**: Your prompt triggered an enterprise content safety filter and has been blocked.",
                    "redacted_text": redacted_text,
                    "spii_detected": spii_detected,
                }
        except Exception:
            # Model Armor endpoint optional/graceful fallback
            pass

    record_audit_event(
        event_type="PROMPT_INSPECTION",
        actor_employee_id=verified_employee_id,
        action="USER_PROMPT",
        decision="ALLOW",
        reason="Prompt passed all safety and governance checks",
        metadata={"spii_detected": spii_detected},
    )

    return {
        "allowed": True,
        "category": "ALLOWED",
        "reason": "Passed all safety and governance checks",
        "user_message": "",
        "redacted_text": redacted_text,
        "spii_detected": spii_detected,
    }


class SafetyGuardrailPlugin(BasePlugin):
    """ADK BasePlugin that hooks into the Runner / App to enforce input and output guardrails."""

    def __init__(self) -> None:
        super().__init__(name="SafetyGuardrailPlugin")

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Inspects user prompt before sending it to Gemini; halts on safety violation."""
        verified_emp_id = callback_context.state.get("verified_employee_id") or "EMP-894"

        # Find latest user prompt
        user_text = ""
        if llm_request.contents:
            for content in reversed(llm_request.contents):
                if content.role == "user" and content.parts:
                    user_text = "\n".join(
                        p.text for p in content.parts if getattr(p, "text", None)
                    )
                    break

        if user_text:
            inspection = inspect_user_prompt(user_text, verified_emp_id)
            if not inspection["allowed"]:
                callback_context.state["last_guardrail_event"] = inspection
                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=inspection["user_message"])],
                    )
                )

        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        """Redacts accidental SPII leakage from model response."""
        if not llm_response.content or not llm_response.content.parts:
            return None

        modified_parts: list[types.Part] = []
        changed = False

        for part in llm_response.content.parts:
            if getattr(part, "text", None):
                redacted, detected = redact_spii(part.text)
                if detected:
                    changed = True
                    record_audit_event(
                        event_type="OUTPUT_INSPECTION",
                        actor_employee_id=callback_context.state.get(
                            "verified_employee_id", "EMP-894"
                        ),
                        action="MODEL_OUTPUT",
                        decision="REDACT",
                        reason=f"Redacted SPII from model output: {detected}",
                    )
                    modified_parts.append(types.Part.from_text(text=redacted))
                else:
                    modified_parts.append(part)
            else:
                modified_parts.append(part)

        if changed:
            return LlmResponse(
                content=types.Content(role="model", parts=modified_parts)
            )
        return None


async def enforce_tool_guardrails(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    """Deterministic tool guardrail enforcing Anti-IDOR, input validation, and ITSM compliance.

    Returns:
        None to allow tool execution, or dict with error message to block it.
    """
    tool_name = getattr(tool, "name", str(tool))
    verified_emp_id = tool_context.state.get("verified_employee_id")
    if not verified_emp_id:
        verified_emp_id = await fetch_verified_employee_id()
        tool_context.state["verified_employee_id"] = verified_emp_id

    # 1. Anti-IDOR Check: employee_id
    if "employee_id" in args:
        emp_arg = str(args.get("employee_id", "")).strip()
        if emp_arg.lower() in ("", "current", "me", "self"):
            args["employee_id"] = verified_emp_id
        elif emp_arg.upper() != verified_emp_id.upper():
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action=f"{tool_name}:Anti-IDOR",
                decision="BLOCK",
                reason=f"Attempted to access employee_id='{emp_arg}' while authenticated as '{verified_emp_id}'",
            )
            return {
                "status": "error",
                "error_code": "ACCESS_DENIED_IDOR",
                "message": (
                    f"Access Denied (Anti-IDOR Policy): Authenticated session for "
                    f"{verified_emp_id} cannot access or modify records belonging to {emp_arg}."
                ),
            }

    # 1. Anti-IDOR Check: requested_by
    if "requested_by" in args:
        req_arg = str(args.get("requested_by", "")).strip()
        if req_arg.lower() in ("", "current", "me", "self"):
            args["requested_by"] = verified_emp_id
        elif req_arg.upper() != verified_emp_id.upper():
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action=f"{tool_name}:Anti-IDOR",
                decision="BLOCK",
                reason=f"Attempted to set requested_by='{req_arg}' while authenticated as '{verified_emp_id}'",
            )
            return {
                "status": "error",
                "error_code": "ACCESS_DENIED_IDOR",
                "message": (
                    f"Access Denied (Anti-IDOR Policy): Authenticated session for "
                    f"{verified_emp_id} cannot create tickets on behalf of {req_arg}."
                ),
            }

    # 2. WorkWeek Tool Validations
    if tool_name == "update_personal_info":
        address = str(args.get("address", "")).strip()
        phone = str(args.get("phone", "")).strip()

        if len(address) < 5 or not re.match(r"^\+?[\d\s\-()]{7,20}$", phone):
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action="update_personal_info",
                decision="BLOCK",
                reason="Invalid address or phone format",
            )
            return {
                "status": "error",
                "error_code": "VALIDATION_ERROR",
                "message": (
                    "Validation Error: Home address must be at least 5 characters "
                    "and phone number must be a valid 7-20 digit telephone format (e.g. +65 9123 4567)."
                ),
            }

    elif tool_name == "request_time_off":
        start_date_str = str(args.get("start_date", "")).strip()
        end_date_str = str(args.get("end_date", "")).strip()
        leave_type_raw = str(args.get("leave_type", "")).strip()
        days_val = float(args.get("days", 0))

        # Check date range validity
        try:
            start_d = datetime.date.fromisoformat(start_date_str)
            end_d = datetime.date.fromisoformat(end_date_str)
            if start_d > end_d:
                record_audit_event(
                    event_type="TOOL_GUARDRAIL",
                    actor_employee_id=verified_emp_id,
                    action="request_time_off",
                    decision="BLOCK",
                    reason=f"Invalid date range: {start_date_str} > {end_date_str}",
                )
                return {
                    "status": "error",
                    "error_code": "INVALID_DATE_RANGE",
                    "message": f"Policy Violation (Handbook Section 1.2): Start date ({start_date_str}) must be on or before end date ({end_date_str}).",
                }
        except ValueError:
            return {
                "status": "error",
                "error_code": "INVALID_DATE_FORMAT",
                "message": "Dates must be provided in valid ISO YYYY-MM-DD format.",
            }

        if days_val <= 0:
            return {
                "status": "error",
                "error_code": "INVALID_DAYS",
                "message": "Requested days must be greater than 0.",
            }

        # Normalize leave type
        leave_type = "Vacation" if "vacation" in leave_type_raw.lower() else "Sick" if "sick" in leave_type_raw.lower() else leave_type_raw
        args["leave_type"] = leave_type

        # Verify sufficient leave balance
        try:
            bal_str = await call_workweek_mcp_tool("get_employee_balances", {"employee_id": verified_emp_id})
            remaining = 0.0
            if leave_type == "Vacation":
                match = re.search(r"Vacation:\s*([\d\.]+)\s*days\s*remaining", bal_str, re.IGNORECASE)
                if match:
                    remaining = float(match.group(1))
            elif leave_type == "Sick":
                match = re.search(r"Sick:\s*([\d\.]+)\s*days\s*remaining", bal_str, re.IGNORECASE)
                if match:
                    remaining = float(match.group(1))

            if days_val > remaining:
                record_audit_event(
                    event_type="TOOL_GUARDRAIL",
                    actor_employee_id=verified_emp_id,
                    action="request_time_off",
                    decision="BLOCK",
                    reason=f"Requested {days_val} days of {leave_type} exceeds remaining balance {remaining}",
                )
                return {
                    "status": "error",
                    "error_code": "INSUFFICIENT_LEAVE_BALANCE",
                    "message": (
                        f"Policy Violation (Handbook Section 1.2): Requested {days_val} days of {leave_type} "
                        f"leave exceeds your remaining balance of {remaining} days."
                    ),
                }
        except Exception as e:
            logger.warning(f"Could not verify leave balances dynamically: {e}")

    # 3. ServiceImmediately Tool Validations
    elif tool_name == "create_ticket":
        desc = str(args.get("short_description", "")).lower()
        prio = str(args.get("priority", "3 - Moderate"))

        minor_facility_keywords = [
            "squeaky", "chair", "cosmetic", "scratch", "paint",
            "wobbly desk", "lightbulb", "coffee", "snack"
        ]
        outage_keywords = [
            "outage", "crash", "down", "downtime", "unresponsive",
            "breach", "corrupt", "production failure"
        ]

        if any(k in desc for k in minor_facility_keywords) and prio != "4 - Low":
            args["priority"] = "4 - Low"
            warning_msg = (
                f"Priority automatically downgraded from '{prio}' to '4 - Low' per Handbook Section 5.5 "
                "(minor facility/cosmetic issues must be classified as '4 - Low')."
            )
            tool_context.state["last_guardrail_warning"] = warning_msg
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action="create_ticket:PRIORITY_DOWNGRADE",
                decision="DOWNGRADE",
                reason=warning_msg,
            )
        elif prio.startswith("1") and not any(k in desc for k in outage_keywords):
            args["priority"] = "3 - Moderate"
            warning_msg = (
                f"Priority automatically downgraded from '{prio}' to '3 - Moderate' per Handbook Section 5.5 "
                "('1 - Critical' requires an active system outage, crash, or downtime)."
            )
            tool_context.state["last_guardrail_warning"] = warning_msg
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action="create_ticket:PRIORITY_DOWNGRADE",
                decision="DOWNGRADE",
                reason=warning_msg,
            )

    elif tool_name == "update_ticket_status":
        ticket_id = str(args.get("ticket_id", "")).strip().upper()
        target_status = str(args.get("status", "")).strip()

        ticket_res = await get_ticket_details(ticket_id)
        ticket_data = ticket_res.get("ticket") or {}

        # Anti-IDOR check on existing ticket
        creator = ticket_data.get("requested_by")
        if creator and creator.upper() != verified_emp_id.upper():
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action="update_ticket_status",
                decision="BLOCK",
                reason=f"Attempted to modify ticket {ticket_id} owned by {creator}",
            )
            return {
                "status": "error",
                "error_code": "ACCESS_DENIED_IDOR",
                "message": (
                    f"Access Denied (Anti-IDOR): Ticket {ticket_id} was requested by {creator}. "
                    f"Your authenticated session ({verified_emp_id}) is not authorized to modify it."
                ),
            }

        # Sequential lifecycle check
        current_status = ticket_data.get("status", "New")
        allowed_map: dict[str, set[str]] = {
            "New": {"In Progress"},
            "In Progress": {"Resolved"},
            "Resolved": {"Closed", "In Progress"},
            "Closed": set(),
        }

        allowed_next = allowed_map.get(current_status, set())
        if target_status not in allowed_next:
            record_audit_event(
                event_type="TOOL_GUARDRAIL",
                actor_employee_id=verified_emp_id,
                action="update_ticket_status",
                decision="BLOCK",
                reason=f"Lifecycle violation: attempted transition from '{current_status}' to '{target_status}'",
            )
            return {
                "status": "error",
                "error_code": "COMPLIANCE_VIOLATION_LIFECYCLE",
                "message": (
                    f"Compliance Violation (Handbook Section 5.5): Cannot transition ticket {ticket_id} "
                    f"directly from '{current_status}' to '{target_status}'. Tickets must progress sequentially "
                    "through New -> In Progress -> Resolved -> Closed. Bypassing intermediate states is strictly prohibited."
                ),
            }

    # All checks passed
    record_audit_event(
        event_type="TOOL_GUARDRAIL",
        actor_employee_id=verified_emp_id,
        action=f"ALLOW:{tool_name}",
        decision="ALLOW",
        reason="Tool invocation passed all guardrail checks",
    )
    return None
