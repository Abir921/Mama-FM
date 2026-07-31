"""Set the bot's profile picture (and optionally banner) from a local image file.

Usage:
    .venv\\Scripts\\python.exe set_avatar.py path\\to\\avatar.png

Discord rate-limits profile edits fairly aggressively (a couple per hour), so
avoid running this in a loop.
"""

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

load_dotenv()

VALID_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_BYTES = 10 * 1024 * 1024  # Discord's limit for avatars


async def main(path: str) -> None:
    if not os.path.isfile(path):
        raise SystemExit(f"No such file: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in VALID_EXT:
        raise SystemExit(f"Unsupported type {ext!r}. Use one of: {', '.join(sorted(VALID_EXT))}")

    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise SystemExit(f"Image is {size / 1024 / 1024:.1f} MB — Discord's limit is 10 MB.")

    with open(path, "rb") as fp:
        data = fp.read()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN missing from .env")

    client = discord.Client(intents=discord.Intents.none())

    @client.event
    async def on_ready() -> None:
        try:
            await client.user.edit(avatar=data)
            print(f"Avatar updated for {client.user} from {os.path.basename(path)} ({size / 1024:.0f} KB)")
        except discord.HTTPException as e:
            # 50035 / rate limits are the usual failures here.
            print(f"Failed to set avatar: {e}")
        finally:
            await client.close()

    await client.start(token)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
