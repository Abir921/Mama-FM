"""Music cog — Lavalink playback via wavelink.

Commands: /mama play, skip, queue, loop, pause, resume, nowplaying, leave.
Auto-disconnects after VOICE_IDLE_MINUTES alone in a voice channel.
"""

import asyncio
import logging
import os

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("mamafm.music")


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

        await interaction.response.defer()
        player = self._player(interaction)
        if player is None:
            try:
                player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            except Exception:
                log.exception("Voice connect failed")
                return await interaction.followup.send("Couldn't join your voice channel.")
            player.autoplay = wavelink.AutoPlayMode.partial  # advance queue, no recommendations

        try:
            tracks = await wavelink.Playable.search(query)
        except wavelink.LavalinkLoadException:
            return await interaction.followup.send("Failed to load that — bad link or unsupported source.")
        if not tracks:
            return await interaction.followup.send(f"No results for `{query}`.")

        if isinstance(tracks, wavelink.Playlist):
            added = await player.queue.put_wait(tracks)
            msg = f"Queued playlist **{tracks.name}** ({added} tracks)."
        else:
            track = tracks[0]
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
