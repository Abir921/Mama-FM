<div align="center">

# 📻 Mama FM

**A Discord bot for music, memes, polls, and Valorant stats.**

Every command lives under a single `/mama` group, so it never collides with the other bots in your server.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2?logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![Lavalink](https://img.shields.io/badge/Lavalink-4.2-FF624D)](https://github.com/lavalink-devs/Lavalink)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](DEPLOY.md)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## Features

| | Feature | Commands |
|:--:|---|---|
| 🎵 | **Music** — queue, loop, and live progress bar, with automatic disconnect when left alone | `play` `skip` `queue` `loop` `pause` `resume` `nowplaying` `leave` |
| 😂 | **Memes** — random memes, or caption your own image in Impact | `meme` `caption` |
| 📊 | **Polls** — reaction polls that auto-close and enforce one vote per person | `poll create` `poll close` `poll results` |
| 🎯 | **Valorant** — live rank, K/D/A, win rate, and a server leaderboard | `valo link` `valo stats` `valo compare` `valo leaderboard` |

<details>
<summary><b>Full command reference</b></summary>

### 🎵 Music
| Command | Description |
|---|---|
| `/mama play <query or url>` | Search or queue a track; joins your voice channel |
| `/mama skip` | Skip the current track |
| `/mama queue` | Show upcoming tracks |
| `/mama loop` | Cycle loop mode: off → track → queue |
| `/mama pause` · `/mama resume` | Pause and resume playback |
| `/mama nowplaying` | Current track with a progress bar |
| `/mama leave` | Disconnect from voice |

### 😂 Memes
| Command | Description |
|---|---|
| `/mama meme` | Post a random meme |
| `/mama caption <image> <top> <bottom>` | Overlay Impact-font text on an uploaded image |

### 📊 Polls
| Command | Description |
|---|---|
| `/mama poll create <question> <option1..10> [duration]` | Create a poll that closes itself |
| `/mama poll close <poll_id>` | Close early and post final results |
| `/mama poll results <poll_id>` | Live tally without closing |

### 🎯 Valorant
| Command | Description |
|---|---|
| `/mama valo link <name#tag> [platform]` | Link your Riot ID |
| `/mama valo unlink` | Remove your linked Riot ID |
| `/mama valo stats [@user]` | Rank, recent K/D/A, win rate, headshot % |
| `/mama valo compare <@user1> <@user2>` | Side-by-side comparison |
| `/mama valo leaderboard` | Rank leaderboard for everyone linked in the server |

</details>

---

## Quick start

### With Docker (recommended)

```bash
git clone https://github.com/Abir921/Mama-FM.git
cd Mama-FM
cp .env.example .env      # then fill in your tokens
docker compose up -d
```

Runs the bot and Lavalink together, restarting both on crash or reboot.
For hosting it 24/7 on a free always-on server, see **[DEPLOY.md](DEPLOY.md)**.

### Without Docker

<details>
<summary>Manual setup</summary>

**Requirements:** Python 3.11+, and Java 17+ if you want music.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

1. **Create the bot** at [discord.com/developers/applications](https://discord.com/developers/applications) → *Bot* → copy the token. No privileged intents needed.

2. **Invite it** with scopes `bot` + `applications.commands` and these permissions:
   Send Messages, Embed Links, Attach Files, Add Reactions, **Manage Messages**,
   Read Message History, Connect, Speak.
   *Manage Messages is what lets polls remove a voter's previous choice.*

3. **Configure** — copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`.
   `HENRIK_API_KEY` (free, from [docs.henrikdev.xyz](https://docs.henrikdev.xyz)) enables Valorant.
   Leave `GUILD_ID` empty for global commands; set it to one server for instant sync while developing.

4. **Start Lavalink** (music only) — drop [`Lavalink.jar`](https://github.com/lavalink-devs/Lavalink/releases) into `lavalink/`, then:
   ```powershell
   .\start-lavalink.ps1
   ```
   Wait for `Lavalink is ready to accept connections.` Everything except music works without it.

5. **Run the bot**
   ```bash
   .venv\Scripts\python bot.py
   ```

</details>

---

## How it works

```
bot.py                 Client, cog loading, slash-command sync
db.py                  SQLite access + additive schema migrations
cogs/
  ├── music.py         Lavalink playback, queue, auto-disconnect
  ├── meme.py          Meme API + Pillow captioning
  ├── poll.py          Reaction polls, one-vote enforcement, auto-close
  └── valorant.py      HenrikDev stats, fetched live per command
lavalink/              Lavalink config (YouTube plugin, OAuth)
sync_commands.py       Switch commands between guild-scoped and global
```

**Design notes**

- **One command tree.** `/mama` is the only top-level command; `poll` and `valo` are subcommand groups. Discord allows three levels, and using them avoids clashing with other bots.
- **Stats are fetched live.** No background polling and no cached match history — every Valorant command hits the API when invoked, so results are current by construction.
- **Votes come from Discord.** Polls tally reactions at close time rather than keeping a votes table, since Discord already stores who reacted.
- **Lavalink is optional.** The node connects in the background, so a Lavalink outage degrades music alone instead of blocking startup.

---

## Engineering notes

Things that turned out to be less obvious than they looked.

<details>
<summary><b>YouTube stopped serving audio to anonymous clients</b></summary>

Search and metadata still work, so tracks resolve and queue normally — then playback fails:

```
Client [ANDROID_VR] failed: This video requires login.
Client [WEB]        failed: No supported audio streams available.
AllClientsFailedException: All clients failed to load the item.
```

The fix is authenticating the source plugin via OAuth, using the `TV` client (the only OAuth-compatible one). The refresh token is read from `${YOUTUBE_REFRESH_TOKEN}` rather than written into `application.yml`, so the committed config carries no credential and the repo stays usable by anyone who clones it.

</details>

<details>
<summary><b>Failures arrive long after the command has replied</b></summary>

A track fails while loading its stream, well after `/mama play` has already answered. With nothing listening for that event, the bot said *"Queued…"* and then played silence.

The cog now handles `track_exception` and `track_stuck`, reports the reason in the channel, and walks to the next search result instead of retrying the one that just failed. Lavalink returns full Java stack traces, so messages are truncated to their first line — an untruncated trace can exceed Discord's 2000-character limit and fail to send at all.

</details>

<details>
<summary><b>SoundCloud previews look like full tracks</b></summary>

Some SoundCloud uploads — typically the official label ones — are 30-second previews (`policy=SNIP`) that still advertise the full duration. Playback simply stopped partway with no error, because as far as Lavalink was concerned the track had ended.

They're distinguishable for free: preview streams use `/preview/` in their URL where full tracks use `/stream/`. Search results are filtered on that, keeping one as a last resort only if every result is a preview.

</details>

<details>
<summary><b>Permission denials look like hangs</b></summary>

Server-wide permissions granted at invite time are routinely overridden per channel. Joining a channel the bot can't see never completes the voice handshake — it just times out after 30 seconds and reports something unhelpful.

`View Channel` / `Connect` / `Speak` are now checked before attempting to join, so the bot replies immediately naming exactly which permission is missing.

</details>

<details>
<summary><b>Matching players by name silently loses games</b></summary>

Riot IDs can be changed. Aggregating match stats by name quietly drops every game played under a previous ID, producing plausible-but-wrong numbers.

Players are matched by `puuid`, with a name fallback for accounts linked before it was stored. Adding the column meant adding migrations too — `CREATE TABLE IF NOT EXISTS` leaves existing databases untouched.

</details>

---

## Built with

[discord.py](https://github.com/Rapptz/discord.py) · [wavelink](https://github.com/PythonistaGuild/Wavelink) · [Lavalink](https://github.com/lavalink-devs/Lavalink) · [Pillow](https://python-pillow.org/) · [aiosqlite](https://github.com/omnilib/aiosqlite) · [HenrikDev API](https://docs.henrikdev.xyz)

## License

[MIT](LICENSE) © Abir Sakib
