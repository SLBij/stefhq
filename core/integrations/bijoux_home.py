"""
Read-only client for BijouxHome data via the /api/bijoux endpoint.
Returns pre-digested summaries suitable for passing to an LLM.
"""

import json
from datetime import date, datetime

import httpx

from config import settings


async def _fetch(key: str) -> dict | list | None:
    if not settings.bijoux_api_secret:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            settings.bijoux_api_url,
            params={"key": key, "secret": settings.bijoux_api_secret},
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("data")


def _days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(date_str[:10]).date()
        return (date.today() - d).days
    except ValueError:
        return None


async def get_shopping_list() -> str:
    data = await _fetch("bijoux_shopping")
    if not data:
        return "Could not fetch shopping list."
    items = data.get("list", [])
    pending = [i for i in items if not i.get("done")]
    done = [i for i in items if i.get("done")]
    if not pending and not done:
        return "Shopping list is empty."
    lines = [f"**Shopping list** ({len(pending)} items to get):"]
    by_store: dict[str, list] = {}
    for item in pending:
        store = item.get("store", "other").capitalize()
        by_store.setdefault(store, []).append(item["name"])
    for store, names in sorted(by_store.items()):
        lines.append(f"\n{store}:")
        for n in names:
            lines.append(f"  - {n}")
    if done:
        lines.append(f"\n({len(done)} already in cart)")
    return "\n".join(lines)


async def get_meds_status() -> str:
    data = await _fetch("bijoux_meds")
    if not data:
        return "Could not fetch meds."
    lines = ["**Medication status:**"]
    for person in ("stef", "angelo"):
        meds = data.get(person, [])
        if not meds:
            continue
        lines.append(f"\n{person.capitalize()}:")
        for med in meds:
            days_supply = int(med.get("daysSupply") or 0)
            last_filled = med.get("lastFilled")
            if last_filled and days_supply:
                from datetime import timedelta
                filled = datetime.fromisoformat(last_filled[:10]).date()
                due = filled + timedelta(days=days_supply)
                days_left = (due - date.today()).days
                if days_left < 0:
                    status = f"⚠️ OVERDUE by {abs(days_left)}d"
                elif days_left <= 7:
                    status = f"⚠️ due in {days_left}d ({due.strftime('%d %b')})"
                elif days_left <= 14:
                    status = f"fill soon — {days_left}d left"
                else:
                    status = f"{days_left}d remaining"
            else:
                status = "no data"
            lines.append(f"  - {med['name']}: {status}")
    return "\n".join(lines)


async def get_car_service() -> str:
    data = await _fetch("bijoux_cars")
    if not data:
        return "Could not fetch car info."
    if not data:
        return "No cars found."
    lines = ["**Car service status:**"]
    for car in data:
        name = car.get("name", "Unknown")
        model = car.get("model", "")
        km = car.get("km", "")
        header = f"\n{name}" + (f" ({model})" if model else "") + (f" — {km} km" if km else "")
        lines.append(header)
        service_items = car.get("serviceItems", [])
        if not service_items:
            lines.append("  No service items recorded.")
        for item in service_items:
            item_name = item.get("name", "service")
            last_date = item.get("lastDate")
            last_km = item.get("lastKm", "")
            days = _days_since(last_date)
            date_str = f"{days}d ago" if days is not None else "never"
            km_str = f" at {last_km} km" if last_km else ""
            lines.append(f"  - {item_name}: last done {date_str}{km_str}")
    return "\n".join(lines)


async def get_spider_info(name_filter: str = "") -> str:
    data = await _fetch("bijoux_spiders")
    if not data:
        return "Could not fetch spider data."
    spiders = data
    if name_filter:
        spiders = [s for s in spiders if name_filter.lower() in (s.get("commonName") or "").lower()
                   or name_filter.lower() in (s.get("scientificName") or "").lower()
                   or name_filter.lower() in (s.get("code") or "").lower()]
    if not spiders:
        return f"No spiders found matching '{name_filter}'." if name_filter else "No spiders in collection."
    lines = [f"**Creepy Crawly Crew** ({len(spiders)} spider{'s' if len(spiders) != 1 else ''}):"]
    for s in spiders:
        code = s.get("code", "")
        name = s.get("commonName") or s.get("scientificName") or "Unknown"
        molt_days = _days_since(s.get("lastMoltDate"))
        fed_days = _days_since(s.get("lastFed"))
        molt_str = f"moulted {molt_days}d ago" if molt_days is not None else "no molt date"
        fed_str = f"fed {fed_days}d ago" if fed_days is not None else "not fed recently"
        if fed_days is not None and fed_days > 14:
            fed_str = f"⚠️ {fed_str}"
        prefix = f"{code} " if code else ""
        lines.append(f"  - {prefix}{name}: {molt_str}, {fed_str}")
    return "\n".join(lines)
