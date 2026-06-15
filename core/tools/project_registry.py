from dataclasses import dataclass


@dataclass
class Project:
    name: str
    repo: str  # owner/repo
    description: str
    keywords: list[str]


PROJECTS: list[Project] = [
    Project(
        name="StefHQ",
        repo="SLBij/stefhq",
        description="Stef's personal unified AI system — FastAPI backend, SvelteKit 5 frontend, Neon Postgres, ARQ workers, deployed on Hetzner via Coolify.",
        keywords=["stefhq", "stef hq", "hive mind", "round table", "this project", "the backend", "the frontend"],
    ),
    Project(
        name="HeadSpace",
        repo="SLBij/headspace",
        description="Telegram bot that forwards messages to StefHQ API. Nothing Phone Essential Space clone.",
        keywords=["headspace", "head space", "telegram", "telegram bot"],
    ),
    Project(
        name="FloraFolio",
        repo="SLBij/house_of_leaves",
        description="Plant care app — Claude & Stef collab project. Mobile + web sharing the same Neon DB.",
        keywords=["florafolio", "flora folio", "plant app", "house of leaves", "plant care app"],
    ),
    Project(
        name="CurtainsCRM",
        repo="SLBij/curtains-crm",
        description="Certain Curtains business CRM — vanilla JS, being migrated to PostgreSQL backend.",
        keywords=["curtainscrm", "curtains crm", "crm", "certain curtains crm", "curtains app"],
    ),
    Project(
        name="Drest",
        repo="SLBij/Drest",
        description="LLM-powered closet manager / weather-based outfit suggestions. Next.js + Neon, deployed on Vercel. Also known as WeatherWear.",
        keywords=["drest", "weatherwear", "weather wear", "closetcast", "outfit", "closet"],
    ),
    Project(
        name="BijouxHome",
        repo="SLBij/BijouxHome",
        description="House of Bijoux — personal home management PWA for Stef & Andre.",
        keywords=["bijouxhome", "bijoux home", "house of bijoux", "home management"],
    ),
]


def find_projects(text: str) -> list[Project]:
    """Return projects mentioned in text, by keyword match."""
    lower = text.lower()
    seen: set[str] = set()
    matches: list[Project] = []
    for project in PROJECTS:
        if project.name in seen:
            continue
        if any(kw in lower for kw in project.keywords):
            matches.append(project)
            seen.add(project.name)
    return matches
