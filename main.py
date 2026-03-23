# %%
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from engine.brain import run_brain
from engine.database import export_to_csv, init_db, is_duplicate, save_job
from engine.listener import load_groups
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
    new_jobs = 0  # counts jobs not seen in previous runs
    new_job_objects: list[ScoredJob] = []
    fitting_jobs: list[ScoredJob] = []

    for job in scored_jobs:
        try:
            job_hash = hashlib.sha256(job.job_link.encode()).hexdigest()

            if is_duplicate(job_hash):
                print(f"[main] Duplicate — skipping: {job.title}")
                continue

            save_job(job)
            new_jobs += 1  # only increments for genuinely new jobs
            new_job_objects.append(job)

            if job.confidence_score > 7:
                fitting_jobs.append(job)

        except Exception as e:
            print(f"[main] Error processing '{job.title}': {e}")

    export_to_csv(new_job_objects)
    print(f"[main] Appended {len(new_job_objects)} new rows → data/jobs.csv")

    # --- Stage 4: Summary first, then per-job alerts ---
    try:
        await send_summary(
            groups_scanned=groups_scanned,
            jobs_found=messages_fetched,  # total messages scanned
            new_jobs=new_jobs,  # fresh jobs this run
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

    # --- Final log ---
    print(
        f"[main] {groups_scanned} groups scanned | "
        f"{messages_fetched} messages fetched | "
        f"{jobs_found} jobs found | "
        f"{alerts_sent} alerts sent"
    )


# %%
if __name__ == "__main__":
    asyncio.run(main())
