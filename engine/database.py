# %%
import csv
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to Python's module search path so engine.models can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from engine.models import ScoredJob
from supabase import create_client

load_dotenv()

# --- Supabase client (module-level) ---
_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY) if _SUPABASE_URL and _SUPABASE_KEY else None

CSV_PATH = Path(__file__).parent.parent / "data" / "jobs.csv"
CSV_HEADERS = [
    "job_hash", "timestamp", "title", "company", "location", "is_junior",
    "tech_stack", "contact_info", "job_link", "raw_text", "confidence_score", "fit_reasoning",
]


# %%
def _hash(job_link: str) -> str:
    # Private helper — converts job_link to a 64-char hex fingerprint
    # Hashing the link (not raw_text) means same job posted in multiple groups = one hash
    return hashlib.sha256(job_link.encode()).hexdigest()


# --- CSV Layer ---

# %%
def save_to_csv(job: ScoredJob) -> bool:
    """Append job to CSV if job_link is not already present. Returns True if new, False if duplicate.

    This is the cross-run dedup layer for GitHub Actions where Supabase may be unavailable.
    jobs.csv is committed to the repo after every run, so it persists across runs.
    """
    CSV_PATH.parent.mkdir(exist_ok=True)

    file_empty = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0

    # Read existing job_links to check for duplicates
    existing_links: set[str] = set()
    if not file_empty:
        try:
            with open(CSV_PATH, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if "job_link" in row:
                        existing_links.add(row["job_link"])
        except Exception:
            pass  # Unreadable CSV treated as empty — will write header

    if job.job_link in existing_links:
        return False  # Duplicate — skip

    # Append new row (write header only if file is new/empty)
    with open(CSV_PATH, "w" if file_empty else "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if file_empty:
            writer.writerow(CSV_HEADERS)
        job_hash = _hash(job.job_link)
        timestamp = datetime.now(timezone.utc).isoformat()
        writer.writerow([
            job_hash,
            timestamp,
            job.title,
            job.company,
            job.location,
            job.is_junior,
            json.dumps(job.tech_stack),  # list → JSON string for CSV storage
            job.contact_info,
            job.job_link,
            job.raw_text,
            job.confidence_score,
            job.fit_reasoning,
        ])
    return True  # New job saved


# --- Supabase Layer ---

# %%
def save_to_supabase(job: ScoredJob, source_group: str) -> bool:
    """Insert job into Supabase jobs table. Returns True if inserted, False if duplicate or error.

    Never raises — all exceptions are caught and logged so this layer cannot block the pipeline.
    Saves the same 12 core fields as CSV, plus: source, source_group, repo, alerted.
    """
    try:
        if _supabase is None:
            logging.warning("save_to_supabase: client not initialised (missing SUPABASE_URL or SUPABASE_KEY)")
            return False

        job_hash = _hash(job.job_link)
        # Use message_date (when Telegram message was posted) as timestamp.
        # Fall back to now() only when message_date is unavailable (e.g. CSV backfill).
        timestamp = job.message_date or datetime.now(timezone.utc).isoformat()
        tech_stack = job.tech_stack if isinstance(job.tech_stack, list) else json.loads(job.tech_stack)

        row = {
            "job_hash": job_hash,
            "timestamp": timestamp,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "is_junior": job.is_junior,
            "tech_stack": tech_stack,
            "contact_info": job.contact_info,
            "job_link": job.job_link,
            "raw_text": job.raw_text,
            "confidence_score": job.confidence_score,
            "fit_reasoning": job.fit_reasoning,
            "source": "telegram",
            "source_group": source_group,
            "repo": "jobpulse",
            "alerted": False,
        }

        _supabase.table("jobs").insert(row).execute()
        return True

    except Exception as exc:
        msg = str(exc)
        # UNIQUE violation: Postgres code 23505, wrapped by PostgREST
        if "23505" in msg or "duplicate" in msg.lower() or "unique" in msg.lower():
            return False  # Known duplicate — no log noise
        logging.error("save_to_supabase error: %s", exc)
        return False
