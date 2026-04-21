# %%
import ast
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path
from scipy import stats

ROOT = Path(__file__).parent.parent
df = pd.read_csv(ROOT / "data" / "eval_results.csv", dtype=str)
df["confidence_score"] = pd.to_numeric(df["confidence_score"], errors="coerce")
df["original_gpt_score"] = pd.to_numeric(df["original_gpt_score"], errors="coerce")
print(f"Loaded {len(df)} rows | models: {df['model'].value_counts().to_dict()}")
# retrive df columns
print(f"Columns: {df.columns.tolist()}")


# Per model: group by score → count, top tech stack, mode seniority
def _parse_stack(val):
    try:
        return ast.literal_eval(val) if pd.notna(val) else []
    except Exception:
        return []


df["_stack"] = df["tech_stack"].apply(_parse_stack)


def _top_skills(rows):
    skills = [s for row in rows for s in row]
    return (
        ", ".join(pd.Series(skills).value_counts().head(5).index.tolist())
        if skills
        else "—"
    )


for model, grp in df.groupby("model"):
    print(f"\n{'─' * 40}\n {model}\n{'─' * 40}")

    by_score = (
        grp.groupby("confidence_score")
        .agg(
            count=("job_hash", "count"),
            junior_mode=(
                "is_junior",
                lambda s: s.mode().iloc[0] if len(s.mode()) else "—",
            ),
            top_skills=("_stack", _top_skills),
        )
        .reset_index()
    )
    print(by_score.to_string(index=False))

# %%
# Mean, std per model + Pearson correlation (Sonnet vs GPT only)
print(f"\n{'─' * 40}\n Score stats per model\n{'─' * 40}")
print(
    df.groupby("model")["confidence_score"]
    .agg(["mean", "std", "count"])
    .round(2)
    .to_string()
)

wide = df.pivot_table(
    index="job_hash", columns="model", values="confidence_score", aggfunc="first"
)
wide.columns.name = None
pair = wide[["gpt-4o-mini", "claude-sonnet"]].dropna()
r, p = stats.pearsonr(pair["gpt-4o-mini"], pair["claude-sonnet"])
sig = "**" if p < 0.01 else ("*" if p < 0.05 else "")
print(
    f"\nPearson  claude-sonnet vs gpt-4o-mini:  r={r:+.3f}  p={p:.4f}  n={len(pair)}  {sig}"
)

# %%
# Scatter: GPT score (x) vs Sonnet score (y), color = count at each coordinate
counts = pair.groupby(["gpt-4o-mini", "claude-sonnet"]).size().reset_index(name="count")

fig, ax = plt.subplots(figsize=(6, 5))
sc = ax.scatter(
    counts["gpt-4o-mini"],
    counts["claude-sonnet"],
    c=counts["count"],
    cmap="YlOrRd",
    vmin=1,
    vmax=counts["count"].max(),
    alpha=0.85,
    edgecolors="grey",
    linewidths=0.4,
    s=90,
)
fig.colorbar(sc, ax=ax, label="count")
ax.plot([1, 10], [1, 10], "k--", linewidth=0.8, alpha=0.5)
ax.text(
    0.05,
    0.93,
    f"r = {r:+.3f}  n = {len(pair)}",
    transform=ax.transAxes,
    fontsize=9,
    va="top",
)
ax.set_xlim(0.5, 10.5)
ax.set_ylim(0.5, 10.5)
ax.set_xticks(range(1, 11))
ax.set_yticks(range(1, 11))
ax.set_xlabel("GPT-4o-mini score")
ax.set_ylabel("Claude Sonnet score")
ax.set_title("Claude Sonnet vs GPT-4o-mini", fontweight="bold")
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()
