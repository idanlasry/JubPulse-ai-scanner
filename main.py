# %%
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from engine.brain import run_brain
from engine.database import init_db, is_duplicate, save_job, save_to_csv
from engine.listener import load_groups, save_last_seen
from engine.listener import main as listener_main
from engine.models import ScoredJob
from engine.notify import send_alert, send_summary

load_dotenv()

RAW_DUMP = Path(__file__).parent / "data" / "raw_dump.json"
SCORED_DUMP_FILE = (
    Path(__file__).parent / "data" / "scored_dump.json"
)  # written after every brain run


# %%
async def main() -> None:
    groups = load_groups()
    groups_scanned = len(groups)
    print(f"[main] Starting pipeline — {groups_scanned} groups to scan")

    # --- Stage 1: Fetch messages ---
    try:
        await listener_main(limit=5)
        # to change messages fetched per group: listener_main(limit=50)
    except Exception as e:
        print(f"[main] Listener failed: {e}")
        return  # raw_dump.json would be stale or missing — cannot proceed

    try:
        messages_fetched = len(json.loads(RAW_DUMP.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"[main] Could not read raw_dump.json: {e}")
        messages_fetched = 0

    print(f"[main] {messages_fetched} messages fetched")

    # --- Stage 2: Score with brain ---
    try:
        scored_jobs = run_brain()
        # to swap model or change scoring: edit engine/brain.py → score_message()
    except Exception as e:
        print(f"[main] Brain failed: {e}")
        scored_jobs = []

    jobs_found = len(scored_jobs)

    # write scored_dump.json after every run — keeps it in sync whether run via main or brain directly
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
    # --- Stage 3: Deduplicate, persist, collect alerts ---
    init_db()

    alerts_sent = 0
    db_new = 0  # jobs new to SQLite (local persistence)
    csv_new = 0  # jobs new to CSV (cross-run persistence on GitHub Actions)
    fitting_jobs: list[ScoredJob] = []

    for job in scored_jobs:
        # SQLite layer — local persistence, ephemeral on GitHub Actions
        try:
            job_hash = hashlib.sha256(job.job_link.encode()).hexdigest()
            if is_duplicate(job_hash):
                print(f"[main] DB duplicate — skipping SQLite: {job.title}")
            else:
                save_job(job)
                db_new += 1
        except Exception as e:
            print(f"[main] DB error for '{job.title}': {e}")

        # CSV layer — cross-run dedup on GitHub Actions (committed to repo)
        try:
            is_new_csv = save_to_csv(job)
            if is_new_csv:
                csv_new += 1
                if job.confidence_score > 7:
                    fitting_jobs.append(job)
            else:
                print(f"[main] CSV duplicate — skipping alert: {job.title}")
        except Exception as e:
            print(f"[main] CSV error for '{job.title}': {e}")

    print(
        f"[main] DB new: {db_new} | CSV new: {csv_new} | Appended {csv_new} rows → data/jobs.csv"
    )

    # --- Stage 4: Summary first, then per-job alerts ---
    try:
        await send_summary(
            groups_scanned=groups_scanned,
            jobs_found=messages_fetched,  # total messages scanned
            new_jobs=csv_new,  # fresh jobs this run (CSV is cross-run truth)
            fitting_jobs=fitting_jobs,
        )
    except Exception as e:
        print(f"[main] Summary failed: {e}")

    for job in fitting_jobs:
        try:
            await send_alert(job)
            alerts_sent += 1
        except Exception as e:
            print(f"[main] Alert failed for '{job.title}': {e}")

    # --- Update last_seen checkpoint (only reached on clean run) ---
    try:
        raw_messages = json.loads(RAW_DUMP.read_text(encoding="utf-8"))
        new_last_seen: dict[str, datetime] = {}
        for msg in raw_messages:
            group_id = msg["group"]
            ts = datetime.fromisoformat(msg["timestamp"])
            if group_id not in new_last_seen or ts > new_last_seen[group_id]:
                new_last_seen[group_id] = ts
        if new_last_seen:
            save_last_seen(new_last_seen)
            print(f"[main] Updated last_seen for {len(new_last_seen)} groups")
    except Exception as e:
        print(f"[main] Could not update last_seen: {e}")

    # --- Final log ---
    print(
        f"[main] {groups_scanned} groups scanned | "
        f"{messages_fetched} messages fetched | "
        f"{jobs_found} jobs found | "
        f"db_new={db_new} csv_new={csv_new} | "
        f"{alerts_sent} alerts sent"
    )


# %%
if __name__ == "__main__":
    asyncio.run(main())
