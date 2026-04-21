# %%
import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

ROOT = Path(__file__).parent.parent
SAMPLE_FILE = ROOT / "data" / "eval_sample.json"


# %%
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("[eval_fetch] ERROR: SUPABASE_URL and SUPABASE_KEY must be set in .env")
        sys.exit(1)
    return create_client(url, key)


def fetch_sample(sb: Client) -> list[dict]:
    print("[eval_fetch] Fetching rows from Supabase...")

    # Fetch 200 recent rows, shuffle locally for random 50
    res = sb.table("jobs").select("*").order("timestamp", desc=True).limit(200).execute()
    all_rows: list[dict] = res.data or []
    random.shuffle(all_rows)
    base_sample = all_rows[:50]
    base_hashes = {row["job_hash"] for row in base_sample}

    # Fetch high-score rows (confidence_score >= 7), dedup against base
    res_high = (
        sb.table("jobs")
        .select("*")
        .gte("confidence_score", 7)
        .order("timestamp", desc=True)
        .limit(100)
        .execute()
    )
    high_rows: list[dict] = res_high.data or []
    random.shuffle(high_rows)
    extras = [r for r in high_rows if r["job_hash"] not in base_hashes][:10]

    combined = base_sample + extras
    print(f"[eval_fetch] Sampled {len(combined)} jobs ({len(base_sample)} random + {len(extras)} high-score extras)")
    return combined


# %%
def main() -> None:
    if SAMPLE_FILE.exists():
        existing = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
        print(f"[eval_fetch] WARNING: {SAMPLE_FILE.name} already exists with {len(existing)} rows.")
        answer = input("Overwrite? This will reset eval_results.csv progress. [y/N] ").strip().lower()
        if answer != "y":
            print("[eval_fetch] Aborted — existing sample preserved.")
            return

    sb = get_supabase_client()
    sample = fetch_sample(sb)

    SAMPLE_FILE.write_text(
        json.dumps(sample, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"[eval_fetch] Saved {len(sample)} rows to data/eval_sample.json")
    print("[eval_fetch] Run 'uv run python scripts/model_test.py' to start scoring.")


# %%
if __name__ == "__main__":
    main()
