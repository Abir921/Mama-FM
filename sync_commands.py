"""Move slash commands between guild-scoped and global registration.

Guild-scoped commands appear instantly but only in one server. Global commands
work in every server the bot joins, but can take up to an hour to show up.

    python sync_commands.py global   # all servers (clears the old guild copies)
    python sync_commands.py guild    # just GUILD_ID from .env, appears instantly
    python sync_commands.py status   # show what is registered where

After switching to global, clear GUILD_ID in .env so bot.py keeps doing the
same thing on its next start.
"""

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import MamaBot  # noqa: E402


class SyncBot(MamaBot):
    """MamaBot that builds its command tree but does not sync on login."""

    async def setup_hook(self) -> None:
        await self._load_tree()


async def run(mode: str) -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN missing from .env")

    guild_id = os.getenv("GUILD_ID", "").strip()
    if mode in ("guild", "global") and not guild_id and mode == "guild":
        raise SystemExit("GUILD_ID is empty in .env — nothing to sync to.")

    bot = SyncBot()
    async with bot:
        # login() runs setup_hook, which builds the tree without syncing.
        await bot.login(token)
        guild = discord.Object(id=int(guild_id)) if guild_id else None

        if mode == "status":
            g = await bot.tree.fetch_commands()
            print(f"global commands: {[c.name for c in g]}")
            if guild:
                gc = await bot.tree.fetch_commands(guild=guild)
                print(f"guild {guild_id} commands: {[c.name for c in gc]}")
            return

        if mode == "global":
            if guild:
                # Remove guild copies first, or they appear duplicated.
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
                print(f"Cleared guild-scoped commands from {guild_id}")
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} command(s) globally.")
            print("These can take up to an hour to appear. Clear GUILD_ID in .env.")
        elif mode == "guild":
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {guild_id} — visible immediately.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("global", "guild", "status"):
        raise SystemExit(__doc__)
    asyncio.run(run(sys.argv[1]))
