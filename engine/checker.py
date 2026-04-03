# %%
import hashlib
import logging
import os
import sys
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


# %%
def _hash(url: str) -> str:
    # Must mirror database.py _hash() exactly — same algorithm, same encoding
    return hashlib.sha256(url.encode()).hexdigest()


def _normalize(url: str) -> str:
    # Strip trailing slash and lowercase for consistent comparison.
    # Applied to both stored job_links and extracted URLs so minor formatting
    # differences (trailing slash, case) don't cause missed duplicates.
    return url.rstrip("/").lower()


def _load_known_data() -> tuple[set[str], set[str], bool]:
    """Fetch all job_hash and job_link values from Supabase jobs table.

    Returns (known_hashes, known_links, supabase_reachable).
    supabase_reachable is True if the query succeeded (even if 0 rows returned),
    False if Supabase is unconfigured or the query threw an exception.

    Two sets are returned to handle URL mismatch between urlextract and GPT:
    - known_hashes: SHA256 of stored job_link — fast O(1) lookup for exact matches
    - known_links: normalized stored job_links — fallback for cases where urlextract
      extracts a slightly different URL string than GPT used (e.g. stripped query params)
    """
    if _supabase is None:
        logging.warning("[checker] Supabase client not initialised — skipping dedup gate")
        return set(), set(), False
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
        return known_hashes, known_links, True  # query succeeded, even if DB is empty
    except Exception as e:
        logging.error("[checker] Failed to load data from Supabase: %s", e)
        return set(), set(), False  # fail open — let everything through to the LLM


# %%
def filter_new_messages(messages: list[dict]) -> tuple[list[dict], int, int, bool]:
    """Remove messages whose job link already exists in Supabase, and drop linkless messages.

    Extracts all http-prefixed URLs from each message's raw text, hashes each one,
    and checks against the known hashes from Supabase. A message is considered a
    duplicate if ANY of its extracted URLs matches a stored hash.

    Returns:
        (fresh_messages, no_link_count, duplicate_count, checker_available)
        fresh_messages     — messages that passed both gates
        no_link_count      — messages dropped because no http URL was extractable
        duplicate_count    — messages dropped because a URL matched a known Supabase hash
        checker_available  — False if Supabase was unavailable (duplicate gate bypassed)
    """
    known_hashes, known_links, checker_available = _load_known_data()

    fresh: list[dict] = []
    no_link_count = 0
    duplicate_count = 0

    for msg in messages:
        raw_text: str = msg.get("text", "")
        http_urls = [u for u in _extractor.gen_urls(raw_text) if u.startswith("http")]

        if not http_urls:
            # No extractable link — brain requires job_link, so this message is useless
            no_link_count += 1
            continue

        is_duplicate = checker_available and any(
            _hash(u) in known_hashes or _normalize(u) in known_links
            for u in http_urls
        )

        if is_duplicate:
            duplicate_count += 1
            print(f"[checker] Duplicate — skipping: {http_urls[0][:80]}")
        else:
            fresh.append(msg)

    return fresh, no_link_count, duplicate_count, checker_available
