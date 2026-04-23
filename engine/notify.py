# %%
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.models import ScoredJob

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


# %%
def _esc(text: str) -> str:
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


async def _post(payload: dict) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(TELEGRAM_API_URL, json=payload, timeout=10)
        r.raise_for_status()


# %%
async def send_alert(job: ScoredJob) -> None:
    if job.confidence_score <= 7:
        return
    try:
        await _post({"chat_id": TELEGRAM_CHAT_ID, "text": _format_alert(job), "parse_mode": "HTML"})
    except httpx.HTTPStatusError as e:
        print(f"[notify] HTTP error sending alert for '{job.title}': {e}")
    except httpx.RequestError as e:
        print(f"[notify] Network error sending alert for '{job.title}': {e}")
    except Exception as e:
        print(f"[notify] Unexpected error sending alert for '{job.title}': {e}")


# %%
async def send_error_alert(text: str) -> None:
    try:
        await _post({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except Exception as e:
        print(f"[notify] Could not send error alert: {e}")


# %%
async def send_summary(
    groups_scanned: int,
    jobs_found: int,
    new_jobs: int,
    fitting_jobs: list[ScoredJob],
    supabase_new: int = 0,
    supabase_errors: int = 0,
    no_link_skipped: int = 0,
    non_job_skipped: int = 0,
    duplicate_skipped: int = 0,
    intra_batch_skipped: int = 0,
    brain_scored: int = 0,
    checker_available: bool = True,
) -> None:
    fitting_count = len(fitting_jobs)
    run_time = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    passed_to_brain = jobs_found - no_link_skipped - non_job_skipped - duplicate_skipped - intra_batch_skipped

    db_status = (
        f"✅ Supabase synced: {supabase_new} new"
        if supabase_errors == 0
        else f"⚠️ Supabase: {supabase_new} saved, {supabase_errors} failed"
    )
    dedup_note = f"{duplicate_skipped} duplicates" if checker_available else f"{duplicate_skipped} duplicates ⚠️ gate offline"
    text = "\n".join([
        "<b>JobPulse Run Summary</b>",
        f"Date: {run_time}",
        f"Groups scanned: {groups_scanned}",
        f"Fetched: {jobs_found} → {no_link_skipped} no-link | {non_job_skipped} non-job | {dedup_note} | {intra_batch_skipped} intra-batch → {passed_to_brain} to brain → {brain_scored} scored",
        f"New jobs (not seen before): {new_jobs}",
        f"High-fit alerts (score &gt; 7): {fitting_count}",
        db_status,
    ])

    try:
        await _post({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
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
        await send_summary(
            groups_scanned=4,
            jobs_found=len(jobs),
            new_jobs=len(eligible),
            fitting_jobs=eligible,
        )
        for job in eligible:
            await send_alert(job)

    asyncio.run(_run())
