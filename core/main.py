from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.activity import router as activity_router
from api.auth import router as auth_router
from api.chat import router as chat_router
from api.conversations import router as conversations_router
from api.google_auth import router as google_auth_router
from api.headspace import router as headspace_router
from api.pip import router as pip_router
from api.memory import router as memory_router
from api.notes import router as notes_router
from api.tasks import router as tasks_router
from database import init_db
from workers.arq_pool import close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Stef HQ Core", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "https://stefhq.io"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(activity_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(conversations_router, prefix="/api")
app.include_router(google_auth_router, prefix="/api")
app.include_router(headspace_router, prefix="/api")
app.include_router(pip_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
