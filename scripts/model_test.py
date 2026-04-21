# %%
import json
import os
import sys
import time
from pathlib import Path

import anthropic
from google import genai
from google.genai import types as genai_types
import pandas as pd
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT = Path(__file__).parent.parent
PORTFOLIO = (ROOT / "config" / "portfolio.txt").read_text(encoding="utf-8")
SAMPLE_FILE = ROOT / "data" / "eval_sample.json"
OUTPUT_FILE = ROOT / "data" / "eval_results.csv"

SONNET_MODEL = "claude-sonnet-4-6"
GEMINI_MODEL = "gemini-2.5-flash"

# Pricing (claude-sonnet-4-6, per million tokens)
SONNET_INPUT_COST_PER_MTOK = 3.00
SONNET_OUTPUT_COST_PER_MTOK = 15.00

# Token usage accumulators
gemini_usage: dict[str, int] = {"input": 0, "output": 0}
sonnet_usage: dict[str, int] = {"input": 0, "output": 0}

# Set to True when Gemini daily quota is exhausted — skip all remaining calls
_gemini_daily_quota_exhausted = False

SYSTEM_PROMPT = """You are an Expert Technical Recruiter evaluating job postings for a specific candidate.
You will be given:
1. A candidate portfolio (skills, experience, preferences)
2. A raw message from a Telegram job group

Your job is to:
1. Decide if the message is a job offer. If not, respond with {"is_job": false}.
2. If it is a job offer, check if it contains an application/job link (URL starting with http/https, or t.me, or linkedin.com, or similar).
   — If NO link is found, respond with {"is_job": false}.
3. If it's a job with a link, extract all fields and score the fit.

Note: Some job postings are just brief lists of keywords, a company name, and a link. Treat these as valid job offers even if they lack full sentences.

Always respond with valid JSON only — no markdown, no explanation outside the JSON.

Response format when IS a job with a link:
{
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
}

Response format when NOT a job or no link:
{
  "is_job": false
}

Scoring rules:
- confidence_score must be an integer 1-10
- Score based on match between job requirements and the candidate portfolio provided
- null is valid for company, location, contact_info if not mentioned
- tech_stack can be an empty list [] if no tools are mentioned
- Messages may be in Hebrew, English, or mixed — handle both equally
"""


# %%
def load_sample() -> list[dict]:
    if not SAMPLE_FILE.exists():
        print("[model_test] ERROR: data/eval_sample.json not found.")
        print("[model_test] Run 'uv run python scripts/eval_fetch.py' first to fetch the sample.")
        sys.exit(1)
    sample = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    print(f"[model_test] Loaded {len(sample)} jobs from data/eval_sample.json")
    return sample


# %%
def _build_user_prompt(raw_text: str) -> str:
    return (
        f"CANDIDATE PORTFOLIO:\n{PORTFOLIO}\n\n---\n\n"
        f"TELEGRAM MESSAGE (from group: unknown):\n{raw_text}"
    )


def _serialize_tech_stack(tech_stack) -> str:
    if isinstance(tech_stack, list):
        return json.dumps(tech_stack, ensure_ascii=False)
    return tech_stack or "[]"


def build_gpt_row(row: dict) -> dict:
    out = dict(row)
    out["tech_stack"] = _serialize_tech_stack(row.get("tech_stack"))
    out["model"] = "gpt-4o-mini"
    out["original_gpt_score"] = row["confidence_score"]
    return out


def build_model_row(sample_row: dict, scored: dict, model_name: str) -> dict:
    out = dict(sample_row)
    for field in ("title", "company", "location", "is_junior", "tech_stack",
                  "contact_info", "job_link", "confidence_score", "fit_reasoning"):
        if field in scored:
            out[field] = scored[field]
    out["tech_stack"] = _serialize_tech_stack(out.get("tech_stack"))
    out["model"] = model_name
    out["original_gpt_score"] = sample_row["confidence_score"]
    return out


# %%
def score_with_gemini(raw_text: str) -> dict | None:
    global gemini_usage, _gemini_daily_quota_exhausted
    if _gemini_daily_quota_exhausted:
        print("[gemini] skipped — daily quota exhausted")
        return None
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[gemini] ERROR: GOOGLE_API_KEY not set")
        return None
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=_build_user_prompt(raw_text),
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0,
            ),
        )

        if response.usage_metadata:
            gemini_usage["input"] += response.usage_metadata.prompt_token_count or 0
            gemini_usage["output"] += response.usage_metadata.candidates_token_count or 0

        data = json.loads(response.text)
        if not data.get("is_job", False):
            return None

        return {
            "title": data.get("title", ""),
            "company": data.get("company"),
            "location": data.get("location"),
            "is_junior": data.get("is_junior", False),
            "tech_stack": data.get("tech_stack", []),
            "contact_info": data.get("contact_info"),
            "job_link": data.get("job_link", ""),
            "confidence_score": data.get("confidence_score"),
            "fit_reasoning": data.get("fit_reasoning", ""),
        }

    except Exception as e:
        err_str = str(e)
        if "GenerateRequestsPerDayPerProjectPerModel" in err_str:
            _gemini_daily_quota_exhausted = True
            print("[gemini] Daily quota exhausted — skipping all remaining Gemini calls")
        else:
            print(f"[gemini] ERROR: {e}")
        return None


def score_with_sonnet(raw_text: str) -> dict | None:
    global sonnet_usage
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[sonnet] ERROR: ANTHROPIC_API_KEY not set")
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1024,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(raw_text)}],
        )

        sonnet_usage["input"] += response.usage.input_tokens
        sonnet_usage["output"] += response.usage.output_tokens

        raw = response.content[0].text.strip()
        # Strip markdown code fences if Claude wraps the JSON
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        data = json.loads(raw)
        if not data.get("is_job", False):
            return None

        return {
            "title": data.get("title", ""),
            "company": data.get("company"),
            "location": data.get("location"),
            "is_junior": data.get("is_junior", False),
            "tech_stack": data.get("tech_stack", []),
            "contact_info": data.get("contact_info"),
            "job_link": data.get("job_link", ""),
            "confidence_score": data.get("confidence_score"),
            "fit_reasoning": data.get("fit_reasoning", ""),
        }

    except Exception as e:
        print(f"[sonnet] ERROR: {e}")
        return None


# %%
def print_cost_summary(total_jobs: int, output_rows: int) -> None:
    sonnet_input_cost = (sonnet_usage["input"] / 1_000_000) * SONNET_INPUT_COST_PER_MTOK
    sonnet_output_cost = (sonnet_usage["output"] / 1_000_000) * SONNET_OUTPUT_COST_PER_MTOK
    sonnet_total = sonnet_input_cost + sonnet_output_cost

    print("\n" + "═" * 39)
    print(" Model Evaluation — Run Summary")
    print("═" * 39)
    print(f"Jobs in sample:      {total_jobs}")
    print(f"Total rows in CSV:   {output_rows}")
    print()
    print(f"Gemini 2.5 Flash (free tier)")
    print(f"  Input tokens:   {gemini_usage['input']:,}")
    print(f"  Output tokens:  {gemini_usage['output']:,}")
    print(f"  Est. cost:      $0.00 (free tier)")
    print()
    print(f"Claude Sonnet 4.6")
    print(f"  Input tokens:   {sonnet_usage['input']:,}  (${SONNET_INPUT_COST_PER_MTOK:.2f}/MTok → ${sonnet_input_cost:.4f})")
    print(f"  Output tokens:  {sonnet_usage['output']:,}  (${SONNET_OUTPUT_COST_PER_MTOK:.2f}/MTok → ${sonnet_output_cost:.4f})")
    print(f"  Est. total:     ${sonnet_total:.4f}")
    print()
    print("GPT-4o-mini")
    print("  API calls:      0 (rows read from eval_sample.json)")
    print("  Est. cost:      $0.00")
    print("═" * 39)


# %%
def main() -> None:
    jobs = load_sample()
    total = len(jobs)

    # Load existing results so previous runs are never overwritten
    if OUTPUT_FILE.exists():
        existing_df = pd.read_csv(OUTPUT_FILE, dtype=str)
        existing_rows: list[dict] = existing_df.to_dict("records")
        done: set[tuple[str, str]] = {
            (r["job_hash"], r["model"]) for r in existing_rows
            if r.get("job_hash") and r.get("model")
        }
        print(f"[model_test] Loaded {len(existing_rows)} existing rows from eval_results.csv ({len(done)} pairs already done)")
    else:
        existing_rows = []
        done = set()

    new_rows: list[dict] = []

    for i, row in enumerate(jobs):
        job_hash = row.get("job_hash") or ""
        job_hash_short = job_hash[:8]
        print(f"\n[{i + 1}/{total}] Processing job_hash={job_hash_short}...")

        # GPT row — read directly from sample, no API call
        if (job_hash, "gpt-4o-mini") not in done:
            new_rows.append(build_gpt_row(row))
            print(f"  [gpt-4o-mini] copied from sample (score={row.get('confidence_score')})")
        else:
            print(f"  [gpt-4o-mini] already in file — skipped")

        # Gemini
        if (job_hash, "gemini-2.5-flash") not in done:
            gemini_scored = score_with_gemini(row["raw_text"])
            if gemini_scored is not None:
                new_rows.append(build_model_row(row, gemini_scored, "gemini-2.5-flash"))
                print(f"  [gemini] score={gemini_scored['confidence_score']}")
            elif not _gemini_daily_quota_exhausted:
                print(f"  [gemini] skipped — is_job=false")
            time.sleep(1)
        else:
            print(f"  [gemini] already in file — skipped")

        # Sonnet
        if (job_hash, "claude-sonnet") not in done:
            sonnet_scored = score_with_sonnet(row["raw_text"])
            if sonnet_scored is not None:
                new_rows.append(build_model_row(row, sonnet_scored, "claude-sonnet"))
                print(f"  [sonnet] score={sonnet_scored['confidence_score']}")
            else:
                print(f"  [sonnet] skipped — is_job=false or error")
            time.sleep(1)
        else:
            print(f"  [sonnet] already in file — skipped")

    # Merge existing + new, sort by job_hash
    all_rows = existing_rows + new_rows
    all_rows.sort(key=lambda r: r.get("job_hash") or "")

    df = pd.DataFrame(all_rows)

    supabase_cols = [
        "job_hash", "timestamp", "title", "company", "location", "is_junior",
        "tech_stack", "contact_info", "job_link", "raw_text", "confidence_score",
        "fit_reasoning", "source", "source_group", "repo", "alerted",
    ]
    extra_cols = ["model", "original_gpt_score"]
    ordered_cols = [c for c in supabase_cols if c in df.columns] + extra_cols
    df = df[ordered_cols]

    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
    print(f"\n[model_test] Written {len(all_rows)} total rows to {OUTPUT_FILE.relative_to(ROOT)} ({len(new_rows)} new this run)")

    print_cost_summary(total, len(all_rows))


# %%
if __name__ == "__main__":
    main()
