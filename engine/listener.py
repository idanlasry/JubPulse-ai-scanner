# %%
import asyncio  # runs async functions — required for Telethon
import json  # saves messages to raw_dump.json
import os  # reads credentials from environment
from pathlib import Path  # builds file paths that work on any OS

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()  # must run before os.getenv() — loads .env into memory

API_ID = int(os.getenv("TELEGRAM_API_ID"))  # must be int, not string
API_HASH = os.getenv("TELEGRAM_API_HASH")  # stays as string

# __file__ = this file (listener.py)
# .parent.parent = two levels up → project root
GROUPS_FILE = Path(__file__).parent.parent / "config" / "groups.txt"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "raw_dump.json"


# %%
def load_groups() -> list[str | int]:
    # reads groups.txt and converts it into a list of group identifiers
    groups = []
    for line in GROUPS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()  # removes whitespace and newline characters
        if not line:
            continue  # skips empty lines
        try:
            groups.append(int(line))  # private group → numeric ID
        except ValueError:
            groups.append(line)  # public group → @username string
    return groups


# %%
async def fetch_recent_messages(
    client: TelegramClient,  # the active Telegram connection
    group: str | int,  # one group — @username or numeric ID
    limit: int = 5,  # default 50 messages, can be overridden
) -> list[dict]:
    messages = []
    try:
        entity = await client.get_entity(group)
        # translates @username or numeric ID into a Telegram object

        async for message in client.iter_messages(entity, limit=limit):
            # async for — each message fetched over the network one by one
            if not message.text:
                continue  # skips photos, videos, stickers — text only
            messages.append(
                {
                    "text": message.text,
                    "timestamp": message.date.isoformat(),  # standardized datetime string
                    "sender_id": message.sender_id,
                    "group": str(group),  # which group this message came from
                }
            )
    except Exception as e:
        print(f"[listener] Failed to fetch from {group}: {e}")
        # one failed group returns empty list — pipeline keeps running
    return messages


# %%
async def main() -> None:
    groups = load_groups()  # file → list of group identifiers
    all_messages: list[dict] = []  # master list — collects from all groups

    async with TelegramClient("jobpulse_session", API_ID, API_HASH) as client:
        # opens Telegram connection once for all groups
        # "jobpulse_session" → saves login state locally after first phone verification
        # automatically closes connection when block finishes
        for group in groups:
            print(f"[listener] Fetching from {group}...")
            msgs = await fetch_recent_messages(client, group)
            print(f"[listener]   → {len(msgs)} messages")
            all_messages.extend(
                msgs
            )  # extend adds items individually, not as nested list
            await asyncio.sleep(2)  # 2 second pause between groups → rate limit

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    # creates /data/ folder if it doesn't exist, no error if it already does

    OUTPUT_FILE.write_text(
        json.dumps(all_messages, ensure_ascii=False, indent=2),
        # ensure_ascii=False → Hebrew characters saved correctly, not escaped
        # indent=2           → human readable JSON
        encoding="utf-8",
    )
    print(f"[listener] Saved {len(all_messages)} messages to {OUTPUT_FILE}")


# %%
if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run() → bridge between regular Python and the async world
    # starts async engine → runs main() → shuts down
    # output: raw_dump.json → ready for brain.py
