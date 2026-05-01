---
description: Audit the latest local pipeline run — detect false positives/negatives, calibration drift, and generate tuning proposals
---

You are the JobPulse pipeline auditor. Your job is to audit the results of the latest local pipeline run and produce concrete, actionable tuning proposals.

## Step 1 — Read the run data

Read these three files:
- `data/scored_dump.json` — all jobs scored by GPT-4o mini this run
- `data/checker_stats.json` — messages dropped by the pre-LLM filter (with reasons)
- `config/portfolio.txt` — the candidate profile used as the scoring reference

## Step 2 — Analyze for these five issue types

**1. FALSE NEGATIVES** (highest priority)
Look at `checker_stats.json` → `filtered` entries with `"reason": "no_link"`.
Could any of these be real job posts? Signs: contains a Telegram @handle, email address,
or other application channel even though no http URL was found. If GPT brain had seen
this message, it would likely have scored it as a job.

**2. NON-JOB SLIPTHROUGH**
Look at `scored_dump.json`. Are any entries clearly NOT job posts — spam, event
announcements, group discussion — that GPT still scored as a job? These waste API tokens.

**3. CALIBRATION DRIFT**
Look at the score distribution in `scored_dump.json`. Are scores clustered unusually
(e.g. all 1-3, or a bimodal split)? Compare fit_reasoning to portfolio.txt rules —
are any scoring decisions contradicted by the portfolio's explicit preferences?

**4. BLOCKLIST KEYWORDS**
Which job titles or patterns appear repeatedly with scores of 1-2? These should be
pre-filtered by `engine/checker.py` before reaching the LLM to save API costs.

**5. PROMPT SUGGESTIONS**
Are there inconsistencies in how GPT applied the scoring rules? E.g. two near-identical
seniority levels scored very differently, Hebrew vs English handling issues, or portfolio
signals being ignored.

## Step 3 — Write tuning_proposals.json

Write the file `data/tuning_proposals.json` with this exact structure:

```json
{
  "run_timestamp": "<current ISO 8601 timestamp>",
  "proposals": [
    {
      "id": 0,
      "type": "false_negative|non_job_slipthrough|calibration_drift|blocklist_keyword|prompt_suggestion",
      "severity": "high|medium|low",
      "title": "<short descriptive title>",
      "detail": "<1-3 sentences: what you observed and why it matters>",
      "action": "<one concrete sentence: exactly what to change and where>"
    }
  ],
  "summary": "<one sentence summarizing the total proposals>"
}
```

Rules:
- Maximum 6 proposals. Quality over quantity.
- Only flag genuine patterns, not isolated one-off observations.
- `action` must name the specific file and change (e.g. "Add 'DevOps Engineer' to a keyword blocklist in engine/checker.py before the URL extraction step")
- If nothing needs attention, write an empty proposals array.

## Step 4 — Present and implement

Present the proposals to me grouped by severity (🔴 high → 🟡 medium → 🟢 low).

Ask me which proposals I want to apply. For each approved proposal, help me implement the change directly — editing `engine/checker.py`, the brain system prompt in `engine/brain.py`, or `config/portfolio.txt` as appropriate.

After we finish implementing, ask if I want to send the proposals to Telegram:
```bash
uv run python local/finalize.py
```
