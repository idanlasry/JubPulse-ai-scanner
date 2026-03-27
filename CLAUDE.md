# JobPulse — Claude Code Context File

> This file is read automatically by Claude Code at the start of every session.
> It contains full project context, architecture, current build status, and design decisions.
> Read this before touching any file.

---

## 🧠 Project Overview

**JobPulse** is an automated pipeline that:
1. Monitors Telegram job groups using Telethon (MTProto API)
2. Scores job offers against a Data Analyst portfolio using GPT-4o mini
3. Sends high-scoring alerts via Telegram Bot to a personal chat
4. Stores all jobs in two independent layers: CSV (cross-run backup, committed to repo) + Supabase (primary DB, cloud-hosted)

**Goal:** Fully automated, running on GitHub Actions 3× daily on weekdays — no local machine needed.

---

## 🏗️ Stack

| Layer | Tool | Purpose |
|---|---|---|
| Ingestion | Python + Telethon | Read Telegram groups as a user (MTProto) |
| Data Modeling | Pydantic v2 | Validate and structure LLM outputs |
| Scoring | OpenAI GPT-4o mini | Score jobs against portfolio.txt |
| Storage (primary) | Supabase (PostgreSQL) | Cloud DB — persists across all runs, queryable |
| Storage (backup) | CSV (`data/jobs.csv`) | Committed to repo — fallback if Supabase is unavailable |
| Alerts | Telegram Bot API | Send scored job alerts to personal chat |
| Scheduling | GitHub Actions | Run pipeline 3× daily on weekdays (Mon–Fri), free tier |
| Package Manager | uv | Python 3.13, pyproject.toml |

---

## 🗄️ Storage Architecture

JobPulse uses two independent storage layers. Neither depends on the other — a failure in one must never block the other.

**Core schema — both layers store the same 12 columns:**

| Column | Type | Notes |
|---|---|---|
| `job_hash` | TEXT | SHA-256 of `job_link` — PRIMARY KEY in Supabase |
| `timestamp` | TEXT/timestamptz | Supabase: Telegram message post time (`message_date`), falls back to processing time. CSV: processing time (UTC ISO) |
| `title` | TEXT | |
| `company` | TEXT | nullable |
| `location` | TEXT | nullable |
| `is_junior` | bool | Supabase: boolean; CSV: True/False string |
| `tech_stack` | TEXT/text[] | Supabase: native array; CSV: JSON-encoded list |
| `contact_info` | TEXT | nullable |
| `job_link` | TEXT | dedup key |
| `raw_text` | TEXT | |
| `confidence_score` | INTEGER | 1–10 |
| `fit_reasoning` | TEXT | |

**Supabase-only columns (pipeline metadata, not in CSV):**

| Column | Type | Notes |
|---|---|---|
| `source` | TEXT | Always `"telegram"` |
| `source_group` | TEXT | Telegram group the job was fetched from |
| `repo` | TEXT | Always `"jobpulse"` |
| `alerted` | boolean | `false` on insert — reserved for future alert-tracking logic |

**CSV layer (`data/jobs.csv`) — cross-run backup**
- Committed to the repo after every GitHub Actions run
- Dedup key: `job_link` (exact string match, checked before every append)
- Append-only — new rows are added; existing rows are never rewritten
- Header is written only when the file is new or empty
- Implemented in `engine/database.py` → `save_to_csv(job: ScoredJob) -> bool`
  - Returns `True` if the job was new and appended, `False` if it was a duplicate and skipped
- Alert eligibility is determined by the CSV layer: only CSV-new jobs with `confidence_score > 7` trigger a Telegram alert

**Supabase layer — primary DB**
- Cloud PostgreSQL, persists across all runs including GitHub Actions
- Dedup key: SHA-256 hash of `job_link` (stored as `job_hash` PRIMARY KEY)
- Implemented in `engine/database.py` → `save_to_supabase(job: ScoredJob, source_group: str) -> bool`
  - Returns `True` if inserted, `False` if duplicate or error — never raises
  - UNIQUE violation (Postgres code 23505) → silent `False`
  - Any other error → logs via `logging.error`, returns `False`
- Credentials: `SUPABASE_URL` + `SUPABASE_KEY` in `.env` and GitHub Secrets
- Client initialised at module level: `_supabase = create_client(...)` — `None` if env vars missing

**How they interact in `main.py`:**
Each scored job is written to both layers independently, each wrapped in its own `try/except`. A Supabase write failure does not affect the CSV write, and vice versa. If any Supabase errors occur during a run, a Telegram error alert is sent via `send_error_alert()`.

---

## 📁 Project Structure

```
/jobs-ai-scanner
├── engine/
│   ├── listener.py     # Telethon client — fetches messages from Telegram groups; load_last_seen() / save_last_seen() for timestamp checkpoints
│   ├── models.py       # Pydantic schemas: JobOpportunity, ScoredJob
│   ├── brain.py        # GPT-4o mini scoring logic
│   ├── database.py     # Dual storage: Supabase (primary) + CSV (backup). Dedup key: job_link. No SQLite.
│   └── notify.py       # Telegram Bot alert sender — send_summary (stats) + send_alert (per job) + send_error_alert (pipeline errors)
│                       # Note: all functions use parse_mode: "HTML" — Markdown breaks on URLs with underscores (e.g. utm_source=telegram)
│                       # Note: send_summary signature: send_summary(groups_scanned, jobs_found, new_jobs, fitting_jobs)
├── config/
│   ├── portfolio.txt   # Candidate profile — used as LLM scoring context
│   └── groups.txt      # Telegram group usernames/IDs to monitor (5 groups, all numeric IDs)
├── data/
│   ├── raw_dump.json   # Intermediary: listener → brain (overwritten each run)
│   ├── scored_dump.json # Intermediary: brain → notify / database (overwritten each run)
│   ├── jobs.csv        # Cross-run job backup — committed to repo, survives GitHub Actions runners
│   └── last_seen.csv   # Checkpoint file — group_id → last_seen_ts (ISO 8601 UTC), committed to repo
├── main.py             # Orchestrator — runs full pipeline
├── notify_all.py       # Standalone script: sends full summary + individual alerts for all high-fit jobs
├── DB_search.py        # Dev utility: query tool (references old SQLite — may need update)
├── connection_test.py  # Dev utility: sends a test message via Telegram Bot API to verify credentials
├── CLAUDE.md           # This file
├── pyproject.toml      # uv dependencies (includes supabase>=2.28.3)
└── .github/
    └── workflows/
        └── run_scanner.yml  # GitHub Actions — scheduled automation
```

---

## 🔑 Environment Variables (.env)

```env
# MTProto API — used by Telethon to READ groups as a user
TELEGRAM_API_ID=
TELEGRAM_API_HASH=

# Bot API — used by notify.py to SEND alerts to personal chat
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# OpenAI — used by brain.py to score job offers
OPENAI_API_KEY=

# Supabase — used by database.py to write to primary DB
SUPABASE_URL=
SUPABASE_KEY=
```

Load with: `from dotenv import load_dotenv`

---

## 📋 Telegram Groups (config/groups.txt)

```
-1002423121294
-1002875221568
-1002375956832
-1002543690045
-1002684951413
```

Note: all groups are private (numeric IDs). Both username and numeric ID formats work with Telethon.

---

## 🧩 Data Models (engine/models.py — Pydantic v2) ✅ COMPLETE

```python
class JobOpportunity(BaseModel):
    title: str
    company: str | None = None
    location: str | None = None
    is_junior: bool
    tech_stack: list[str]
    contact_info: str | None = None
    job_link: str                   # REQUIRED — no default, not optional
    raw_text: str
    message_date: str | None = None  # ISO 8601 UTC — Telegram message post time, set by brain.py
    source_group: str | None = None  # Telegram group the job was fetched from, set by brain.py

class ScoredJob(JobOpportunity):
    confidence_score: int           # 1-10, enforced by field_validator
    fit_reasoning: str
```

### ⚠️ Critical Design Decisions — Do Not Change

**`job_link: str` is required with no default:**
- A job post without an apply link is not actionable — discard it
- `brain.py` must skip any message where GPT cannot extract a `job_link`
- Deduplication in `database.py` hashes by `job_link`, not `raw_text`
  - Reason: same job posted across multiple groups has same link but different raw_text
  - Hashing by `job_link` = one alert per job, regardless of how many groups posted it
- `notify.py` must include `job_link` in the alert message so user can tap and apply

**`confidence_score` validator:**
- Must be int between 1 and 10
- If GPT returns out-of-range value → `ValidationError` raised → object never created
- Wrap all `ScoredJob(...)` creation in `brain.py` with `try/except ValidationError` — skip bad responses, never crash the pipeline

---

## ⚙️ Code Style Rules

- **Async** — use `async/await` and `asyncio.run()` for all Telethon code
- **Type hints** — on all functions
- **Pydantic v2 syntax** — use `model_validator`, `field_validator` (not v1 decorators)
- **Cell markers** — add `# %%` markers for VS Code interactive kernel execution
- **dotenv** — always load `.env` at the top of each engine file
- **uv** — run scripts with `uv run python filename.py`
- **Graceful errors** — one failed group or bad LLM response must never crash the full run

---

## 🗂️ GitHub Setup

- Repo: https://github.com/idanlasry/jobs-ai-scanner
- Secrets stored in: Settings → Secrets and variables → Actions
- Workflow file: `.github/workflows/run_scanner.yml`
- Schedule: Mon–Fri at 08:00, 14:00, 18:00 Israel time (UTC+3) — `cron: '0 5 * * 1-5'`, `'0 11 * * 1-5'`, `'0 15 * * 1-5'`
- `SUPABASE_URL` and `SUPABASE_KEY` must be added to GitHub Secrets for the pipeline to write to Supabase on Actions

---

## ✅ Current Build Status

### Stage 1 — Repo & Environment ✅ COMPLETE
- Repo initialized and pushed to GitHub
- All credentials in `.env`
- All packages installed via uv
- portfolio.txt written and structured
- groups.txt populated with 5 groups

### Stage 2 — Ingestion & Data Modeling ✅ COMPLETE
- [x] engine/listener.py written and tested
- [x] First-time phone verification completed — jobpulse_session.session created
- [x] engine/models.py written and tested
- [x] field_validator on confidence_score verified
- [x] job_link added as required field

### Stage 3 — Brain, Persistence & Alerts ✅ COMPLETE
- [x] engine/brain.py — GPT-4o mini scoring
- [x] engine/database.py — dual storage
- [x] engine/notify.py — Telegram alerts
- [x] End-to-end scoring + alerts tested

### Stage 4 — Orchestration & Deployment ✅ COMPLETE
- [x] main.py written and tested
- [x] .github/workflows/run_scanner.yml written
- [x] GitHub Secrets added
- [x] Automated run confirmed on GitHub Actions

### Stage 5 — Storage & Deduplication ✅ COMPLETE
- [x] Dual storage architecture implemented — CSV + SQLite independent layers
- [x] CSV layer: cross-run deduplication via committed data/jobs.csv
- [x] Pipeline deployed and verified end-to-end

### Stage 6 — Schema Consolidation ✅ COMPLETE
- [x] Unified schema across all storage layers — 12 core columns
- [x] CSV includes job_hash and timestamp

### Stage 7 — Optimised Listening (Checkpoint-Based Skip) ✅ COMPLETE
- [x] `data/last_seen.csv` tracks `last_seen_ts` per group — committed to repo
- [x] `listener.py` loads checkpoint on startup, skips already-processed messages
- [x] `main.py` calls `save_last_seen()` after a clean run
- **Implementation note:** Timestamp-based filtering (not `min_id`) — messages with `date <= last_seen_ts` are skipped

### Stage 8 — Supabase Integration & SQLite Removal ✅ COMPLETE
- [x] Supabase added as primary DB (`engine/database.py` → `save_to_supabase()`)
- [x] SQLite fully removed from pipeline and `database.py` — `init_db`, `save_job`, `is_duplicate` deleted
- [x] `message_date` field added to model — stores Telegram message post time, used as `timestamp` in Supabase
- [x] `source_group` field added to model — threaded from `listener.py` → `brain.py` → `save_to_supabase()`
- [x] `send_error_alert()` added to `notify.py` — fires Telegram alert if Supabase writes fail during a run
- [x] 158-row CSV backfill uploaded to Supabase; all score > 7 rows marked `alerted = true`
- [x] Full pipeline test run passed: Supabase and CSV both updated correctly

---

## 🔭 Future Scaling (Post-MVP)

| Feature | Description |
|---|---|
| Keyword Trends | Query Supabase for most in-demand skills over time |
| CV Recommendations | LLM compares job patterns against portfolio.txt |
| Fit Score Tuning | Review scoring history in Supabase, refine prompts |
| alerted flag wiring | After send_alert() succeeds, UPDATE jobs SET alerted=true WHERE job_hash=... in Supabase |
| Multi-source Ingestion | Add LinkedIn RSS or other sources to listener.py |

---

## 🚨 Open Tasks

> No blocking tasks. The pipeline is fully deployed and running on GitHub Actions.

### Future Improvements (non-blocking)

- **Wire `alerted` flag** — after `send_alert()` succeeds in `main.py`, call `_supabase.table("jobs").update({"alerted": True}).eq("job_hash", job_hash).execute()` to mark the row
- **Raise fetch limit** — `listener.py` currently uses `limit=3` per group. Can be raised to `limit=50` safely — checkpoint-based skipping prevents re-processing old messages
- **Add SUPABASE_URL / SUPABASE_KEY to GitHub Secrets** — required for Supabase writes to work on Actions
