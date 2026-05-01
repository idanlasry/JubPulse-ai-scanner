# %%
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from supabase import create_client
from urlextract import URLExtract

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY) if _SUPABASE_URL and _SUPABASE_KEY else None

_extractor = URLExtract()

_CHECKER_STATS_FILE = Path(__file__).parent.parent / "data" / "checker_stats.json"


# %%
def _hash(url: str) -> str:
    # Must mirror database.py _hash() exactly — same algorithm, same encoding
    return hashlib.sha256(url.encode()).hexdigest()


def _normalize(url: str) -> str:
    # Strip trailing slash and lowercase for consistent comparison.
    # Applied to both stored job_links and extracted URLs so minor formatting
    # differences (trailing slash, case) don't cause missed duplicates.
    return url.rstrip("/").lower()


def _load_known_data() -> tuple[set[str], set[str]]:
    """Fetch all job_hash and job_link values from Supabase jobs table.

    Returns (known_hashes, known_links) — both empty sets if Supabase is unavailable,
    causing all messages to pass through rather than risking false duplicate skips.

    Two sets are returned to handle URL mismatch between urlextract and GPT:
    - known_hashes: SHA256 of stored job_link — fast O(1) lookup for exact matches
    - known_links: normalized stored job_links — fallback for cases where urlextract
      extracts a slightly different URL string than GPT used (e.g. stripped query params)
    """
    if _supabase is None:
        logging.warning("[checker] Supabase client not initialised — skipping dedup gate")
        return set(), set()
    try:
        # Supabase REST pagination: max 1000 rows per request
        known_hashes: set[str] = set()
        known_links: set[str] = set()
        page = 0
        page_size = 1000
        while True:
            response = (
                _supabase.table("jobs")
                .select("job_hash,job_link")
                .range(page * page_size, (page + 1) * page_size - 1)
                .execute()
            )
            rows = response.data or []
            for row in rows:
                if row.get("job_hash"):
                    known_hashes.add(row["job_hash"])
                if row.get("job_link"):
                    known_links.add(_normalize(row["job_link"]))
            if len(rows) < page_size:
                break  # last page
            page += 1
        print(f"[checker] Loaded {len(known_hashes)} known hashes from Supabase")
        return known_hashes, known_links
    except Exception as e:
        logging.error("[checker] Failed to load data from Supabase: %s", e)
        return set(), set()  # fail open — let everything through to the LLM


# %%
def filter_new_messages(messages: list[dict]) -> tuple[list[dict], int]:
    """Remove messages whose job link already exists in Supabase.

    Extracts all http-prefixed URLs from each message's raw text, hashes each one,
    and checks against the known hashes from Supabase. A message is considered a
    duplicate if ANY of its extracted URLs matches a stored hash.

    Returns:
        (fresh_messages, skipped_count)
        fresh_messages — messages that passed the gate (no URL matched a known hash)
        skipped_count  — number of messages dropped as duplicates
    """
    known_hashes, known_links = _load_known_data()

    if not known_hashes and not known_links:
        # Supabase unavailable or empty DB (first run) — pass everything through
        return messages, 0

    fresh: list[dict] = []
    skipped = 0
    filtered_entries: list[dict] = []

    for msg in messages:
        raw_text: str = msg.get("text", "")
        http_urls = [u for u in _extractor.gen_urls(raw_text) if u.startswith("http")]

        if not http_urls:
            # No extractable link — brain requires job_link, so this message is useless
            skipped += 1
            filtered_entries.append({"reason": "no_link", "text": raw_text[:200]})
            continue

        is_duplicate = any(
            _hash(u) in known_hashes or _normalize(u) in known_links
            for u in http_urls
        )

        if is_duplicate:
            skipped += 1
            print(f"[checker] Duplicate — skipping: {http_urls[0][:80]}")
            filtered_entries.append({
                "reason": "duplicate_url",
                "url": http_urls[0][:200],
                "text": raw_text[:200],
            })
        else:
            fresh.append(msg)

    try:
        stats = {
            "run_timestamp": datetime.now(timezone(timedelta(hours=3))).isoformat(),
            "total_input": len(messages),
            "total_passed": len(fresh),
            "total_skipped": skipped,
            "filtered": filtered_entries,
        }
        _CHECKER_STATS_FILE.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logging.warning("[checker] Could not write checker_stats.json: %s", e)

    return fresh, skipped
