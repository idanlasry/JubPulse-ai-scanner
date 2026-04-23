# %%
import hashlib
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from supabase import create_client
from urlextract import URLExtract

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_supabase = create_client(_SUPABASE_URL, _SUPABASE_KEY) if _SUPABASE_URL and _SUPABASE_KEY else None

_extractor = URLExtract()

_NON_JOB_KEYWORDS: frozenset[str] = frozenset([
    # Hebrew — workshop/event/course signals
    "סדנה", "ווביינר", "הרצאה", "קורס", "להרשמה", "הרשמה",
    # English
    "workshop", "webinar", "seminar", "register now", "registration",
    "sign up", "buy now", "purchase", "discount", "coupon",
    "advertisement",
])

# Domains that only ever carry ads/services, never job postings
_NON_JOB_DOMAINS: frozenset[str] = frozenset([
    "tech-cv.com",
    "secrethuntercv.lovable.app",
])

# URL prefixes that indicate non-job content on otherwise-valid domains
# (e.g. LinkedIn feed posts vs. LinkedIn job listings)
_NON_JOB_URL_PREFIXES: tuple[str, ...] = (
    "https://www.linkedin.com/feed/update/",
    "https://linkedin.com/feed/update/",
)

_JOB_SAFEGUARDS: frozenset[str] = frozenset([
    # Hebrew — job-posting signals that override the blocklist
    "דרוש", "דרושה", "דרושים", "מגייסים", "משרה", "תפקיד",
    # English
    "hiring", "we're hiring", "job opening", "open position",
    "full-time", "part-time", "apply now",
])


# %%
def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _normalize(url: str) -> str:
    return url.rstrip("/").lower()


def _url_dedup_key(url: str) -> str:
    """Dedup key for intra-batch: scheme + host + path, query/fragment stripped."""
    p = urlparse(url.lower())
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def _dedup_batch(messages: list[dict]) -> tuple[list[dict], int]:
    """Within one batch, keep one message per URL — the one with the longest text."""
    seen: dict[str, int] = {}  # dedup key -> index in unique
    unique: list[dict] = []
    intra_count = 0
    for msg in messages:
        raw_text: str = msg.get("text", "")
        http_urls = [u for u in _extractor.gen_urls(raw_text) if u.startswith("http")]
        key = _url_dedup_key(http_urls[0]) if http_urls else None
        if key is None or key not in seen:
            if key is not None:
                seen[key] = len(unique)
            unique.append(msg)
        else:
            existing_idx = seen[key]
            if len(raw_text) > len(unique[existing_idx].get("text", "")):
                unique[existing_idx] = msg
            intra_count += 1
    return unique, intra_count


def _has_non_job_domain(http_urls: list[str]) -> bool:
    for url in http_urls:
        host = urlparse(url).netloc.removeprefix("www.")
        if host in _NON_JOB_DOMAINS:
            return True
        if any(url.startswith(prefix) for prefix in _NON_JOB_URL_PREFIXES):
            return True
    return False


def _is_non_job(text: str, http_urls: list[str] | None = None) -> bool:
    # Domain blocklist is a hard exclusion — job safeguards cannot override it
    if http_urls and _has_non_job_domain(http_urls):
        return True
    lower = text.lower()
    has_non_job = any(kw in lower for kw in _NON_JOB_KEYWORDS)
    if not has_non_job:
        return False
    has_job_signal = any(kw in lower for kw in _JOB_SAFEGUARDS)
    return not has_job_signal


def _load_known_data() -> tuple[set[str], set[str], bool]:
    if _supabase is None:
        logging.warning("[checker] Supabase client not initialised — skipping dedup gate")
        return set(), set(), False
    try:
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
                break
            page += 1
        logging.info("[checker] Loaded %d known hashes from Supabase", len(known_hashes))
        return known_hashes, known_links, True
    except Exception as e:
        logging.error("[checker] Failed to load data from Supabase: %s", e)
        return set(), set(), False


# %%
def filter_new_messages(messages: list[dict]) -> tuple[list[dict], int, int, int, int, bool]:
    known_hashes, known_links, checker_available = _load_known_data()

    fresh: list[dict] = []
    no_link_count = 0
    non_job_count = 0
    duplicate_count = 0

    for msg in messages:
        raw_text: str = msg.get("text", "")
        http_urls = [u for u in _extractor.gen_urls(raw_text) if u.startswith("http")]

        if not http_urls:
            no_link_count += 1
            continue

        if _is_non_job(raw_text, http_urls):
            non_job_count += 1
            continue

        is_duplicate = checker_available and any(
            _hash(u) in known_hashes or _normalize(u) in known_links
            for u in http_urls
        )

        if is_duplicate:
            duplicate_count += 1
        else:
            fresh.append(msg)

    fresh, intra_batch_count = _dedup_batch(fresh)

    return fresh, no_link_count, non_job_count, duplicate_count, intra_batch_count, checker_available
