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

Notes:
- Bot auto-leaves voice after `VOICE_IDLE_MINUTES` (default 5) alone in a channel.
- Poll data and Riot ID links are stored in `mama.db` (SQLite, auto-created).
- Valorant stats are fetched live from HenrikDev on every command — nothing runs in the background.
