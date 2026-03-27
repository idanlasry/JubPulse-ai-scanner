# %%
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# Add project root to Python's module search path so engine.models can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.models import ScoredJob

load_dotenv()

# Bot credentials loaded from .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Full API endpoint — token baked in once at module level
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


# %%
def _esc(text: str) -> str:
    # Escape HTML special chars — safe to apply to any dynamic content
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_alert(job: ScoredJob) -> str:
    # HTML parse_mode — more reliable than Markdown because URLs with underscores
    # (e.g. utm_source=telegram) don't break the parser
    lines = [
        f"<b>{_esc(job.title)}</b>",
        f"Company: {_esc(job.company or 'N/A')}",
        f"Score: {job.confidence_score}/10",
        f"Fit: {_esc(job.fit_reasoning)}",
    ]
    if job.contact_info:
        lines.append(f"Contact: {_esc(job.contact_info)}")
    lines.append(f"Apply: {job.job_link}")
    return "\n".join(lines)


# %%
async def send_alert(job: ScoredJob) -> None:
    # Guard — exit immediately for low scoring jobs, no network call made
    if job.confidence_score <= 7:
        return

    text = _format_alert(job)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TELEGRAM_API_URL,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",  # HTML is immune to URL underscores breaking the parser
                },
                timeout=10,  # fail fast — don't hang the pipeline
            )
            response.raise_for_status()  # raises exception on 4xx/5xx HTTP errors
            print(f"[notify] Alert sent: {job.title} (score={job.confidence_score})")

    except httpx.HTTPStatusError as e:
        print(f"[notify] HTTP error sending alert for '{job.title}': {e}")
    except httpx.RequestError as e:
        print(f"[notify] Network error sending alert for '{job.title}': {e}")
    except Exception as e:
        print(f"[notify] Unexpected error sending alert for '{job.title}': {e}")


# %%
async def send_error_alert(text: str) -> None:
    """Send a plain-text error notification to the user's Telegram chat."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TELEGRAM_API_URL,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            response.raise_for_status()
            print("[notify] Error alert sent")
    except Exception as e:
        print(f"[notify] Could not send error alert: {e}")


# %%
async def send_summary(
    groups_scanned: int,
    jobs_found: int,
    new_jobs: int,  # new addition — jobs not seen in previous runs
    fitting_jobs: list[ScoredJob],
) -> None:
    fitting_count = len(fitting_jobs)
    run_time = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")

    lines = [
        "<b>JobPulse Run Summary</b>",
        f"Date: {run_time}",
        f"Groups scanned: {groups_scanned}",
        f"Messages scanned: {jobs_found}",
        f"New jobs (not seen before): {new_jobs}",
        f"High-fit alerts sent (score &gt; 7): {fitting_count}",
    ]

    text = "\n".join(lines)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                TELEGRAM_API_URL,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            response.raise_for_status()
            print(f"[notify] Summary sent — {fitting_count} fitting jobs")

    except httpx.HTTPStatusError as e:
        print(f"[notify] HTTP error sending summary: {e}")
    except httpx.RequestError as e:
        print(f"[notify] Network error sending summary: {e}")
    except Exception as e:
        print(f"[notify] Unexpected error sending summary: {e}")


# %%
if __name__ == "__main__":
    # Test harness — runs notify flow directly against scored_dump.json
    import json

    scored_dump = Path(__file__).parent.parent / "data" / "scored_dump.json"
    data = json.loads(scored_dump.read_text(encoding="utf-8"))

    jobs = []
    for item in data:
        try:
            jobs.append(ScoredJob(**item))
        except Exception as e:
            print(f"[notify] Skipping malformed entry: {e}")

    eligible = [j for j in jobs if j.confidence_score > 7]
    print(f"[notify] {len(eligible)}/{len(jobs)} jobs qualify (score > 7)")

    async def _run() -> None:
        # Summary first — overview before details land
        await send_summary(
            groups_scanned=4,  # placeholder — main.py passes the real count
            jobs_found=len(jobs),
            new_jobs=len(eligible),
            fitting_jobs=eligible,
        )
        for job in eligible:
            await send_alert(job)

    asyncio.run(_run())
