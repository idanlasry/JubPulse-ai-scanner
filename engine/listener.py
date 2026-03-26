# %%
import asyncio
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")

GROUPS_FILE = Path(__file__).parent.parent / "config" / "groups.txt"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "raw_dump.json"
LAST_SEEN_FILE = Path(__file__).parent.parent / "data" / "last_seen.csv"

LIMIT = 50  # safety ceiling — always active


# %%
def load_groups() -> list[str | int]:
    groups = []
    for line in GROUPS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            groups.append(int(line))
        except ValueError:
            groups.append(line)
    return groups


# %%
def load_last_seen(path: Path = LAST_SEEN_FILE) -> dict[str, datetime]:
    """Read last_seen.csv and return {group_id: datetime}.
    Returns {} if the file is missing (first run / Case 0).
    Groups not present in the file get None via .get() at the call site."""
    if not path.exists():
        return {}
    result: dict[str, datetime] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            result[row["group_id"]] = datetime.fromisoformat(row["last_seen_ts"])
    return result


def save_last_seen(last_seen: dict[str, datetime], path: Path = LAST_SEEN_FILE) -> None:
    """Write {group_id: datetime} to last_seen.csv.
    Only rows for groups in last_seen are written — stale groups are dropped."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["group_id", "last_seen_ts"])
        writer.writeheader()
        for group_id, ts in last_seen.items():
            writer.writerow({"group_id": group_id, "last_seen_ts": ts.isoformat()})


# %%
async def fetch_recent_messages(
    client: TelegramClient,
    group: str | int,
    limit: int = LIMIT,
    last_seen_ts: datetime | None = None,
) -> list[dict]:
    messages = []
    try:
        entity = await client.get_entity(group)

        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            if last_seen_ts is not None and message.date <= last_seen_ts:
                print(f"[{group}] Reached last seen checkpoint. Stopping.")
                break
            messages.append(
                {
                    "text": message.text,
                    "timestamp": message.date.isoformat(),
                    "sender_id": message.sender_id,
                    "group": str(group),
                }
            )
            if len(messages) == limit:
                print(f"[{group}] Reached fetch limit of {limit}. Stopping.")
                break
    except Exception as e:
        print(f"[listener] Failed to fetch from {group}: {e}")
    return messages


# %%
async def main(limit: int = LIMIT) -> None:
    last_seen_map = load_last_seen()
    groups = load_groups()
    all_messages: list[dict] = []

    async with TelegramClient("jobpulse_session", API_ID, API_HASH) as client:
        for group in groups:
            print(f"[listener] Fetching from {group}...")
            last_seen_ts = last_seen_map.get(str(group))
            msgs = await fetch_recent_messages(
                client, group, limit=limit, last_seen_ts=last_seen_ts
            )
            print(f"[listener]   -> {len(msgs)} messages")
            all_messages.extend(msgs)
            await asyncio.sleep(2)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(all_messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[listener] Saved {len(all_messages)} messages to {OUTPUT_FILE}")


# %%
if __name__ == "__main__":
    asyncio.run(main())
