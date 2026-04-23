# %%
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from engine.brain import run_brain
from engine.checker import filter_new_messages
from engine.database import save_to_csv, save_to_supabase
from engine.listener import load_groups, load_last_seen, save_last_seen
from engine.listener import main as listener_main
from engine.models import ScoredJob
from engine.notify import send_alert, send_error_alert, send_summary

load_dotenv()

RAW_DUMP = Path(__file__).parent / "data" / "raw_dump.json"
SCORED_DUMP_FILE = Path(__file__).parent / "data" / "scored_dump.json"


# %%
async def main() -> None:
    groups = load_groups()
    groups_scanned = len(groups)
    print(f"[main] Starting pipeline — {groups_scanned} groups to scan")

    try:
        await listener_main(limit=50)
    except Exception as e:
        print(f"[main] Listener failed: {e}")
        return  # raw_dump.json would be stale or missing — cannot proceed

    try:
        raw_messages = json.loads(RAW_DUMP.read_text(encoding="utf-8"))
        messages_fetched = len(raw_messages)
    except Exception as e:
        print(f"[main] Could not read raw_dump.json: {e}")
        raw_messages = []
        messages_fetched = 0

    print(f"[main] {messages_fetched} messages fetched")

    all_raw_messages = raw_messages  # preserve full list for last_seen checkpoint
    checker_available = False
    no_link_skipped = 0
    non_job_skipped = 0
    duplicate_skipped = 0
    intra_batch_skipped = 0
    try:
        raw_messages, no_link_skipped, non_job_skipped, duplicate_skipped, intra_batch_skipped, checker_available = (
            filter_new_messages(raw_messages)
        )
        gate_status = (
            "active" if checker_available else "offline (Supabase unavailable)"
        )
        print(
            f"[checker] {no_link_skipped} no-link | {non_job_skipped} non-job | {duplicate_skipped} duplicates | {intra_batch_skipped} intra-batch | {len(raw_messages)} passed to brain | gate: {gate_status}"
        )
        RAW_DUMP.write_text(
            json.dumps(raw_messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[main] Checker failed — passing all messages to brain: {e}")

    try:
        scored_jobs = run_brain()
    except Exception as e:
        print(f"[main] Brain failed: {e}")
        scored_jobs = []

    jobs_found = len(scored_jobs)

    try:
        SCORED_DUMP_FILE.write_text(
            json.dumps(
                [json.loads(job.model_dump_json()) for job in scored_jobs],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"[main] Saved {jobs_found} scored jobs → {SCORED_DUMP_FILE}")
    except Exception as e:
        print(f"[main] Could not write scored_dump.json: {e}")

    alerts_sent = 0
    supabase_new = 0
    supabase_errors = 0
    csv_new = 0
    fitting_jobs: list[ScoredJob] = []

    for job in scored_jobs:
        csv_ok = False
        try:
            csv_ok = save_to_csv(job)
            if csv_ok:
                csv_new += 1
            else:
                print(f"[main] CSV duplicate — skipping alert: {job.title}")
        except Exception as e:
            print(f"[main] CSV error for '{job.title}': {e}")

        if csv_ok and job.confidence_score > 7:
            fitting_jobs.append(job)

        try:
            ok = save_to_supabase(job, source_group=job.source_group or "unknown")
            if ok:
                supabase_new += 1
                print(f"[main] Supabase: saved — {job.title}")
            else:
                print(f"[main] Supabase: duplicate/error — {job.title}")
        except Exception as e:
            supabase_errors += 1
            print(f"[main] Supabase error for '{job.title}': {e}")

    # Alert user if Supabase was unreachable (read) or had write failures
    if not checker_available or supabase_errors > 0:
        lines = ["⚠️ <b>JobPulse — Supabase issue</b>"]
        if not checker_available:
            lines.append(
                "• Dedup gate offline — Supabase unreachable at read time. Duplicate jobs may have been scored."
            )
        if supabase_errors > 0:
            lines.append(f"• {supabase_errors} job(s) failed to save to Supabase.")
        try:
            await send_error_alert("\n".join(lines))
        except Exception as e:
            print(f"[main] Could not send Supabase error alert: {e}")

    try:
        await send_summary(
            groups_scanned=groups_scanned,
            jobs_found=messages_fetched,  # total messages scanned
            new_jobs=csv_new,  # fresh jobs this run (CSV is cross-run truth)
            fitting_jobs=fitting_jobs,
            supabase_new=supabase_new,
            supabase_errors=supabase_errors,
            no_link_skipped=no_link_skipped,
            non_job_skipped=non_job_skipped,
            duplicate_skipped=duplicate_skipped,
            intra_batch_skipped=intra_batch_skipped,
            brain_scored=jobs_found,  # actual ScoredJob outputs from brain
            checker_available=checker_available,
        )
    except Exception as e:
        print(f"[main] Summary failed: {e}")

    for job in fitting_jobs:
        try:
            await send_alert(job)
            alerts_sent += 1
        except Exception as e:
            print(f"[main] Alert failed for '{job.title}': {e}")

    try:
        # Use pre-checker messages so checkpoint advances for duplicates too
        checkpoint_messages = (
            all_raw_messages
            if all_raw_messages
            else json.loads(RAW_DUMP.read_text(encoding="utf-8"))
        )
        new_last_seen: dict[str, datetime] = load_last_seen()
        for msg in checkpoint_messages:
            group_id = msg["group"]
            ts = datetime.fromisoformat(msg["timestamp"])
            if group_id not in new_last_seen or ts > new_last_seen[group_id]:
                new_last_seen[group_id] = ts
        if new_last_seen:
            save_last_seen(new_last_seen)
    except Exception as e:
        print(f"[main] Could not update last_seen: {e}")

    print(
        f"[main] {groups_scanned} groups scanned | "
        f"{messages_fetched} fetched | "
        f"{no_link_skipped} no-link | {non_job_skipped} non-job | {duplicate_skipped} duplicates | {intra_batch_skipped} intra-batch | "
        f"{jobs_found} scored | "
        f"supabase_new={supabase_new} csv_new={csv_new} | "
        f"{alerts_sent} alerts sent"
    )


# %%
if __name__ == "__main__":
    asyncio.run(main())
