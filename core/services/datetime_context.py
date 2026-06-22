from datetime import datetime, timedelta, timezone

SAST = timezone(timedelta(hours=2))


def format_current_datetime() -> str:
    """Current SAST date/time plus an explicit weekday->date lookup table.

    LLMs are unreliable at mentally computing "next Friday" from a weekday name —
    handing over the next two weeks as a literal table means the agent looks the
    date up instead of calculating it.
    """
    now = datetime.now(SAST)
    lines = [f"Current date and time: {now.strftime('%A, %d %B %Y at %H:%M SAST')}"]
    lines.append("")
    lines.append(
        "Upcoming dates — use these exact mappings, never calculate a weekday's date yourself:"
    )
    entries = []
    for i in range(14):
        d = now + timedelta(days=i)
        suffix = " (today)" if i == 0 else " (tomorrow)" if i == 1 else ""
        entries.append(f"{d.strftime('%a %d %b')}{suffix}")
    lines.append(", ".join(entries))
    return "\n".join(lines)
