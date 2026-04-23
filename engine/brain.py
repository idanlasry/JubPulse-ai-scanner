# %%
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.models import ScoredJob

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

PORTFOLIO_FILE = Path(__file__).parent.parent / "config" / "portfolio.txt"
RAW_DUMP_FILE = Path(__file__).parent.parent / "data" / "raw_dump.json"
SCORED_DUMP_FILE = Path(__file__).parent.parent / "data" / "scored_dump.json"

client = OpenAI(api_key=OPENAI_API_KEY)


# %%
def load_portfolio() -> str:
    return PORTFOLIO_FILE.read_text(encoding="utf-8")


def load_messages() -> list[dict]:
    return json.loads(RAW_DUMP_FILE.read_text(encoding="utf-8"))


# %%
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
  "fit_reasoning": "POSITIVES: ...\\nNEGATIVES: ...\\nHARD BLOCK: NONE\\nSCORE: N — one sentence"
}

Response format when NOT a job or no link:
{
  "is_job": false
}

─────────────────────────────────────────
HARD EXCLUSION RULES  (check first)
─────────────────────────────────────────
Set confidence_score to 1 or 2 ONLY if:
  • Job TITLE contains: Senior, Lead, Manager, Principal, Staff
  • Role is located OUTSIDE ISRAEL (e.g. San Antonio, Berlin, New York — any non-Israeli city)
  • Role is purely: DevOps / Infrastructure / Cloud Engineering / Mobile / Pure Frontend /
    Pure Backend with zero analytics duties

These are the ONLY three hard exclusion triggers.

─────────────────────────────────────────
NOT HARD EXCLUSIONS — use score modifiers
─────────────────────────────────────────
The following are NOT hard exclusions. Do NOT score 1-2 for these:
  • "5+ years experience required" → score DOWN 2-3 points. Minimum score: 2.
  • "Mid-level" or "3 years" or "3+ years" → mild negative, -1 point at most.
  • "Data Scientist" title → NOT a hard exclusion. See Role Type rules.
  • Partial tech stack overlap → reduce score, never exclude.

─────────────────────────────────────────
SENIORITY CALIBRATION
─────────────────────────────────────────
  Junior / Entry level / 0-2 years, exact title, preferred city, full stack match  →  9-10
  Mid-level, exact title, preferred city, strong stack match                        →  7-8
  "3+ years", exact title, preferred city, stack match                              →  6-7
  "5+ years", relevant analytical role, partial match                               →  2-4
  Senior / Lead / Manager in TITLE                                                  →  1-2  (stop here)

─────────────────────────────────────────
LOCATION RULES
─────────────────────────────────────────
  HARD EXCLUDE: outside Israel → score 1-2
  PREFERRED (score UP): Tel Aviv, Ramat Gan, Rehovot, Herzliya, Bnei Brak, Lod, ~30km radius
  ACCEPTABLE: Remote (Israel-based), hybrid, any Israeli city
  MILD NEGATIVE: on-site only with no remote option

─────────────────────────────────────────
ROLE TYPE RULES
─────────────────────────────────────────
  RELEVANT: Data Analyst, Business Analyst, BI Analyst, Product Analyst, BI Developer,
            Data & Insights Analyst, Sales Analyst, Revenue Analyst, Growth Analyst
  DATA SCIENTIST rule:
    Requirements mention ONLY ML / deep learning / statistics / NLP research  →  score 2-4
    Requirements mention LLM / Prompt Engineering / A-B Testing / dashboards  →  treat as analytical, score 4-7
    A Data Scientist role CANNOT receive score 1-2 unless Senior/Lead/Manager is also in the title.
  NOT RELEVANT: Data Engineer (pure infra), DevOps, Backend, Frontend, Mobile
  CONTENT OVERRIDE: if job duties are ONLY annotation / fact-checking / data entry / customer support
    with no analytical output  →  score 2-4 regardless of seniority or location.

─────────────────────────────────────────
REQUIRED fit_reasoning FORMAT
─────────────────────────────────────────
  POSITIVES: [signal 1, signal 2, ...]
  NEGATIVES: [signal 1, signal 2, ...]
  HARD BLOCK: NONE  — OR —  [name the exact rule: "Senior in title" / "outside Israel" / "pure DevOps"]
  SCORE: [N] — [one sentence]

─────────────────────────────────────────
SCORED EXAMPLES  (study these before scoring)
─────────────────────────────────────────

Example A — Mid-level, strong stack, preferred city:
  Role: Data Analyst, Mid-level, Tel Aviv | Stack: SQL, Power BI
  fit_reasoning: "POSITIVES: Exact title, preferred city, SQL and Power BI are primary candidate tools.
NEGATIVES: Mid-level — mild penalty only (-1 point).
HARD BLOCK: NONE
SCORE: 7 — strong match; mid-level is acceptable, one point deducted for seniority."
  confidence_score: 7

Example B — Perfect junior match:
  Role: Data Analyst, Junior, Tel Aviv | Stack: SQL, Python, Pandas
  fit_reasoning: "POSITIVES: Exact title, junior, preferred city, full primary stack match.
NEGATIVES: None.
HARD BLOCK: NONE
SCORE: 10 — all signals align, no negatives."
  confidence_score: 10

Example C — Data Scientist with LLM/Prompt Engineering:
  Role: Data Scientist, Mid-level, Tel Aviv | Stack: LLM, Prompt Engineering, A/B Testing, Python
  fit_reasoning: "POSITIVES: LLM and Prompt Engineering match candidate skills, preferred city, Python match.
NEGATIVES: Data Scientist title is not the primary target role; mid-level.
HARD BLOCK: NONE — role contains LLM/Prompt Engineering, which is analytical/borderline, not a hard exclude.
SCORE: 5 — relevant work despite title; LLM overlap prevents a low score."
  confidence_score: 5

Example D — 5+ years required, analytical role:
  Role: Sales Analyst, 5+ years required, Tel Aviv | Stack: (none specified)
  fit_reasoning: "POSITIVES: Analytical role (pipeline/revenue work), preferred city.
NEGATIVES: 5+ years required — significant penalty.
HARD BLOCK: NONE — experience count is not a hard exclusion; minimum score is 2.
SCORE: 3 — relevant analytical work, penalized 2-3 points for experience requirement."
  confidence_score: 3

─────────────────────────────────────────
REFLECTION — verify before outputting
─────────────────────────────────────────
R1 — Block/score consistency:
  If HARD BLOCK is NONE → confidence_score must be 3 or higher.
  If confidence_score is 1-2 → HARD BLOCK must name one of the three exact triggers above.
  If these are inconsistent, fix the error before outputting.

R2 — Negative proportionality:
  If your only NEGATIVES are "mid-level", "3 years", "3+ years", or "mid-level experience"
  AND your confidence_score is below 6:
  You have over-penalized. Revise upward or add a substantive second negative that justifies the low score.

─────────────────────────────────────────
ADDITIONAL RULES
─────────────────────────────────────────
- confidence_score must be an integer 1-10
- null is valid for company, location, contact_info if not mentioned
- tech_stack lists the tools the JOB requires (not filtered to candidate skills)
- tech_stack should list specific tools/technologies, not generic skill labels like "Business Analysis"
- Messages may be in Hebrew, English, or mixed — handle both equally
"""


# %%
def score_message(message: dict, portfolio: str) -> ScoredJob | None:
    user_content = f"""CANDIDATE PORTFOLIO:
{portfolio}

---

TELEGRAM MESSAGE (from group: {message.get("group", "unknown")}):
{message.get("text", "")}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        data = json.loads(raw)

        if not data.get("is_job", False):
            return None

        # Code-level guard: DS + analytical LLM stack scored ≤ 3 is the known GPT hallucination pattern
        _ANALYTICAL_DS_SIGNALS = {
            "llm",
            "prompt engineering",
            "a/b testing",
            "ab testing",
        }
        _title = (data.get("title") or "").lower()
        _stack = {t.lower() for t in data.get("tech_stack", [])}
        if (
            "data " in _title
            and _ANALYTICAL_DS_SIGNALS & _stack
            and data.get("confidence_score", 10) <= 3
        ):
            data["confidence_score"] = 5
            data["fit_reasoning"] = (
                data.get("fit_reasoning", "")
                + "\n[POST-PROCESSING: Score raised to 5 — LLM/Prompt Engineering/A-B Testing detected; score ≤ 3 is over-penalized for this role type.]"
            )

        # data["job_link"] raises KeyError if missing — enforces: no link = no job
        job = ScoredJob(
            title=data["title"],
            company=data.get("company"),
            location=data.get("location"),
            is_junior=data["is_junior"],
            tech_stack=data.get("tech_stack", []),
            contact_info=data.get("contact_info"),
            job_link=data["job_link"],
            raw_text=message["text"],
            message_date=message.get("timestamp"),
            source_group=message.get("group", "unknown"),
            confidence_score=data["confidence_score"],
            fit_reasoning=data["fit_reasoning"],
        )
        return job

    except ValidationError as e:
        print(f"[brain] Skipping — ValidationError: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"[brain] Skipping — bad LLM response: {e}")
        return None
    except Exception as e:
        print(f"[brain] Skipping — unexpected error: {e}")
        return None


# %%
def run_brain() -> list[ScoredJob]:
    portfolio = load_portfolio()
    messages = load_messages()

    print(f"[brain] Processing {len(messages)} messages...")

    scored_jobs: list[ScoredJob] = []

    for i, message in enumerate(messages):
        result = score_message(message, portfolio)

        if result:
            scored_jobs.append(result)
            print(
                f"[brain] [{i + 1}/{len(messages)}] Job found: {result.title} (score={result.confidence_score})"
            )

    print(
        f"[brain] Done — {len(scored_jobs)} jobs extracted from {len(messages)} messages"
    )

    try:
        SCORED_DUMP_FILE.write_text(
            json.dumps(
                [json.loads(job.model_dump_json()) for job in scored_jobs],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[brain] Could not write scored_dump.json: {e}")

    return scored_jobs


# %%
if __name__ == "__main__":
    jobs = run_brain()  # dump is already written inside run_brain()
    for job in jobs:
        print(job.model_dump_json(indent=2))
