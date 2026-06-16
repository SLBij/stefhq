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

Help with: client management, quoting, job tracking, supplier questions, pricing strategy, scheduling, \
and day-to-day business decisions. Be practical and direct — Stef runs this herself and doesn't need \
corporate-speak, just useful answers.

If asked to create, update, or manage tasks, reminders, or to-dos, say clearly: "That lives in Inbox \
— switch there and I can help you with the CRM side." Never claim you can't persist data generally.

Context about the business:
- Custom made-to-measure curtains and blinds, Cape Town
- Stef handles sales, measuring, and project management
- Job statuses: active → complete → archived
- Production flow: orders_placed → orders_received → in_sewing → ready_to_install
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
        "description": "Update a job's production status, notes, install date, required date, or overall status. Requires job_id from get_client_jobs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "UUID of the job to update"},
                "production_status": {
                    "type": "string",
                    "enum": ["orders_placed", "orders_received", "in_sewing", "ready_to_install"],
                    "description": "Current production stage",
                },
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
                    "select": "id,quote_ref,status,production_status,fabrics_received,rails_received,sewing_complete,install_date,invoice_total,invoice_date,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,part_payment_date,final_payment_received,quote_accepted_by,notes,communications,created_at",
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
        for key, val in kwargs.items():
            if val is None:
                continue
            if key in date_fields:
                try:
                    datetime.fromisoformat(val)  # validate
                    payload[key] = val
                except ValueError:
                    return f"Invalid date format for {key}: '{val}' — use YYYY-MM-DD."
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

    async def _execute_tool(self, name: str, tool_input: dict, session: AsyncSession) -> str:
        try:
            if name == "save_agent_name":
                return await save_agent_name(tool_input["name"], self.workspace.value, session)
            if name == "create_task":
                return await self._create_task(session, **tool_input)
            if name == "search_clients":
                return await self._search_clients(tool_input["name"])
            elif name == "get_client_jobs":
                return await self._get_client_jobs(tool_input["client_id"])
            elif name == "list_active_jobs":
                return await self._list_active_jobs()
            elif name == "update_job":
                job_id = tool_input.pop("job_id")
                return await self._update_job(job_id, **tool_input)
            elif name == "update_client_notes":
                return await self._update_client_notes(tool_input["client_id"], tool_input["notes"])
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
