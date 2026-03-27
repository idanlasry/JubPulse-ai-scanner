# %%
from pydantic import BaseModel, field_validator
# BaseModel   → enforces field types automatically on object creation
# field_validator → lets you write custom range/logic checks beyond basic types


# %%
class JobOpportunity(BaseModel):
    # The base "shape" of a job post — every job object in the pipeline must fit this contract
    # Required fields (no default) → must always be present
    # Optional fields (= None)    → Telegram posts are messy, these may be missing

    title: str  # Required — a job must have a title
    company: str | None = None  # Optional — recruiters often skip this
    location: str | None = None  # Optional — remote jobs may not mention one
    is_junior: bool  # Required — LLM decides True/False
    tech_stack: list[str]  # Required — even an empty list [] is valid
    contact_info: str | None = None  # Optional — may be missing
    job_link: str  # required — no default, no None
    # no link = not a real job post = skip
    raw_text: str  # Required — original Telegram message, never modified
    message_date: str | None = None  # ISO 8601 UTC — when the Telegram message was posted
    source_group: str | None = None  # Telegram group this job was fetched from


# %%
class ScoredJob(JobOpportunity):
    # Inherits all fields from JobOpportunity, adds score + reasoning
    # Inheritance chain: BaseModel → JobOpportunity → ScoredJob

    confidence_score: int  # LLM's match score, must be 1-10
    fit_reasoning: str  # LLM's explanation of why this score was given

    @field_validator("confidence_score")
    @classmethod
    # @classmethod required by Pydantic — validator runs during construction,
    # before the object fully exists
    def score_in_range(cls, v: int) -> int:
        # v = the value being validated
        # if LLM returns 15 → ValidationError raised → object never created
        # handle this in brain.py with try/except to skip bad responses gracefully
        if not 1 <= v <= 10:
            raise ValueError(f"confidence_score must be 1-10, got {v}")
        return v  # valid → return unchanged, Pydantic continues building the object


# %%
if __name__ == "__main__":
    # Quick sanity check — runs only when this file is executed directly
    # not when imported by brain.py or main.py
    job = ScoredJob(
        title="Data Analyst",
        company="Acme Corp",
        location="Tel Aviv",
        is_junior=True,
        tech_stack=["Python", "SQL", "Tableau"],
        contact_info="@recruiter",
        raw_text="We are looking for a junior data analyst...",
        confidence_score=8,
        fit_reasoning="Strong SQL and Python match; junior-friendly role.",
    )
    print(job.model_dump_json(indent=2))
# %%
