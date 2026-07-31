"""Valorant cog — on-demand stats via the HenrikDev API.

Commands: /mama valo link | stats | compare | leaderboard.
Every command fetches live from the API and replies in the invoking channel.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import db

log = logging.getLogger("mamafm.valo")

API_BASE = "https://api.henrikdev.xyz"


class HenrikError(Exception):
    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        super().__init__(f"HenrikDev API error {status}: {detail}")


class Valorant(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.getenv("HENRIK_API_KEY", "")
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(headers={"Authorization": self.api_key})
        if not self.api_key:
            log.warning("HENRIK_API_KEY not set — Valorant commands will fail")

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    # ---------- API helpers ----------

    async def _get(self, path: str, **params) -> dict:
        url = f"{API_BASE}{path}"
        async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                detail = str(body.get("errors", body)) if isinstance(body, dict) else ""
                raise HenrikError(resp.status, detail)
            return body["data"]

    async def _get_account(self, name: str, tag: str) -> dict:
        return await self._get(f"/valorant/v1/account/{name}/{tag}")

    async def _get_mmr(self, region: str, name: str, tag: str) -> dict:
        return await self._get(f"/valorant/v2/mmr/{region}/{name}/{tag}")

    async def _get_matches(self, region: str, name: str, tag: str, size: int = 5) -> list[dict]:
        return await self._get(
            f"/valorant/v3/matches/{region}/{name}/{tag}", mode="competitive", size=size
        )

    async def _linked(self, user_id: int) -> dict | None:
        async with db.connect() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM valorant_accounts WHERE discord_user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _err_msg(e: Exception) -> str:
        if isinstance(e, HenrikError):
            if e.status == 401:
                return "HenrikDev API key missing or invalid — set HENRIK_API_KEY in .env."
            if e.status == 404:
                return "Riot account not found. Check the name#tag."
            if e.status == 429:
                return "Rate-limited by the Valorant API — wait a minute and retry."
            return f"Valorant API error ({e.status})."
        return "Valorant API request failed."

    # ---------- stat crunching ----------

    async def _fetch_stats(self, acct: dict) -> dict:
        """One player's live stats: rank + recent competitive performance."""
        region, name, tag = acct["region"], acct["riot_name"], acct["riot_tag"]
        mmr = await self._get_mmr(region, name, tag)
        current = mmr.get("current_data") or {}

        stats = {
            "name": f"{name}#{tag}",
            "rank": current.get("currenttierpatched") or "Unranked",
            "rr": current.get("ranking_in_tier"),
            "elo": current.get("elo"),
            "kills": 0, "deaths": 0, "assists": 0, "wins": 0, "games": 0,
        }

        try:
            matches = await self._get_matches(region, name, tag)
        except HenrikError:
            matches = []
        me_lower = name.lower()
        for match in matches:
            players = match.get("players", {}).get("all_players", [])
            me = next(
                (p for p in players if p["name"].lower() == me_lower and p["tag"].lower() == tag.lower()),
                None,
            )
            if me is None:
                continue
            s = me.get("stats", {})
            stats["kills"] += s.get("kills", 0)
            stats["deaths"] += s.get("deaths", 0)
            stats["assists"] += s.get("assists", 0)
            stats["games"] += 1
            my_team = (me.get("team") or "").lower()
            team_data = match.get("teams", {}).get(my_team, {})
            if team_data.get("has_won"):
                stats["wins"] += 1
        return stats

    @staticmethod
    def _stat_lines(s: dict) -> str:
        lines = [f"**Rank:** {s['rank']}" + (f" ({s['rr']} RR)" if s["rr"] is not None else "")]
        if s["games"]:
            kd = s["kills"] / max(1, s["deaths"])
            lines.append(
                f"**Last {s['games']} comp games:** {s['kills']}/{s['deaths']}/{s['assists']} "
                f"(K/D {kd:.2f})"
            )
            lines.append(f"**Win rate:** {100 * s['wins'] / s['games']:.0f}% ({s['wins']}W {s['games'] - s['wins']}L)")
        else:
            lines.append("No recent competitive matches found.")
        return "\n".join(lines)

    # ---------- command callbacks ----------

    async def link(self, interaction: discord.Interaction, riot_id: str) -> None:
        if "#" not in riot_id:
            return await interaction.response.send_message(
                "Format is `name#tag`, e.g. `TenZ#SEN`.", ephemeral=True
            )
        name, _, tag = riot_id.rpartition("#")
        name, tag = name.strip(), tag.strip()
        if not name or not tag:
            return await interaction.response.send_message("Format is `name#tag`.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            account = await self._get_account(name, tag)
        except Exception as e:
            log.exception("Account lookup failed")
            return await interaction.followup.send(self._err_msg(e), ephemeral=True)

        region = account.get("region", "eu")
        async with db.connect() as conn:
            await conn.execute(
                "INSERT INTO valorant_accounts (discord_user_id, riot_name, riot_tag, region, linked_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(discord_user_id) DO UPDATE SET"
                " riot_name = excluded.riot_name, riot_tag = excluded.riot_tag,"
                " region = excluded.region, linked_at = excluded.linked_at",
                (
                    interaction.user.id,
                    account.get("name", name),
                    account.get("tag", tag),
                    region,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await conn.commit()
        await interaction.followup.send(
            f"Linked **{account.get('name', name)}#{account.get('tag', tag)}** ({region.upper()}) "
            f"to {interaction.user.mention}.",
            ephemeral=True,
        )

    async def stats(self, interaction: discord.Interaction, user: discord.User | None) -> None:
        target = user or interaction.user
        acct = await self._linked(target.id)
        if acct is None:
            who = "You haven't" if target == interaction.user else f"{target.display_name} hasn't"
            return await interaction.response.send_message(
                f"{who} linked a Riot ID yet — use `/mama valo link`.", ephemeral=True
            )
        await interaction.response.defer()
        try:
            s = await self._fetch_stats(acct)
        except Exception as e:
            log.exception("Stats fetch failed")
            return await interaction.followup.send(self._err_msg(e))

        embed = discord.Embed(
            title=f"🎯 {s['name']}",
            description=self._stat_lines(s),
            color=discord.Color.red(),
        )
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)

    async def compare(
        self, interaction: discord.Interaction, user1: discord.User, user2: discord.User
    ) -> None:
        accts = []
        for u in (user1, user2):
            acct = await self._linked(u.id)
            if acct is None:
                return await interaction.response.send_message(
                    f"{u.display_name} hasn't linked a Riot ID yet.", ephemeral=True
                )
            accts.append(acct)

        await interaction.response.defer()
        try:
            s1, s2 = await asyncio.gather(*(self._fetch_stats(a) for a in accts))
        except Exception as e:
            log.exception("Compare fetch failed")
            return await interaction.followup.send(self._err_msg(e))

        embed = discord.Embed(title="⚔️ Head to head", color=discord.Color.red())
        embed.add_field(name=f"{user1.display_name} · {s1['name']}", value=self._stat_lines(s1), inline=True)
        embed.add_field(name=f"{user2.display_name} · {s2['name']}", value=self._stat_lines(s2), inline=True)
        await interaction.followup.send(embed=embed)

    async def leaderboard(self, interaction: discord.Interaction) -> None:
        async with db.connect() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM valorant_accounts") as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        # Only members of this server.
        linked = []
        for row in rows:
            member = interaction.guild.get_member(row["discord_user_id"])
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(row["discord_user_id"])
                except discord.HTTPException:
                    continue
            linked.append((member, row))

        if not linked:
            return await interaction.response.send_message(
                "Nobody here has linked a Riot ID yet — use `/mama valo link`.", ephemeral=True
            )

        await interaction.response.defer()
        entries = []
        for member, row in linked:
            try:
                mmr = await self._get_mmr(row["region"], row["riot_name"], row["riot_tag"])
            except Exception:
                log.exception("Leaderboard fetch failed for %s", row["riot_name"])
                continue
            current = mmr.get("current_data") or {}
            entries.append({
                "member": member,
                "riot": f"{row['riot_name']}#{row['riot_tag']}",
                "rank": current.get("currenttierpatched") or "Unranked",
                "elo": current.get("elo") or 0,
            })

        if not entries:
            return await interaction.followup.send("Couldn't fetch stats for anyone — API trouble?")

        entries.sort(key=lambda e: e["elo"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'`{i + 1}.`'} **{e['member'].display_name}** "
            f"({e['riot']}) — {e['rank']}"
            for i, e in enumerate(entries)
        ]
        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Valorant leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    cog = Valorant(bot)
    await bot.add_cog(cog)

    valo = app_commands.Group(
        name="valo", description="Valorant stats", guild_only=True, parent=bot.mama
    )

    @valo.command(name="link", description="Link your Riot ID to your Discord account")
    @app_commands.describe(riot_id="Your Riot ID as name#tag, e.g. TenZ#SEN")
    async def link(interaction: discord.Interaction, riot_id: str) -> None:
        await cog.link(interaction, riot_id)

    @valo.command(name="stats", description="Current rank, recent K/D/A, and win rate")
    @app_commands.describe(user="Whose stats (default: you)")
    async def stats(interaction: discord.Interaction, user: discord.User = None) -> None:
        await cog.stats(interaction, user)

    @valo.command(name="compare", description="Side-by-side stat comparison of two players")
    async def compare(interaction: discord.Interaction, user1: discord.User, user2: discord.User) -> None:
        await cog.compare(interaction, user1, user2)

    @valo.command(name="leaderboard", description="Rank leaderboard of everyone linked in this server")
    async def leaderboard(interaction: discord.Interaction) -> None:
        await cog.leaderboard(interaction)
