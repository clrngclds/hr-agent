# Architecture Drift Evaluation Report — Altostrat Enterprise HR Agentic Solution (MVP 1)

**Evaluated Repository:** `/usr/local/google/home/doralyn/secure-agent-lab/hr-agent`  
**Authoritative SDD Baseline:** `enterprise_agentic_solution_design_doc_mvp1.md` (Draft v0.3, 2026-10-16)  
**BRD Context:** `HR Agentic Solution BRD` (`1B46ERMVZapwSN8RPmsJ0_NnayuUkaTT6ZcujgwdjTX8`)  
**Evaluation Skill:** `architecture-drift-evaluation` (`evaluation-plugins/skills/architecture-drift-evaluation/SKILL.md`)  
**Evaluation Timestamp:** `2026-10-16 05:48:00 UTC`

---

## Phase 1: Scope & Context Summary

```text
SCOPE SUMMARY:
- Target Codebase: /usr/local/google/home/doralyn/secure-agent-lab/hr-agent
- SDD Specification: enterprise_agentic_solution_design_doc_mvp1.md (v0.3, Confirmed)
- BRD Context: HR Agentic Solution BRD (Confirmed)
- Tech Stack: Python 3.12, Google ADK v2.8.0, FastAPI, MCP (Streamable HTTP), uv, Terraform, Docker
```

---

## Executive Scorecard

| # | Rubric Axis | Weight | Integer Score (1–5) | Weighted Contribution | Capping Principles Triggered |
| :---: | :--- | :---: | :---: | :---: | :--- |
| **1** | **Component & Topology Alignment** | **30%** | **3** | `0.90` | **Principle 3** (Unfulfilled SDD §5.3 Saga/DLQ & §1.3 Sync Pipeline) |
| **2** | **Design Pattern & Abstraction Drift** | **25%** | **3** | `0.75` | **Principles 1, 2, 3** (Hardcoded `EMP-894` & tokens, no OAuth middleware, no circuit breaker) |
| **3** | **API & Data Contract Compliance** | **20%** | **3** | `0.60` | Field/Type/Unit discrepancies vs. SDD §5.1, §5.4.2, §5.4.3 |
| **4** | **Deployment, Evaluation & Operational Readiness** | **25%** | **3** | `0.75` | **Principles 1, 2, 3** (Hardcoded host/project, `.env.example` gaps, Terraform & dataset scale drift) |
| — | **OVERALL WEIGHTED DRIFT SCORE** | **100%** | — | **`3.00 / 5.00`** | **Grade: `C` (`Moderate Drift`)** |

> [!WARNING]
> **Overall Verdict: `3.00 / 5.00` — Grade `C` (`Moderate Drift`)**  
> Per the `architecture-drift-evaluation` rubric (`2.5 – 3.4: Moderate Drift`), the `hr-agent` implementation is **fully functional** (passing `33/33` unit/integration tests and scoring `5.0000 / 5.0000` on its 10-case golden benchmark), but carries **environment hardcoding (Principle 1)**, **authentication/secret shortcuts (Principle 2)**, **schema/contract variations**, and **unfulfilled SDD MVP 1 infrastructure/resilience commitments (Principle 3)** that cap each axis at **3**.

---

## Phase 2: Scope Calibration (In-Scope MVP 1 vs. Future State)

Following the **Scope Calibration Rule** in `SKILL.md`, features explicitly marked in **SDD Section 2 (Production-Ready Future State Design)** or **SDD Section 1.2 (Out-of-Scope for MVP 1)** were **excluded from penalty**:

| Future-State / Out-of-Scope Item | SDD Reference | Calibration Disposition |
| :--- | :---: | :--- |
| **RFC 8693 Enterprise Identity Broker (Okta / Azure AD / Ping token exchange)** | SDD §1.2, §2.1, §4.1.1 | **Not Penalized** (Future State — MVP 1 explicitly uses functional credentials + `X-On-Behalf-Of` compensating controls). |
| **Multi-Tenancy (`X-Tenant-ID`) & Active-Active Multi-Region (`us-central1` + `europe-west1`)** | SDD §1.2, §2.2, §5.5.2 | **Not Penalized** (Future State — Single-region `us-central1` accepted for MVP 1 pilot in R-9). |
| **Omnichannel Clients (Google Chat, Slack, MS Teams) & HITL Approval Cards** | SDD §2.3, §2.4 | **Not Penalized** (Future State — Standalone Web Chat UI is the MVP 1 surface). |
| **Payroll, Compensation, Performance Reviews, Voice & Multi-Lingual Support** | SDD §1.2 | **Not Penalized** (Explicitly out-of-scope; verified as blocked by `SafetyGuardrailPlugin`). |

---

## Phase 3: Detailed Axis-by-Axis Codebase Audit

### Axis 1: Component & Topology Alignment — Score: `3 / 5` (Weight: 30%)

#### Alignment Evidence (What Matches SDD §1.3 & §3.1)
- **ADK Multi-Agent Supervisor Hierarchy (`app/agent.py:L71-L204`)**:
  - Root Supervisor `hr_agent` (`app/agent.py:L182-L197`) coordinates three domain-specialist sub-agents:
    1. `hr_policy_agent` (`app/agent.py:L71-L78`) — HR Policy & Compliance Specialist
    2. `workweek_agent` (`app/agent.py:L100-L107`) — WorkWeek HCM Specialist
    3. `serviceimmediately_agent` (`app/agent.py:L133-L140`) — ServiceImmediately ITSM Specialist
- **Layered Guardrail Plugin & Tool Interceptors (`app/guardrails.py:L336-L666`)**:
  - `SafetyGuardrailPlugin` (`BasePlugin`) intercepts prompts pre-LLM (`before_model_callback`) and sanitizes responses post-LLM (`after_model_callback`), while `enforce_tool_guardrails` validates tool calls pre-execution.
- **Containerized Web Chat UI & FastAPI Backend (`app/fast_api_app.py:L88-L125`, `app/static/index.html`)**:
  - Serves the interactive Altostrat Enterprise HR Assistant portal with live employee profile, leave balance, ticket cards, and guardrail telemetry.

#### Architectural Drift Findings & Gaps (Capped at 3 via Principle 3)
1. **Missing Saga / Compensating Action Orchestrator & Cloud Tasks DLQ (SDD §5.3)**:
   - **SDD Specification**: SDD §5.3 mandates a **Saga / Compensating Action Pattern** with a durable Cloud Tasks retry queue (`DLQ`), Pub/Sub $\to$ IT Ops P2 alerting, and `saga_state` tracking (`PENDING_RETRY`, `RECOVERED`, `ESCALATED`) when Step-2 ITSM ticket creation fails after a WorkWeek write (e.g., UC-2.2 Medical Leave or UC-2.3 Relocation).
   - **Code Reality**: No Saga coordinator, Cloud Tasks enqueueing logic, compensating `add_ticket_comment` handler, or `saga_state` tracking exists anywhere in `app/`.
2. **Missing Event-Driven GCS-to-Vertex AI Search Sync Pipeline (SDD §1.3 & §5.5.1)**:
   - **SDD Specification**: SDD §1.3 specifies `gs://altostrat-hr-policies` with Pub/Sub notifications triggering a `Cloud Run Ingestion Function` (`SyncPipeline`) to refresh Vertex AI Search within 15 minutes.
   - **Code Reality**: `app/policy_rag.py:L26, L122-L183` loads a static local Markdown file (`app/data/hr_policy_handbook.md`) into an in-memory BM25 scorer. Vertex AI Search (`app/policy_rag.py:L311-L343`) is disabled by default (`ENABLE_VERTEX_SEARCH="false"`), and no ingestion function exists.
3. **External Hosted Mock SaaS MCP vs. In-Repo FastMCP Microservices (SDD §1.3 & §5.1)**:
   - **SDD Specification**: SDD §1.3 & §5.1 specify two dedicated Python FastMCP microservices (`workweek-mcp` and `serviceimmediately-mcp`) with per-service circuit breakers (`CB_WW`, `CB_SI`).
   - **Code Reality**: `app/mcp_tools.py:L35-L51` acts solely as an MCP client connecting to external pre-hosted URLs (`https://mock-saas.aishprabhat.demo.altostrat.com/...`); the FastMCP server implementations are not part of the repository.

---

### Axis 2: Design Pattern & Abstraction Drift — Score: `3 / 5` (Weight: 25%)

#### Alignment Evidence
- **ADK Plugin & Callback Encapsulation**: `SafetyGuardrailPlugin` (`app/guardrails.py:L336-L405`) and `enforce_tool_guardrails` (`app/guardrails.py:L408-L666`) cleanly separate security policy checks from agent orchestration.
- **Shared Session & Artifact Service Registry**: `app/app_utils/services.py:L39-L72` registers `shared://session` and `shared://artifact` across Web and A2A surfaces, supporting `VertexAiSessionService` when `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set and `InMemorySessionService` in `dev` (matching SDD §5.4.1).

#### Architectural Drift Findings & Gaps (Capped at 3 via Principles 1, 2 & 3)
1. **Hardcoded Static Employee Identity (`EMP-894`) & Global Cache (Principles 1 & 2 Violation — SDD §4.1.1)**:
   - **SDD Specification**: SDD §4.1.1 requires extracting the OAuth 2.0 ID token subject (`sub` / email) per request, resolving `employee_id` via `get_employee_by_email` in pre-LLM identity middleware, and storing it per-session in `session.state["verified_employee_id"]`.
   - **Code Reality**:
     - `app/agent.py:L55, L87, L116, L153` hardcodes `'EMP-894' (Doralyn Employee)` directly into the LLM system instructions (`HR_POLICY_INSTRUCTION`, `WORKWEEK_INSTRUCTION`, `SERVICEIMMEDIATELY_INSTRUCTION`, `ROOT_INSTRUCTION`).
     - `app/mcp_tools.py:L54, L123-L139` caches `_CACHED_VERIFIED_EMPLOYEE_ID` in a module-level global variable and falls back to `"EMP-894"` (`L138`), making the runtime single-user.
2. **Hardcoded Secrets in Source Code (Principle 2 Violation — SDD §4.1)**:
   - **SDD Specification**: SDD §4.1 requires storing WorkWeek and ServiceImmediately credentials in Google Cloud Secret Manager (`sa-fastmcp-workweek`, `sa-fastmcp-serviceimmediately`).
   - **Code Reality**: `app/mcp_tools.py:L39-L51` hardcodes live bearer tokens (`mcp_iQST8fpRKYWxgawtrtoH-QhrWnUhAFxLciSyYYk7XG0` and `mcp_tspNuAUI501n-aa9bChLsRcQ8zPbxsigPSsRD0evnOA`) as default strings in Python code and commits them in `.env` (`hr-agent/.env:L7, L9`).
3. **Missing OAuth 2.0 Middleware, `X-On-Behalf-Of`, and `X-Automation-Origin` Headers (Principle 2 Violation — SDD §4.1.1 & §4.4)**:
   - **SDD Specification**: SDD §4.1.1 requires setting `X-On-Behalf-Of: <employee_id>` from the verified OAuth token prior to LLM execution, and SDD §4.4 requires `X-Automation-Origin: altostrat-hr-agent` and payload metadata (`created_by_agent`, `agent_version`, `initiating_user`, `trace_id`) on all backend writes.
   - **Code Reality**: `app/fast_api_app.py:L127-L215` performs no OAuth Bearer token verification, and `app/mcp_tools.py:L59, L91` sends only `headers = {"X-MCP-Token": ...}`.
4. **Missing Circuit Breaker & Exponential Backoff on MCP HTTP Calls (Principle 3 Violation — SDD §5.2 & §5.5.3)**:
   - **SDD Specification**: SDD §5.2 & §5.5.3 specify 3-attempt exponential backoff with jitter (`500ms, 1000ms, 2000ms`) on transient `429/503/Timeout` errors and a circuit breaker that trips after 5 consecutive failures.
   - **Code Reality**: `call_workweek_mcp_tool` and `call_serviceimmediately_mcp_tool` (`app/mcp_tools.py:L57-L104`) instantiate a fresh `httpx.AsyncClient(timeout=15.0)` per request with zero retry backoff and no circuit breaker.
5. **Supervisor Direct Tool Attachment Bypassing Sub-Agent Layering (SDD §3.1)**:
   - **SDD Specification**: SDD §3.1 specifies that the Supervisor Agent delegates domain tasks to specialist sub-agents (`hr_policy_agent`, `workweek_agent`, `serviceimmediately_agent`).
   - **Code Reality**: `root_agent` (`app/agent.py:L188-L195`) also attaches all tools (`search_hr_policy`, `get_employee_profile`, `get_ticket_details`, `create_workweek_mcp_toolset()`, `create_serviceimmediately_mcp_toolset()`) directly to the supervisor, allowing it to bypass specialist sub-agents.

---

### Axis 3: API & Data Contract Compliance — Score: `3 / 5` (Weight: 20%)

#### Alignment Evidence
- Live MCP tool calls (`get_employee_balances`, `get_personal_info`, `update_personal_info`, `request_time_off`, `list_tickets`, `create_ticket`, `add_ticket_comment`, `update_ticket_status`), MCP resource readers (`workweek://employees/{id}/profile`, `serviceimmediately://tickets/{id}`), and FastAPI endpoints (`/api/chat`, `/api/context`, `/api/audit-logs`) execute cleanly.

#### Architectural Drift Findings & Gaps
1. **FastMCP Tool Name, Parameter, and Unit Discrepancies vs. SDD §5.1**:
   - **WorkWeek Tools (SDD §5.1.1 vs. `app/agent.py:L89-L96`, `app/guardrails.py:L467-L531`)**:
     - SDD specifies `get_employee_by_email(email: str) -> EmployeeProfile` (missing; code uses `get_current_employee_id({})` at `app/mcp_tools.py:L130`).
     - SDD specifies `get_leave_balances(employee_id: str) -> LeaveBalanceResponse` in **hours** (`accrued_hours`, `used_hours`, `remaining_hours`); code calls `get_employee_balances` in **days**.
     - SDD specifies `submit_leave_request(employee_id, leave_type, start_date, end_date, hours: float)`; code calls `request_time_off(..., days: float)` and does not enforce `start_d >= datetime.date.today()` in `app/guardrails.py:L495-L510` (only checks `start_d > end_d`).
     - SDD specifies `update_contact_info(employee_id, home_address, phone_number)`; code calls `update_personal_info(employee_id, address, phone)`.
   - **ServiceImmediately Tools (SDD §5.1.2 vs. `app/agent.py:L118-L125`, `app/guardrails.py:L562-L657`)**:
     - SDD specifies `query_ticket_details(ticket_id)`; code uses `get_ticket_details(ticket_id)`.
     - SDD specifies `create_incident_ticket(caller_id, category, short_description, priority, origin)` with a **4-hour duplicate ticket scan**; code calls `create_ticket(requested_by=...)` without an `origin` parameter and without a 4-hour duplicate scan in `enforce_tool_guardrails` (`app/guardrails.py:L562-L603`).
     - SDD specifies `update_ticket_status(ticket_id, target_state, resolution_notes)`; code uses `status` instead of `target_state`.
   - **Policy RAG Tool (SDD §5.1.3 vs. `app/policy_rag.py:L274`)**:
     - SDD specifies `search_hr_policies(query: str, top_k: int = 3) -> List[PolicyChunk]`; code defines `search_hr_policy(query: str, top_k: int = 4) -> dict[str, Any]`.
2. **Missing SDD §5.4.2 Pydantic Entity Schemas & `ToolError` Envelope**:
   - **SDD Specification**: SDD §5.4.2 defines 11 strict Pydantic models (`EmployeeProfile`, `Address`, `LeaveBalanceResponse`, `LeaveBalance`, `LeaveSubmissionResult`, `UpdateResult`, `TicketDetails`, `TicketComment`, `TicketCreationResult`, `StatusUpdateResult`, `PolicyChunk`) and a unified `ToolError` envelope (`PERMISSION_DENIED`, `NOT_FOUND`, `VALIDATION_FAILED`, `RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `INTERNAL`).
   - **Code Reality**: Only `ChatRequest` (`app/fast_api_app.py:L102`) uses Pydantic. `app/fast_api_app.py:L137-L161` parses unstructured text via regex (`re.search(r"Vacation:\s*([\d\.]+)\s*days...")`) and hardcodes fallback ticket ID `"INC0005099"` (`L159`).
3. **Audit Log Schema Mismatch vs. SDD §5.4.3 & PII Masking Gaps vs. SDD §4.3**:
   - **SDD Specification**: SDD §5.4.3 specifies `BigQuery hr_agent_audit.events` with columns `event_time`, `trace_id`, `session_id`, `user_subject_hash` (SHA-256), `verified_employee_id`, `event_type`, `agent`, `tool_name`, `tool_args_redacted`, `backend_object_id`, `latency_ms`, `model_id`, `input_tokens`, `output_tokens`, and `armor_verdict`. SDD §4.3 requires masking phone numbers, home street addresses, and medical notes to `[REDACTED_PII]`.
   - **Code Reality**: `record_audit_event` (`app/guardrails.py:L48-L71`) appends a 7-key dict to an in-memory 200-item list (`AUDIT_LOG`), and `redact_spii` (`app/guardrails.py:L79-L107`) only masks SG NRIC, US SSN, and credit card numbers (omitting phone numbers, street addresses, and medical text).

---

### Axis 4: Deployment, Evaluation & Operational Readiness — Score: `3 / 5` (Weight: 25%)

#### Alignment Evidence
- **Containerization (`Dockerfile:L1-L33`)**: Clean `python:3.12-slim` image with `uv sync --frozen` exposing port `8080`.
- **Automated Test & Evaluation Suite**:
  - `33/33` unit and integration tests passing (`tests/unit/test_hr_agent_unit.py`, `tests/integration/test_agent.py`, `tests/integration/test_server_e2e.py`).
  - `agents-cli eval run` golden benchmark (`tests/eval/datasets/hr-mvp1-golden-dataset.json`, `tests/eval/eval_config.yaml`, `tests/eval/response_quality.py`) achieves `5.0000 / 5.0000` (`10/10` pass) with comprehensive reports in `tests/eval/evaluation_report.md` and `artifacts/docs/eval_report.md`.

#### Architectural Drift Findings & Gaps (Capped at 3 via Principles 1, 2 & 3)
1. **Environment & Hostname Hardcoding (Principle 1 Violation)**:
   - Hardcoded GCP project ID `"dc-test2-489503"` in `app/agent.py:L39`, `app/fast_api_app.py:L51`, `app/guardrails.py:L284`, `app/policy_rag.py:L315`, and `tests/eval/response_quality.py:L26`.
   - Hardcoded machine-specific Cloudtop URL `"http://doralyn.c.googlers.com:8000,https://doralyn.c.googlers.com:8000"` and wildcard `"*"` in `app/fast_api_app.py:L55-L58`.
2. **Incomplete `.env.example` & Committed `.env` Secrets (Principle 2 Violation)**:
   - `hr-agent/.env.example:L1-L11` contains only 3 generic variables (`GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`), omitting `GEMINI_MODEL`, `WORKWEEK_MCP_URL`, `WORKWEEK_MCP_TOKEN`, `SERVICEIMMEDIATELY_MCP_URL`, `SERVICEIMMEDIATELY_MCP_TOKEN`, `MODEL_ARMOR_TEMPLATE_ID`, `ENABLE_VERTEX_SEARCH`, and `VERTEX_AI_DATA_STORE_ID`.
   - Meanwhile, `hr-agent/.env` sits in the project root with live MCP tokens.
3. **Unupdated Scaffold `README.md`**:
   - `hr-agent/README.md:L1-L93` still reads `"# hr-agent — Simple ReAct agent"` and omits architecture diagrams, MCP configuration instructions, custom API endpoints (`/ui`, `/hr`, `/api/chat`, `/api/context`, `/api/audit-logs`), and evaluation execution steps.
4. **Terraform IaC Drift vs. SDD §4.2, §5.5.3 & §7.1 (Principle 3 Violation)**:
   - **SDD Specification**: SDD §7.1 specifies modular Terraform (`modules/cloud_run`, `modules/vertex_ai`, `modules/security`, `modules/storage`), Cloud Run sizing (`2 vCPU, 4Gi RAM, concurrency=80, min_instances=1, max_instances=20`, SDD §5.5.3 & §6.2), VPC-SC perimeter, Serverless VPC Access, Cloud Armor, CMEK, Secret Manager secrets, Vertex AI Search datastore, Model Armor template, and `gs://altostrat-hr-policies` with Pub/Sub sync.
   - **Code Reality**: `deployment/terraform/single-project/service.tf:L21, L52, L99, L103` is a flat single-project template with `ingress = "INGRESS_TRAFFIC_ALL"`, `cpu = "1"`, `max_instance_request_concurrency = 8`, `max_instance_count = 10`, and omits Vertex AI Search, Model Armor, Secret Manager, CMEK, VPC-SC, and policy bucket resources.
5. **Evaluation Dataset Volume & Load Testing Gap vs. SDD §9.1 & §9.2 (Principle 3 Violation)**:
   - **SDD Specification**: SDD §9.1 & §9.2 commit to a **250-scenario Golden Test Set** (150 Policy, 50 HCM, 50 ITSM), **50-vector Red-Team Suite**, **20 Identity-Isolation cases**, **200 Benign False-Positive conversations**, and **50/500-user load testing**.
   - **Code Reality**: The repository implements 10 golden benchmark cases (`hr-mvp1-golden-dataset.json`), 4 basic cases (`basic-dataset.json`), and 33 pytest tests, with no load test suite (`locustfile.py`).

---

## Prioritized Remediation Roadmap (Path from Grade `C` $\to$ Grade `A`)

To eliminate all Principle 1, 2, and 3 caps and raise the Architecture Drift score from **`3.00 (C)`** to **`4.75+ (A)`**, apply the following five prioritized engineering remediations:

1. **P0 — Eliminate Hardcoded Identities, Secrets, Project IDs & Hostnames (Unlocks Principles 1 & 2)**:
   - Remove hardcoded `"EMP-894"` from `app/agent.py`, `app/guardrails.py`, and `app/mcp_tools.py`; inject authenticated user identity dynamically per session via ADK `CallbackContext.state["verified_employee_id"]` and instruction templates.
   - Remove default MCP token strings from `app/mcp_tools.py:L39-L51`, remove `"dc-test2-489503"` and `"doralyn.c.googlers.com"` defaults from `app/*.py`, and populate `.env.example` with all required configuration keys.
2. **P0 — Implement OAuth 2.0 Identity Middleware & Audit Headers (SDD §4.1.1 & §4.4)**:
   - Add FastAPI middleware in `app/fast_api_app.py` to validate `Authorization: Bearer <token>`, resolve `verified_employee_id` per session (replacing the global `_CACHED_VERIFIED_EMPLOYEE_ID`), and pass `X-On-Behalf-Of` and `X-Automation-Origin: altostrat-hr-agent` headers in `app/mcp_tools.py`.
3. **P1 — Add MCP Circuit Breaker, Exponential Backoff & 4-Hour Duplicate Ticket Scan (SDD §5.1.2 & §5.2)**:
   - Implement a 3-attempt exponential backoff with jitter (`500ms, 1000ms, 2000ms`) and a 5-failure circuit breaker in `app/mcp_tools.py`, plus a 4-hour open-ticket duplicate check and future-date check (`start_d >= today`) in `enforce_tool_guardrails` (`app/guardrails.py`).
4. **P1 — Implement Saga Compensating Action Handler & SDD §5.4 Schemas (SDD §5.3 & §5.4)**:
   - Add `app/schemas.py` defining the 11 Pydantic models and `ToolError` envelope from SDD §5.4.2, expand `redact_spii` to mask phone numbers and addresses (`[REDACTED_PII]`), align `record_audit_event` with the SDD §5.4.3 BigQuery schema, and add a Saga compensating handler (`saga_state`) for multi-step cross-system failures.
5. **P2 — Align Terraform IaC, `README.md`, and Evaluation Scale with SDD §7.1 & §9.2**:
   - Update `deployment/terraform/single-project/service.tf` to match SDD §5.5.3 (`cpu = "2"`, `max_instance_request_concurrency = 80`, `max_instance_count = 20`, Secret Manager bindings, policy bucket), update `README.md` to document the Altostrat HR Multi-Agent architecture, and add load-testing / expanded benchmark suites.

---

## Structured JSON Output (`ArchitectureDriftEvalResult`)

Full structured evaluation output saved at [`artifacts/docs/architecture_drift_result.json`](file:///usr/local/google/home/doralyn/secure-agent-lab/hr-agent/artifacts/docs/architecture_drift_result.json):

```json
{
  "overall_drift_score": 3.0,
  "grade": "C",
  "drift_level": "Moderate Drift",
  "axes": [
    { "name": "Component & Topology Alignment", "score": 3, "weight": 0.30 },
    { "name": "Design Pattern & Abstraction Drift", "score": 3, "weight": 0.25 },
    { "name": "API & Data Contract Compliance", "score": 3, "weight": 0.20 },
    { "name": "Deployment, Evaluation & Operational Readiness", "score": 3, "weight": 0.25 }
  ],
  "red_flags": [
    "Hardcoded MCP authentication tokens (WORKWEEK_MCP_TOKEN, SERVICEIMMEDIATELY_MCP_TOKEN) in app/mcp_tools.py:L39-L51 and committed .env file.",
    "Hardcoded user identity ('EMP-894') in system prompts (app/agent.py:L55,L87,L116,L153) and process-global _CACHED_VERIFIED_EMPLOYEE_ID (app/mcp_tools.py:L54).",
    "Hardcoded GCP project ID ('dc-test2-489503') and machine-specific hostname ('http://doralyn.c.googlers.com:8000') in app/fast_api_app.py:L51-L58."
  ]
}
```
