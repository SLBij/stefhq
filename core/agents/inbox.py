import json
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from models.db import Task
from services.activity import log_activity
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.streaming import ServerSentEvent, error_event, status_event, token_event

_SYSTEM = """You are Stef's inbox and task manager. Your job is to capture, organise, and clear tasks \
so nothing falls through the cracks.

You have three tools that connect directly to the task database — use them for ALL task operations:
- list_tasks: show tasks with their IDs, filtered by status and/or priority
- create_task: capture a new task
- update_task: change status, priority, or details — requires a task_id from list_tasks

IMPORTANT RULES:
- ALWAYS use tools for task operations. Never say you've done something without calling the tool.
- To mark a task done: call list_tasks first to get the task ID, then call update_task with that ID.
- Never claim a limitation like "I can't update the system" — you have direct database access via tools.
- After marking something done, confirm with the actual task title from the tool result.

Be practical and action-oriented. Default to showing open + in_progress tasks when listing. \
Keep task titles short and actionable ("Email Drest re: quote" not "Send an email to Drest about the quote").""" + agent_name_prompt(
    "task manager and personal organiser — clear, efficient, action-oriented"
)

_TOOLS = [
    AGENT_NAME_TOOL,
    {
        "name": "list_tasks",
        "description": "List Stef's tasks. Defaults to open and in_progress tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "in_progress", "done", "cancelled", "all"],
                    "description": "Filter by status. Omit for open+in_progress.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Filter by priority. Omit for all priorities.",
                },
            },
        },
    },
    {
        "name": "create_task",
        "description": "Create a new task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short actionable task title"},
                "description": {"type": "string", "description": "Optional extra detail"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Defaults to medium",
                },
                "due_date": {
                    "type": "string",
                    "description": "ISO 8601 date string, e.g. 2026-06-20. Optional.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags e.g. ['curtains', 'client']",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": "Update an existing task — change status, priority, title, description, or due date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID of the task to update"},
                "status": {
                    "type": "string",
                    "enum": ["open", "in_progress", "done", "cancelled"],
                },
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "due_date": {
                    "type": "string",
                    "description": "ISO 8601 date string e.g. 2026-06-20, or 'clear' to remove the due date.",
                },
            },
            "required": ["task_id"],
        },
    },
]

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class InboxAgent(DeskAgent):
    workspace = Workspace.INBOX
    system_prompt = _SYSTEM

    async def _list_tasks(self, session: AsyncSession, status: str | None, priority: str | None) -> str:
        query = sa.select(Task)
        if status == "all":
            pass
        elif status in ("open", "in_progress", "done", "cancelled"):
            query = query.where(Task.status == status)
        else:
            query = query.where(Task.status.in_(["open", "in_progress"]))

        if priority in ("low", "medium", "high"):
            query = query.where(Task.priority == priority)

        query = query.order_by(Task.created_at.desc()).limit(50)
        result = await session.execute(query)
        tasks = result.scalars().all()

        if not tasks:
            return "No tasks found."

        rows = sorted(
            [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "status": t.status,
                    "priority": t.priority,
                    "due_date": t.due_date.date().isoformat() if t.due_date else None,
                    "tags": t.tags,
                    "description": t.description,
                }
                for t in tasks
            ],
            key=lambda t: (_PRIORITY_ORDER.get(t["priority"], 1), t["title"]),
        )
        return json.dumps(rows)

    async def _create_task(
        self,
        session: AsyncSession,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        due_date: str | None = None,
        tags: list[str] | None = None,
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
            tags=tags or [],
            source="inbox",
        )
        session.add(task)
        await session.commit()
        return f"Created task '{title}' (id: {task.id}, priority: {priority})"

    async def _update_task(
        self,
        session: AsyncSession,
        task_id: str,
        status: str | None = None,
        priority: str | None = None,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
    ) -> str:
        result = await session.execute(sa.select(Task).where(Task.id == uuid.UUID(task_id)))
        task = result.scalar_one_or_none()
        if not task:
            return f"Task {task_id} not found."

        if status:
            task.status = status
        if priority:
            task.priority = priority
        if title:
            task.title = title
        if description is not None:
            task.description = description
        if due_date is not None:
            if due_date.lower() == "clear":
                task.due_date = None
            else:
                try:
                    task.due_date = datetime.fromisoformat(due_date).replace(tzinfo=timezone.utc)
                except ValueError:
                    return f"Invalid due_date format '{due_date}' — use YYYY-MM-DD."

        task.updated_at = datetime.now(timezone.utc)
        await session.commit()
        due_str = task.due_date.date().isoformat() if task.due_date else "none"
        return f"Updated task '{task.title}' → status: {task.status}, priority: {task.priority}, due: {due_str}"

    async def _execute_tool(self, name: str, tool_input: dict, session: AsyncSession) -> str:
        try:
            if name == "save_agent_name":
                return await save_agent_name(tool_input["name"], self.workspace.value, session)
            if name == "create_task":
                result = await self._create_task(session, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"create_task: {tool_input.get('title', '')[:80]}")
                return result
            if name == "list_tasks":
                return await self._list_tasks(
                    session, tool_input.get("status"), tool_input.get("priority")
                )
            elif name == "update_task":
                task_id = tool_input.pop("task_id")
                result = await self._update_task(session, task_id, **tool_input)
                await log_activity(session, "web", self.workspace.value, "tool_call",
                                   f"update_task: {result[:120]}", {"task_id": task_id})
                return result
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
        memory_context = "\n".join(f"- {m['content']}" for m in context.get("memories", []))
        system = self.system_prompt
        if memory_context:
            system += f"\n\nRelevant context from memory:\n{memory_context}"

        messages = [*context.get("history", []), {"role": "user", "content": self._user_content(message, attachments)}]

        try:
            while True:
                async with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
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
                        yield status_event(f"{block.name.replace('_', ' ').title()}…")
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
