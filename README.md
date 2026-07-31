# Mama FM 📻

Discord bot: music, memes, polls, Valorant stats. All commands live under `/mama`.

## Setup

1. **Python deps** (already installed into `.venv`):
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

2. **Discord application** — [discord.com/developers/applications](https://discord.com/developers/applications):
   - New Application → Bot → copy the **token**.
   - No privileged intents required.
   - Invite URL (OAuth2 → URL Generator): scopes `bot` + `applications.commands`; permissions:
     **Send Messages, Embed Links, Attach Files, Add Reactions, Manage Messages,
     Read Message History, Connect, Speak**.
     (*Manage Messages* is what lets the poll system remove a user's old vote.)

3. **Config** — copy `.env.example` to `.env`, fill in:
   - `DISCORD_TOKEN` — required.
   - `GUILD_ID` — your server ID; makes slash commands appear instantly instead of ~1 hour.
   - `HENRIK_API_KEY` — for Valorant commands ([docs.henrikdev.xyz](https://docs.henrikdev.xyz), free tier is fine).

4. **Lavalink** (music only) — needs Java 17+:
   ```
   .\start-lavalink.ps1
   ```
   The script finds a JDK 17+ (checking `JAVA_HOME` first) and launches
   `lavalink/Lavalink.jar`, which picks up `lavalink/application.yml` automatically.
   Wait for `Lavalink is ready to accept connections.`

   - If `Lavalink.jar` is missing, download it from
     [github.com/lavalink-devs/Lavalink/releases](https://github.com/lavalink-devs/Lavalink/releases)
     into `lavalink/`. It is ~96 MB and deliberately not committed.
   - The YouTube source plugin is downloaded automatically on first start.
     If playback starts failing, bump the `youtube-plugin` version in
     `application.yml` — YouTube breaks old extractors periodically and Lavalink
     logs a warning when a newer one exists.
   - Run Lavalink **before** the bot, and keep it running alongside.
     Everything except music works fine with Lavalink down.

5. **Run the bot**:
   ```
   .venv\Scripts\python bot.py
   ```

## Commands

| Command | What it does |
|---|---|
| `/mama play <query>` | Play/queue a track, joins your voice channel |
| `/mama skip` · `pause` · `resume` · `leave` | Playback control |
| `/mama queue` · `nowplaying` | What's queued / playing |
| `/mama loop` | Cycle loop: off → track → queue |
| `/mama meme` | Random meme |
| `/mama caption <image> <top> <bottom>` | Impact-font caption an image |
| `/mama poll create <q> <opt1..10> [duration]` | Reaction poll, auto-closes; one vote per person |
| `/mama poll close <id>` · `results <id>` | Close early / live tally |
| `/mama valo link <name#tag>` | Link your Riot ID |
| `/mama valo stats [@user]` · `compare` · `leaderboard` | Live Valorant stats |

## Music sources & the YouTube login wall

YouTube now refuses anonymous audio extraction. Search and metadata still work,
but starting playback fails with:

```
Client [ANDROID_VR] failed: This video requires login.
Client [WEB]        failed: This video requires login.
AllClientsFailedException: All clients failed to load the item.
```

This is YouTube blocking unauthenticated clients, not a bug in the bot or in
Lavalink. Because of it, `MUSIC_SOURCE` defaults to `soundcloud`, which needs no
authentication. `MUSIC_FALLBACK=1` additionally retries any failed track on
SoundCloud and posts a notice in the channel, so a blocked track degrades
loudly instead of playing silence.

To use YouTube, authenticate the source plugin — either is a one-time setup:

- **OAuth**: add `youtube: { oauth: { enabled: true } }` under `plugins:` in
  `lavalink/application.yml` and restart. Lavalink prints a device code to link
  a Google account. Use a throwaway account — the token is stored on disk and
  a flagged account affects that account.
- **poToken**: generate `poToken` + `visitorData` with
  [youtube-trusted-session-generator](https://github.com/iv-org/youtube-trusted-session-generator)
  and set them under the `youtube` plugin config.

Then set `MUSIC_SOURCE=youtube` in `.env`.

Notes:
- Bot auto-leaves voice after `VOICE_IDLE_MINUTES` (default 5) alone in a channel.
- Poll data and Riot ID links are stored in `mama.db` (SQLite, auto-created).
- Valorant stats are fetched live from HenrikDev on every command — nothing runs in the background.
