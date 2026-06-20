import asyncio
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base import DeskAgent
from agents.router import Workspace
from config import settings
from services.agent_naming import AGENT_NAME_TOOL, agent_name_prompt, save_agent_name
from services.project_notes import add_note, list_notes, resolve_note
from services.streaming import ServerSentEvent, error_event, status_event, token_event
from tools.github_tools import build_project_context
from tools.project_registry import PROJECTS, find_projects

_SYSTEM = """You are Stef's project manager and technical thinking partner in Round Table — a space \
to keep track of where every project stands, think through plans and blockers, and advise on \
architecture, debugging, and code review.

Be precise, opinionated, and direct. Prefer concrete solutions over exhaustive lists of options. \
When writing code, make it production-quality — no placeholders, no hand-waving. When reviewing, \
say what's actually wrong and how to fix it.

You track notes per project — todos, bugs, progress updates, open questions:
- add_project_note: log something worth remembering (always pick a project name and a kind)
- list_project_notes: recall open (or all) notes for a project — use this proactively when a project \
comes up so you're not relying on Stef to re-brief you
- resolve_project_note: mark a note done once it's actually resolved

Known projects: """ + ", ".join(p.name for p in PROJECTS) + """

Stef's main stack: Python (FastAPI, SQLAlchemy, ARQ, pydantic), SvelteKit 5 with Svelte 5 runes, \
TypeScript, PostgreSQL + pgvector, Tailwind CSS v4, Docker. She also has a vanilla JS project \
(CurtainsCRM) and a Next.js app (Drest on Vercel/Neon).

When a project is mentioned, live GitHub context (README, file tree, recent commits) is fetched \
automatically and appended below — use it, don't ask Stef to repeat what's already there.

Relevant memories and recent conversation history are provided as context.""" + agent_name_prompt(
    "project manager and technical thinking partner — precise, opinionated, engineering-minded"
)

_TOOLS = [
    AGENT_NAME_TOOL,
    {
        "name": "add_project_note",
        "description": "Log a note against a project — a todo, a bug, a progress update, or an open question. Use whenever something worth remembering for next time comes up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name, e.g. 'FloraFolio', 'CurtainsCRM'"},
                "text": {"type": "string", "description": "The note itself"},
                "kind": {"type": "string", "enum": ["todo", "bug", "progress", "question"], "description": "Defaults to todo"},
            },
            "required": ["project", "text"],
        },
    },
    {
        "name": "list_project_notes",
        "description": "List notes for a project. Use proactively when a project is mentioned so you have full context without Stef re-briefing you.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name"},
                "status": {"type": "string", "enum": ["open", "done", "all"], "description": "Defaults to open"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "resolve_project_note",
        "description": "Mark a note as done once it's actually resolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name"},
                "note_id": {"type": "string", "description": "Note ID from list_project_notes"},
            },
            "required": ["project", "note_id"],
        },
    },
]


class RoundTableAgent(DeskAgent):
    workspace = Workspace.ROUND_TABLE
    system_prompt = _SYSTEM

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

        # Pre-flight: inject GitHub context for any mentioned projects
        if settings.github_token:
            projects = find_projects(message)
            if projects:
                yield status_event(f"Fetching context for: {', '.join(p.name for p in projects)}")
                contexts = await asyncio.gather(
                    *[build_project_context(p.repo, p.description) for p in projects],
                    return_exceptions=True,
                )
                project_blocks = [c for c in contexts if isinstance(c, str)]
                if project_blocks:
                    system += "\n\n## Project context (live from GitHub)\n\n" + "\n\n".join(project_blocks)

        messages = [*context.get("history", []), {"role": "user", "content": self._user_content(message, attachments)}]

        try:
            while True:
                async with self.client.messages.stream(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                    tools=_TOOLS,
                ) as stream:
                    async for text in stream.text_stream:
                        yield token_event(text)
                    final = await stream.get_final_message()

                if final.stop_reason != "tool_use":
                    break

                tool_results = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    if block.name == "save_agent_name":
                        result = await save_agent_name(block.input["name"], self.workspace.value, session)
                    elif block.name == "add_project_note":
                        note = await add_note(block.input["project"], block.input["text"], block.input.get("kind", "todo"))
                        result = f"Note added [{note['id']}] ({note['kind']}): {note['text']}"
                    elif block.name == "list_project_notes":
                        status = block.input.get("status", "open")
                        notes = await list_notes(block.input["project"], status=None if status == "all" else status)
                        result = "\n".join(
                            f"- [{n['id']}] ({n['kind']}, {n['status']}, {n['date']}) {n['text']}" for n in notes
                        ) if notes else "No notes for this project."
                    elif block.name == "resolve_project_note":
                        ok = await resolve_note(block.input["project"], block.input["note_id"])
                        result = "Note marked done." if ok else "Note not found."
                    else:
                        result = f"Unknown tool: {block.name}"
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

                messages = [
                    *messages,
                    {"role": "assistant", "content": [
                        {"type": "text", "text": b.text} if b.type == "text"
                        else {"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)}
                        for b in final.content
                    ]},
                    {"role": "user", "content": tool_results},
                ]
        except Exception as e:
            yield error_event(str(e))
