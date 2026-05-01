# %%
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

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
    supabase_new: int = 0,    # jobs inserted into Supabase this run
    supabase_errors: int = 0, # Supabase write failures this run
    checker_skipped: int = 0, # messages dropped by pre-LLM dedup gate
) -> None:
    fitting_count = len(fitting_jobs)
    run_time = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    passed_to_brain = jobs_found - checker_skipped

    if supabase_errors == 0:
        db_status = f"✅ Supabase synced: {supabase_new} new"
    else:
        db_status = f"⚠️ Supabase: {supabase_new} saved, {supabase_errors} failed"

    lines = [
        "<b>JobPulse Run Summary</b>",
        f"Date: {run_time}",
        f"Groups scanned: {groups_scanned}",
        f"Messages fetched: {jobs_found} → {checker_skipped} skipped by checker → {passed_to_brain} scored",
        f"New jobs (not seen before): {new_jobs}",
        f"High-fit alerts sent (score &gt; 7): {fitting_count}",
        db_status,
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
async def send_proposals(proposals_path: Path) -> None:
    """Send tuning proposals to Telegram with inline approve/reject buttons.

    Each proposal gets its own message with ✅ Apply / ❌ Skip inline keyboard.
    Fire-and-forget — no bot callback handler required; buttons expire gracefully.
    """
    try:
        data = json.loads(proposals_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[notify] Could not read proposals file: {e}")
        return

    proposals_list = data.get("proposals", [])
    if not proposals_list:
        print("[notify] No proposals to send")
        return

    severity_order = {"high": 0, "medium": 1, "low": 2}
    sorted_proposals = sorted(
        proposals_list,
        key=lambda p: severity_order.get(p.get("severity", "low"), 2),
    )

    run_date = data.get("run_timestamp", "")[:10]
    summary = _esc(data.get("summary", f"{len(proposals_list)} proposals"))
    header = f"<b>JobPulse Audit — {run_date}</b>\n{summary}"
    await send_error_alert(header)

    severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    inline_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for proposal in sorted_proposals:
        pid = proposal.get("id", 0)
        sev = proposal.get("severity", "low")
        ptype = proposal.get("type", "unknown")
        text = (
            f"{severity_emoji.get(sev, '⚪')} <b>{_esc(proposal.get('title', ''))}</b>\n"
            f"Type: {ptype} | Severity: {sev}\n\n"
            f"{_esc(proposal.get('detail', ''))}\n\n"
            f"<i>Action: {_esc(proposal.get('action', ''))}</i>"
        )
        reply_markup = json.dumps({
            "inline_keyboard": [[
                {"text": "✅ Apply", "callback_data": f"apply:{pid}"},
                {"text": "❌ Skip",  "callback_data": f"skip:{pid}"},
            ]]
        })
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    inline_url,
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup,
                    },
                    timeout=10,
                )
                response.raise_for_status()
                print(f"[notify] Proposal {pid} sent ({sev})")
        except Exception as e:
            print(f"[notify] Failed to send proposal {pid}: {e}")


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
