# %%
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from engine.checker import (
    _dedup_batch,
    _extractor,
    _hash,
    _is_non_job,
    _load_known_data,
    _normalize,
)

RAW_DUMP = ROOT / "data" / "raw_dump.json"
DECISIONS_OUT = ROOT / "data" / "checker_decisions.txt"

# %%
print("--- Step 1: Loading and Screening Raw Dump ---")
if not RAW_DUMP.exists():
    print(f"Cannot find {RAW_DUMP.name}. Make sure to run the listener first.")
    raw_messages = []
else:
    try:
        raw_messages = json.loads(RAW_DUMP.read_text(encoding="utf-8"))
        print(f"Loaded {len(raw_messages)} messages from {RAW_DUMP.name}")
    except Exception as e:
        print(f"Failed to parse JSON: {e}")
        raw_messages = []

# %%
# 1. Structural Validation
print("\n[Structural Validation]")
breaking_jds = []
valid_messages = []

for i, msg in enumerate(raw_messages):
    issues = []
    if not isinstance(msg, dict):
        issues.append("not a dict")
    else:
        if "text" not in msg or not isinstance(msg["text"], str):
            issues.append("missing/invalid text")
        if "group" not in msg:
            issues.append("missing group")
        if "timestamp" not in msg:
            issues.append("missing timestamp")

    if issues:
        breaking_jds.append({"index": i, "message": msg, "issues": issues})
    else:
        valid_messages.append(msg)

if breaking_jds:
    print(f"Found {len(breaking_jds)} structurally invalid messages:")
    for b in breaking_jds[:5]:
        print(f"  Index {b['index']}: {b['issues']} — {str(b['message'])[:100]}")
elif valid_messages:
    print(f"All {len(valid_messages)} messages have valid structure.")
else:
    print("No messages to validate.")

# %%
# 2. Per-message Checker Decision Table
print("\n[Checker Decision Table]")
if not valid_messages:
    print("No valid messages to screen.")
else:
    known_hashes, known_links, checker_available = _load_known_data()
    print(
        f"Gate status: {'Active' if checker_available else 'Offline (Supabase unavailable)'}\n"
    )

    VERDICT_ORDER = {
        "passed": 0,
        "intra-batch": 1,
        "duplicate": 2,
        "non-job": 3,
        "no-link": 4,
    }

    rows = []
    for i, msg in enumerate(valid_messages):
        text: str = msg.get("text", "")
        group = str(msg.get("group", ""))
        http_urls = [u for u in _extractor.gen_urls(text) if u.startswith("http")]

        if not http_urls:
            verdict = "no-link"
            first_url = ""
        elif _is_non_job(text, http_urls):
            verdict = "non-job"
            first_url = http_urls[0]
        elif checker_available and any(
            _hash(u) in known_hashes or _normalize(u) in known_links for u in http_urls
        ):
            verdict = "duplicate"
            first_url = http_urls[0]
        else:
            verdict = "passed"
            first_url = http_urls[0]

        text_preview = text.replace("\n", " ").strip()
        rows.append(
            {
                "#": i + 1,
                "verdict": verdict,
                "group": group,
                "url": first_url,
                "text": text_preview,
                "_msg": msg,
            }
        )

    # Intra-batch dedup: delegate to the same _dedup_batch used in the real pipeline
    passed_idxs = [idx for idx, r in enumerate(rows) if r["verdict"] == "passed"]
    if passed_idxs:
        candidate_msgs = [{**rows[i]["_msg"], "_eval_row_idx": i} for i in passed_idxs]
        kept_msgs, _ = _dedup_batch(candidate_msgs)
        kept_idxs = {m["_eval_row_idx"] for m in kept_msgs}
        for i in passed_idxs:
            if i not in kept_idxs:
                rows[i]["verdict"] = "intra-batch"

    # Sort: passed first, then intra-batch, duplicate, non-job, no-link
    rows.sort(key=lambda r: VERDICT_ORDER.get(r["verdict"], 99))

    # Column widths
    C = {"#": 3, "verdict": 11, "group": 16, "url": 70, "text": 60}

    def _cell(val: object, width: int) -> str:
        s = str(val)
        # Truncate visually — Hebrew chars are single-width in most terminals
        if len(s) > width:
            s = s[: width - 1] + "…"
        return s.ljust(width)

    sep = "+-" + "-+-".join("-" * w for w in C.values()) + "-+"
    header = "| " + " | ".join(_cell(col, w) for col, w in C.items()) + " |"

    table_lines = [sep, header, sep]
    for r in rows:
        line = "| " + " | ".join(_cell(r[col], w) for col, w in C.items()) + " |"
        table_lines.append(line)
    table_lines.append(sep)

    table_str = "\n".join(table_lines)
    print(table_str)

    # Summary
    counts = Counter(r["verdict"] for r in rows)
    summary_lines = [
        "",
        f"Total: {len(rows)}  |  "
        f"passed={counts['passed']}  "
        f"intra-batch={counts['intra-batch']}  "
        f"duplicate={counts['duplicate']}  "
        f"non-job={counts['non-job']}  "
        f"no-link={counts['no-link']}",
    ]
    summary = "\n".join(summary_lines)
    print(summary)

    # Save table + summary to file
    DECISIONS_OUT.write_text(table_str + "\n" + summary + "\n", encoding="utf-8")
    print(f"\nSaved to {DECISIONS_OUT.relative_to(ROOT)}")
