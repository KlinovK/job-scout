import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]
phone = os.environ["TELEGRAM_PHONE"]

client = TelegramClient(
    "telegram_collector",
    api_id,
    api_hash,
)


async def main() -> None:
    await client.start(phone=phone)

    me = await client.get_me()

    print("Login successful")
    print(f"Name: {me.first_name}")
    print(f"Username: {me.username}")
    print(f"ID: {me.id}")

    await client.disconnect()


asyncio.run(main())
