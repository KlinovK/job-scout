import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient, events

load_dotenv()

CHANNEL_ID = -1002888160310

client = TelegramClient(
    "telegram_collector",
    int(os.environ["TELEGRAM_API_ID"]),
    os.environ["TELEGRAM_API_HASH"],
)

@client.on(events.NewMessage(chats=CHANNEL_ID))
async def handler(event):
    print("\n==============================")
    print(f"Message ID: {event.message.id}")
    print(f"Date: {event.message.date}")
    print()
    print(event.raw_text)
    print("==============================")

async def main():
    await client.start()

    print("Listening for new messages...")
    print(f"Channel ID: {CHANNEL_ID}")

    await client.run_until_disconnected()

asyncio.run(main())
