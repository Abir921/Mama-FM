"""Poll cog — reaction-based polls with one-vote-per-user enforcement.

Commands: /mama poll create | close | results.
Votes are tallied straight from message reactions; a background loop
auto-closes polls whose duration has elapsed.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

import db

log = logging.getLogger("mamafm.poll")

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _bar(count: int, total: int, width: int = 12) -> str:
    filled = round(width * count / total) if total else 0
    return "█" * filled + "░" * (width - filled)


class Poll(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # message_id -> set of option emojis, for fast lookup in the reaction listener
        self._open_polls: dict[int, set[str]] = {}

    async def cog_load(self) -> None:
        async with db.connect() as conn:
            conn.row_factory = None
            async with conn.execute(
                "SELECT message_id, options FROM polls WHERE closed = 0"
            ) as cur:
                async for message_id, options_json in cur:
                    n = len(json.loads(options_json))
                    self._open_polls[message_id] = set(NUMBER_EMOJIS[:n])
        self.auto_close.start()

    async def cog_unload(self) -> None:
        self.auto_close.cancel()

    # ---------- one vote per user ----------

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        emojis = self._open_polls.get(payload.message_id)
        if emojis is None or payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) not in emojis:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return

        # Remove any *other* poll reaction this user already has, so only the newest stands.
        for reaction in message.reactions:
            emoji = str(reaction.emoji)
            if emoji == str(payload.emoji) or emoji not in emojis:
                continue
            async for user in reaction.users():
                if user.id == payload.user_id:
                    try:
                        await reaction.remove(user)
                    except discord.HTTPException:
                        log.warning("Couldn't remove old vote (need Manage Messages permission)")
                    break

    # ---------- tallying ----------

    async def _tally(self, row: dict) -> tuple[list[tuple[str, int]], discord.Message | None]:
        """Return [(option, votes)] from live reactions. Bot's own seed reactions excluded."""
        options = json.loads(row["options"])
        counts = {NUMBER_EMOJIS[i]: 0 for i in range(len(options))}

        message = None
        channel = self.bot.get_channel(row["channel_id"])
        if channel is not None:
            try:
                message = await channel.fetch_message(row["message_id"])
            except discord.HTTPException:
                message = None
        if message is not None:
            for reaction in message.reactions:
                emoji = str(reaction.emoji)
                if emoji in counts:
                    counts[emoji] = max(0, reaction.count - reaction.me)

        return [(opt, counts[NUMBER_EMOJIS[i]]) for i, opt in enumerate(options)], message

    def _results_embed(self, row: dict, results: list[tuple[str, int]], *, final: bool) -> discord.Embed:
        total = sum(v for _, v in results)
        lines = []
        top = max((v for _, v in results), default=0)
        for i, (opt, votes) in enumerate(results):
            marker = "🏆 " if final and votes == top and top > 0 else ""
            lines.append(f"{NUMBER_EMOJIS[i]} {marker}**{opt}** — {votes}\n{_bar(votes, total)}")
        embed = discord.Embed(
            title=("📊 Final results" if final else "📊 Live tally") + f": {row['question']}",
            description="\n".join(lines) or "No options?",
            color=discord.Color.green() if final else discord.Color.blurple(),
        )
        embed.set_footer(text=f"Poll #{row['poll_id']} · {total} vote{'s' if total != 1 else ''}")
        return embed

    async def _close_poll(self, row: dict) -> None:
        results, message = await self._tally(row)
        async with db.connect() as conn:
            await conn.execute("UPDATE polls SET closed = 1 WHERE poll_id = ?", (row["poll_id"],))
            await conn.commit()
        self._open_polls.pop(row["message_id"], None)

        embed = self._results_embed(row, results, final=True)
        if message is not None:
            # Mark the original poll message closed and post results as a reply.
            orig = message.embeds[0] if message.embeds else discord.Embed(title=row["question"])
            orig.color = discord.Color.dark_grey()
            orig.set_footer(text=f"Poll #{row['poll_id']} · CLOSED")
            try:
                await message.edit(embed=orig)
                await message.reply(embed=embed)
                return
            except discord.HTTPException:
                pass
        channel = self.bot.get_channel(row["channel_id"])
        if channel is not None:
            await channel.send(embed=embed)

    @tasks.loop(seconds=30)
    async def auto_close(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with db.connect() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM polls WHERE closed = 0 AND close_at IS NOT NULL AND close_at <= ?",
                (now,),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            try:
                await self._close_poll(row)
            except Exception:
                log.exception("Auto-close failed for poll %s", row["poll_id"])

    @auto_close.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _fetch_poll(self, poll_id: int, guild_id: int) -> dict | None:
        async with db.connect() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM polls WHERE poll_id = ? AND guild_id = ?", (poll_id, guild_id)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    # ---------- command callbacks ----------

    async def create(
        self,
        interaction: discord.Interaction,
        question: str,
        options: list[str],
        duration: int | None,
    ) -> None:
        options = [o.strip() for o in options if o and o.strip()]
        if len(options) < 2:
            return await interaction.response.send_message("Need at least 2 options.", ephemeral=True)

        if duration is None:
            async with db.connect() as conn:
                async with conn.execute(
                    "SELECT poll_default_duration FROM guild_settings WHERE guild_id = ?",
                    (interaction.guild_id,),
                ) as cur:
                    r = await cur.fetchone()
            duration = r[0] if r else 60

        now = datetime.now(timezone.utc)
        close_at = now + timedelta(minutes=duration)

        desc = "\n".join(f"{NUMBER_EMOJIS[i]} {opt}" for i, opt in enumerate(options))
        embed = discord.Embed(title=f"📊 {question}", description=desc, color=discord.Color.blurple())
        embed.add_field(name="Closes", value=discord.utils.format_dt(close_at, "R"))

        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()

        async with db.connect() as conn:
            cur = await conn.execute(
                "INSERT INTO polls (message_id, channel_id, guild_id, question, options, created_by, created_at, close_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message.id,
                    interaction.channel_id,
                    interaction.guild_id,
                    question,
                    json.dumps(options),
                    interaction.user.id,
                    now.isoformat(),
                    close_at.isoformat(),
                ),
            )
            poll_id = cur.lastrowid
            await conn.commit()

        embed.set_footer(text=f"Poll #{poll_id} · one vote per person — new vote replaces old")
        await message.edit(embed=embed)
        self._open_polls[message.id] = set(NUMBER_EMOJIS[: len(options)])
        for emoji in NUMBER_EMOJIS[: len(options)]:
            await message.add_reaction(emoji)

    async def close(self, interaction: discord.Interaction, poll_id: int) -> None:
        row = await self._fetch_poll(poll_id, interaction.guild_id)
        if row is None:
            return await interaction.response.send_message(f"No poll #{poll_id} in this server.", ephemeral=True)
        if row["closed"]:
            return await interaction.response.send_message(f"Poll #{poll_id} is already closed.", ephemeral=True)
        if row["created_by"] != interaction.user.id and not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(
                "Only the poll creator (or someone with Manage Messages) can close it.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await self._close_poll(row)
        await interaction.followup.send(f"Poll #{poll_id} closed.", ephemeral=True)

    async def results(self, interaction: discord.Interaction, poll_id: int) -> None:
        row = await self._fetch_poll(poll_id, interaction.guild_id)
        if row is None:
            return await interaction.response.send_message(f"No poll #{poll_id} in this server.", ephemeral=True)
        await interaction.response.defer()
        results, _ = await self._tally(row)
        await interaction.followup.send(embed=self._results_embed(row, results, final=bool(row["closed"])))


async def setup(bot: commands.Bot) -> None:
    cog = Poll(bot)
    await bot.add_cog(cog)

    poll_group = app_commands.Group(
        name="poll", description="Polls & voting", guild_only=True, parent=bot.mama
    )

    @poll_group.command(name="create", description="Create a reaction poll")
    @app_commands.describe(
        question="The poll question",
        option1="Option 1",
        option2="Option 2",
        option3="Option 3",
        option4="Option 4",
        option5="Option 5",
        option6="Option 6",
        option7="Option 7",
        option8="Option 8",
        option9="Option 9",
        option10="Option 10",
        duration="Minutes until the poll auto-closes (default: server setting or 60)",
    )
    async def create(
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str,
        option3: str = None,
        option4: str = None,
        option5: str = None,
        option6: str = None,
        option7: str = None,
        option8: str = None,
        option9: str = None,
        option10: str = None,
        duration: app_commands.Range[int, 1, 10080] = None,
    ) -> None:
        opts = [option1, option2, option3, option4, option5, option6, option7, option8, option9, option10]
        await cog.create(interaction, question, [o for o in opts if o], duration)

    @poll_group.command(name="close", description="Close a poll early and post final results")
    @app_commands.describe(poll_id="Poll number (shown in the poll's footer)")
    async def close(interaction: discord.Interaction, poll_id: int) -> None:
        await cog.close(interaction, poll_id)

    @poll_group.command(name="results", description="Show a live tally without closing the poll")
    @app_commands.describe(poll_id="Poll number (shown in the poll's footer)")
    async def results(interaction: discord.Interaction, poll_id: int) -> None:
        await cog.results(interaction, poll_id)
