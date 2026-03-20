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
4. Stores all jobs in SQLite with SHA-256 deduplication by job_link

**Goal:** Fully automated, running on GitHub Actions every 3 hours — no local machine needed.

---

## 🏗️ Stack

| Layer | Tool | Purpose |
|---|---|---|
| Ingestion | Python + Telethon | Read Telegram groups as a user (MTProto) |
| Data Modeling | Pydantic v2 | Validate and structure LLM outputs |
| Scoring | OpenAI GPT-4o mini | Score jobs against portfolio.txt |
| Storage | SQLite | Persist jobs, prevent duplicates |
| Alerts | Telegram Bot API | Send scored job alerts to personal chat |
| Scheduling | GitHub Actions | Run pipeline every 3 hours, free tier |
| Package Manager | uv | Python 3.13, pyproject.toml |

---

## 📁 Project Structure

```
/jobs-ai-scanner
├── engine/
│   ├── listener.py     # Telethon client — fetches messages from Telegram groups
│   ├── models.py       # Pydantic schemas: JobOpportunity, ScoredJob
│   ├── brain.py        # GPT-4o mini scoring logic
│   ├── database.py     # SQLite storage, deduplication by job_link hash
│   └── notify.py       # Telegram Bot alert sender — includes job_link
├── config/
│   ├── portfolio.txt   # Candidate profile — used as LLM scoring context
│   └── groups.txt      # Telegram group usernames/IDs to monitor
├── data/
│   ├── raw_dump.json   # Intermediary: listener → brain (overwritten each run)
│   └── jobs.db         # Persistent job storage (gitignored)
├── main.py             # Orchestrator — runs full pipeline
├── CLAUDE.md           # This file
├── pyproject.toml      # uv dependencies
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
```

Load with: `from dotenv import load_dotenv`

---

## 📋 Telegram Groups (config/groups.txt)

```
hitechjobsjunior
hitechjobsdata
-1002423121294
-1002875221568
```

Note: numeric IDs are private groups. Both formats work with Telethon.
Note: -1002423121294 currently throws PeerChannel error — fix by opening the group in the Telegram app and scrolling once before next run.

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
- Schedule: every 3 hours (`cron: '0 */3 * * *'`)

---

## ✅ Current Build Status

### Stage 1 — Repo & Environment ✅ COMPLETE
- Repo initialized and pushed to GitHub
- All Telegram credentials in `.env`
- All packages installed via uv (telethon, openai, pydantic, python-dotenv, ipykernel)
- portfolio.txt written and structured
- groups.txt populated with 4 groups

### Stage 2 — Ingestion & Data Modeling ✅ COMPLETE
- [x] engine/listener.py written and tested
- [x] First-time phone verification completed — jobpulse_session.session created
- [x] 15 messages fetched from 3/4 groups, saved to raw_dump.json
- [x] engine/models.py written and tested
- [x] field_validator on confidence_score verified
- [x] job_link added as required field

### Stage 3 — Brain, Persistence & Alerts ⏳ PENDING
- [x] Write engine/brain.py 13/15 jobs found 
- [ ] Write engine/database.py
- [ ] Write engine/notify.py
- [ ] Test scoring + alerts end-to-end

### Stage 4 — Orchestration & Deployment ⏳ PENDING
- [ ] Write main.py
- [ ] Write .github/workflows/run_scanner.yml
- [ ] Add GitHub Secrets
- [ ] Confirm automated run on GitHub Actions

---

## 🛠️ Prompts for Each Remaining File

### engine/brain.py
```
Write engine/brain.py using the OpenAI API (GPT-4o mini).
- Load OPENAI_API_KEY from .env
- Load config/portfolio.txt and data/raw_dump.json
- For each message, use GPT-4o mini to:
  1. Determine if it's a job offer (skip if not)
  2. If yes, check if the message contains an apply link (job_link)
     — if no link found, discard the message entirely
  3. If link exists, parse the message into a JobOpportunity object
  4. Compare requirements against portfolio.txt content
  5. Assign a confidence_score (1-10) and fit_reasoning
- Use a system prompt with role: "Expert Technical Recruiter"
- Messages may be in Hebrew, English, or mixed — handle both
- Return a list of ScoredJob objects
- Import JobOpportunity and ScoredJob from engine/models.py
- Wrap each ScoredJob(...) creation in try/except ValidationError
  — skip bad LLM responses gracefully, never crash the pipeline
- Add # %% cell markers
```

### engine/database.py + engine/notify.py
```
Create engine/database.py:
- Setup a SQLite database at data/jobs.db
- Create a table: jobs with columns:
  job_hash TEXT PRIMARY KEY, title TEXT, company TEXT,
  confidence_score INTEGER, fit_reasoning TEXT,
  contact_info TEXT, job_link TEXT, timestamp TEXT
- Use SHA-256 hash of job_link as job_hash
  (deduplicates correctly when same job is posted across multiple groups)
- Functions: init_db(), is_duplicate(hash), save_job(ScoredJob)

Create engine/notify.py:
- Load TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from .env
- Function send_alert(job: ScoredJob) that sends a formatted Telegram
  message including: title, company, score, fit_reasoning, contact_info, job_link
- Only send if confidence_score > 7
- Add # %% cell markers
```

### main.py
```
Write main.py as the orchestrator for the full JobPulse pipeline:
1. Call listener.py → fetch messages → save to data/raw_dump.json
2. Call brain.py → score messages → return list of ScoredJob objects
3. For each ScoredJob:
   - Call database.py: check if job_hash already exists
   - If new: save to jobs.db
   - If score > 7: call notify.py to send Telegram alert
4. Print a summary log: "X messages scanned, Y job offers found, Z alerts sent"
5. Handle errors gracefully — one failed group should not crash the whole run
```

### .github/workflows/run_scanner.yml
```
Write a GitHub Actions workflow file at .github/workflows/run_scanner.yml that:
- Triggers on a schedule every 3 hours (cron)
- Also has a manual trigger (workflow_dispatch)
- Runs on ubuntu-latest with Python 3.11
- Installs dependencies via pip from pyproject.toml
- Runs python main.py
- Injects these secrets as env variables: OPENAI_API_KEY,
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

---

## 🔭 Future Scaling (Post-MVP)

| Feature | Description |
|---|---|
| Keyword Trends | Analyze jobs.db for most in-demand skills |
| CV Recommendations | LLM compares job patterns against portfolio.txt |
| Fit Score Tuning | Review scoring history, refine prompts |
| Multi-source Ingestion | Add LinkedIn RSS or other sources to listener.py |

---

## 🚨 Open Tasks — Fix Before Deploying

### 1. Optimised Listening — Skip Already-Scanned Messages

**Problem:** Every run fetches and scores all messages from each group, even ones already processed in previous runs. With 150 messages and a 3-hour schedule, this means ~1,200 redundant LLM calls per day — wasted cost and time.

**Solution:** Track the last seen Telegram message ID per group in `jobs.db`. On each run, only fetch messages newer than that ID.

**Why `min_id` works:** Every Telegram message has a unique integer ID that increments over time. Fetching with `min_id=last_seen_id` returns only messages posted after that point — guaranteed to be new.

**Implementation — two steps:**

Step 1: Add a new table to `database.py`:
```python
# in database.py — add alongside the existing jobs table
# stores the highest message ID seen per group so listener.py knows where to resume
CREATE TABLE IF NOT EXISTS last_seen (
    group_id TEXT PRIMARY KEY,
    last_message_id INTEGER
)

# add two functions:
def load_last_seen_id(group_id: str) -> int:
    # returns last saved message ID for this group, or 0 if first run
    
def save_last_seen_id(group_id: str, last_id: int) -> None:
    # upserts (insert or replace) the latest message ID for this group
```

Step 2: Update `listener.py` to use `min_id`:
```python
# in listener.py — update fetch logic per group
from engine.database import load_last_seen_id, save_last_seen_id

# before fetching:
last_seen_id = load_last_seen_id(group)  # read last checkpoint from jobs.db

# pass min_id to Telethon so it only returns messages newer than last run:
messages = await client.get_messages(group, limit=100, min_id=last_seen_id)

# after fetching — save the highest ID seen so next run starts from here:
if messages:
    save_last_seen_id(group, messages[0].id)  # messages[0] is the newest (Telethon returns newest first)
```

**Expected result:** After the first full run, each subsequent run processes only 3-10 new messages per group instead of 150 — dramatically cutting LLM cost and run time.

**When to implement:** After Stage 3 (brain, database, notify) is complete and tested end-to-end. Do not implement before `database.py` exists.
