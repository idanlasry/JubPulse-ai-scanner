"""
local/run_local.py — local pipeline runner.

Mirrors main.py exactly, with two additions:
  1. Saves data/raw_dump_unfiltered.json before checker (false-negative detection)
  2. checker.py now also writes data/checker_stats.json (what was filtered and why)

After the run, use the /analyze-run Claude Code skill to audit the results
and generate data/tuning_proposals.json, then run local/finalize.py to
send proposals to Telegram.

Run once:       uv run python local/run_local.py
Via scheduler:  bash local/run_pipeline.sh
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.brain import run_brain
from engine.checker import filter_new_messages
from engine.database import save_to_csv, save_to_supabase
from engine.listener import load_groups, load_last_seen, save_last_seen
from engine.listener import main as listener_main
from engine.models import ScoredJob
from engine.notify import send_alert, send_error_alert, send_summary

load_dotenv()

ROOT = Path(__file__).parent.parent

RAW_DUMP            = ROOT / "data" / "raw_dump.json"
RAW_DUMP_UNFILTERED = ROOT / "data" / "raw_dump_unfiltered.json"
SCORED_DUMP_FILE    = ROOT / "data" / "scored_dump.json"


async def main() -> None:
    groups = load_groups()
    groups_scanned = len(groups)
    print(f"[local] Starting pipeline — {groups_scanned} groups to scan")

    # --- Stage 1: Fetch messages ---
    try:
        await listener_main(limit=50)
    except Exception as e:
        print(f"[local] Listener failed: {e}")
        return

    try:
        raw_messages = json.loads(RAW_DUMP.read_text(encoding="utf-8"))
        messages_fetched = len(raw_messages)
    except Exception as e:
        print(f"[local] Could not read raw_dump.json: {e}")
        raw_messages = []
        messages_fetched = 0

    print(f"[local] {messages_fetched} messages fetched")

    # --- NEW: Save pre-checker dump for false-negative analysis ---
    try:
        RAW_DUMP_UNFILTERED.write_text(
            json.dumps(raw_messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[local] Pre-checker dump saved → {RAW_DUMP_UNFILTERED.name}")
    except Exception as e:
        print(f"[local] Could not write raw_dump_unfiltered.json: {e}")

    # --- Stage 1.5: Pre-LLM dedup gate (also writes data/checker_stats.json) ---
    all_raw_messages = raw_messages
    try:
        raw_messages, skipped_count = filter_new_messages(raw_messages)
        print(f"[checker] {skipped_count} skipped | {len(raw_messages)} passed to brain")
        RAW_DUMP.write_text(
            json.dumps(raw_messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"[local] Checker failed — passing all messages to brain: {e}")
        skipped_count = 0

    # --- Stage 2: Score with brain ---
    try:
        scored_jobs = run_brain()
    except Exception as e:
        print(f"[local] Brain failed: {e}")
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
        print(f"[local] Saved {jobs_found} scored jobs → {SCORED_DUMP_FILE.name}")
    except Exception as e:
        print(f"[local] Could not write scored_dump.json: {e}")

    # --- Stage 3: Deduplicate, persist, collect alerts ---
    alerts_sent = 0
    supabase_new = 0
    supabase_errors = 0
    csv_new = 0
    fitting_jobs: list[ScoredJob] = []

    for job in scored_jobs:
        try:
            is_new_csv = save_to_csv(job)
            if is_new_csv:
                csv_new += 1
                if job.confidence_score > 7:
                    fitting_jobs.append(job)
            else:
                print(f"[local] CSV duplicate — skipping alert: {job.title}")
        except Exception as e:
            print(f"[local] CSV error for '{job.title}': {e}")

        try:
            ok = save_to_supabase(job, source_group=job.source_group or "unknown")
            if ok:
                supabase_new += 1
            else:
                print(f"[local] Supabase: duplicate/error — {job.title}")
        except Exception as e:
            supabase_errors += 1
            print(f"[local] Supabase error for '{job.title}': {e}")

    if supabase_errors > 0:
        try:
            await send_error_alert(
                f"⚠️ <b>JobPulse Local — Supabase error</b>\n"
                f"{supabase_errors} job(s) failed to save this run."
            )
        except Exception as e:
            print(f"[local] Could not send Supabase error alert: {e}")

    # --- Stage 4: Summary, then per-job alerts ---
    try:
        await send_summary(
            groups_scanned=groups_scanned,
            jobs_found=messages_fetched,
            new_jobs=csv_new,
            fitting_jobs=fitting_jobs,
            supabase_new=supabase_new,
            supabase_errors=supabase_errors,
            checker_skipped=skipped_count,
        )
    except Exception as e:
        print(f"[local] Summary failed: {e}")

    for job in fitting_jobs:
        try:
            await send_alert(job)
            alerts_sent += 1
        except Exception as e:
            print(f"[local] Alert failed for '{job.title}': {e}")

    # --- Update last_seen checkpoint ---
    try:
        checkpoint_messages = all_raw_messages or json.loads(RAW_DUMP.read_text(encoding="utf-8"))
        new_last_seen: dict[str, datetime] = load_last_seen()
        for msg in checkpoint_messages:
            group_id = msg["group"]
            ts = datetime.fromisoformat(msg["timestamp"])
            if group_id not in new_last_seen or ts > new_last_seen[group_id]:
                new_last_seen[group_id] = ts
        if new_last_seen:
            save_last_seen(new_last_seen)
            print(f"[local] Updated last_seen for {len(new_last_seen)} groups")
    except Exception as e:
        print(f"[local] Could not update last_seen: {e}")

    print(
        f"[local] Done — {groups_scanned} groups | {messages_fetched} fetched | "
        f"{jobs_found} scored | {alerts_sent} alerts | "
        f"supabase_new={supabase_new} csv_new={csv_new}"
    )
    print("[local] Run /analyze-run in Claude Code to audit this run's results")


if __name__ == "__main__":
    asyncio.run(main())
