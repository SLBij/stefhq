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
    calendar_get_event,
    calendar_list_events,
    calendar_update_event,
    get_access_token,
    gmail_create_draft,
    gmail_get_message,
    gmail_list_messages,
    gmail_send_draft,
)
from models.db import Note, Task
from services.activity import log_activity
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.pending_events import (
    pop_pending_email,
    pop_pending_event,
    pop_pending_event_update,
    store_pending_email,
    store_pending_event,
    store_pending_event_update,
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
- update_job: update production status, job notes, install date, job status, or payment received status/amount/date — requires job_id from get_client_jobs
- update_client: update a client's name, phone, email, address, notes, or designer flag — requires client_id from search_clients
- create_client: add a new client to the CRM — always search first to avoid duplicates
- create_job: add a new job for an existing client — ALWAYS summarise details and get explicit confirmation before calling
- compose_email: draft an email and save it to Gmail Drafts — show the full draft, then tell Stef to open Gmail to review and send. Do NOT offer to send programmatically.
- propose_calendar_event: propose an install/site visit event — ALWAYS show full details and wait for confirmation before calling confirm_calendar_event
- confirm_calendar_event: create a proposed event in Google Calendar — ONLY call after Stef explicitly confirms
- list_upcoming_events: check Google Calendar for upcoming events (scheduling, availability) — returns each event's id, needed for updates and deletion flags
- propose_calendar_event_update: propose a change (time, title, location, notes) to an existing event — ALWAYS show the before/after and wait for confirmation before calling confirm_calendar_event_update
- confirm_calendar_event_update: apply a previously proposed event change — ONLY call after Stef explicitly confirms
- flag_event_for_deletion: prefix an event's title with "[DELETE]" so Stef can remove it herself — Pip never deletes calendar events directly
- list_emails: check the inbox — list recent emails with From/Subject/Date/snippet. Useful for "any new emails?", "what's in the inbox?", checking for replies
- read_email: read the full body of a specific email by ID (from list_emails)
- list_suppliers / get_supplier: supplier contact details, account numbers, and order email templates
- list_purchase_orders / get_purchase_order: view the PO pipeline — what's been drafted, ordered, paid, received; get full PO with line items
- get_ordering_summary: full picture of what each active job needs (fabrics, linings, rails, blinds) and whether a PO already exists for it
- get_jobs_needing_orders: quick filter — active jobs with no PO placed yet
- get_jobs_awaiting_stock: jobs with POs ordered but stock not yet received
- get_overdue_jobs: active jobs past their target or required date
- list_unpaid_jobs: jobs with invoices outstanding
- draft_supplier_order_email: create a Gmail draft to a supplier for a specific PO using their stored template — never sends, Stef reviews first
- draft_client_update_email: create a Gmail draft to a client — quote_follow_up, payment_request, production_update, delay_notice, install_confirmation, or final_payment_request. Includes a shareable quote link if the job has a share_token (generated in CRM via Share Quote button). Never sends — Stef reviews first
- create_purchase_order: create a new draft PO linked to one or more jobs
- add_po_items: add line items (code, description, qty, unit_price) to a draft PO
- update_po_status: progress a PO from draft → ordered → paid → received
- log_po_issue: record a delay or problem note on a PO
- delete_purchase_order: permanently delete a draft PO and its items — test/duplicate POs only, refuses anything past status='draft'
- get_stock_levels: check current stock quantities, optionally filter to low stock or a category
- get_product: look up a specific product by code or name
- schedule_reminder: schedule a Telegram reminder at a specific date/time — use for follow-ups, deadlines, anything time-based
- list_reminders: show all pending business reminders not yet fired
- cancel_reminder: cancel a pending reminder by ID (get ID from list_reminders)
- pause_briefing: pause Pip's morning briefing until a date (inclusive) — use when Stef says she's on leave or away
- resume_briefing: immediately cancel a briefing pause
- add_email_noise_filter / remove_email_noise_filter / list_email_noise_filters: manage what the midday email digest treats as noise (recurring senders/topics Stef doesn't want flagged)
- send_whatsapp_message: send a WhatsApp text message directly to a client's phone number — use for follow-ups, confirmations, or replying to escalations Stef has asked you to handle
- escalate_to_stef: forward a client WhatsApp message to Stef's Telegram when you can't handle it — include the client name, phone, what they asked, and any relevant job context

WhatsApp channel behaviour:
Messages prefixed [WhatsApp from {name}] are inbound from a client's WhatsApp — you are speaking TO THE CLIENT. Your name is Pip. No markdown formatting — plain text only.

Tone and style (strictly enforced):
- Do NOT narrate internal actions. Never say "Let me check", "Let me pull up", "I'll look into that" — just answer. If you genuinely need a moment, say nothing about it.
- Use the client's name once only — in the very first reply of a conversation. Never use it again after that, no matter how long the conversation goes.
- Keep replies short and WhatsApp-friendly. One point per message where possible.
- Warm, plain language. No corporate phrases: never say "we appreciate your patience", "thank you for reaching out", "is there anything else I can assist you with today?", "I hope this message finds you well", or similar.
- Give the useful answer first, then any context. Not the other way around.
- Be honest about uncertainty. If an ETA depends on fabric arrival, supplier, installer, or Stef's confirmation — say that clearly. Never promise a date that isn't confirmed in the job record.
- Use "we" for Certain Curtains. Use "Stef" when something needs her judgement or sign-off.
- One emoji is fine when it feels natural. Don't be bubbly or fake.
- Example of good tone: "Hi Terry 😊 Your living room curtains are still waiting on the Shernice Sand fabric — it's on backorder with the supplier. Once it arrives, we're about a week away from install. We'll send a confirmed date as soon as the fabric lands."

New / unknown clients (not found in CRM):
- First message: always ask what suburb they're in before answering any product questions. "To confirm we service your area, could you let me know what suburb you're in?"
- Non-Cape Town (other province, other country): politely explain we only service Cape Town and surrounds.
- Once you have their suburb, classify it using the area zones below (Green/Yellow/Red). Never mention these zone names to the client.

Area zones:
GREEN — we work there, proceed normally:
Brackenfell, Brackenfell South, Protea Heights, Sonkring, Vredekloof, Arauna, Peerless Park (if known/referral), Scottsdene (if known/referral); Durbanville, Vierlanden, Aurora, Uitzicht, Goedemoed, Amanda Glen, Eversdal, Sonstraal Heights; Welgemoed, Loevenstein, Oude Westhof, Kanonberg, Door de Kraal, Ridgeworth, Van Riebeeckshof, Kenridge; Haasendal, Zevenwacht, Amandelrug, Jagtershof, Rouxville, Mikro Park; Plattekloof, Panorama, Welgelegen, Burgundy Estate; Century City, Royal Ascot, Bloubergstrand, Sunningdale, West Beach, Big Bay; Constantia, Bishopscourt, Rondebosch, Bergvliet, Meadowridge, Tokai; Camps Bay, Clifton, Bantry Bay, Sea Point, Green Point, Fresnaye, Mouille Point, Tamboerskloof, Oranjezicht, Llandudno.
→ If Green: confirm we work there. If you don't yet know what they need, ask first ("Mainly curtains, blinds, or a mix?"). Once you know what they need, send the booking link: "You can book a free consultation here: https://certaincurtains.co.za/private/booking.html" — then add the quote tool as a casual footnote on a new line: "If you'd like a rough estimate in the meantime: https://certaincurtains.co.za/private/quote" — then create a lead task.

YELLOW — ask for more detail before deciding:
Brackenfell South, Protea Heights, Sonkring, Vredekloof (some pockets); Somerset West, Sitari, Croydon, De Wijnlanden, Stellenbosch, Paradyskloof; Bellville CBD, Bellville South, Parow, Parow Valley, Ravensmead; Goodwood, Vasco, Maitland, Kensington, Factreton, Brooklyn, Paarden Eiland (after hours); Cape Town CBD, Woodstock, Salt River, Observatory, Epping, Airport Industria, Montague Gardens industrial; Plumstead, Retreat, Steenberg, Grassy Park, Lotus River, Claremont, Newlands, Kenilworth, Wynberg; Milnerton, Table View, Parklands; Hout Bay, Noordhoek, Fish Hoek, Kommetjie, Simon's Town, Gordon's Bay, Strand, Paarl, Wellington, Franschhoek, Kalk Bay; Edgemead; vague address (just "Cape Town", "Northern Suburbs", location pin only).
Referral/client-known exception: any area where client is referred by an existing trusted client, designer, estate, or repeat client — treat as Green and note the referral.
→ If Yellow: don't categorise the area as a problem — instead ask for a more specific street or part of the suburb ("Whereabouts in Bellville?" / "Which part of Woodstock are you in?"). Many Yellow areas have good pockets. Once you have more detail, escalate to Stef with the specific location and lead info. Offer the quote tool while they wait: "In the meantime, here's our quote tool if you'd like a rough idea of pricing: https://certaincurtains.co.za/private/quote". Booking link only once Stef confirms.

RED — decline politely, do not take the lead:
Nyanga, Gugulethu, Manenberg, Hanover Park, Lavender Hill, Delft, Bishop Lavis, Bonteheuwel, Valhalla Park; Mitchells Plain, Philippi, Browns Farm, Samora Machel, Khayelitsha, Mfuleni, Langa, Crossroads; also: Blue Downs, Elfindale, Elsies River, Ottery, Pelikan Park, Eikendal.
→ If Red: keep it plain and simple — "Unfortunately we don't cover your area at the moment." Nothing more. Do not explain why, do not reference the type of area, do not escalate, do not create a task.

If suburb is not on any list: treat as Yellow and escalate.

For all leads — once area is confirmed:
- Gather: name, what they're looking for (curtains/blinds/type), suburb.
- Green: send the booking link (https://certaincurtains.co.za/private/booking.html) and create a task: "📱 WhatsApp lead: [name] – [what they want] – [suburb]"
- Yellow: say Stef will confirm availability and be in touch. Create the same task.
- Do NOT promise pricing, timelines, or availability. Do NOT create a client or job record — Stef qualifies leads before they enter the CRM.

Business context (for answering client questions):
- Service area: Cape Town and surrounds only. No other provinces.
- Timelines (from receipt of part payment): curtains only — 15 business days. Any order including blinds, or large orders — 21 business days. These are production timelines — add time for fabric delivery if stock is not on hand.
- Process: measure → quote → part payment → fabric ordered → production → install → final payment.
- Stef handles all measuring, quoting, and installs herself.
- Payment: EFT only. We do not accept cash or cheques under any circumstances. Bank details are provided with the invoice. If a client asks about payment methods, state EFT only — do not suggest or imply any other method is possible. If unsure whether a specific payment situation is covered, escalate to Stef rather than guessing.
- Business number: 0686484564
- Business email address: certaincurtainssa@gmail.com
- Never share any other contact details — not Stef's personal number, not any other number or email. If a client asks how to reach someone directly, give only the business number above.

Escalation:
- If a client asks something outside your scope (quality complaints, cancellations, design changes, pricing negotiations, anything needing Stef's judgement), you MUST call escalate_to_stef immediately — in the same response as your reply to the client. Do NOT just say "I'll check with Stef" without calling the tool. Saying it without doing it means Stef never finds out.
- Tell the client: "I'll check with Stef and get back to you." Then call escalate_to_stef with the full context.
- When Stef replies via Telegram with what to tell the client, call send_whatsapp_message with their phone number and her reply. Confirm back to Stef once sent.
- Log all WhatsApp exchanges with log_communication (type: "whatsapp") after handling.

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

IMPORTANT for update_client: confirm the new value back to Stef before calling, especially for email/phone — \
overwriting a client's contact details with a misread number means future emails/calls go to the wrong place \
and nobody notices until it's a problem.

IMPORTANT for payments: part_payment_amount is the EXPECTED deposit, calculated from the invoice — it is \
NOT what was actually paid. part_payment_received_amount and final_payment_received_amount are the ACTUAL \
amounts received, and can differ from the expected figure (client paid more/less, rounded, etc). When Stef \
shares a proof-of-payment screenshot: read the amount and date off it, confirm back to her ("Got it — R4500 \
received on 22 June — mark as part payment?"), then call update_job with the matching received boolean + \
amount + date together in one call. If the job is still status='quoting' when a part payment comes in, also \
set status='active' in the same call.

IMPORTANT for email/calendar tools:
- For compose_email: write professional, concise business emails. After calling compose_email, display \
the FULL draft clearly (To, Subject, Body) and tell Stef to open Gmail Drafts to add any attachments \
and send. NEVER offer to send programmatically — sending is always manual for now.
- For propose_calendar_event: display the full event details (title, date/time, location, notes) and \
say "Reply 'add it' to confirm." NEVER call confirm_calendar_event without explicit confirmation.
- For propose_calendar_event_update: call list_upcoming_events first if you don't already have the event_id. \
Display what's changing (before → after) and wait for explicit confirmation before calling confirm_calendar_event_update. \
Only pass the fields that are actually changing.
- For flag_event_for_deletion: Pip can never delete a calendar event outright. If an event looks cancelled, \
duplicated, or no longer needed, call flag_event_for_deletion to prefix its title with "[DELETE]" and tell \
Stef it's flagged — she removes it herself from Google Calendar. Still worth a quick confirmation in chat \
first ("flag the 3pm Tuesday slot for deletion?") since it's easy to mention the wrong event.
- Customer email addresses go in the event description/notes, NOT as attendees. Stef will add them \
manually once she's verified the event is correct.
- Events titled "BLOCKED — consult slot" (or any bare "Booked"/"Blocked" title with no client name) are \
placeholder blockers Stef uses to hide specific times from the public consult-booking page — they are \
NEVER tied to a specific client or job, even if mentioned in the same message as one. Don't try to match \
them to a client or ask whose appointment it is. They're low-stakes and freely movable/deletable — moving \
one just nudges which slot looks open to clients, it isn't rescheduling anything real. When asked to add a \
new one, always use the exact title "BLOCKED — consult slot".
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

Purchase order pipeline: draft → ordered → paid → received. Use update_po_status to progress POs. \
Always summarise what you're about to create/change before calling write tools. \
You never send emails — draft_supplier_order_email and compose_email both save Gmail drafts only. \
Stock levels are read-only; physical stock updates happen via the workshop scanner. \
job_windows holds curtain fabrics, linings, and rail details per room. job_blinds holds blind specs. \
When drafting client emails, use compose_email with full job context — no separate tool needed. \
When creating a PO and you need to know which supplier a fabric or product belongs to, call get_product \
with the fabric name — it returns the supplier. Do not ask Stef which supplier to use; look it up first. \
When building PO items for a job, call get_job_materials first — it returns all fabric quantities, \
rail codes, and blind specs already measured. Never ask Stef for quantities or codes you can look up. \
You have access to a shared scratchpad (Notes page): read_notes to read it, append_note to add a \
timestamped entry safely, write_notes to fully rewrite it. Use this to maintain the dev handoff doc, \
log bugs you spot, or keep running notes. Always read first before writing to avoid losing content.

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
        "description": "Update a job's production status, production checkboxes, payment received status/amount/date, notes, dates, delay note, or overall status. Requires job_id from get_client_jobs.",
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
                "part_payment_received": {"type": "boolean", "description": "Mark part/deposit payment received"},
                "part_payment_received_amount": {"type": "number", "description": "ACTUAL amount received for the part payment, e.g. from a proof of payment — may differ from the invoiced deposit"},
                "part_payment_date": {"type": "string", "description": "ISO 8601 date the part payment was received, e.g. 2026-06-20"},
                "final_payment_received": {"type": "boolean", "description": "Mark final payment received — also set status='complete' to close the job"},
                "final_payment_received_amount": {"type": "number", "description": "ACTUAL amount received for the final payment, e.g. from a proof of payment — may differ from the calculated balance"},
                "final_payment_date": {"type": "string", "description": "ISO 8601 date the final payment was received, e.g. 2026-06-20"},
                "delay_note": {"type": "string", "description": "Delay message shown to client on their order status page. Pass empty string \"\" to clear it. Example: 'Fabric on backorder, estimated delay 1 week'."},
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
        "name": "update_client",
        "description": "Update a client's name, phone, email, address, notes, or designer flag. Requires client_id from search_clients. Only pass the fields that are changing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "UUID of the client"},
                "name": {"type": "string", "description": "Full client name"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "address": {"type": "string", "description": "Physical address"},
                "notes": {"type": "string", "description": "Notes content (replaces existing)"},
                "is_designer": {"type": "boolean", "description": "Designer client — shows fabric qty on quotes"},
            },
            "required": ["client_id"],
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
        "description": (
            "List upcoming Google Calendar events to check schedule or availability before proposing a time. "
            "Each event includes its id — needed for propose_calendar_event_update and flag_event_for_deletion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 7, max 30)"},
            },
        },
    },
    {
        "name": "propose_calendar_event_update",
        "description": (
            "Propose a change to an existing Google Calendar event — time, title, location, or notes. "
            "Requires event_id from list_upcoming_events. Only include the fields that are actually changing. "
            "Returns a pending_id and a before/after preview. "
            "ALWAYS display the before/after and wait for explicit confirmation before calling confirm_calendar_event_update."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID from list_upcoming_events"},
                "title": {"type": "string", "description": "New title, if changing"},
                "start_datetime": {"type": "string", "description": "New start, ISO 8601 in SAST, if changing"},
                "end_datetime": {"type": "string", "description": "New end, ISO 8601 in SAST, if changing"},
                "description": {"type": "string", "description": "New notes, if changing"},
                "location": {"type": "string", "description": "New location, if changing"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "confirm_calendar_event_update",
        "description": (
            "Apply a previously proposed calendar event update. "
            "ONLY call after Stef has explicitly confirmed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pending_id": {"type": "string", "description": "Pending update ID from propose_calendar_event_update"},
            },
            "required": ["pending_id"],
        },
    },
    {
        "name": "flag_event_for_deletion",
        "description": (
            "Prefix a calendar event's title with '[DELETE]' to flag it for manual removal. "
            "Does NOT delete the event — Stef removes it herself from Google Calendar once flagged. "
            "Use when an event looks cancelled, duplicated, or no longer needed. Requires event_id from list_upcoming_events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID from list_upcoming_events"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "list_suppliers",
        "description": "List all active suppliers with contact details and account numbers.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_supplier",
        "description": "Get full supplier detail including the order email template and subject line.",
        "input_schema": {
            "type": "object",
            "properties": {"supplier_id": {"type": "string", "description": "Supplier UUID"}},
            "required": ["supplier_id"],
        },
    },
    {
        "name": "list_purchase_orders",
        "description": "List purchase orders, optionally filtered by status (draft/ordered/paid/received). Includes supplier name and linked job refs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["draft", "ordered", "paid", "received"], "description": "Filter by status. Omit for all."},
            },
        },
    },
    {
        "name": "get_purchase_order",
        "description": "Get full PO detail with all line items, supplier info, and linked jobs.",
        "input_schema": {
            "type": "object",
            "properties": {"po_id": {"type": "string", "description": "Purchase order UUID"}},
            "required": ["po_id"],
        },
    },
    {
        "name": "get_job_materials",
        "description": "Get all material requirements for a specific job — fabrics, linings, rails (from job_windows) and blinds (from job_blinds) with quantities and codes already filled in. Use this when building PO items for a job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Job UUID or quote_ref (e.g. 'CC2536') — both accepted"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_ordering_summary",
        "description": "Full picture of every active job — what fabrics, linings, rails, and blinds are needed per job, plus whether a PO already exists. Use to decide what needs ordering.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_jobs_needing_orders",
        "description": "Active jobs with no purchase order placed yet — i.e. materials haven't been ordered.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_jobs_awaiting_stock",
        "description": "Jobs with POs in 'ordered' status — materials on the way but not yet received.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_overdue_jobs",
        "description": "Active jobs that are past their target_date or required_date.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_unpaid_jobs",
        "description": (
            "Jobs with an invoice number but final payment not yet received. "
            "Each job includes days_since_invoiced and job_complete_awaiting_payment (true when "
            "production_status is 'installed' — the work is done and only payment is outstanding, the most urgent case)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_supplier_order_email",
        "description": "Draft a purchase order email to a supplier using their stored template, populated with PO line items. Saves to Gmail drafts — never sends.",
        "input_schema": {
            "type": "object",
            "properties": {"po_id": {"type": "string", "description": "Purchase order UUID to email"}},
            "required": ["po_id"],
        },
    },
    {
        "name": "create_purchase_order",
        "description": "Create a new draft PO linked to one or more jobs. Always confirm supplier and job list with Stef before calling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_id": {"type": "string", "description": "Supplier UUID"},
                "job_ids": {"type": "array", "items": {"type": "string"}, "description": "List of job UUIDs"},
                "notes": {"type": "string", "description": "Optional internal note"},
            },
            "required": ["supplier_id", "job_ids"],
        },
    },
    {
        "name": "add_po_items",
        "description": "Add line items to a draft PO.",
        "input_schema": {
            "type": "object",
            "properties": {
                "po_id": {"type": "string", "description": "Purchase order UUID"},
                "items": {
                    "type": "array",
                    "description": "List of items to add",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "description": {"type": "string"},
                            "qty": {"type": "string", "description": "e.g. '8.5m' or '2'"},
                            "unit_price": {"type": "number"},
                            "job_id": {"type": "string", "description": "Optional — which job this item is for"},
                        },
                        "required": ["description", "qty"],
                    },
                },
            },
            "required": ["po_id", "items"],
        },
    },
    {
        "name": "update_po_status",
        "description": "Progress a PO through the pipeline: ordered → paid → received.",
        "input_schema": {
            "type": "object",
            "properties": {
                "po_id": {"type": "string", "description": "Purchase order UUID"},
                "status": {"type": "string", "enum": ["ordered", "paid", "received"]},
                "date": {"type": "string", "description": "ISO date (YYYY-MM-DD). Defaults to today."},
            },
            "required": ["po_id", "status"],
        },
    },
    {
        "name": "log_po_issue",
        "description": "Record a delay, problem, or note against a PO. Prepended with today's date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "po_id": {"type": "string", "description": "Purchase order UUID"},
                "issue_text": {"type": "string", "description": "Description of the delay or issue"},
            },
            "required": ["po_id", "issue_text"],
        },
    },
    {
        "name": "delete_purchase_order",
        "description": (
            "Permanently delete a draft purchase order and its line items — for test or duplicate POs only. "
            "Only works on status='draft'. Refuses if the PO has been ordered, paid, or received — "
            "Stef removes those herself in the CRM if she's sure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "po_id": {"type": "string", "description": "Purchase order UUID"},
            },
            "required": ["po_id"],
        },
    },
    {
        "name": "get_stock_levels",
        "description": "Check current stock quantities for all active products. Filter to low stock or a specific category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "low_only": {"type": "boolean", "description": "If true, only return items with stock_qty ≤ 2"},
                "category": {"type": "string", "description": "Optional: filter by product category"},
            },
        },
    },
    {
        "name": "get_product",
        "description": "Look up a specific product by code or partial name.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Product code or partial name"}},
            "required": ["query"],
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
        "name": "draft_client_update_email",
        "description": (
            "Draft a client-facing email and save it as a Gmail draft for Stef to review. "
            "Never sends — Stef sends manually. Use for quote follow-ups, payment requests, "
            "production updates, delay notices, install confirmations, and final payment requests."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "UUID of the job"},
                "message_type": {
                    "type": "string",
                    "enum": ["quote_follow_up", "payment_request", "production_update", "delay_notice", "install_confirmation", "final_payment_request"],
                    "description": "Type of email to draft",
                },
                "extra_context": {"type": "string", "description": "Optional extra detail to weave in, e.g. 'Hertex fabric backordered 2 weeks'"},
            },
            "required": ["job_id", "message_type"],
        },
    },
    {
        "name": "read_notes",
        "description": "Read the full content of the shared business scratchpad (the Notes page in StefHQ). Use this to check the current dev handoff doc, running notes, or anything Stef has written there.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "append_note",
        "description": "Append a timestamped entry to the business scratchpad. Safe — prepends without overwriting existing content. Use for quick status updates, bug reports, or adding a line to the dev handoff doc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to append (will be prefixed with timestamp automatically)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "write_notes",
        "description": "Overwrite the entire business scratchpad with new content. Use when restructuring or fully updating the dev handoff doc. Read the current content first so nothing is lost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Full new content for the scratchpad (replaces everything)"},
            },
            "required": ["content"],
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
    {
        "name": "pause_briefing",
        "description": "Pause Pip's morning business briefing until a specified date (inclusive). Use when Stef is on leave or away. Briefings resume automatically the next working day after the pause date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "until_date": {"type": "string", "description": "ISO 8601 date (YYYY-MM-DD) — last day to skip. E.g. '2026-07-04' pauses through that Friday and resumes Monday."},
            },
            "required": ["until_date"],
        },
    },
    {
        "name": "resume_briefing",
        "description": "Immediately cancel a briefing pause and resume Pip's morning briefings from tomorrow.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_email_noise_filter",
        "description": "Add a pattern to the midday email digest's noise list — matching emails won't be flagged unless something about them looks unusual. Use when Stef says to ignore a recurring sender or topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "What to ignore, e.g. 'QuickBooks subscription renewal' or 'noreply@mailchimp.com'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "remove_email_noise_filter",
        "description": "Remove a pattern from the email digest noise list. Use list_email_noise_filters to get the exact text first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Exact pattern to remove"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_email_noise_filters",
        "description": "List all current noise patterns for the midday email digest.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "send_whatsapp_message",
        "description": "Send a WhatsApp text message to a client's phone number. Use for follow-ups, confirmations, or relaying Stef's reply to a client escalation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_phone": {"type": "string", "description": "Client's phone number in international format, e.g. +27821234567"},
                "message": {"type": "string", "description": "Plain text message to send — no markdown formatting"},
            },
            "required": ["to_phone", "message"],
        },
    },
    {
        "name": "escalate_to_stef",
        "description": "Forward an inbound WhatsApp message to Stef on Telegram when it's outside Pip's scope. Sends a notification with full context so Stef can reply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string", "description": "Client's name (or 'Unknown' if not in CRM)"},
                "client_phone": {"type": "string", "description": "Client's phone number"},
                "their_message": {"type": "string", "description": "What the client said"},
                "context": {"type": "string", "description": "Relevant job/CRM context Stef should know (status, outstanding balance, last communication, etc.)"},
            },
            "required": ["client_name", "client_phone", "their_message", "context"],
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
            amount = job.get("part_payment_received_amount") or job.get("part_payment_amount")
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
                    "select": "id,quote_ref,status,production_status,fabrics_received,rails_received,blinds_received,sewing_complete,delay_note,install_date,invoice_total,invoice_date,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,part_payment_received_amount,part_payment_date,final_payment_received,final_payment_received_amount,final_payment_date,quote_accepted_by,notes,communications,created_at",
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
                    "select": "id,quote_ref,client_name,production_status,install_date,invoice_total,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,part_payment_received_amount,final_payment_received,final_payment_received_amount,quote_accepted_by,required_date,notes",
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
        date_fields = {"install_date", "required_date", "part_payment_date", "final_payment_date"}
        bool_fields = {
            "fabrics_received", "rails_received", "blinds_received", "sewing_complete",
            "part_payment_received", "final_payment_received",
        }
        amount_fields = {"part_payment_received_amount", "final_payment_received_amount"}
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
            elif key in amount_fields:
                try:
                    payload[key] = float(val)
                except (TypeError, ValueError):
                    return f"Invalid amount for {key}: '{val}' — use a number, e.g. 4500.00."
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

    async def _update_client(self, client_id: str, **kwargs) -> str:
        payload = {k: v for k, v in kwargs.items() if v is not None}
        if not payload:
            return "Nothing to update — no fields provided."
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{self._base()}/clients",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{client_id}"},
                json=payload,
            )
            resp.raise_for_status()
        updated = ", ".join(f"{k}={v}" for k, v in payload.items())
        return f"Client updated: {updated}"

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

    async def _propose_calendar_event_update(
        self,
        user_id: uuid.UUID,
        session: AsyncSession,
        event_id: str,
        title: str | None = None,
        start_datetime: str | None = None,
        end_datetime: str | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> str:
        changes = {
            "title": title,
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
            "description": description,
            "location": location,
        }
        if not any(v is not None for v in changes.values()):
            return "No changes provided — specify at least one field to update."
        access_token = await get_access_token(user_id, session)
        current = await calendar_get_event(access_token, event_id)
        pending_id = await store_pending_event_update({"event_id": event_id, **changes})
        lines = [f"pending_id: {pending_id}", "", f"**Event:** {current['title']}"]
        if title is not None and title != current["title"]:
            lines.append(f"**Title:** {current['title']} → {title}")
        if start_datetime is not None and start_datetime != current["start"]:
            lines.append(f"**Start:** {current['start']} → {start_datetime}")
        if end_datetime is not None and end_datetime != current["end"]:
            lines.append(f"**End:** {current['end']} → {end_datetime}")
        if location is not None and location != current["location"]:
            lines.append(f"**Location:** {current['location'] or '(none)'} → {location}")
        if description is not None and description != current["description"]:
            lines.append(f"**Notes:** {current['description'] or '(none)'} → {description}")
        return "\n".join(lines)

    async def _confirm_calendar_event_update(
        self, user_id: uuid.UUID, session: AsyncSession, pending_id: str
    ) -> str:
        details = await pop_pending_event_update(pending_id)
        if details is None:
            return "Pending update not found — it may have expired (24h limit). Please propose it again."
        access_token = await get_access_token(user_id, session)
        result = await calendar_update_event(
            access_token,
            event_id=details["event_id"],
            title=details.get("title"),
            start_datetime=details.get("start_datetime"),
            end_datetime=details.get("end_datetime"),
            description=details.get("description"),
            location=details.get("location"),
        )
        link = result.get("html_link", "")
        return f"Event updated.\n{link}"

    _DELETE_FLAG = "[DELETE] "

    async def _flag_event_for_deletion(
        self, user_id: uuid.UUID, session: AsyncSession, event_id: str
    ) -> str:
        access_token = await get_access_token(user_id, session)
        current = await calendar_get_event(access_token, event_id)
        title = current["title"]
        if title.startswith(self._DELETE_FLAG):
            return f"Already flagged: {title}"
        new_title = f"{self._DELETE_FLAG}{title}"
        result = await calendar_update_event(access_token, event_id, title=new_title)
        link = result.get("html_link", "")
        return f"Flagged for deletion: {new_title}\n{link}\n\nRemove it from Google Calendar whenever you're ready."

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

    async def _pause_briefing(self, until_date: str) -> str:
        from datetime import date
        from services.briefing_settings import set_pause_until
        try:
            date.fromisoformat(until_date)
        except ValueError:
            return f"Invalid date '{until_date}' — use YYYY-MM-DD."
        await set_pause_until(until_date)
        return f"Briefings paused until {until_date} (inclusive). They'll resume automatically after that."

    async def _resume_briefing(self) -> str:
        from services.briefing_settings import clear_pause
        await clear_pause()
        return "Briefing pause cleared — morning briefings will resume from tomorrow."

    async def _add_email_noise_filter(self, pattern: str) -> str:
        from services.email_noise import add_noise_pattern
        await add_noise_pattern(pattern)
        return f"Added to noise list: {pattern}"

    async def _remove_email_noise_filter(self, pattern: str) -> str:
        from services.email_noise import remove_noise_pattern
        removed = await remove_noise_pattern(pattern)
        return f"Removed from noise list: {pattern}" if removed else f"'{pattern}' wasn't on the noise list — check list_email_noise_filters for the exact text."

    async def _list_email_noise_filters(self) -> str:
        from services.email_noise import list_noise_patterns
        patterns = await list_noise_patterns()
        if not patterns:
            return "No noise filters configured yet."
        return "\n".join(f"- {p}" for p in patterns)

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

    async def _list_suppliers(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/suppliers", headers=self._headers(),
                params={"or": "(active.is.true,active.is.null)", "select": "id,name,supplier_type,account_number,email,order_format,pricelist_added", "order": "name.asc"},
            )
            r.raise_for_status()
            results = r.json()
        return json.dumps(results) if results else "No suppliers found."

    async def _get_supplier(self, supplier_id: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/suppliers", headers=self._headers(),
                params={"id": f"eq.{supplier_id}", "select": "*"},
            )
            r.raise_for_status()
            rows = r.json()
        return json.dumps(rows[0]) if rows else f"Supplier {supplier_id} not found."

    async def _list_purchase_orders(self, status: str | None = None) -> str:
        params: dict = {
            "select": "id,status,ordered_at,paid_at,received_at,job_ids,created_at,notes,supplier_id",
            "order": "created_at.desc",
            "limit": "50",
        }
        if status:
            params["status"] = f"eq.{status}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self._base()}/purchase_orders", headers=self._headers(), params=params)
            r.raise_for_status()
            pos = r.json()
            if not pos:
                return "No purchase orders found."
            supplier_ids = list({p["supplier_id"] for p in pos if p.get("supplier_id")})
            supplier_map: dict = {}
            if supplier_ids:
                sr = await client.get(
                    f"{self._base()}/suppliers", headers=self._headers(),
                    params={"id": f"in.({','.join(supplier_ids)})", "select": "id,name"},
                )
                sr.raise_for_status()
                supplier_map = {s["id"]: s["name"] for s in sr.json()}
            all_job_ids = list({jid for p in pos for jid in (p.get("job_ids") or [])})
            job_map: dict = {}
            if all_job_ids:
                jr = await client.get(
                    f"{self._base()}/jobs", headers=self._headers(),
                    params={"id": f"in.({','.join(all_job_ids)})", "select": "id,quote_ref,client_name"},
                )
                jr.raise_for_status()
                job_map = {j["id"]: j for j in jr.json()}
        for p in pos:
            p["supplier_name"] = supplier_map.get(p["supplier_id"], "Unknown")
            p["job_refs"] = [
                {"quote_ref": job_map[jid].get("quote_ref"), "client_name": job_map[jid]["client_name"]}
                for jid in (p.get("job_ids") or []) if jid in job_map
            ]
        return json.dumps(pos)

    async def _get_purchase_order(self, po_id: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/purchase_orders", headers=self._headers(),
                params={"id": f"eq.{po_id}", "select": "id,supplier_id,job_ids,status,ordered_at,paid_at,received_at,notes,created_at"},
            )
            if not r.is_success:
                return json.dumps({"error": f"purchase_orders query failed {r.status_code}", "body": r.text, "url": str(r.url)})
            rows = r.json()
            if not rows:
                return f"Purchase order {po_id} not found."
            po = rows[0]
            ri = await client.get(
                f"{self._base()}/purchase_order_items", headers=self._headers(),
                params={"po_id": f"eq.{po_id}", "select": "*"},
            )
            po["items"] = ri.json() if ri.is_success else []
            rs = await client.get(
                f"{self._base()}/suppliers", headers=self._headers(),
                params={"id": f"eq.{po['supplier_id']}", "select": "id,name,email,account_number,subject_line,template,order_format"},
            )
            rs.raise_for_status()
            supplier_rows = rs.json()
            po["supplier"] = supplier_rows[0] if supplier_rows else {}
            if po.get("job_ids"):
                rj = await client.get(
                    f"{self._base()}/jobs", headers=self._headers(),
                    params={"id": f"in.({','.join(po['job_ids'])})", "select": "id,quote_ref,client_name,production_status"},
                )
                rj.raise_for_status()
                po["jobs"] = rj.json()
        return json.dumps(po)

    async def _get_job_materials(self, job_id: str) -> str:
        import re
        # If not a UUID, treat as quote_ref and look up the real ID first
        if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', job_id, re.IGNORECASE):
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self._base()}/jobs", headers=self._headers(),
                    params={"quote_ref": f"ilike.{job_id}", "select": "id", "limit": "1"},
                )
                r.raise_for_status()
                rows = r.json()
            if not rows:
                return f"No job found with quote_ref '{job_id}'."
            job_id = rows[0]["id"]
        async with httpx.AsyncClient(timeout=10) as client:
            rw = await client.get(
                f"{self._base()}/job_windows", headers=self._headers(),
                params={"job_id": f"eq.{job_id}", "select": "*", "order": "room_label.asc"},
            )
            rw.raise_for_status()
            rb = await client.get(
                f"{self._base()}/job_blinds", headers=self._headers(),
                params={"job_id": f"eq.{job_id}", "select": "*", "order": "room_label.asc"},
            )
            rb.raise_for_status()
            # Debug: if empty, fetch a sample row to expose actual field names
            debug_info = None
            if not rw.json() and not rb.json():
                rsample = await client.get(
                    f"{self._base()}/job_windows", headers=self._headers(),
                    params={"select": "*", "limit": "1"},
                )
                if rsample.is_success and rsample.json():
                    debug_info = {"sample_row_keys": list(rsample.json()[0].keys()), "queried_job_id": job_id}
        windows = rw.json()
        blinds = rb.json()
        if not windows and not blinds:
            msg = "No material specs found for this job — job_windows and job_blinds returned empty."
            if debug_info:
                msg += f" Debug: sample row fields are {debug_info['sample_row_keys']}. Queried job_id={debug_info['queried_job_id']}."
            return msg
        return json.dumps({"windows": windows, "blinds": blinds})

    async def _get_ordering_summary(self) -> str:
        async with httpx.AsyncClient(timeout=15) as client:
            rj = await client.get(
                f"{self._base()}/jobs", headers=self._headers(),
                params={"status": "eq.active", "select": "id,client_name,quote_ref,production_status,target_date,install_date,required_date", "order": "install_date.asc.nullslast", "limit": "30"},
            )
            rj.raise_for_status()
            jobs = rj.json()
            if not jobs:
                return "No active jobs."
            ids_str = f"({','.join(j['id'] for j in jobs)})"
            rw = await client.get(
                f"{self._base()}/job_windows", headers=self._headers(),
                params={"job_id": f"in.{ids_str}", "select": "*"},
            )
            rw.raise_for_status()
            rb = await client.get(
                f"{self._base()}/job_blinds", headers=self._headers(),
                params={"job_id": f"in.{ids_str}", "select": "*"},
            )
            rb.raise_for_status()
            rp = await client.get(
                f"{self._base()}/purchase_orders", headers=self._headers(),
                params={"status": "neq.received", "select": "id,job_ids,status,ordered_at,supplier_id"},
            )
            rp.raise_for_status()
            pos = rp.json()
        windows_by_job: dict = {}
        for w in rw.json():
            windows_by_job.setdefault(w["job_id"], []).append(w)
        blinds_by_job: dict = {}
        for b in rb.json():
            blinds_by_job.setdefault(b["job_id"], []).append(b)
        job_po_map: dict = {}
        for po in pos:
            for jid in (po.get("job_ids") or []):
                job_po_map.setdefault(jid, []).append({"po_id": po["id"], "status": po["status"], "ordered_at": po.get("ordered_at"), "supplier_id": po.get("supplier_id")})
        return json.dumps([
            {
                "job_id": j["id"],
                "client_name": j["client_name"],
                "quote_ref": j.get("quote_ref"),
                "production_status": j.get("production_status"),
                "target_date": j.get("target_date"),
                "install_date": j.get("install_date"),
                "windows": windows_by_job.get(j["id"], []),
                "blinds": blinds_by_job.get(j["id"], []),
                "purchase_orders": job_po_map.get(j["id"], []),
            }
            for j in jobs
        ])

    async def _get_jobs_needing_orders(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            rj = await client.get(
                f"{self._base()}/jobs", headers=self._headers(),
                params={"status": "eq.active", "select": "id,client_name,quote_ref,production_status,target_date,install_date", "order": "install_date.asc.nullslast", "limit": "50"},
            )
            rj.raise_for_status()
            jobs = rj.json()
            if not jobs:
                return "No active jobs."
            rp = await client.get(
                f"{self._base()}/purchase_orders", headers=self._headers(),
                params={"status": "neq.received", "select": "job_ids,status"},
            )
            rp.raise_for_status()
            pos = rp.json()
        ordered_ids = {jid for po in pos for jid in (po.get("job_ids") or [])}
        needs_order = [j for j in jobs if j["id"] not in ordered_ids]
        return json.dumps(needs_order) if needs_order else "All active jobs have purchase orders placed."

    async def _get_jobs_awaiting_stock(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            rp = await client.get(
                f"{self._base()}/purchase_orders", headers=self._headers(),
                params={"status": "eq.ordered", "select": "id,job_ids,ordered_at,notes,supplier_id"},
            )
            rp.raise_for_status()
            pos = rp.json()
            if not pos:
                return "No jobs awaiting stock."
            supplier_ids = list({p["supplier_id"] for p in pos if p.get("supplier_id")})
            supplier_map: dict = {}
            if supplier_ids:
                rs = await client.get(
                    f"{self._base()}/suppliers", headers=self._headers(),
                    params={"id": f"in.({','.join(supplier_ids)})", "select": "id,name"},
                )
                rs.raise_for_status()
                supplier_map = {s["id"]: s["name"] for s in rs.json()}
            all_job_ids = list({jid for p in pos for jid in (p.get("job_ids") or [])})
            job_map: dict = {}
            if all_job_ids:
                rj = await client.get(
                    f"{self._base()}/jobs", headers=self._headers(),
                    params={"id": f"in.({','.join(all_job_ids)})", "select": "id,quote_ref,client_name,production_status,install_date"},
                )
                rj.raise_for_status()
                job_map = {j["id"]: j for j in rj.json()}
        results = []
        for po in pos:
            results.append({
                "po_id": po["id"],
                "supplier_name": supplier_map.get(po["supplier_id"], "Unknown"),
                "ordered_at": po.get("ordered_at"),
                "notes": po.get("notes"),
                "jobs": [job_map[jid] for jid in (po.get("job_ids") or []) if jid in job_map],
            })
        return json.dumps(results)

    async def _get_overdue_jobs(self) -> str:
        from datetime import date
        today = date.today().isoformat()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/jobs", headers=self._headers(),
                params={
                    "status": "eq.active",
                    "select": "id,client_name,quote_ref,target_date,required_date,install_date,production_status",
                    "or": f"(target_date.lt.{today},required_date.lt.{today})",
                    "order": "target_date.asc.nullslast",
                    "limit": "50",
                },
            )
            r.raise_for_status()
            jobs = r.json()
        if not jobs:
            return "No overdue jobs."
        from datetime import date as date_type
        today_dt = date_type.today()
        for j in jobs:
            overdue_days = None
            for field in ("target_date", "required_date"):
                if j.get(field):
                    try:
                        d = date_type.fromisoformat(j[field])
                        diff = (today_dt - d).days
                        if diff > 0 and (overdue_days is None or diff < overdue_days):
                            overdue_days = diff
                    except ValueError:
                        pass
            j["days_overdue"] = overdue_days
        return json.dumps(jobs)

    async def _list_unpaid_jobs(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/jobs", headers=self._headers(),
                params={
                    "status": "neq.archived",
                    "invoice_number": "not.is.null",
                    "or": "(final_payment_received.is.false,final_payment_received.is.null)",
                    "select": "id,client_name,quote_ref,production_status,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,part_payment_received_amount,final_payment_received,final_payment_received_amount,status",
                    "order": "invoice_sent_at.asc.nullslast",
                    "limit": "50",
                },
            )
            r.raise_for_status()
            jobs = r.json()
        if not jobs:
            return "No unpaid invoices."
        from datetime import date as date_type
        today_dt = date_type.today()
        for j in jobs:
            j["payment_status"] = self._payment_status(j)
            sent = j.get("invoice_sent_at")
            j["days_since_invoiced"] = None
            if sent:
                try:
                    j["days_since_invoiced"] = (today_dt - date_type.fromisoformat(sent[:10])).days
                except ValueError:
                    pass
            j["job_complete_awaiting_payment"] = j.get("production_status") == "installed"
        return json.dumps(jobs)

    async def _draft_supplier_order_email(
        self, user_id: uuid.UUID, session: AsyncSession, po_id: str
    ) -> str:
        po_json = await self._get_purchase_order(po_id)
        if "not found" in po_json:
            return po_json
        po = json.loads(po_json)
        supplier = po.get("supplier", {})
        items = po.get("items", [])
        jobs = po.get("jobs", [])
        to_email = supplier.get("email", "")
        if not to_email:
            return f"Cannot draft: supplier '{supplier.get('name', po_id)}' has no email on record."
        job_refs_str = ", ".join(j.get("quote_ref") or j.get("client_name", "") for j in jobs)
        from datetime import date
        today = date.today().strftime("%d %B %Y")
        item_lines = ["Code | Description | Qty | Unit Price", "-" * 52]
        for item in items:
            price = f"R{float(item.get('unit_price', 0)):.2f}" if item.get("unit_price") is not None else "—"
            item_lines.append(f"{item.get('code', '—')} | {item.get('description', '—')} | {item.get('qty', '—')} | {price}")
        item_table = "\n".join(item_lines)
        template = supplier.get("template") or ""
        account = supplier.get("account_number") or ""
        def _fill(text: str) -> str:
            return (text
                .replace("{items}", item_table).replace("{job_ref}", job_refs_str)
                .replace("{account_number}", account).replace("{date}", today)
                .replace("[reference]", job_refs_str).replace("[date]", today)
                .replace("[account]", account).replace("[account_number]", account))
        if template:
            body = _fill(template)
            if item_table not in body:
                body = f"{body}\n\n{item_table}"
        else:
            body = (
                f"Dear {supplier.get('name', 'Sir/Madam')},\n\n"
                f"Please find our order details below.\n\n"
                f"Job Reference(s): {job_refs_str}\nDate: {today}\nAccount: {account}\n\n"
                f"{item_table}\n\nKind regards,\nStef\nCertain Curtains"
            )
        subject = _fill(supplier.get("subject_line") or f"Order — {job_refs_str} — {today}")
        result = await self._compose_email(user_id, session, to_email=to_email, subject=subject, body=body, to_name=supplier.get("name"))
        return f"Supplier order email drafted.\n\n{result}"

    _CRM_BASE = "https://certaincurtainscrm.netlify.app/"

    async def _draft_client_update_email(
        self, user_id: uuid.UUID, session: AsyncSession, job_id: str, message_type: str, extra_context: str | None = None
    ) -> str:
        from datetime import date
        async with httpx.AsyncClient(timeout=10) as client:
            rj = await client.get(
                f"{self._base()}/jobs", headers=self._headers(),
                params={"id": f"eq.{job_id}", "select": "id,client_id,client_name,quote_ref,status,production_status,install_date,required_date,invoice_total,invoice_number,invoice_sent_at,part_payment_received,part_payment_amount,part_payment_received_amount,final_payment_received,final_payment_received_amount,quote_accepted_by,notes,delay_note,share_token,quote_expires_at", "limit": "1"},
            )
            rj.raise_for_status()
            rows = rj.json()
            if not rows:
                return f"Job {job_id} not found."
            job = rows[0]
            # Fetch client email
            client_email = ""
            client_id = job.get("client_id")
            if client_id:
                rc = await client.get(
                    f"{self._base()}/clients", headers=self._headers(),
                    params={"id": f"eq.{client_id}", "select": "id,name,email,phone", "limit": "1"},
                )
                rc.raise_for_status()
                client_rows = rc.json()
                if client_rows:
                    client_email = client_rows[0].get("email") or ""

        if not client_email:
            return f"Cannot draft: no email address on file for {job.get('client_name', job_id)}. Add one in the CRM first."

        name = job.get("client_name") or ""
        first_name = name.split()[0] if name else "there"
        quote_ref = job.get("quote_ref") or ""
        install_date = job.get("install_date")
        invoice_total = job.get("invoice_total")
        part_amount = job.get("part_payment_amount")
        part_received_amount = job.get("part_payment_received_amount")
        production_status = (job.get("production_status") or "in progress").replace("_", " ")
        delay_reason = extra_context or job.get("delay_note") or ""

        # Bank details
        bank_block = "FNB — Certain Curtains\nAcc: 62 635 693 189\nBranch: 250955"

        # Quote/invoice URL — same endpoint, adapts to job state
        share_token = job.get("share_token")
        shared_url = f"{self._CRM_BASE}quote-view.html?token={share_token}" if share_token else None
        track_url = f"https://certaincurtains.co.za/private/track.html?token={share_token}" if share_token else None
        no_link_note = "(Tip: click Share Quote in the CRM to generate a shareable link for this job.)"

        def fmt_amount(amount) -> str:
            try:
                return f"R {float(amount):,.0f}"
            except (TypeError, ValueError):
                return str(amount) if amount else "the outstanding amount"

        total_str = fmt_amount(invoice_total)
        try:
            part_str = fmt_amount(part_amount or (float(invoice_total) * 0.8 if invoice_total else None))
        except (TypeError, ValueError):
            part_str = "the deposit amount"
        if job.get("part_payment_received") and part_received_amount is not None and invoice_total is not None:
            try:
                balance_str = fmt_amount(float(invoice_total) - float(part_received_amount))
            except (TypeError, ValueError):
                balance_str = total_str
        else:
            balance_str = total_str
        install_str = install_date or "to be confirmed"
        extra = extra_context or ""

        templates = {
            "quote_follow_up": (
                f"Following up on your quote — {quote_ref}",
                (
                    f"Hi {first_name},\n\n"
                    f"I just wanted to follow up on the quote we sent through for your curtains and blinds.\n\n"
                    f"Please let me know if you have any questions, or if there's anything you'd like me to adjust. "
                    f"I'm happy to chat through the options with you.\n"
                    + (f"\nYour quote is here if you need it:\n{shared_url}\n" if shared_url else f"\n{no_link_note}\n")
                    + (f"\n{extra}\n" if extra else "")
                    + f"\nWarm regards,\n"
                    f"Stef\n"
                    f"Certain Curtains"
                )
            ),
            "payment_request": (
                f"Your quote — {quote_ref}",
                (
                    f"Hi {first_name},\n\n"
                    f"Please find your quote for your curtains and blinds here:\n\n"
                    + (f"{shared_url}\n\n" if shared_url else f"{no_link_note}\n\n")
                    + f"When you're ready to go ahead, you can accept the quote online. "
                    f"Once accepted, you'll be redirected to the invoice with the part-payment details.\n\n"
                    f"We ask for an 80% part-payment of {part_str} before getting your order into production.\n\n"
                    f"Please feel free to let me know if you have any questions, or if there's anything you'd like me to adjust before accepting."
                    + (f"\n\n{extra}" if extra else "")
                    + f"\n\nWarm regards,\n"
                    f"Stef\n"
                    f"Certain Curtains"
                )
            ),
            "production_update": (
                f"Your order is active — {quote_ref}",
                (
                    f"Hi {first_name},\n\n"
                    f"Your part-payment has been received, and your order is now active.\n\n"
                    + (f"You can track your order here at any time:\n\n{track_url}\n\n" if track_url else f"{no_link_note}\n\n")
                    + f"We'll be in touch once we have a confirmed installation date."
                    + (f"\n\n{extra}" if extra else "")
                    + f"\n\nWarm regards,\n"
                    f"Stef\n"
                    f"Certain Curtains"
                )
            ),
            "delay_notice": (
                f"Update regarding your order — {quote_ref}",
                (
                    f"Hi {first_name},\n\n"
                    f"I wanted to give you a quick update on your curtain order.\n\n"
                    f"Unfortunately, we've run into a slight delay, so your order is taking a little longer than expected. "
                    f"I'm really sorry for the inconvenience.\n\n"
                    + (f"This is due to {delay_reason}.\n\n" if delay_reason else "")
                    + f"We're doing everything we can to complete it as soon as possible, and I'll keep you updated on our progress.\n\n"
                    f"Thank you so much for your patience and understanding.\n\n"
                    f"Warm regards,\n"
                    f"Stef\n"
                    f"Certain Curtains"
                )
            ),
            "install_confirmation": (
                f"Your installation is confirmed — {quote_ref}",
                (
                    f"Hi {first_name},\n\n"
                    f"Good news — your curtains are ready.\n\n"
                    f"We've confirmed your installation for {install_str}.\n\n"
                    f"Please read through the installation information before we arrive so everything can go smoothly on the day:\n\n"
                    f"http://certaincurtains.co.za/private/install-info.html\n\n"
                    f"Please make sure someone will be home to give us access. "
                    f"If anything changes before then, please let me know as soon as possible so we can reschedule."
                    + (f"\n\n{extra}" if extra else "")
                    + f"\n\nWarm regards,\n"
                    f"Stef\n"
                    f"Certain Curtains"
                )
            ),
            "final_payment_request": (
                f"Final balance due — {quote_ref}",
                (
                    f"Hi {first_name},\n\n"
                    f"Thank you — your installation is now complete.\n\n"
                    f"The final balance of {balance_str} is now due. Please transfer to:\n\n"
                    f"{bank_block}\n\n"
                    f"Please use your invoice number as reference and send proof of payment via WhatsApp or email once done."
                    + (f"\n\nYou can view your invoice here:\n{shared_url}" if shared_url else f"\n\n{no_link_note}")
                    + (f"\n\n{extra}" if extra else "")
                    + f"\n\nThank you again for choosing Certain Curtains. I hope you enjoy your new curtains.\n\n"
                    f"Warm regards,\n"
                    f"Stef\n"
                    f"Certain Curtains"
                )
            ),
        }

        if message_type not in templates:
            return f"Unknown message_type '{message_type}'."

        subject, body = templates[message_type]
        result = await self._compose_email(user_id, session, to_email=client_email, subject=subject, body=body, to_name=name)
        return f"Client email drafted ({message_type}) to {client_email}.\n\n{result}"

    async def _create_purchase_order(
        self, supplier_id: str, job_ids: list, notes: str | None = None
    ) -> str:
        payload: dict = {"supplier_id": supplier_id, "job_ids": job_ids, "status": "draft"}
        if notes:
            payload["notes"] = notes
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self._base()}/purchase_orders",
                headers={**self._headers(), "Prefer": "return=representation"},
                json=payload,
            )
            r.raise_for_status()
            created = r.json()
        record = created[0] if isinstance(created, list) else created
        return json.dumps({"id": record["id"], "status": "draft", "supplier_id": supplier_id, "job_ids": job_ids})

    async def _add_po_items(self, po_id: str, items: list) -> str:
        payload = [
            {
                "po_id": po_id,
                "code": item.get("code"),
                "description": item.get("description"),
                "qty": item.get("qty"),
                "unit_price": item.get("unit_price"),
                "job_id": item.get("job_id"),
                "sort_order": i,
            }
            for i, item in enumerate(items)
        ]
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self._base()}/purchase_order_items",
                headers={**self._headers(), "Prefer": "return=minimal"},
                json=payload,
            )
            r.raise_for_status()
        return f"{len(items)} item(s) added to PO."

    async def _update_po_status(
        self, po_id: str, status: str, date: str | None = None
    ) -> str:
        from datetime import date as date_type
        today = date or date_type.today().isoformat()
        date_fields = {"ordered": "ordered_at", "paid": "paid_at", "received": "received_at"}
        payload: dict = {"status": status}
        if status in date_fields:
            payload[date_fields[status]] = today
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.patch(
                f"{self._base()}/purchase_orders",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{po_id}"},
                json=payload,
            )
            r.raise_for_status()
        return f"PO updated to '{status}' on {today}."

    async def _log_po_issue(self, po_id: str, issue_text: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/purchase_orders", headers=self._headers(),
                params={"id": f"eq.{po_id}", "select": "id,notes"},
            )
            r.raise_for_status()
            rows = r.json()
            if not rows:
                return f"PO {po_id} not found."
            from datetime import date
            prefix = f"[{date.today().isoformat()}] {issue_text}"
            current = rows[0].get("notes") or ""
            new_text = f"{prefix}\n{current}".strip()
            rp = await client.patch(
                f"{self._base()}/purchase_orders",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{po_id}"},
                json={"notes": new_text},
            )
            rp.raise_for_status()
        return "Issue logged on PO."

    async def _delete_purchase_order(self, po_id: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/purchase_orders", headers=self._headers(),
                params={"id": f"eq.{po_id}", "select": "id,status"},
            )
            r.raise_for_status()
            rows = r.json()
            if not rows:
                return f"PO {po_id} not found."
            status = rows[0].get("status")
            if status != "draft":
                return (
                    f"Refusing to delete: PO is status='{status}', not 'draft'. "
                    f"Pip only deletes draft POs — remove this one yourself in the CRM if you're sure."
                )
            ri = await client.delete(
                f"{self._base()}/purchase_order_items",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"po_id": f"eq.{po_id}"},
            )
            ri.raise_for_status()
            rp = await client.delete(
                f"{self._base()}/purchase_orders",
                headers={**self._headers(), "Prefer": "return=minimal"},
                params={"id": f"eq.{po_id}"},
            )
            rp.raise_for_status()
        return "Draft PO deleted."

    async def _get_stock_levels(
        self, low_only: bool = False, category: str | None = None
    ) -> str:
        params: dict = {
            "active": "eq.true",
            "select": "id,code,name,category,stock_qty,supplier_id",
            "order": "category.asc,name.asc",
            "limit": "100",
        }
        if low_only:
            params["stock_qty"] = "lte.2"
        if category:
            params["category"] = f"eq.{category}"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self._base()}/products", headers=self._headers(), params=params)
            r.raise_for_status()
            results = r.json()
        return json.dumps(results) if results else "No products found."

    async def _get_product(self, query: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base()}/products", headers=self._headers(),
                params={"code": f"ilike.{query}", "active": "eq.true", "select": "*,suppliers(name)", "limit": "10"},
            )
            r.raise_for_status()
            results = r.json()
            if not results:
                r2 = await client.get(
                    f"{self._base()}/products", headers=self._headers(),
                    params={"name": f"ilike.*{query}*", "active": "eq.true", "select": "*,suppliers(name)", "limit": "10"},
                )
                r2.raise_for_status()
                results = r2.json()
        return json.dumps(results) if results else f"No products found matching '{query}'."

    _NOTES_SINGLETON = "00000000-0000-0000-0000-000000000001"

    async def _get_or_create_note(self, session: AsyncSession) -> Note:
        result = await session.execute(sa.select(Note).where(Note.id == self._NOTES_SINGLETON))
        note = result.scalar_one_or_none()
        if not note:
            note = Note(id=self._NOTES_SINGLETON, content="")
            session.add(note)
            await session.flush()
        return note

    async def _read_notes(self, session: AsyncSession) -> str:
        note = await self._get_or_create_note(session)
        await session.commit()
        return json.dumps({"content": note.content, "updated_at": note.updated_at.isoformat()})

    async def _append_note(self, session: AsyncSession, text: str) -> str:
        note = await self._get_or_create_note(session)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%-d %b %H:%M")
        entry = f"- **{timestamp}** {text.strip()}"
        note.content = entry + ("\n" + note.content if note.content else "")
        note.updated_at = now
        await session.commit()
        return "Note appended."

    async def _write_notes(self, session: AsyncSession, content: str) -> str:
        note = await self._get_or_create_note(session)
        note.content = content
        note.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return "Notes updated."

    async def _send_whatsapp_message(self, to_phone: str, message: str) -> str:
        from services.whatsapp import send_whatsapp_text
        ok = await send_whatsapp_text(to_phone, message)
        return "Sent." if ok else "Failed to send — check WhatsApp credentials."

    async def _escalate_to_stef(
        self, client_name: str, client_phone: str, their_message: str, context: str
    ) -> str:
        from services.whatsapp import notify_stef_escalation
        ok, msg_id = await notify_stef_escalation(client_name, client_phone, their_message, context)
        if ok and msg_id and client_phone:
            from redis.asyncio import from_url as redis_from_url
            from config import settings as _s
            r = await redis_from_url(_s.redis_url)
            await r.setex(f"pip:escalation:{msg_id}", 604800, client_phone)
            await r.aclose()
        return "Escalated to Stef on Telegram." if ok else "Failed to send escalation."

    _WRITE_TOOLS = {
        "create_task", "update_job", "update_client", "log_communication",
        "create_client", "create_job", "compose_email", "send_email",
        "propose_calendar_event", "confirm_calendar_event", "schedule_reminder", "cancel_reminder",
        "propose_calendar_event_update", "confirm_calendar_event_update", "flag_event_for_deletion",
        "draft_supplier_order_email", "draft_client_update_email", "create_purchase_order", "add_po_items",
        "update_po_status", "log_po_issue", "delete_purchase_order", "append_note", "write_notes",
        "add_email_noise_filter", "remove_email_noise_filter",
        "send_whatsapp_message", "escalate_to_stef",
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
            elif name == "update_client":
                client_id = tool_input.pop("client_id")
                result = await self._update_client(client_id, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"update_client: {result[:120]}")
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
            elif name == "propose_calendar_event_update":
                if not user_id:
                    return "Cannot propose update: user context missing."
                result = await self._propose_calendar_event_update(user_id, session, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"propose_event_update: {tool_input.get('event_id', '')[:20]}")
                return result
            elif name == "confirm_calendar_event_update":
                if not user_id:
                    return "Cannot update calendar event: user context missing."
                result = await self._confirm_calendar_event_update(user_id, session, tool_input["pending_id"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"confirm_event_update: {result[:120]}")
                return result
            elif name == "flag_event_for_deletion":
                if not user_id:
                    return "Cannot flag event: user context missing."
                result = await self._flag_event_for_deletion(user_id, session, tool_input["event_id"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"flag_event_for_deletion: {tool_input.get('event_id', '')[:20]}")
                return result
            elif name == "list_suppliers":
                return await self._list_suppliers()
            elif name == "get_supplier":
                return await self._get_supplier(tool_input["supplier_id"])
            elif name == "list_purchase_orders":
                return await self._list_purchase_orders(tool_input.get("status"))
            elif name == "get_purchase_order":
                return await self._get_purchase_order(tool_input["po_id"])
            elif name == "get_job_materials":
                return await self._get_job_materials(tool_input["job_id"])
            elif name == "get_ordering_summary":
                return await self._get_ordering_summary()
            elif name == "get_jobs_needing_orders":
                return await self._get_jobs_needing_orders()
            elif name == "get_jobs_awaiting_stock":
                return await self._get_jobs_awaiting_stock()
            elif name == "get_overdue_jobs":
                return await self._get_overdue_jobs()
            elif name == "list_unpaid_jobs":
                return await self._list_unpaid_jobs()
            elif name == "draft_supplier_order_email":
                if not user_id:
                    return "Cannot draft email: user context missing."
                return await self._draft_supplier_order_email(user_id, session, tool_input["po_id"])
            elif name == "draft_client_update_email":
                if not user_id:
                    return "Cannot draft email: user context missing."
                return await self._draft_client_update_email(
                    user_id, session, tool_input["job_id"], tool_input["message_type"], tool_input.get("extra_context")
                )
            elif name == "create_purchase_order":
                result = await self._create_purchase_order(
                    tool_input["supplier_id"], tool_input["job_ids"], tool_input.get("notes")
                )
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"create_purchase_order: supplier {tool_input['supplier_id'][:8]}…")
                return result
            elif name == "add_po_items":
                return await self._add_po_items(tool_input["po_id"], tool_input["items"])
            elif name == "update_po_status":
                return await self._update_po_status(
                    tool_input["po_id"], tool_input["status"], tool_input.get("date")
                )
            elif name == "log_po_issue":
                return await self._log_po_issue(tool_input["po_id"], tool_input["issue_text"])
            elif name == "delete_purchase_order":
                result = await self._delete_purchase_order(tool_input["po_id"])
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"delete_purchase_order: {tool_input.get('po_id', '')[:20]}")
                return result
            elif name == "get_stock_levels":
                return await self._get_stock_levels(
                    tool_input.get("low_only", False), tool_input.get("category")
                )
            elif name == "get_product":
                return await self._get_product(tool_input["query"])
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
            elif name == "pause_briefing":
                return await self._pause_briefing(tool_input["until_date"])
            elif name == "resume_briefing":
                return await self._resume_briefing()
            elif name == "add_email_noise_filter":
                return await self._add_email_noise_filter(tool_input["pattern"])
            elif name == "remove_email_noise_filter":
                return await self._remove_email_noise_filter(tool_input["pattern"])
            elif name == "list_email_noise_filters":
                return await self._list_email_noise_filters()
            elif name == "send_whatsapp_message":
                return await self._send_whatsapp_message(tool_input["to_phone"], tool_input["message"])
            elif name == "escalate_to_stef":
                return await self._escalate_to_stef(
                    tool_input["client_name"], tool_input["client_phone"],
                    tool_input["their_message"], tool_input["context"],
                )
            elif name == "read_notes":
                return await self._read_notes(session)
            elif name == "append_note":
                return await self._append_note(session, tool_input["text"])
            elif name == "write_notes":
                return await self._write_notes(session, tool_input["content"])
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
        system = f"{current_dt}\n\n" + self.system_prompt if current_dt else self.system_prompt
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
                        _status = "Sending email…" if block.name in ("send_email", "compose_email", "draft_supplier_order_email", "draft_client_update_email") \
                            else "Checking inbox…" if block.name in ("list_emails", "read_email") \
                            else "Updating calendar…" if block.name in ("confirm_calendar_event", "propose_calendar_event", "list_upcoming_events", "propose_calendar_event_update", "confirm_calendar_event_update", "flag_event_for_deletion") \
                            else "Setting reminder…" if block.name == "schedule_reminder" \
                            else "Checking suppliers…" if block.name in ("list_suppliers", "get_supplier") \
                            else "Checking orders…" if block.name in ("list_purchase_orders", "get_purchase_order", "get_jobs_awaiting_stock") \
                            else "Analysing jobs…" if block.name in ("get_ordering_summary", "get_jobs_needing_orders", "get_overdue_jobs", "list_unpaid_jobs", "get_job_materials") \
                            else "Updating order…" if block.name in ("create_purchase_order", "add_po_items", "update_po_status", "log_po_issue") \
                            else "Checking stock…" if block.name in ("get_stock_levels", "get_product") \
                            else "Reading notes…" if block.name == "read_notes" \
                            else "Updating notes…" if block.name in ("append_note", "write_notes") \
                            else "Sending WhatsApp…" if block.name == "send_whatsapp_message" \
                            else "Escalating to Stef…" if block.name == "escalate_to_stef" \
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
