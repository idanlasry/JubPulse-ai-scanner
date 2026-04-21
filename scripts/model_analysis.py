# %%
import json
from pathlib import Path

import pandas as pd
from scipy import stats

ROOT = Path(__file__).parent.parent
RESULTS_FILE = ROOT / "data" / "eval_results.csv"


# %%
def load_results() -> pd.DataFrame:
    df = pd.read_csv(RESULTS_FILE, dtype=str)
    df["confidence_score"] = pd.to_numeric(df["confidence_score"], errors="coerce")
    df["original_gpt_score"] = pd.to_numeric(df["original_gpt_score"], errors="coerce")
    print(f"[model_analysis] Loaded {len(df)} rows | models: {df['model'].value_counts().to_dict()}")
    return df


# %%
def pivot_scores(df: pd.DataFrame) -> pd.DataFrame:
    """One row per job_hash, one column per model's score."""
    wide = df.pivot_table(
        index="job_hash",
        columns="model",
        values="confidence_score",
        aggfunc="first",
    )
    wide.columns.name = None
    wide = wide.reset_index()
    return wide


# %%
def pearson_correlation(wide: pd.DataFrame) -> None:
    print("\n" + "=" * 45)
    print(" Pearson Correlation -- Model Score Agreement")
    print("=" * 45)

    pairs = [
        ("claude-sonnet", "gpt-4o-mini"),
        ("gemini-2.5-flash", "gpt-4o-mini"),
        ("claude-sonnet", "gemini-2.5-flash"),
    ]

    for model_a, model_b in pairs:
        if model_a not in wide.columns or model_b not in wide.columns:
            print(f"  {model_a} vs {model_b}: skipped (column missing)")
            continue
        pair_df = wide[[model_a, model_b]].dropna()
        n = len(pair_df)
        if n < 3:
            print(f"  {model_a} vs {model_b}: skipped (only {n} shared jobs)")
            continue
        r, p = stats.pearsonr(pair_df[model_a], pair_df[model_b])
        sig = "**" if p < 0.01 else ("*" if p < 0.05 else "")
        print(f"  {model_a:<20} vs {model_b:<15}  r={r:+.3f}  p={p:.4f}  n={n}  {sig}")

    print("=" * 45)


# %%
def score_distribution(wide: pd.DataFrame) -> None:
    print("\n" + "=" * 45)
    print(" Score Distribution per Model")
    print("=" * 45)

    models = [c for c in ["gpt-4o-mini", "claude-sonnet", "gemini-2.5-flash"] if c in wide.columns]
    stats_df = wide[models].describe().loc[["count", "mean", "std", "min", "50%", "max"]]
    stats_df = stats_df.rename(index={"50%": "median"})
    print(stats_df.round(2).to_string())
    print("=" * 45)


# %%
def score_delta(df: pd.DataFrame) -> None:
    """Per-job delta: model score minus GPT score."""
    non_gpt = df[df["model"] != "gpt-4o-mini"].copy()
    non_gpt["delta"] = non_gpt["confidence_score"] - non_gpt["original_gpt_score"]

    print("\n" + "=" * 45)
    print(" Score Delta vs GPT-4o-mini (model - gpt)")
    print("=" * 45)

    for model, grp in non_gpt.groupby("model"):
        d = grp["delta"].dropna()
        print(f"  {model}")
        print(f"    mean delta : {d.mean():+.2f}")
        print(f"    std        : {d.std():.2f}")
        print(f"    % exact    : {(d == 0).mean() * 100:.1f}%")
        print(f"    % within±1 : {(d.abs() <= 1).mean() * 100:.1f}%")

    print("=" * 45)


# %%
def main() -> None:
    df = load_results()
    wide = pivot_scores(df)
    print(f"[model_analysis] Wide table: {len(wide)} jobs with scores from 1+ model")

    score_distribution(wide)
    pearson_correlation(wide)
    score_delta(df)


# %%
if __name__ == "__main__":
    main()
