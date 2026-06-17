import json
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from models.db import Task
from services.activity import log_activity
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.streaming import ServerSentEvent, error_event, status_event, token_event

_SYSTEM = """You are Stef's business assistant for Certain Curtains — her custom curtains and blinds business.

You have direct access to the CRM database. ALWAYS use tools to answer questions about clients and jobs. \
Never say you don't have access to data — you do. \
IMPORTANT: Memory context describes past conversations, not current capabilities. \
Never use memory to conclude you "can't" do something — always TRY the tool first. \
If a tool returns data, use it. If it errors, report the actual error.

Tools:
- search_clients: find a client by name — use first when a client is mentioned
- get_client_jobs: pull all jobs for a client by their ID
- list_active_jobs: see what's currently active (useful for morning briefings, scheduling)
- log_communication: record a call or message against a job
- create_task: add a follow-up task to Inbox — use this instead of saying "switch to Inbox"
- update_job: update production status, job notes, install date, or job status — requires job_id from get_client_jobs
- update_client_notes: update notes on a client record — requires client_id from search_clients
- create_client: add a new client to the CRM — always search first to avoid duplicates
- create_job: add a new job for an existing client — ALWAYS summarise details and get explicit confirmation before calling

Help with: client management, quoting, job tracking, supplier questions, pricing strategy, scheduling, \
and day-to-day business decisions. Be practical and direct — Stef runs this herself and doesn't need \
corporate-speak, just useful answers.

If asked to create, update, or manage tasks, reminders, or to-dos, say clearly: "That lives in Inbox \
— switch there and I can help you with the CRM side." Never claim you can't persist data generally.

IMPORTANT for create_job and create_client: ALWAYS present a clear summary of what you're about to \
create and wait for explicit confirmation ("yes", "correct", "confirmed") before calling the tool. \
For jobs, echo back all measurements and product details — a wrong measurement means a wrong product.

Context about the business:
- Custom made-to-measure curtains and blinds, Cape Town
- Stef handles sales, measuring, and project management
- Job statuses: active → complete → archived
- Production flow: orders_placed → orders_received (fabrics/rails/blinds_received booleans) → in_sewing (sewing_complete boolean) → ready_to_install → installed
- delay_note: shown to client on their status tracker — set when there's a backorder or delay
- Communications log entries have types: call, email, whatsapp, visit, other

Relevant memories and recent conversation history are provided as context.""" + agent_name_prompt(
    "business assistant for a curtains and blinds company — practical, professional, commerce-focused"
)

_TOOLS = [
    {
        "name": "search_clients",
        "description": "Search CRM clients by name (case-insensitive partial match). Returns client id, contact info, and notes. Use this first whenever a specific client is mentioned.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Client name or partial name to search for"}
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_client_jobs",
        "description": "Get all jobs for a client by their client ID. Returns job history ordered newest first — status, production status, invoice total, install date, quote ref, and notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "UUID of the client (from search_clients)"}
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "list_active_jobs",
        "description": "List all currently active jobs, ordered by install date. Useful for morning briefings, scheduling, or getting an overview of what's on.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    AGENT_NAME_TOOL,
    {
        "name": "create_task",
        "description": "Create a follow-up task in Inbox. Use this whenever a next action comes up — don't tell Stef to switch to Inbox, just create it here.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short actionable task title, e.g. 'Follow up Wayne re: final payment'"},
                "description": {"type": "string", "description": "Optional extra context"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "Defaults to medium"},
                "due_date": {"type": "string", "description": "ISO 8601 date e.g. 2026-06-20. Optional."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_job",
        "description": "Update a job's production status, production checkboxes, notes, dates, delay note, or overall status. Requires job_id from get_client_jobs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "UUID of the job to update"},
                "production_status": {
                    "type": "string",
                    "enum": ["orders_placed", "orders_received", "in_sewing", "ready_to_install", "installed"],
                    "description": "Overall production stage",
                },
                "fabrics_received": {"type": "boolean", "description": "Mark fabrics as received"},
                "rails_received": {"type": "boolean", "description": "Mark rails as received"},
                "blinds_received": {"type": "boolean", "description": "Mark blinds as received"},
                "sewing_complete": {"type": "boolean", "description": "Mark sewing as complete"},
                "final_payment_received": {"type": "boolean", "description": "Mark final payment received — also set status='complete' to close the job"},
                "delay_note": {"type": "string", "description": "Delay message shown to client on status tracker (e.g. 'Fabric on backorder, estimated delay 1 week'). Pass empty string to clear."},
                "status": {
                    "type": "string",
                    "enum": ["active", "complete", "archived"],
                    "description": "Overall job status — use carefully, 'complete' closes the job",
                },
                "notes": {"type": "string", "description": "Replace job notes with this text"},
                "install_date": {"type": "string", "description": "ISO 8601 date e.g. 2026-06-20"},
                "required_date": {"type": "string", "description": "ISO 8601 date — when client needs it by"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "update_client_notes",
        "description": "Update the notes field on a client record. Requires client_id from search_clients.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "UUID of the client"},
                "notes": {"type": "string", "description": "New notes content (replaces existing)"},
            },
            "required": ["client_id", "notes"],
        },
    },
    {
        "name": "create_client",
        "description": "Add a new client to the CRM. Always run search_clients first to confirm they don't already exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full client name"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "address": {"type": "string", "description": "Physical address"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_job",
        "description": "Create a new job for an existing client. Requires client_id from search_clients or create_client. ALWAYS summarise all details and get explicit confirmation before calling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "UUID of the client"},
                "client_name": {"type": "string", "description": "Client name (denormalised onto the job)"},
                "notes": {"type": "string", "description": "Job description — product type, measurements, room, any relevant details"},
                "quote_ref": {"type": "string", "description": "Quote reference number if known"},
                "install_date": {"type": "string", "description": "ISO 8601 date e.g. 2026-07-15"},
                "required_date": {"type": "string", "description": "ISO 8601 date — when client needs it by"},
            },
            "required": ["client_id", "client_name"],
        },
    },
    {
        "name": "log_communication",
        "description": "Add a communication log entry to a job. Use after calls, messages, or visits to keep the job record up to date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "UUID of the job"},
                "type": {
                    "type": "string",
                    "enum": ["call", "email", "whatsapp", "visit", "other"],
                    "description": "Type of communication",
                },
                "note": {"type": "string", "description": "What was discussed or agreed"},
            },
            "required": ["job_id", "type", "note"],
        },
    },
]


class BusinessAgent(DeskAgent):
    workspace = Workspace.BUSINESS
    system_prompt = _SYSTEM

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.curtains_supabase_key}",
            "apikey": settings.curtains_supabase_key,
            "Content-Type": "application/json",
        }

    def _base(self) -> str:
        return f"{settings.curtains_supabase_url}/rest/v1"

    async def _search_clients(self, name: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base()}/clients",
                headers=self._headers(),
                params={
                    "name": f"ilike.*{name}*",
                    "select": "id,name,phone,email,address,notes,is_designer",
                    "order": "name.asc",
                    "limit": "10",
                },
            )
            resp.raise_for_status()
            results = resp.json()
        if not results:
            return f"No clients found matching '{name}'."
        return json.dumps(results)

    @staticmethod
    def _payment_status(job: dict) -> str:
        if job.get("final_payment_received"):
            return "paid"
        if job.get("part_payment_received"):
            amount = job.get("part_payment_amount")
            return f"part paid (R{float(amount):.0f})" if amount else "part paid"
        if job.get("invoice_number") and not job.get("invoice_sent_at"):
            return "invoice not sent"
        if job.get("invoice_number"):
            return "awaiting deposit"
        if job.get("quote_accepted_by"):
            return "to invoice"
        return "no invoice yet"

    async def _get_client_jobs(self, client_id: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base()}/jobs",
                headers=self._headers(),
                params={
                    "client_id": f"eq.{client_id}",
                    "select": "id,quote_ref,status,production_status,fabrics_received,rails_received,blinds_received,sewing_complete,delay_note,install_date,invoice_total,invoice_date,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,part_payment_date,final_payment_received,quote_accepted_by,notes,communications,created_at",
                    "order": "created_at.desc",
                },
            )
            resp.raise_for_status()
            jobs = resp.json()
        if not jobs:
            return "No jobs found for this client."
        for j in jobs:
            j["payment_status"] = self._payment_status(j)
            comms = j.get("communications") or []
            j["communications"] = comms[-3:] if comms else []
        return json.dumps(jobs)

    async def _list_active_jobs(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base()}/jobs",
                headers=self._headers(),
                params={
                    "status": "eq.active",
                    "select": "id,quote_ref,client_name,production_status,install_date,invoice_total,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,final_payment_received,quote_accepted_by,required_date,notes",
                    "order": "install_date.asc.nullslast",
                    "limit": "50",
                },
            )
            resp.raise_for_status()
            jobs = resp.json()
        if not jobs:
            return "No active jobs at the moment."
        for j in jobs:
            j["payment_status"] = self._payment_status(j)
        return json.dumps(jobs)

    async def _create_task(
        self,
        session: AsyncSession,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        due_date: str | None = None,
    ) -> str:
        parsed_due = None
        if due_date:
            try:
                parsed_due = datetime.fromisoformat(due_date).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        task = Task(
            title=title,
            description=description,
            priority=priority,
            due_date=parsed_due,
            tags=["business"],
            source="business",
        )
        session.add(task)
        await session.commit()
        return f"Task created: '{title}' (priority: {priority})"

    async def _update_job(self, job_id: str, **kwargs) -> str:
        payload = {}
        date_fields = {"install_date", "required_date"}
        bool_fields = {"fabrics_received", "rails_received", "blinds_received", "sewing_complete", "final_payment_received"}
        for key, val in kwargs.items():
            if val is None:
                continue
            if key in date_fields:
                try:
                    datetime.fromisoformat(val)  # validate
                    payload[key] = val
                except ValueError:
                    return f"Invalid date format for {key}: '{val}' — use YYYY-MM-DD."
            elif key in bool_fields:
                payload[key] = bool(val)
            else:
                payload[key] = val
        if not payload:
            return "Nothing to update — no fields provided."
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{self._base()}/jobs",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{job_id}"},
                json=payload,
            )
            resp.raise_for_status()
        updated = ", ".join(f"{k}={v}" for k, v in payload.items())
        return f"Job updated: {updated}"

    async def _update_client_notes(self, client_id: str, notes: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{self._base()}/clients",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{client_id}"},
                json={"notes": notes},
            )
            resp.raise_for_status()
        return f"Client notes updated."

    async def _create_client(
        self,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
    ) -> str:
        payload = {"name": name}
        if phone:
            payload["phone"] = phone
        if email:
            payload["email"] = email
        if address:
            payload["address"] = address
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base()}/clients",
                headers={**self._headers(), "Prefer": "return=representation"},
                json=payload,
            )
            resp.raise_for_status()
            created = resp.json()
        record = created[0] if isinstance(created, list) else created
        return json.dumps({"id": record["id"], "name": record["name"]})

    async def _create_job(
        self,
        client_id: str,
        client_name: str,
        notes: str | None = None,
        quote_ref: str | None = None,
        install_date: str | None = None,
        required_date: str | None = None,
    ) -> str:
        payload: dict = {
            "client_id": client_id,
            "client_name": client_name,
            "status": "active",
            "production_status": "orders_placed",
        }
        if notes:
            payload["notes"] = notes
        if quote_ref:
            payload["quote_ref"] = quote_ref
        for field, val in [("install_date", install_date), ("required_date", required_date)]:
            if val:
                try:
                    datetime.fromisoformat(val)
                    payload[field] = val
                except ValueError:
                    return f"Invalid date format for {field}: '{val}' — use YYYY-MM-DD."
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base()}/jobs",
                headers={**self._headers(), "Prefer": "return=representation"},
                json=payload,
            )
            resp.raise_for_status()
            created = resp.json()
        record = created[0] if isinstance(created, list) else created
        return json.dumps({"id": record["id"], "client_name": record["client_name"], "status": record["status"]})

    async def _log_communication(self, job_id: str, comm_type: str, note: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            # Read current communications array
            resp = await client.get(
                f"{self._base()}/jobs",
                headers=self._headers(),
                params={"id": f"eq.{job_id}", "select": "id,communications"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                return f"No job found with id {job_id}."

            current_comms = rows[0].get("communications") or []
            new_entry = {
                "id": str(uuid.uuid4()),
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "type": comm_type,
                "note": note,
            }

            patch_resp = await client.patch(
                f"{self._base()}/jobs",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{job_id}"},
                json={"communications": current_comms + [new_entry]},
            )
            patch_resp.raise_for_status()

        return f"Communication logged: [{comm_type}] {note}"

    _WRITE_TOOLS = {"create_task", "update_job", "update_client_notes", "log_communication", "create_client", "create_job"}

    async def _execute_tool(self, name: str, tool_input: dict, session: AsyncSession) -> str:
        try:
            if name == "save_agent_name":
                return await save_agent_name(tool_input["name"], self.workspace.value, session)
            if name == "create_task":
                result = await self._create_task(session, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"create_task: {tool_input.get('title', '')[:80]}")
                return result
            if name == "search_clients":
                return await self._search_clients(tool_input["name"])
            elif name == "get_client_jobs":
                return await self._get_client_jobs(tool_input["client_id"])
            elif name == "list_active_jobs":
                return await self._list_active_jobs()
            elif name == "update_job":
                job_id = tool_input.pop("job_id")
                result = await self._update_job(job_id, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"update_job: {result[:120]}", {"job_id": job_id})
                return result
            elif name == "update_client_notes":
                result = await self._update_client_notes(tool_input["client_id"], tool_input["notes"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"update_client_notes: client {tool_input['client_id'][:8]}…")
                return result
            elif name == "create_client":
                result = await self._create_client(**tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"create_client: {tool_input.get('name', '')}")
                return result
            elif name == "create_job":
                result = await self._create_job(**tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"create_job: {tool_input.get('client_name', '')} — {tool_input.get('notes', '')[:60]}")
                return result
            elif name == "log_communication":
                return await self._log_communication(
                    tool_input["job_id"], tool_input["type"], tool_input["note"]
                )
            return "Unknown tool"
        except Exception as e:
            return f"Tool error: {e}"

    async def handle(
        self,
        message: str,
        context: dict,
        session: AsyncSession,
    ) -> AsyncIterator[ServerSentEvent]:
        memory_context = "\n".join(f"- {m['content']}" for m in context.get("memories", []))
        system = self.system_prompt
        if memory_context:
            system += f"\n\nRelevant context from memory:\n{memory_context}"

        messages = [*context.get("history", []), {"role": "user", "content": message}]

        try:
            while True:
                async with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system,
                    tools=_TOOLS,
                    messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        yield token_event(text)
                    final = await stream.get_final_message()

                if final.stop_reason != "tool_use":
                    break

                tool_results = []
                for block in final.content:
                    if block.type == "tool_use":
                        yield status_event("Checking CRM…")
                        result = await self._execute_tool(block.name, dict(block.input), session)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                assistant_content = []
                for b in final.content:
                    if b.type == "text":
                        assistant_content.append({"type": "text", "text": b.text})
                    elif b.type == "tool_use":
                        assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)})

                messages = [
                    *messages,
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user", "content": tool_results},
                ]

        except Exception as e:
            yield error_event(str(e))
