import httpx
import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

url = f"https://api.telegram.org/bot{token}/sendMessage"

response = httpx.post(
    url, json={"chat_id": chat_id, "text": "✅ JobPulse notify connection test"}
)

print(response.status_code)
print(response.json())
