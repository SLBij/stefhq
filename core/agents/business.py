import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from integrations.google import (
    calendar_create_event,
    calendar_list_events,
    get_access_token,
    gmail_create_draft,
    gmail_get_message,
    gmail_list_messages,
    gmail_send_draft,
)
from models.db import Task
from services.activity import log_activity
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.pending_events import (
    pop_pending_email,
    pop_pending_event,
    store_pending_email,
    store_pending_event,
)
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
- compose_email: draft an email and save it to Gmail Drafts — show the full draft, then tell Stef to open Gmail to review and send. Do NOT offer to send programmatically.
- propose_calendar_event: propose an install/site visit event — ALWAYS show full details and wait for confirmation before calling confirm_calendar_event
- confirm_calendar_event: create a proposed event in Google Calendar — ONLY call after Stef explicitly confirms
- list_upcoming_events: check Google Calendar for upcoming events (scheduling, availability)
- list_emails: check the inbox — list recent emails with From/Subject/Date/snippet. Useful for "any new emails?", "what's in the inbox?", checking for replies
- read_email: read the full body of a specific email by ID (from list_emails)
- schedule_reminder: schedule a Telegram reminder at a specific date/time — use for follow-ups, deadlines, anything time-based
- list_reminders: show all pending business reminders not yet fired
- cancel_reminder: cancel a pending reminder by ID (get ID from list_reminders)

Help with: client management, quoting, job tracking, supplier questions, pricing strategy, scheduling, \
email drafting, calendar management, and day-to-day business decisions. Be practical and direct — Stef \
runs this herself and doesn't need corporate-speak, just useful answers.

If asked to create, update, or manage tasks, reminders, or to-dos, say clearly: "That lives in Inbox \
— switch there and I can help you with the CRM side." Never claim you can't persist data generally.

IMPORTANT for create_job and create_client: ALWAYS present a clear summary of what you're about to \
create and wait for explicit confirmation ("yes", "correct", "confirmed") before calling the tool. \
For jobs, echo back all measurements and product details — a wrong measurement means a wrong product. \
NEVER call create_job without measurements (width + drop). If missing, ask before doing anything else — \
Stef may be on site and can still measure; once she leaves, those numbers are gone.

IMPORTANT for email/calendar tools:
- For compose_email: write professional, concise business emails. After calling compose_email, display \
the FULL draft clearly (To, Subject, Body) and tell Stef to open Gmail Drafts to add any attachments \
and send. NEVER offer to send programmatically — sending is always manual for now.
- For propose_calendar_event: display the full event details (title, date/time, location, notes) and \
say "Reply 'add it' to confirm." NEVER call confirm_calendar_event without explicit confirmation.
- Customer email addresses go in the event description/notes, NOT as attendees. Stef will add them \
manually once she's verified the event is correct.
- All times are SAST (UTC+2, Africa/Johannesburg). Use ISO 8601 with +02:00 offset.
- After sending email, call log_communication to record it against the job (type: "email").

When Stef sends /newjob, respond ONLY with this template (no extra text):
New job — please fill in:
Client:
Room/location:
Product (curtains/blinds/type):
Width (mm):
Drop (mm):
Stack direction:
Fabric/colour:
Quote ref (if known):
Install date (if known):

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
                "measurements": {"type": "string", "description": "Width x drop for each window e.g. '5685w x 2764d' or 'LR: 5685x2764, MBR: 3200x2800'. REQUIRED — do not call without this."},
                "product": {"type": "string", "description": "Product type, style, stack, fabric e.g. 'Wave curtains, LR stack, Shernice Sand'"},
                "room": {"type": "string", "description": "Room or location e.g. 'Main bedroom', 'Living room'"},
                "notes": {"type": "string", "description": "Any extra notes not covered above"},
                "quote_ref": {"type": "string", "description": "Quote reference number if known"},
                "install_date": {"type": "string", "description": "ISO 8601 date e.g. 2026-07-15"},
                "required_date": {"type": "string", "description": "ISO 8601 date — when client needs it by"},
            },
            "required": ["client_id", "client_name", "measurements"],
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
    {
        "name": "compose_email",
        "description": (
            "Compose an email and save it as a Gmail draft. "
            "Display the full draft (To, Subject, Body) so Stef can review it, "
            "then tell her to open Gmail Drafts to add attachments and send. "
            "Do NOT offer to send it — sending is manual for now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string", "description": "Recipient email address"},
                "to_name": {"type": "string", "description": "Recipient display name, e.g. 'Sarah van der Berg'"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body — plain text, professional tone. Sign off as Stef / Certain Curtains."},
                "cc": {"type": "string", "description": "CC email address, if any"},
            },
            "required": ["to_email", "subject", "body"],
        },
    },
    {
        "name": "propose_calendar_event",
        "description": (
            "Propose a Google Calendar event (install appointment, site visit, measure, etc). "
            "Returns a pending_id and full event details for review. "
            "ALWAYS display the full details and wait for explicit confirmation before calling confirm_calendar_event. "
            "Put client contact info in the description, NOT as attendees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title, e.g. 'Install — Sarah van der Berg'"},
                "start_datetime": {"type": "string", "description": "ISO 8601 in SAST, e.g. '2026-06-25T14:00:00+02:00'"},
                "end_datetime": {"type": "string", "description": "ISO 8601 in SAST, e.g. '2026-06-25T16:00:00+02:00'"},
                "description": {"type": "string", "description": "Notes — include client name, address, what's being installed, client phone/email"},
                "location": {"type": "string", "description": "Install address"},
            },
            "required": ["title", "start_datetime", "end_datetime"],
        },
    },
    {
        "name": "confirm_calendar_event",
        "description": (
            "Create a previously proposed calendar event in Google Calendar. "
            "ONLY call after Stef has explicitly confirmed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pending_id": {"type": "string", "description": "Pending event ID from propose_calendar_event"},
            },
            "required": ["pending_id"],
        },
    },
    {
        "name": "list_upcoming_events",
        "description": "List upcoming Google Calendar events to check schedule or availability before proposing a time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 7, max 30)"},
            },
        },
    },
    {
        "name": "list_emails",
        "description": "List recent Gmail inbox emails. Returns From, Subject, Date, and a snippet per message. Use for 'any new emails?', 'check the inbox', or before reading a specific one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'in:inbox is:unread', 'from:someone@example.com'. Defaults to 'in:inbox'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of emails to return (1–20, default 10).",
                },
            },
        },
    },
    {
        "name": "read_email",
        "description": "Read the full body of a specific email by its ID (from list_emails).",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Gmail message ID from list_emails"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "schedule_reminder",
        "description": "Schedule a Telegram reminder at a specific date and time. Use for follow-ups, payment chasers, deadlines, or any time-based nudge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Reminder text, e.g. 'Follow up with Angelique re: provisional install date'"},
                "remind_at": {"type": "string", "description": "ISO 8601 datetime in SAST (UTC+2), e.g. '2026-06-23T15:00:00+02:00'"},
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List all pending business Telegram reminders that haven't fired yet.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending business reminder. Use the short ID shown in list_reminders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Full UUID of the reminder (from list_reminders)"},
            },
            "required": ["reminder_id"],
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
        measurements: str,
        product: str | None = None,
        room: str | None = None,
        notes: str | None = None,
        quote_ref: str | None = None,
        install_date: str | None = None,
        required_date: str | None = None,
    ) -> str:
        note_parts = []
        if room:
            note_parts.append(f"Room: {room}")
        if product:
            note_parts.append(f"Product: {product}")
        note_parts.append(f"Measurements: {measurements}")
        if notes:
            note_parts.append(notes)
        payload: dict = {
            "client_id": client_id,
            "client_name": client_name,
            "status": "quoting",
            "notes": "\n".join(note_parts),
        }
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

    async def _compose_email(
        self,
        user_id: uuid.UUID,
        session: AsyncSession,
        to_email: str,
        subject: str,
        body: str,
        to_name: str | None = None,
        cc: str | None = None,
    ) -> str:
        access_token = await get_access_token(user_id, session)
        result = await gmail_create_draft(access_token, to_email, subject, body, to_name, cc)
        pending_email_id = await store_pending_email({
            "draft_id": result["draft_id"],
            "to_email": to_email,
            "to_name": to_name,
            "subject": subject,
        })
        to_display = f"{to_name} <{to_email}>" if to_name else to_email
        return (
            f"Draft saved to Gmail.\n\n"
            f"**To:** {to_display}\n"
            + (f"**Cc:** {cc}\n" if cc else "")
            + f"**Subject:** {subject}\n\n"
            f"{body}"
        )

    async def _send_email(
        self, user_id: uuid.UUID, session: AsyncSession, pending_email_id: str
    ) -> str:
        details = await pop_pending_email(pending_email_id)
        if details is None:
            return "Pending email not found — it may have expired. Please use compose_email again."
        draft_id = details.get("draft_id")
        if not draft_id:
            return f"Stored email is missing draft_id. Stored keys: {list(details.keys())}. Please compose again."
        access_token = await get_access_token(user_id, session)
        await gmail_send_draft(access_token, draft_id)
        return f"Email sent to {details.get('to_name') or details['to_email']} — Subject: {details['subject']}"

    async def _propose_calendar_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str,
        description: str | None = None,
        location: str | None = None,
    ) -> str:
        details = {
            "title": title,
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
            "description": description,
            "location": location,
        }
        pending_id = await store_pending_event(details)
        preview_lines = [
            f"pending_id: {pending_id}",
            "",
            f"**Event:** {title}",
            f"**Start:** {start_datetime}",
            f"**End:** {end_datetime}",
        ]
        if location:
            preview_lines.append(f"**Location:** {location}")
        if description:
            preview_lines.append(f"**Notes:** {description}")
        return "\n".join(preview_lines)

    async def _confirm_calendar_event(
        self, user_id: uuid.UUID, session: AsyncSession, pending_id: str
    ) -> str:
        details = await pop_pending_event(pending_id)
        if details is None:
            return "Pending event not found — it may have expired (24h limit). Please propose it again."
        access_token = await get_access_token(user_id, session)
        result = await calendar_create_event(
            access_token,
            title=details["title"],
            start_datetime=details["start_datetime"],
            end_datetime=details["end_datetime"],
            description=details.get("description"),
            location=details.get("location"),
        )
        link = result.get("html_link", "")
        return f"Event created: {details['title']}\n{link}"

    async def _list_upcoming_events(
        self, user_id: uuid.UUID, session: AsyncSession, days_ahead: int = 7
    ) -> str:
        access_token = await get_access_token(user_id, session)
        events = await calendar_list_events(access_token, days_ahead)
        if not events:
            return f"No events in the next {days_ahead} days."
        return json.dumps(events)

    async def _list_emails(
        self, user_id: uuid.UUID, session: AsyncSession,
        query: str = "in:inbox", max_results: int = 10,
    ) -> str:
        access_token = await get_access_token(user_id, session)
        emails = await gmail_list_messages(access_token, query, max_results)
        if not emails:
            return "No emails found."
        return json.dumps(emails)

    async def _read_email(
        self, user_id: uuid.UUID, session: AsyncSession, email_id: str
    ) -> str:
        access_token = await get_access_token(user_id, session)
        email = await gmail_get_message(access_token, email_id)
        return json.dumps(email)

    async def _schedule_reminder(self, session: AsyncSession, message: str, remind_at: str) -> str:
        from services.reminders import create_reminder, set_arq_job_id
        from workers.arq_pool import get_pool
        dt = datetime.fromisoformat(remind_at)
        dt_utc = dt.astimezone(timezone.utc)
        reminder = await create_reminder(session, message, dt_utc, "business")
        pool = await get_pool()
        job = await pool.enqueue_job(
            "send_telegram_reminder", message=message,
            reminder_id=str(reminder.id), _defer_until=dt_utc,
        )
        if job:
            await set_arq_job_id(session, reminder, job.job_id)
        else:
            await session.commit()
        local_str = dt.strftime("%-d %b at %-I:%M %p")
        return f"Reminder set for {local_str} SAST: {message}"

    async def _list_reminders(self, session: AsyncSession) -> str:
        from services.reminders import list_pending
        reminders = await list_pending(session, "business")
        if not reminders:
            return "No pending business reminders."
        SAST = timezone(timedelta(hours=2))
        lines = []
        for r in reminders:
            local = r.remind_at.astimezone(SAST).strftime("%-d %b at %-I:%M %p")
            lines.append(f"• [{str(r.id)[:8]}] {local} — {r.message}")
        return "\n".join(lines)

    async def _cancel_reminder(self, session: AsyncSession, reminder_id: str) -> str:
        from services.reminders import cancel
        ok = await cancel(session, reminder_id)
        return "Reminder cancelled." if ok else "Reminder not found — it may have already fired."

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

    _WRITE_TOOLS = {
        "create_task", "update_job", "update_client_notes", "log_communication",
        "create_client", "create_job", "compose_email", "send_email",
        "propose_calendar_event", "confirm_calendar_event", "schedule_reminder", "cancel_reminder",
    }

    async def _execute_tool(
        self, name: str, tool_input: dict, session: AsyncSession, user_id: uuid.UUID | None = None
    ) -> str:
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
            elif name == "compose_email":
                if not user_id:
                    return "Cannot compose email: user context missing."
                result = await self._compose_email(user_id, session, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"compose_email: {tool_input.get('subject', '')[:80]}")
                return result
            elif name == "send_email":
                if not user_id:
                    return "Cannot send email: user context missing."
                result = await self._send_email(user_id, session, tool_input["pending_email_id"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"send_email: pending {tool_input['pending_email_id'][:12]}…")
                return result
            elif name == "propose_calendar_event":
                result = await self._propose_calendar_event(**tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"propose_event: {tool_input.get('title', '')[:80]}")
                return result
            elif name == "confirm_calendar_event":
                if not user_id:
                    return "Cannot create calendar event: user context missing."
                result = await self._confirm_calendar_event(user_id, session, tool_input["pending_id"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"confirm_event: {result[:120]}")
                return result
            elif name == "list_upcoming_events":
                if not user_id:
                    return "Cannot access calendar: user context missing."
                return await self._list_upcoming_events(
                    user_id, session, tool_input.get("days_ahead", 7)
                )
            elif name == "list_emails":
                if not user_id:
                    return "Cannot access email: user context missing."
                return await self._list_emails(
                    user_id, session,
                    query=tool_input.get("query", "in:inbox"),
                    max_results=tool_input.get("max_results", 10),
                )
            elif name == "read_email":
                if not user_id:
                    return "Cannot access email: user context missing."
                return await self._read_email(user_id, session, tool_input["email_id"])
            elif name == "schedule_reminder":
                result = await self._schedule_reminder(session, tool_input["message"], tool_input["remind_at"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"schedule_reminder: {tool_input.get('remind_at', '')} — {tool_input.get('message', '')[:60]}")
                return result
            elif name == "list_reminders":
                return await self._list_reminders(session)
            elif name == "cancel_reminder":
                return await self._cancel_reminder(session, tool_input["reminder_id"])
            return "Unknown tool"
        except Exception as e:
            return f"Tool error: {e}"

    async def handle(
        self,
        message: str,
        context: dict,
        session: AsyncSession,
        attachments: list | None = None,
    ) -> AsyncIterator[ServerSentEvent]:
        user_id: uuid.UUID | None = context.get("user_id")
        memory_context = "\n".join(f"- {m['content']}" for m in context.get("memories", []))
        current_dt = context.get("current_datetime", "")
        system = f"Current date and time: {current_dt}\n\n" + self.system_prompt if current_dt else self.system_prompt
        if memory_context:
            system += f"\n\nRelevant context from memory:\n{memory_context}"

        messages = [*context.get("history", []), {"role": "user", "content": self._user_content(message, attachments)}]

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
                        _status = "Sending email…" if block.name in ("send_email", "compose_email") \
                            else "Checking inbox…" if block.name in ("list_emails", "read_email") \
                            else "Updating calendar…" if block.name in ("confirm_calendar_event", "propose_calendar_event", "list_upcoming_events") \
                            else "Setting reminder…" if block.name == "schedule_reminder" \
                            else "Checking CRM…"
                        yield status_event(_status)
                        result = await self._execute_tool(block.name, dict(block.input), session, user_id)
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
