import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mamafm")

EXTENSIONS = [
    "cogs.music",
    "cogs.meme",
    "cogs.poll",
    "cogs.valorant",
]


class MamaBot(commands.Bot):
    """Mama FM — all slash commands live under the single top-level /mama group."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.mama = app_commands.Group(
            name="mama", description="Mama FM commands"
        )

    async def setup_hook(self) -> None:
        await db.init()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded extension %s", ext)
        self.tree.add_command(self.mama)

        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            # Guild-scoped sync shows commands instantly (global takes up to an hour).
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                log.info("Synced commands to guild %s", guild_id)
                return
            except discord.Forbidden:
                log.error(
                    "Can't sync to guild %s — the bot isn't in that server, or was invited "
                    "without the applications.commands scope. Invite it with:\n%s\n"
                    "Falling back to a global sync (commands may take up to an hour to appear).",
                    guild_id,
                    self.invite_url(),
                )

        await self.tree.sync()
        log.info("Synced commands globally")

    def invite_url(self) -> str:
        perms = discord.Permissions(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            add_reactions=True,
            manage_messages=True,
            read_message_history=True,
            connect=True,
            speak=True,
        )
        return discord.utils.oauth_url(
            self.application_id, permissions=perms, scopes=("bot", "applications.commands")
        )

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        if not self.guilds:
            log.warning(
                "Bot is not in any server yet. Invite it here:\n%s", self.invite_url()
            )
        else:
            log.info("Active in: %s", ", ".join(g.name for g in self.guilds))


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN missing — copy .env.example to .env and fill it in.")
    MamaBot().run(token)


if __name__ == "__main__":
    main()
