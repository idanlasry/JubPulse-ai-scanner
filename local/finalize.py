"""
local/finalize.py — standalone Telegram proposals sender.

Reads data/tuning_proposals.json and sends each proposal to Telegram
with inline ✅ Apply / ❌ Skip buttons.

Run manually after any pipeline run:  uv run python local/finalize.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from engine.notify import send_proposals

load_dotenv()

TUNING_PROPOSALS_FILE = Path(__file__).parent.parent / "data" / "tuning_proposals.json"


async def main() -> None:
    if not TUNING_PROPOSALS_FILE.exists():
        print(f"[finalize] {TUNING_PROPOSALS_FILE.name} not found — nothing to send")
        return
    await send_proposals(TUNING_PROPOSALS_FILE)
    print("[finalize] Done")


if __name__ == "__main__":
    asyncio.run(main())
