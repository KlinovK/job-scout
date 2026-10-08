import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

client = TelegramClient(
    "telegram_collector",
    int(os.environ["TELEGRAM_API_ID"]),
    os.environ["TELEGRAM_API_HASH"],
)

async def main():
    await client.start()

    print("\nYour Telegram dialogs:\n")

    async for dialog in client.iter_dialogs():
        print(f"{dialog.name} | id={dialog.id}")

    await client.disconnect()

asyncio.run(main())
