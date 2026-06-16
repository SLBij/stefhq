# StefHQ — Developer Guide

A practical reference for returning to this project after time away. No assumed memory.

---

## Quick-start cheat sheet

```bash
# SSH into the VPS
ssh root@178.105.107.126

# Run the backend locally (from StefHQ/core/)
cd /home/stef/Documents/Projects/StefHQ/core
uv run uvicorn main:app --reload

# Run the ARQ worker locally (separate terminal, same folder)
uv run arq workers.memory_worker.WorkerSettings

# Run the frontend locally (from StefHQ/frontend/)
cd /home/stef/Documents/Projects/StefHQ/frontend
npm run dev

# Git — check, add, commit, push
git status
git add <file>
git commit -m "what and why"
git push

# Deploy — just push to GitHub. Coolify auto-deploys on push.
```

---

## 1. Project overview

**What it is:** StefHQ is your personal AI system — a single app with multiple "workspaces", each backed by a specialised AI agent with memory and tool access.

**Live URLs:**
- App: https://stefhq.io
- API: https://api.stefhq.io
- Coolify dashboard: https://coolify.stefhq.io (manage containers, env vars, logs)

**Tech stack:**

| Layer | Technology |
|---|---|
| Backend API | Python + FastAPI |
| Frontend | SvelteKit 5 (Svelte 5 runes syntax) |
| Database | Neon Postgres + pgvector (cloud, shared between local and prod) |
| Task queue | ARQ + Redis (for async memory extraction jobs) |
| Embeddings | Ollama (`mxbai-embed-large` — local GPU in dev, CPU on VPS) |
| AI | Anthropic Claude |
| Deployment | Hetzner VPS via Coolify + Docker Compose |
| Package manager (Python) | `uv` |
| Package manager (JS) | `npm` |

**Workspaces (agents):**

| Workspace | What it does |
|---|---|
| Hive Mind | General assistant with memory r/w tools |
| Inbox | Task manager (create, list, update tasks in DB) |
| Business | Certain Curtains CRM — reads/writes Supabase jobs |
| Plant Atlas | FloraFolio plant collection — search and add plants |
| Round Table | Technical coding/architecture partner |

---

## 2. Folder structure

```
StefHQ/
├── docker-compose.yml       ← Production deployment config (all services)
├── DEV_GUIDE.md             ← This file
│
├── core/                    ← Python backend (FastAPI)
│   ├── main.py              ← App entry point, mounts all routers
│   ├── config.py            ← All environment variables (pydantic settings)
│   ├── database.py          ← DB connection setup
│   ├── pyproject.toml       ← Python dependencies
│   ├── .env                 ← Local secrets (never committed)
│   ├── .env.example         ← Template — copy this to .env
│   │
│   ├── agents/              ← One file per workspace agent
│   │   ├── router.py        ← Routes messages to the right agent
│   │   ├── hive_mind.py
│   │   ├── inbox.py
│   │   ├── business.py
│   │   ├── plant_atlas.py
│   │   └── round_table.py
│   │
│   ├── api/                 ← HTTP endpoint handlers
│   │   ├── auth.py          ← Login/token
│   │   ├── chat.py          ← Main streaming chat endpoint
│   │   ├── conversations.py
│   │   ├── memory.py
│   │   ├── tasks.py
│   │   └── headspace.py     ← Telegram bot passthrough
│   │
│   ├── models/
│   │   └── db.py            ← SQLAlchemy DB models (Memory, Task, Conversation…)
│   │
│   ├── services/
│   │   ├── context.py       ← Builds memory context for each agent call
│   │   ├── memory.py        ← Save/search memories
│   │   ├── agent_naming.py  ← Shared agent name tool
│   │   └── streaming.py     ← SSE event helpers
│   │
│   └── workers/
│       ├── memory_worker.py ← ARQ worker settings + cron jobs
│       └── briefing_worker.py ← Morning Telegram briefing (8:15am SAST)
│
└── frontend/                ← SvelteKit app
    └── src/
        ├── routes/
        │   ├── login/       ← Login page
        │   └── (app)/       ← Authenticated app shell
        │       ├── [workspace]/ ← Main chat page (all workspaces share this)
        │       ├── inbox/   ← Task list view
        │       └── review/  ← Memory review queue
        └── lib/
            ├── api.ts       ← All API calls to the backend
            ├── types.ts     ← Shared TypeScript types
            └── auth.svelte.ts ← Auth state (token, user)
```

---

## 3. First-time setup (local dev)

### Prerequisites

- Python 3.12+
- Node.js 20+
- `uv` installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Ollama running locally with `mxbai-embed-large` pulled: `ollama pull mxbai-embed-large`
- Redis running locally: `docker run -d -p 6379:6379 redis:7-alpine`

### Backend setup

```bash
cd /home/stef/Documents/Projects/StefHQ/core

# Copy env template
cp .env.example .env
# Then fill in .env with real values (see section 4)

# Install dependencies (uv does this automatically on first run, but explicit is fine)
uv sync

# Run the API
uv run uvicorn main:app --reload
# → Backend available at http://localhost:8000
```

### Frontend setup

```bash
cd /home/stef/Documents/Projects/StefHQ/frontend
npm install

# The frontend needs to know where the backend is
# In dev it points to localhost — this is already handled by the dev vite config
npm run dev
# → Frontend available at http://localhost:5173
```

---

## 4. Environment variables

All backend env vars live in `core/.env`. They are **never committed to git**.

| Variable | What it is | Where to find it |
|---|---|---|
| `DATABASE_URL` | Neon Postgres connection string | Neon dashboard → Connection string (asyncpg driver) |
| `REDIS_URL` | Redis URL | `redis://localhost:6379` locally |
| `SECRET_KEY` | JWT signing secret | Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ANTHROPIC_API_KEY` | Claude API key | console.anthropic.com |
| `OLLAMA_BASE_URL` | Ollama URL | `http://localhost:11434` locally |
| `EMBEDDING_MODEL` | Embedding model name | `mxbai-embed-large` |
| `APP_ENV` | `development` or `production` | Set manually |
| `CURTAINS_SUPABASE_URL` | Supabase URL for CurtainsCRM | Supabase dashboard |
| `CURTAINS_SUPABASE_KEY` | Supabase service role key | Supabase dashboard → Settings → API |
| `FLORAFOLIO_URL` | FloraFolio app URL | FloraFolio project |
| `FLORAFOLIO_HEADSPACE_KEY` | FloraFolio API key | FloraFolio project env |
| `GITHUB_TOKEN` | GitHub PAT for Round Table | GitHub → Settings → Developer tokens |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID | `8931649167` (your confirmed ID) |

**In production**, all of these are set in Coolify → your project → Environment Variables (not in a file on the VPS).

---

## 5. Everyday commands

### Backend (run from `core/`)

```bash
# Start dev server
uv run uvicorn main:app --reload

# Start ARQ worker (in a separate terminal)
uv run arq workers.memory_worker.WorkerSettings

# Add a Python package
uv add package-name

# Run a one-off Python script
uv run python seed.py

# Test the morning briefing manually
uv run python -c "
import asyncio
from workers.briefing_worker import send_morning_briefing
asyncio.run(send_morning_briefing({}))
"
```

### Frontend (run from `frontend/`)

```bash
# Start dev server
npm run dev

# Type-check the Svelte/TypeScript code
npm run check

# Build for production (you won't usually need this — Docker handles it)
npm run build
```

### Database

The database is **shared between local and production** (Neon Postgres in the cloud). Changes you make locally affect prod. Be careful.

```bash
# No migrations yet — schema changes are applied manually or via seed.py
# CHECK THIS if Alembic migrations are added in future
```

---

## 6. Git / GitHub workflow

**Plain English:** Git tracks changes on your computer. GitHub stores a copy online. Coolify watches GitHub and redeploys automatically when you push.

```bash
# See what changed
git status

# See exactly what changed in a file
git diff

# Stage specific files (safer than "git add .")
git add core/agents/inbox.py

# Stage everything
git add .

# Commit with a message
git commit -m "Add task sorting to inbox"

# Push to GitHub → triggers Coolify redeploy
git push

# Pull latest from GitHub (if you edited something on another machine)
git pull

# What branch am I on?
git branch

# See recent commits
git log --oneline -10
```

**Before troubleshooting a deployment issue — always check that you actually pushed:**
```bash
git status   # should say "nothing to commit, working tree clean"
git log --oneline -3   # latest commit should match what you expect
```

---

## 7. Deployment

Deployment is automatic — **push to GitHub and Coolify rebuilds everything**.

### How it works

1. You push to `main` on GitHub
2. Coolify detects the push (webhook)
3. Coolify rebuilds Docker images for `backend`, `worker`, and `frontend`
4. New containers replace the old ones

### Coolify dashboard

URL: https://coolify.stefhq.io

- **Deployments tab** — see build logs, redeploy manually
- **Environment Variables** — add/change prod env vars here (not in files on the VPS)
- **Logs** — live container logs for each service

### SSH into the VPS directly

```bash
ssh root@178.105.107.126
```

Once on the VPS, useful commands:

```bash
# See running containers
docker ps

# See logs for a specific container (find name from docker ps)
docker logs stefhq-backend-1 --tail 100 -f

# Run a one-off command inside a running container
docker exec -it stefhq-backend-1 uv run python -c "print('hello')"

# Restart a container
docker restart stefhq-backend-1
```

### Important Docker/Coolify rules (hard-won)

- The Redis service in `docker-compose.yml` is named **`cache`**, not `redis`. Coolify already has a service called `redis` on its network — naming ours the same causes auth errors.
- The backend and frontend need `networks: [coolify, default]` for Traefik to route traffic.
- `PUBLIC_API_BASE` (`https://api.stefhq.io`) is **baked into the frontend at build time**. Changing it requires a full rebuild.
- Don't add a `curl`-based healthcheck — `curl` isn't in the Python slim image.

---

## 8. Common workflows

### Add a new Python package to the backend

```bash
cd core
uv add package-name
git add pyproject.toml uv.lock
git commit -m "Add package-name"
git push
```

### Add a new API endpoint

1. Add the handler in the relevant file under `core/api/` (e.g. `tasks.py`)
2. If it's a new file, register the router in `core/main.py`
3. Add the matching fetch call in `frontend/src/lib/api.ts`
4. Test locally, then push

### Add a tool to an agent

1. Define the tool dict in the agent file (e.g. `core/agents/inbox.py`) and add it to `_TOOLS`
2. Add the handler in `_execute_tool()` in the same file
3. Test locally with `uv run uvicorn main:app --reload`
4. Push

### Add a new agent / workspace

1. Create `core/agents/yourworkspace.py` (copy an existing one as template)
2. Add the new `Workspace` enum value in `core/agents/router.py`
3. Add the workspace metadata (label, icon, color) in `frontend/src/lib/types.ts` in the `WORKSPACES` array
4. Register the agent in `core/agents/__init__.py`
5. Test, then push

### Change the system prompt for an agent

Edit the `_SYSTEM` string at the top of the relevant agent file in `core/agents/`. Push.

### Update env vars in production

Go to Coolify → your project → Environment Variables. Add or change the value. Then redeploy (Coolify will rebuild with the new env).

### Test the morning Telegram briefing

```bash
cd /home/stef/Documents/Projects/StefHQ/core
uv run python -c "
import asyncio
from workers.briefing_worker import send_morning_briefing
asyncio.run(send_morning_briefing({}))
"
```

---

## 9. Troubleshooting

### Backend won't start

```bash
# Check for syntax errors
uv run python -c "import main"

# Missing env var — look for the error message, then add to .env
# Common culprit: DATABASE_URL not set
```

### "Address already in use" (port 8000 or 5173)

```bash
# Find what's using the port
lsof -i :8000

# Kill it
kill -9 <PID>
```

### Frontend shows "Failed to fetch" or CORS errors

- Is the backend running? Check http://localhost:8000/docs
- Are you hitting the right URL? In dev the frontend calls `http://localhost:8000`. In prod it calls `https://api.stefhq.io` (baked at build time).
- On prod: check that the backend container has the Traefik labels in `docker-compose.yml` (they must be there or `api.stefhq.io` returns 504).

### Changes deployed but not showing up

1. Did you actually push? `git log --oneline -1`
2. Did Coolify finish building? Check the Coolify deployment logs.
3. Hard refresh the browser (Ctrl+Shift+R) — browser may have cached the old frontend.

### Memory extraction not working / memories not appearing

- The ARQ worker must be running (`uv run arq workers.memory_worker.WorkerSettings`)
- Ollama must be running with `mxbai-embed-large` pulled
- Embeddings take ~20s locally on CPU — the frontend polls at 10s, 25s, 40s after each message

### Agent routing to wrong workspace

The router can be overly eager to route short messages (like "hi") to Hive Mind. This is handled by a code-level fallback in `core/agents/router.py` — if you see weird routing, check that file.

### Telegram briefing not arriving

1. Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in Coolify env vars
2. Your confirmed chat ID is `8931649167`
3. Test manually with the command in section 8
4. Check worker logs in Coolify — the worker container must be running

### Bad/duplicate agent name in memory

If an agent saved a bad name (a sentence instead of just the name), clean it up on the VPS:

```bash
ssh root@178.105.107.126
docker exec -it <worker-or-backend-container> uv run python -c "
import asyncio, sqlalchemy as sa
from database import async_session_factory
from models.db import Memory

async def fix():
    async with async_session_factory() as s:
        r = await s.execute(sa.select(Memory).where(Memory.tags.contains(['agent_name'])))
        for m in r.scalars().all():
            print(m.workspace, repr(m.content))
        # Uncomment to delete bad ones:
        # [await s.delete(m) for m in r.scalars().all() if len(m.content) > 30]
        # await s.commit()

asyncio.run(fix())
"
```

---

## 10. Where do I change X?

| I want to change… | Go to… |
|---|---|
| An agent's personality / instructions | `core/agents/<workspace>.py` → `_SYSTEM` string |
| An agent's tools | `core/agents/<workspace>.py` → `_TOOLS` list + `_execute_tool()` |
| API routes / endpoints | `core/api/<area>.py` |
| Database models | `core/models/db.py` |
| App-wide config / env vars | `core/config.py` (schema) + `core/.env` (values) |
| Frontend page for a workspace | `frontend/src/routes/(app)/[workspace]/+page.svelte` |
| Workspace labels, icons, colors | `frontend/src/lib/types.ts` → `WORKSPACES` array |
| All API calls from frontend | `frontend/src/lib/api.ts` |
| Global styles | `frontend/src/app.css` |
| Auth logic (frontend) | `frontend/src/lib/auth.svelte.ts` |
| Morning briefing content | `core/workers/briefing_worker.py` |
| Memory extraction logic | `core/workers/memory_worker.py` + `core/services/memory.py` |
| Docker/deployment config | `docker-compose.yml` |

---

## 11. Glossary

**ARQ** — Python task queue that runs jobs in the background (like "extract memories from this conversation"). Uses Redis to store jobs.

**Coolify** — Self-hosted deployment platform running on your VPS. It's like a personal Heroku — watches GitHub, builds Docker images, manages containers.

**Docker / Docker Compose** — Docker packages the app into containers (isolated boxes with everything they need). Docker Compose runs multiple containers together (`backend`, `frontend`, `worker`, `redis`, `ollama`).

**FastAPI** — Python web framework for the backend. Auto-generates API docs at `/docs`.

**Neon** — Cloud Postgres database (like a managed PostgreSQL server). Your DB lives there, not on your VPS.

**Ollama** — Runs AI models locally. Used here for text embeddings (`mxbai-embed-large`), which power the memory search.

**pgvector** — Postgres extension that lets you store and search vector embeddings (used for semantic memory search — "find memories similar to this message").

**Redis** — In-memory data store. Used as the message queue between the API and the ARQ worker.

**Runes** — Svelte 5's reactivity system. `$state`, `$derived`, `$effect` are runes. The `$` prefix means "reactive".

**SSE (Server-Sent Events)** — How the streaming chat works. The backend sends a stream of small events (`token`, `status`, `done`) and the frontend displays them as they arrive.

**Traefik** — Reverse proxy that runs on your VPS (managed by Coolify). Routes traffic from `stefhq.io` → frontend container and `api.stefhq.io` → backend container. Also handles HTTPS certificates.

**uv** — Fast Python package manager. Replaces `pip` + `venv`. `uv run` runs commands inside the project's virtual environment automatically.

**Workspace** — One of the five sections of StefHQ (Hive Mind, Inbox, Business, Plant Atlas, Round Table). Each has its own agent, system prompt, and tools.
