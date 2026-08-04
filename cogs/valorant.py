"""Valorant cog — on-demand stats via the HenrikDev API.

Commands: /mama valo link | stats | compare | leaderboard | unlink.
Every command fetches live from the API and replies in the invoking channel.

Endpoint versions matter here: account v1, MMR v3 and matches v4 are current.
MMR v1/v2 and matches v2/v3 are deprecated and return a different shape.
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
DEFAULT_PLATFORM = "pc"
RECENT_MATCHES = 5

# HenrikDev's free tier is rate limited, so the leaderboard fetches a few
# players at a time rather than firing one request per member at once.
LEADERBOARD_CONCURRENCY = 3

# Rough elo floor per tier, used only to sort unranked/missing elo sensibly.
RANK_EMOJI = {
    "Iron": "⬛", "Bronze": "🟫", "Silver": "⬜", "Gold": "🟨",
    "Platinum": "🟦", "Diamond": "🟪", "Ascendant": "🟩",
    "Immortal": "🟥", "Radiant": "✨",
}


def _rank_emoji(tier: str) -> str:
    return next((e for name, e in RANK_EMOJI.items() if tier.startswith(name)), "▫️")


class HenrikError(Exception):
    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HenrikDev API error {status}: {detail}")


class Valorant(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.getenv("HENRIK_API_KEY", "").strip()
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(headers={"Authorization": self.api_key})
        if not self.api_key:
            log.warning("HENRIK_API_KEY not set — Valorant commands will report it and stop")

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    # ---------- API ----------

    async def _get(self, path: str, **params) -> dict | list:
        url = f"{API_BASE}{path}"
        async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = {}
            if resp.status != 200:
                detail = ""
                if isinstance(body, dict):
                    errors = body.get("errors")
                    if isinstance(errors, list) and errors:
                        detail = str(errors[0].get("message", ""))
                raise HenrikError(resp.status, detail)
            return body.get("data") if isinstance(body, dict) else body

    async def _account(self, name: str, tag: str) -> dict:
        return await self._get(f"/valorant/v1/account/{name}/{tag}")

    async def _mmr(self, region: str, platform: str, name: str, tag: str) -> dict:
        # v3: /valorant/v3/mmr/{affinity}/{platform}/{name}/{tag}
        return await self._get(f"/valorant/v3/mmr/{region}/{platform}/{name}/{tag}")

    async def _matches(self, region: str, platform: str, name: str, tag: str) -> list:
        # v4: /valorant/v4/matches/{affinity}/{platform}/{name}/{tag}
        return await self._get(
            f"/valorant/v4/matches/{region}/{platform}/{name}/{tag}",
            mode="competitive",
            size=RECENT_MATCHES,
        )

    # ---------- parsing ----------

    @staticmethod
    def _parse_rank(mmr: dict) -> dict:
        """Pull current rank out of an MMR v3 payload."""
        current = (mmr or {}).get("current") or {}
        tier = (current.get("tier") or {}).get("name") or "Unranked"
        placement = current.get("leaderboard_placement") or {}
        return {
            "rank": tier,
            "rr": current.get("rr"),
            "elo": current.get("elo") or 0,
            "leaderboard": placement.get("rank"),
        }

    @staticmethod
    def _parse_matches(matches: list, puuid: str | None, name: str, tag: str) -> dict:
        """Aggregate K/D/A and wins from a matches v4 payload.

        Matches the requesting player by puuid where possible, since Riot IDs
        can be changed; falls back to name/tag for accounts linked before puuid
        was stored.
        """
        totals = {"kills": 0, "deaths": 0, "assists": 0, "wins": 0, "games": 0, "headshots": 0, "bodyshots": 0, "legshots": 0}

        for match in matches or []:
            players = match.get("players") or []
            me = None
            if puuid:
                me = next((p for p in players if p.get("puuid") == puuid), None)
            if me is None:
                me = next(
                    (
                        p
                        for p in players
                        if (p.get("name") or "").lower() == name.lower()
                        and (p.get("tag") or "").lower() == tag.lower()
                    ),
                    None,
                )
            if me is None:
                continue

            stats = me.get("stats") or {}
            totals["kills"] += stats.get("kills") or 0
            totals["deaths"] += stats.get("deaths") or 0
            totals["assists"] += stats.get("assists") or 0
            for shot in ("headshots", "bodyshots", "legshots"):
                totals[shot] += stats.get(shot) or 0
            totals["games"] += 1

            my_team = me.get("team_id")
            team = next((t for t in (match.get("teams") or []) if t.get("team_id") == my_team), None)
            if team and team.get("won"):
                totals["wins"] += 1

        return totals

    async def _fetch_stats(self, acct: dict) -> dict:
        """One player's live stats: current rank plus recent competitive form."""
        region = acct["region"]
        platform = acct.get("platform") or DEFAULT_PLATFORM
        name, tag = acct["riot_name"], acct["riot_tag"]

        mmr = await self._mmr(region, platform, name, tag)
        stats = {"name": f"{name}#{tag}", **self._parse_rank(mmr)}

        try:
            matches = await self._matches(region, platform, name, tag)
        except HenrikError as e:
            log.warning("Match history unavailable for %s#%s: %s", name, tag, e)
            matches = []

        stats.update(self._parse_matches(matches, acct.get("puuid"), name, tag))
        return stats

    # ---------- formatting ----------

    @staticmethod
    def _stat_lines(s: dict) -> str:
        rank = f"{_rank_emoji(s['rank'])} **{s['rank']}**"
        # Unrated accounts report rr/elo of 0; "0 RR" is just noise.
        if s.get("rr") is not None and s.get("elo"):
            rank += f" · {s['rr']} RR"
        if s.get("leaderboard"):
            rank += f" · #{s['leaderboard']} leaderboard"
        lines = [rank]

        if s.get("games"):
            deaths = max(1, s["deaths"])
            kd = s["kills"] / deaths
            losses = s["games"] - s["wins"]
            winrate = 100 * s["wins"] / s["games"]
            lines.append(f"**K/D/A** {s['kills']}/{s['deaths']}/{s['assists']} (KD {kd:.2f})")
            lines.append(f"**Win rate** {winrate:.0f}% — {s['wins']}W {losses}L")

            shots = s["headshots"] + s["bodyshots"] + s["legshots"]
            if shots:
                lines.append(f"**Headshot** {100 * s['headshots'] / shots:.0f}%")
            lines.append(f"*last {s['games']} competitive*")
        else:
            lines.append("*No recent competitive matches.*")
        return "\n".join(lines)

    def _error_message(self, e: Exception) -> str:
        if isinstance(e, HenrikError):
            if e.status in (401, 403):
                return "HenrikDev API key missing or invalid — set `HENRIK_API_KEY` in `.env`."
            if e.status == 404:
                return "Riot account not found. Check the spelling of `name#tag`."
            if e.status == 429:
                return "Rate limited by the Valorant API — wait a minute and try again."
            if e.status >= 500:
                return "The Valorant API is having problems right now. Try again shortly."
            return f"Valorant API error {e.status}{f' — {e.detail}' if e.detail else ''}."
        if isinstance(e, asyncio.TimeoutError):
            return "The Valorant API timed out. Try again."
        return "Couldn't reach the Valorant API."

    def _needs_key(self) -> str | None:
        if not self.api_key:
            return (
                "No HenrikDev API key configured. Get one at https://docs.henrikdev.xyz "
                "and set `HENRIK_API_KEY` in `.env`, then restart the bot."
            )
        return None

    # ---------- storage ----------

    async def _linked(self, user_id: int) -> dict | None:
        async with db.connect() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM valorant_accounts WHERE discord_user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    # ---------- commands ----------

    async def link(self, interaction: discord.Interaction, riot_id: str, platform: str) -> None:
        if msg := self._needs_key():
            return await interaction.response.send_message(msg, ephemeral=True)
        if "#" not in riot_id:
            return await interaction.response.send_message(
                "Use the format `name#tag`, for example `TenZ#SEN`.", ephemeral=True
            )

        name, _, tag = riot_id.rpartition("#")
        name, tag = name.strip(), tag.strip()
        if not name or not tag:
            return await interaction.response.send_message("Use the format `name#tag`.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            account = await self._account(name, tag)
        except Exception as e:
            log.exception("Account lookup failed")
            return await interaction.followup.send(self._error_message(e), ephemeral=True)

        resolved_name = account.get("name") or name
        resolved_tag = account.get("tag") or tag
        region = account.get("region") or "eu"

        async with db.connect() as conn:
            await conn.execute(
                "INSERT INTO valorant_accounts"
                " (discord_user_id, riot_name, riot_tag, region, linked_at, puuid, platform)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(discord_user_id) DO UPDATE SET"
                " riot_name = excluded.riot_name, riot_tag = excluded.riot_tag,"
                " region = excluded.region, linked_at = excluded.linked_at,"
                " puuid = excluded.puuid, platform = excluded.platform",
                (
                    interaction.user.id,
                    resolved_name,
                    resolved_tag,
                    region,
                    datetime.now(timezone.utc).isoformat(),
                    account.get("puuid"),
                    platform,
                ),
            )
            await conn.commit()

        await interaction.followup.send(
            f"Linked **{resolved_name}#{resolved_tag}** ({region.upper()}, {platform}) "
            f"to {interaction.user.mention}. Try `/mama valo stats`.",
            ephemeral=True,
        )

    async def unlink(self, interaction: discord.Interaction) -> None:
        async with db.connect() as conn:
            cur = await conn.execute(
                "DELETE FROM valorant_accounts WHERE discord_user_id = ?", (interaction.user.id,)
            )
            await conn.commit()
        if cur.rowcount:
            await interaction.response.send_message("Riot ID unlinked.", ephemeral=True)
        else:
            await interaction.response.send_message("You had no Riot ID linked.", ephemeral=True)

    async def stats(self, interaction: discord.Interaction, user: discord.User | None) -> None:
        if msg := self._needs_key():
            return await interaction.response.send_message(msg, ephemeral=True)

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
            return await interaction.followup.send(self._error_message(e))

        embed = discord.Embed(
            title=f"🎯 {s['name']}",
            description=self._stat_lines(s),
            color=discord.Color.red(),
        )
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.set_footer(text=f"{acct['region'].upper()} · {acct.get('platform') or DEFAULT_PLATFORM} · live from HenrikDev")
        await interaction.followup.send(embed=embed)

    async def compare(
        self, interaction: discord.Interaction, user1: discord.User, user2: discord.User
    ) -> None:
        if msg := self._needs_key():
            return await interaction.response.send_message(msg, ephemeral=True)
        if user1.id == user2.id:
            return await interaction.response.send_message(
                "Pick two different people.", ephemeral=True
            )

        accounts = []
        for u in (user1, user2):
            acct = await self._linked(u.id)
            if acct is None:
                return await interaction.response.send_message(
                    f"{u.display_name} hasn't linked a Riot ID yet.", ephemeral=True
                )
            accounts.append(acct)

        await interaction.response.defer()
        try:
            s1, s2 = await asyncio.gather(*(self._fetch_stats(a) for a in accounts))
        except Exception as e:
            log.exception("Compare fetch failed")
            return await interaction.followup.send(self._error_message(e))

        embed = discord.Embed(title="⚔️ Head to head", color=discord.Color.red())
        embed.add_field(name=f"{user1.display_name} · {s1['name']}", value=self._stat_lines(s1), inline=True)
        embed.add_field(name=f"{user2.display_name} · {s2['name']}", value=self._stat_lines(s2), inline=True)

        # Call it on elo, falling back to KD when both are unranked.
        verdict = None
        if s1["elo"] != s2["elo"]:
            higher = user1 if s1["elo"] > s2["elo"] else user2
            verdict = f"{higher.display_name} is the higher rank."
        elif s1.get("games") and s2.get("games"):
            kd1 = s1["kills"] / max(1, s1["deaths"])
            kd2 = s2["kills"] / max(1, s2["deaths"])
            if abs(kd1 - kd2) > 0.01:
                verdict = f"{(user1 if kd1 > kd2 else user2).display_name} has the better recent K/D."
        if verdict:
            embed.set_footer(text=verdict)
        await interaction.followup.send(embed=embed)

    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if msg := self._needs_key():
            return await interaction.response.send_message(msg, ephemeral=True)

        async with db.connect() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM valorant_accounts") as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        members: list[tuple[discord.Member, dict]] = []
        for row in rows:
            member = interaction.guild.get_member(row["discord_user_id"])
            if member is not None:
                members.append((member, row))

        if not members:
            return await interaction.response.send_message(
                "Nobody in this server has linked a Riot ID yet — use `/mama valo link`.",
                ephemeral=True,
            )

        await interaction.response.defer()
        semaphore = asyncio.Semaphore(LEADERBOARD_CONCURRENCY)

        async def one(member: discord.Member, row: dict) -> dict | None:
            async with semaphore:
                try:
                    mmr = await self._mmr(
                        row["region"], row.get("platform") or DEFAULT_PLATFORM,
                        row["riot_name"], row["riot_tag"],
                    )
                except Exception:
                    log.warning("Leaderboard fetch failed for %s#%s", row["riot_name"], row["riot_tag"])
                    return None
                return {
                    "member": member,
                    "riot": f"{row['riot_name']}#{row['riot_tag']}",
                    **self._parse_rank(mmr),
                }

        entries = [e for e in await asyncio.gather(*(one(m, r) for m, r in members)) if e]
        if not entries:
            return await interaction.followup.send(
                "Couldn't fetch ranks for anyone — the API may be rate limiting or down."
            )

        entries.sort(key=lambda e: e["elo"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, e in enumerate(entries):
            place = medals[i] if i < 3 else f"`{i + 1}.`"
            rr = f" · {e['rr']} RR" if e.get("rr") is not None else ""
            lines.append(
                f"{place} **{e['member'].display_name}** — {_rank_emoji(e['rank'])} {e['rank']}{rr}\n"
                f"　　`{e['riot']}`"
            )

        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} — Valorant ranks",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        skipped = len(members) - len(entries)
        if skipped:
            embed.set_footer(text=f"{skipped} player(s) couldn't be fetched")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    cog = Valorant(bot)
    await bot.add_cog(cog)

    valo = app_commands.Group(
        name="valo", description="Valorant stats", guild_only=True, parent=bot.mama
    )

    platform_choices = [
        app_commands.Choice(name="PC", value="pc"),
        app_commands.Choice(name="Console", value="console"),
    ]

    @valo.command(name="link", description="Link your Riot ID to your Discord account")
    @app_commands.describe(riot_id="Your Riot ID as name#tag, e.g. TenZ#SEN", platform="PC or console")
    @app_commands.choices(platform=platform_choices)
    async def link(
        interaction: discord.Interaction,
        riot_id: str,
        platform: app_commands.Choice[str] = None,
    ) -> None:
        await cog.link(interaction, riot_id, platform.value if platform else DEFAULT_PLATFORM)

    @valo.command(name="unlink", description="Remove your linked Riot ID")
    async def unlink(interaction: discord.Interaction) -> None:
        await cog.unlink(interaction)

    @valo.command(name="stats", description="Current rank, recent K/D/A and win rate")
    @app_commands.describe(user="Whose stats to show (default: you)")
    async def stats(interaction: discord.Interaction, user: discord.User = None) -> None:
        await cog.stats(interaction, user)

    @valo.command(name="compare", description="Side-by-side comparison of two players")
    async def compare(interaction: discord.Interaction, user1: discord.User, user2: discord.User) -> None:
        await cog.compare(interaction, user1, user2)

    @valo.command(name="leaderboard", description="Rank leaderboard for everyone linked in this server")
    async def leaderboard(interaction: discord.Interaction) -> None:
        await cog.leaderboard(interaction)
