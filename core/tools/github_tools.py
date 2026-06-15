import base64

import httpx

from config import settings

_BASE = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_recent_commits(repo: str, n: int = 5) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_BASE}/repos/{repo}/commits?per_page={n}", headers=_headers())
        if r.status_code != 200:
            return []
        return [
            {"sha": c["sha"][:7], "message": c["commit"]["message"].split("\n")[0], "date": c["commit"]["author"]["date"][:10]}
            for c in r.json()
        ]


async def get_tree(repo: str, path: str = "") -> list[str]:
    """Top-level directory listing at path."""
    async with httpx.AsyncClient() as client:
        url = f"{_BASE}/repos/{repo}/contents/{path}"
        r = await client.get(url, headers=_headers())
        if r.status_code != 200:
            return []
        return [f"{item['name']}{'/' if item['type'] == 'dir' else ''}" for item in r.json()]


async def get_file(repo: str, path: str) -> str | None:
    """Read a file from a repo. Returns decoded text content or None."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_BASE}/repos/{repo}/contents/{path}", headers=_headers())
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None


async def get_readme(repo: str) -> str | None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_BASE}/repos/{repo}/readme", headers=_headers())
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("encoding") == "base64":
            raw = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return raw[:2000]  # cap at 2k chars
        return None


async def build_project_context(repo: str, description: str) -> str:
    """Fetch README + tree + recent commits and format as a context block."""
    readme, tree, commits = await _gather(repo)

    lines = [f"### {repo.split('/')[1]}", f"{description}", ""]

    if tree:
        lines.append("**Structure:** " + "  ".join(tree[:20]))
        lines.append("")

    if commits:
        lines.append("**Recent commits:**")
        for c in commits:
            lines.append(f"- `{c['sha']}` {c['date']} — {c['message']}")
        lines.append("")

    if readme:
        lines.append("**README (excerpt):**")
        lines.append(readme.strip()[:1500])

    return "\n".join(lines)


async def _gather(repo: str):
    import asyncio
    readme, tree, commits = await asyncio.gather(
        get_readme(repo),
        get_tree(repo),
        get_recent_commits(repo),
        return_exceptions=True,
    )
    if isinstance(readme, Exception):
        readme = None
    if isinstance(tree, Exception):
        tree = []
    if isinstance(commits, Exception):
        commits = []
    return readme, tree, commits
