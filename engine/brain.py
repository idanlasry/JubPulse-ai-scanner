# %%
# --- IMPORTS & SETUP ---
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

# brain.py lives inside engine/ — this line adds the project root to Python's search path
# so that "from engine.models import ..." works correctly regardless of where you run from
sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.models import (
    JobOpportunity,
    ScoredJob,
)  # OOP models: JobOpportunity (base) → ScoredJob (child)

load_dotenv()  # reads .env file and loads all credentials into environment

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY"
)  # pulls the key from environment into a variable

# Path() builds absolute file paths relative to THIS file's location
# .parent = engine/, .parent.parent = project root
PORTFOLIO_FILE = Path(__file__).parent.parent / "config" / "portfolio.txt"
RAW_DUMP_FILE = Path(__file__).parent.parent / "data" / "raw_dump.json"

client = OpenAI(
    api_key=OPENAI_API_KEY
)  # single OpenAI connection object reused across all API calls


# %%
# --- LOADERS ---
# Two thin functions that only read data — no transformation, no logic
# Their outputs are passed into run_brain() and held in memory for the duration of the run


def load_portfolio() -> str:
    # reads portfolio.txt as a plain string — injected directly into every LLM prompt
    return PORTFOLIO_FILE.read_text(encoding="utf-8")


def load_messages() -> list[dict]:
    # reads raw_dump.json (written by listener.py) and converts it to a Python list of dicts
    # json.loads() = convert JSON string → Python object ("loads" = load from string)
    return json.loads(RAW_DUMP_FILE.read_text(encoding="utf-8"))


# %%
# --- SYSTEM PROMPT ---
# A constant string sent to GPT-4o mini as the "system" role on every single API call
# Defines: persona ("Expert Technical Recruiter"), decision tree, output format, scoring rules
# Double curly braces {{ }} are escape characters — they produce literal { } in the final string
# (needed in case this ever becomes an f-string, where single { } mean "insert variable here")
SYSTEM_PROMPT = """You are an Expert Technical Recruiter evaluating job postings for a specific candidate.
You will be given:
1. A candidate portfolio (skills, experience, preferences)
2. A raw message from a Telegram job group

Your job is to:
1. Decide if the message is a job offer. If not, respond with {{"is_job": false}}.
2. If it is a job offer, check if it contains an application/job link (URL starting with http/https, or t.me, or linkedin.com, or similar).
   — If NO link is found, respond with {{"is_job": false}}.
3. If it's a job with a link, extract all fields and score the fit.

Always respond with valid JSON only — no markdown, no explanation outside the JSON.

Response format when IS a job with a link:
{{
  "is_job": true,
  "title": "...",
  "company": "...",
  "location": "...",
  "is_junior": true,
  "tech_stack": ["Python", "SQL"],
  "contact_info": "...",
  "job_link": "https://...",
  "confidence_score": 7,
  "fit_reasoning": "..."
}}

Response format when NOT a job or no link:
{{
  "is_job": false
}}

Scoring rules:
- confidence_score must be an integer 1-10
- Score based on match between job requirements and the candidate portfolio provided
- null is valid for company, location, contact_info if not mentioned
- tech_stack can be an empty list [] if no tools are mentioned
- Messages may be in Hebrew, English, or mixed — handle both equally
"""


# %%
# --- CORE SCORING FUNCTION ---
# Takes ONE raw message dict + the portfolio string
# Returns a ScoredJob instance if valid job found, or None if not
# Called once per message inside the run_brain() loop


def score_message(message: dict, portfolio: str) -> ScoredJob | None:
    # Build the user-side prompt by injecting portfolio + message text into an f-string
    # message.get("key", default) = safely pulls value from dict, returns default if key missing
    user_content = f"""CANDIDATE PORTFOLIO:
{portfolio}

---

TELEGRAM MESSAGE (from group: {message.get("group", "unknown")}):
{message.get("text", "")}
"""

    try:
        # Send the API call to GPT-4o mini
        # messages list has two roles: system (persona + rules) and user (portfolio + message)
        # temperature=0 = deterministic output — same input always gives same result, no randomness
        # response_format=json_object = forces JSON output at the API level (double enforcement with prompt)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,  # deterministic — consistent structured extraction
            response_format={"type": "json_object"},
        )

        # response is a nested object: response.choices[0].message.content = the raw text string
        # choices is a list because OpenAI supports n>1 responses — we always use [0] (first/only)
        raw = response.choices[0].message.content

        # json.loads() converts the raw JSON string into a Python dictionary we can access by key
        data = json.loads(raw)

        # if LLM said not a job, or no link found — return None silently, no noise in logs
        if not data.get("is_job", False):
            return None  # not a job post or no link — skip silently

        # Create a ScoredJob instance (child of JobOpportunity) with data extracted by LLM
        # data["job_link"] uses square brackets intentionally — raises KeyError if missing (caught below)
        # this enforces the critical design rule: no link = no job
        # raw_text comes from the original message dict, not from the LLM
        job = ScoredJob(
            title=data["title"],
            company=data.get("company"),
            location=data.get("location"),
            is_junior=data["is_junior"],
            tech_stack=data.get("tech_stack", []),
            contact_info=data.get("contact_info"),
            job_link=data["job_link"],
            raw_text=message["text"],
            confidence_score=data["confidence_score"],
            fit_reasoning=data["fit_reasoning"],
        )
        return job

    # --- EXCEPTION HANDLERS — safety net, one bad message never crashes the full run ---

    except ValidationError as e:
        # Pydantic rejected the data — e.g. confidence_score=15 fails the 1-10 field_validator
        print(f"[brain] Skipping — ValidationError: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        # KeyError: LLM response missing a required field (e.g. no job_link key)
        # JSONDecodeError: response wasn't valid JSON at all
        print(f"[brain] Skipping — bad LLM response: {e}")
        return None
    except Exception as e:
        # catch-all safety net for anything unexpected (network error, API timeout, etc.)
        # must always be LAST — it catches everything including the errors above
        print(f"[brain] Skipping — unexpected error: {e}")
        return None


# %%
# --- ORCHESTRATOR FUNCTION ---
# Loads all data, loops through every message, collects ScoredJob results
# This is what main.py will call — it returns the full list of scored jobs


def run_brain() -> list[ScoredJob]:
    portfolio = load_portfolio()  # full portfolio.txt as a string
    messages = load_messages()  # list of message dicts from raw_dump.json

    print(f"[brain] Processing {len(messages)} messages...")

    scored_jobs: list[
        ScoredJob
    ] = []  # empty list — will collect all valid ScoredJob instances

    # enumerate() gives both index (i) and value (message) on each iteration
    # used for progress logging: [1/150], [2/150] etc — i+1 because enumerate starts at 0
    for i, message in enumerate(messages):
        result = score_message(message, portfolio)  # returns ScoredJob or None

        # ScoredJob object is truthy, None is falsy — clean Pythonic check
        if result:
            scored_jobs.append(result)  # add to results list
            print(
                f"[brain] [{i + 1}/{len(messages)}] Job found: {result.title} (score={result.confidence_score})"
            )
        else:
            print(f"[brain] [{i + 1}/{len(messages)}] Not a job — skipped")

    print(
        f"[brain] Done — {len(scored_jobs)} jobs extracted from {len(messages)} messages"
    )
    return scored_jobs  # returned to main.py which handles storage and alerts


# %%
# --- ENTRY POINT ---
# __name__ == "__main__" is True only when this file is run directly (uv run python engine/brain.py)
# When main.py imports brain.py, __name__ == "brain" — this block is skipped entirely
# Without this guard, importing brain.py would trigger a full 150-message scoring run automatically
if __name__ == "__main__":
    jobs = run_brain()
    for job in jobs:
        # model_dump_json() is a Pydantic method — serializes ScoredJob instance to pretty JSON string
        # indent=2 = 2-space indentation for readable output
        print(job.model_dump_json(indent=2))
