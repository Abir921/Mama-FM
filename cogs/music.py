"""Music cog — Lavalink playback via wavelink.

Commands: /mama play, skip, queue, loop, pause, resume, nowplaying, leave.
Auto-disconnects after VOICE_IDLE_MINUTES alone in a voice channel.
"""

import asyncio
import logging
import os
import re

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("mamafm.music")

# YouTube increasingly refuses anonymous audio extraction ("This video requires
# login"), even though its search/metadata stays public. SoundCloud needs no
# auth, so it doubles as the default fallback when a YouTube track won't play.
SOURCES = {
    "youtube": wavelink.TrackSource.YouTube,
    "youtubemusic": wavelink.TrackSource.YouTubeMusic,
    "soundcloud": wavelink.TrackSource.SoundCloud,
}
FALLBACK_SOURCE = wavelink.TrackSource.SoundCloud

# Individual tracks often can't be streamed even when the source works: SoundCloud
# serves some tracks only as encrypted HLS, and some transcodings simply 404. So on
# failure we walk to the next search result rather than retrying the same one.
MAX_PLAY_ATTEMPTS = 4


def _is_url(query: str) -> bool:
    return query.strip().lower().startswith(("http://", "https://"))


def _is_preview(track: wavelink.Playable) -> bool:
    """True for SoundCloud 30-second previews (policy=SNIP).

    These advertise the full duration in their metadata but only serve a short
    clip, so they play normally and then cut out partway through. Their stream
    URL uses /preview/ where a full track uses /stream/.
    """
    return "/preview/" in (track.identifier or "")


def _pick_track(tracks: list[wavelink.Playable]) -> wavelink.Playable:
    """First fully playable result, falling back to the top hit."""
    return next((t for t in tracks if not _is_preview(t)), tracks[0])


def _query_variants(track: wavelink.Playable) -> list[str]:
    """Progressively simpler search terms for finding a track on another source.

    Uploaded titles are messy ("Song A/Song B (SEAMLESS TRANSITION) - Artist x
    Artist") and on YouTube ``author`` is the uploading channel rather than the
    artist, so searching either verbatim usually returns nothing. Strip the noise
    down and try the plainest forms first.
    """
    title = (track.title or "").strip()
    author = (track.author or "").strip()

    # Drop "(Official Video)", "[4K]" and similar.
    cleaned = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")

    # "Song - Artist": keep the first song of a mashup and the first artist.
    song, _, rest = cleaned.partition(" - ")
    song = re.split(r"\s*[/|]\s*", song)[0].strip()
    artist = re.split(r"\s+x\s+|,|&|feat\.?|ft\.?", rest, flags=re.I)[0].strip() if rest else ""

    variants = [
        f"{song} {artist}".strip() if song and artist else "",
        song,
        f"{song} {author}".strip() if song and author else "",
        cleaned,
        title,
    ]

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _short_reason(cause: str, limit: int = 180) -> str:
    """First meaningful line of a Lavalink error.

    Lavalink returns full Java stack traces; posting one raw can exceed
    Discord's 2000 character message limit and fail to send at all.
    """
    for line in (cause or "").splitlines():
        line = line.strip()
        if line and not line.startswith("at "):
            return line[:limit] + ("…" if len(line) > limit else "")
    return "unknown error"


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.idle_minutes = int(os.getenv("VOICE_IDLE_MINUTES", "5"))
        self._idle_tasks: dict[int, asyncio.Task] = {}  # guild_id -> pending disconnect
        self.source = SOURCES.get(
            os.getenv("MUSIC_SOURCE", "").strip().lower(), wavelink.TrackSource.YouTubeMusic
        )
        self.fallback = os.getenv("MUSIC_FALLBACK", "1").strip().lower() not in ("0", "false", "no")

    async def cog_load(self) -> None:
        # Pool.connect keeps retrying while Lavalink is down, so run it in the
        # background — otherwise it would block bot startup entirely.
        self._connect_task = asyncio.create_task(self._connect_node())

    async def _connect_node(self) -> None:
        node = wavelink.Node(
            uri=os.getenv("LAVALINK_URI", "http://127.0.0.1:2333"),
            password=os.getenv("LAVALINK_PASSWORD", "youshallnotpass"),
        )
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
        except Exception:
            log.exception("Could not connect to Lavalink — music commands will fail until it is up")

    async def cog_unload(self) -> None:
        self._connect_task.cancel()
        for task in self._idle_tasks.values():
            task.cancel()
        await wavelink.Pool.close()

    # ---------- helpers ----------

    @staticmethod
    def _node_up() -> bool:
        return any(
            n.status is wavelink.NodeStatus.CONNECTED for n in wavelink.Pool.nodes.values()
        )

    @staticmethod
    def _channel_problem(channel: discord.VoiceChannel, me: discord.Member) -> str | None:
        """Why the bot can't join this channel, or None if it can.

        Server-wide permissions from the invite are routinely overridden per
        channel, so this has to be checked against the specific channel.
        """
        perms = channel.permissions_for(me)
        missing = [
            name
            for name, ok in (
                ("View Channel", perms.view_channel),
                ("Connect", perms.connect),
                ("Speak", perms.speak),
            )
            if not ok
        ]
        if missing:
            return (
                f"I don't have permission to join **{channel.name}** — missing "
                f"{', '.join(f'`{m}`' for m in missing)}.\n"
                "Ask a server admin to allow those for me on that channel, "
                "or use a channel where I already have access."
            )
        if channel.user_limit and len(channel.members) >= channel.user_limit:
            return (
                f"**{channel.name}** is full ({len(channel.members)}/{channel.user_limit}). "
                "Bots need a free slot unless they have `Move Members`."
            )
        return None

    def _player(self, interaction: discord.Interaction) -> wavelink.Player | None:
        vc = interaction.guild.voice_client
        return vc if isinstance(vc, wavelink.Player) else None

    @staticmethod
    async def _reply(interaction: discord.Interaction, content: str = None, *, embed: discord.Embed = None, ephemeral: bool = False) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, embed=embed, ephemeral=ephemeral)

    # ---------- events ----------

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        log.info("Lavalink node ready: %s", payload.node.identifier)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        # AutoPlayMode.partial advances the queue automatically; nothing to do here.
        pass

    async def _next_candidate(
        self, query: str, tried: list[str], failed: wavelink.Playable | None = None
    ) -> wavelink.Playable | None:
        """Next search result not yet attempted, preferring the configured source."""
        # A URL always resolves back to the same track on every source, so once it
        # fails there is nothing left to walk. Search the title instead.
        queries = [query]
        if _is_url(query) and failed is not None:
            queries = _query_variants(failed)

        sources = [self.source]
        if FALLBACK_SOURCE != self.source:
            sources.append(FALLBACK_SOURCE)

        preview_fallback: wavelink.Playable | None = None

        for text in queries:
            for source in sources:
                try:
                    results = await wavelink.Playable.search(text, source=source)
                except Exception:
                    continue
                if not results or isinstance(results, wavelink.Playlist):
                    continue
                for candidate in results:
                    if candidate.identifier in tried:
                        continue
                    if _is_preview(candidate):
                        # Keep as a last resort; 30s of audio beats nothing.
                        preview_fallback = preview_fallback or candidate
                        continue
                    return candidate

        return preview_fallback

    async def _announce(self, player: wavelink.Player, message: str) -> None:
        channel = getattr(player, "home", None)
        if channel is None:
            return
        try:
            await channel.send(message)
        except discord.HTTPException:
            log.warning("Couldn't post playback notice to %s", channel)

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        """A track failed mid-load. Retry once on the fallback source, else say so.

        Without this the user just sees "Queued ..." and then silence, because the
        failure happens asynchronously long after the command has replied.
        """
        player, track = payload.player, payload.track
        # exception is a TypedDict (message/severity/cause), not an object.
        exc = payload.exception or {}
        cause = _short_reason(exc.get("message") or exc.get("cause") or "")
        # Full trace goes to the log; the channel gets the one-line version.
        log.warning("Track failed: %s [%s] — %s", track.title, track.source, exc)

        if player is None:
            return

        extras = dict(getattr(track, "extras", None) or {})
        query = extras.get("query")

        tried: list[str] = list(extras.get("tried") or [])
        if track.identifier not in tried:
            tried.append(track.identifier)

        if self.fallback and query and len(tried) < MAX_PLAY_ATTEMPTS:
            alt = await self._next_candidate(query, tried, failed=track)
            if alt is not None:
                alt.extras = {**extras, "tried": tried}
                await self._announce(
                    player,
                    f"⚠️ **{track.title}** wouldn't play ({cause}). "
                    f"Trying **{alt.title}** from `{alt.source}` instead.",
                )
                await player.play(alt)
                return

        await self._announce(
            player, f"❌ Couldn't play **{track.title}** from `{track.source}` — {cause}"
        )
        if not player.queue.is_empty:
            await player.play(player.queue.get())

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        player, track = payload.player, payload.track
        log.warning("Track stuck: %s", track.title)
        if player is None:
            return
        await self._announce(player, f"⏭️ **{track.title}** stalled — skipping.")
        if not player.queue.is_empty:
            await player.play(player.queue.get())

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Start a disconnect countdown when the bot is left alone; cancel it when someone returns."""
        guild = member.guild
        vc = guild.voice_client
        if not isinstance(vc, wavelink.Player) or not vc.channel:
            return

        humans = [m for m in vc.channel.members if not m.bot]
        pending = self._idle_tasks.pop(guild.id, None)
        if pending:
            pending.cancel()
        if not humans:
            self._idle_tasks[guild.id] = asyncio.create_task(self._idle_disconnect(guild.id))

    async def _idle_disconnect(self, guild_id: int) -> None:
        try:
            await asyncio.sleep(self.idle_minutes * 60)
        except asyncio.CancelledError:
            return
        self._idle_tasks.pop(guild_id, None)
        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None
        if isinstance(vc, wavelink.Player):
            humans = [m for m in vc.channel.members if not m.bot]
            if not humans:
                await vc.disconnect()
                log.info("Auto-disconnected from empty channel in guild %s", guild_id)

    # ---------- command callbacks ----------

    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if not self._node_up():
            return await self._reply(
                interaction,
                "Music backend (Lavalink) isn't running — see README to start it.",
                ephemeral=True,
            )
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await self._reply(interaction, "Join a voice channel first.", ephemeral=True)

        channel = interaction.user.voice.channel

        # Check access up front. Without it discord.py waits the full 30s for a
        # voice handshake that a permission-denied channel will never complete.
        if problem := self._channel_problem(channel, interaction.guild.me):
            return await self._reply(interaction, problem, ephemeral=True)

        await interaction.response.defer()
        player = self._player(interaction)
        if player is None:
            try:
                player = await channel.connect(cls=wavelink.Player)
            except wavelink.ChannelTimeoutException:
                log.warning("Voice connect timed out for %s", channel)
                return await interaction.followup.send(
                    f"Timed out joining **{channel.name}**. Discord may be having "
                    "voice issues, or the channel is restricted — try another one."
                )
            except Exception:
                log.exception("Voice connect failed")
                return await interaction.followup.send("Couldn't join your voice channel.")
            player.autoplay = wavelink.AutoPlayMode.partial  # advance queue, no recommendations

        # Where playback problems get reported, since they surface long after this reply.
        player.home = interaction.channel

        try:
            tracks = await wavelink.Playable.search(query, source=self.source)
        except wavelink.LavalinkLoadException:
            return await interaction.followup.send("Failed to load that — bad link or unsupported source.")
        if not tracks:
            return await interaction.followup.send(f"No results for `{query}`.")

        # Keep the query on the track so a failed load can advance to another result.
        extras = {"query": query, "requested_by": interaction.user.id, "tried": []}

        if isinstance(tracks, wavelink.Playlist):
            tracks.extras = extras
            added = await player.queue.put_wait(tracks)
            msg = f"Queued playlist **{tracks.name}** ({added} tracks)."
        else:
            track = _pick_track(list(tracks))
            track.extras = extras
            await player.queue.put_wait(track)
            msg = f"Queued **{track.title}** ({_fmt_ms(track.length)})."

        if not player.playing:
            await player.play(player.queue.get())
        await interaction.followup.send(msg)

    async def skip(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            return await self._reply(interaction, "Nothing is playing.", ephemeral=True)
        title = player.current.title
        await player.skip(force=True)
        await self._reply(interaction, f"Skipped **{title}**.")

    async def queue(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or (not player.current and player.queue.is_empty):
            return await self._reply(interaction, "Queue is empty.", ephemeral=True)

        lines = []
        if player.current:
            lines.append(f"**Now:** {player.current.title} ({_fmt_ms(player.current.length)})")
        for i, track in enumerate(list(player.queue)[:10], start=1):
            lines.append(f"`{i}.` {track.title} ({_fmt_ms(track.length)})")
        remaining = len(player.queue) - 10
        if remaining > 0:
            lines.append(f"…and {remaining} more.")

        embed = discord.Embed(title="Queue", description="\n".join(lines), color=discord.Color.blurple())
        mode = player.queue.mode
        if mode is not wavelink.QueueMode.normal:
            embed.set_footer(text=f"Loop: {'track' if mode is wavelink.QueueMode.loop else 'queue'}")
        await self._reply(interaction, embed=embed)

    async def loop(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player:
            return await self._reply(interaction, "Not playing anything.", ephemeral=True)
        # Cycle: off -> loop track -> loop queue -> off
        cycle = {
            wavelink.QueueMode.normal: (wavelink.QueueMode.loop, "Looping **current track**."),
            wavelink.QueueMode.loop: (wavelink.QueueMode.loop_all, "Looping **whole queue**."),
            wavelink.QueueMode.loop_all: (wavelink.QueueMode.normal, "Loop **off**."),
        }
        player.queue.mode, msg = cycle[player.queue.mode]
        await self._reply(interaction, msg)

    async def pause(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            return await self._reply(interaction, "Nothing is playing.", ephemeral=True)
        if player.paused:
            return await self._reply(interaction, "Already paused.", ephemeral=True)
        await player.pause(True)
        await self._reply(interaction, "Paused.")

    async def resume(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            return await self._reply(interaction, "Nothing is playing.", ephemeral=True)
        if not player.paused:
            return await self._reply(interaction, "Not paused.", ephemeral=True)
        await player.pause(False)
        await self._reply(interaction, "Resumed.")

    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player or not player.current:
            return await self._reply(interaction, "Nothing is playing.", ephemeral=True)

        track = player.current
        pos, length = player.position, track.length
        bar_len = 20
        filled = int(bar_len * pos / length) if length else 0
        bar = "▬" * filled + "🔘" + "▬" * (bar_len - filled)

        embed = discord.Embed(
            title="Now playing",
            description=f"**{track.title}**\n{bar}\n`{_fmt_ms(pos)} / {_fmt_ms(length)}`",
            color=discord.Color.blurple(),
        )
        if track.author:
            embed.add_field(name="Artist", value=track.author)
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        if player.paused:
            embed.set_footer(text="⏸ Paused")
        await self._reply(interaction, embed=embed)

    async def leave(self, interaction: discord.Interaction) -> None:
        player = self._player(interaction)
        if not player:
            return await self._reply(interaction, "Not in a voice channel.", ephemeral=True)
        await player.disconnect()
        await self._reply(interaction, "Left the voice channel. 👋")


async def setup(bot: commands.Bot) -> None:
    cog = Music(bot)
    await bot.add_cog(cog)
    mama: app_commands.Group = bot.mama

    @mama.command(name="play", description="Play or queue a track (search or URL)")
    @app_commands.describe(query="Song name or URL")
    @app_commands.guild_only()
    async def play(interaction: discord.Interaction, query: str) -> None:
        await cog.play(interaction, query)

    @mama.command(name="skip", description="Skip the current track")
    @app_commands.guild_only()
    async def skip(interaction: discord.Interaction) -> None:
        await cog.skip(interaction)

    @mama.command(name="queue", description="Show upcoming tracks")
    @app_commands.guild_only()
    async def queue(interaction: discord.Interaction) -> None:
        await cog.queue(interaction)

    @mama.command(name="loop", description="Cycle loop mode: off → track → queue")
    @app_commands.guild_only()
    async def loop(interaction: discord.Interaction) -> None:
        await cog.loop(interaction)

    @mama.command(name="pause", description="Pause playback")
    @app_commands.guild_only()
    async def pause(interaction: discord.Interaction) -> None:
        await cog.pause(interaction)

    @mama.command(name="resume", description="Resume playback")
    @app_commands.guild_only()
    async def resume(interaction: discord.Interaction) -> None:
        await cog.resume(interaction)

    @mama.command(name="nowplaying", description="Show the current track and progress")
    @app_commands.guild_only()
    async def nowplaying(interaction: discord.Interaction) -> None:
        await cog.nowplaying(interaction)

    @mama.command(name="leave", description="Disconnect from voice")
    @app_commands.guild_only()
    async def leave(interaction: discord.Interaction) -> None:
        await cog.leave(interaction)
