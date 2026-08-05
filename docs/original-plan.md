# Mama FM — Build Plan

Scope: Music playback · Meme generator · Poll/voting · Valorant stat tracking (HenrikDev API)
Stack: `discord.py` + SQLite + `wavelink`/Lavalink (music) + HenrikDev API (Valorant)

All commands are namespaced under `/mama` to avoid collisions with other bots in the server (e.g. `/mama play` instead of a bare `/play`).

---

## 1. Commands

### Music
| Command | Description |
|---|---|
| `/mama play <query or url>` | Search/queue a track, join voice channel if needed |
| `/mama skip` | Skip current track |
| `/mama queue` | Show upcoming tracks |
| `/mama loop` | Toggle loop for current track/queue |
| `/mama pause` / `/mama resume` | Pause/resume playback |
| `/mama nowplaying` | Show current track + progress |
| `/mama leave` | Disconnect from voice |

Auto-disconnect after N minutes alone in a voice channel (empty-channel check on `on_voice_state_update`).

### Meme Generator
| Command | Description |
|---|---|
| `/mama meme` | Post a random meme (from a meme API or curated subreddit feed) |
| `/mama caption <image> <top_text> <bottom_text>` | Overlay text on an uploaded image (Pillow) |

### Poll / Voting
| Command | Description |
|---|---|
| `/mama poll create <question> <option1> <option2> ... [duration]` | Posts embed, reacts with emoji per option |
| `/mama poll close <poll_id>` | Manually close and post results early |
| `/mama poll results <poll_id>` | Check live tally without closing |

**One vote per user**: on `on_raw_reaction_add`, if the user already has a reaction on this poll message (tracked in-memory or via a quick reaction scan), the bot removes their previous reaction before the new one counts — so only their latest pick stands.

### Valorant Stats
| Command | Description |
|---|---|
| `/mama valo link <riot_name>#<tag>` | Each friend links their own Riot ID to their Discord account |
| `/mama valo stats [@user]` | Current rank, recent match K/D/A, win rate |
| `/mama valo compare <@user1> <@user2>` | Side-by-side stat comparison |
| `/mama valo leaderboard` | Rank leaderboard across everyone linked in the server |

All Valorant commands reply in the channel they're invoked from — no separate alert channel, no background posting. Each call fetches live from the API, so results are current as of that moment.

---

## 2. Data Model (SQLite)

**polls**
`poll_id (PK) · message_id · channel_id · question · options (JSON) · created_by · created_at · close_at · closed (bool)`

**valorant_accounts**
`discord_user_id (PK) · riot_name · riot_tag · region · linked_at`

**guild_settings**
`guild_id (PK) · poll_default_duration`

(Poll votes don't need their own table — tally directly from message reactions at close time, since Discord already stores who reacted.)

---

## 3. Valorant Stats: On-Demand Only

No background task, no separate alert channel. When someone runs `/mama valo stats [@user]`, `/mama valo compare`, or `/mama valo leaderboard`, the bot fetches live from HenrikDev right then and replies in that same channel — so what's shown is always current as of the moment it's asked, without needing to store or track match history between calls.

---

## 4. Command Structure Note

Discord slash commands support up to 3 levels: top-level command → subcommand group → subcommand. `Mama` is the top-level command; `poll` and `valo` are subcommand groups (each with their own subcommands); `play`, `skip`, `meme`, etc. are direct subcommands of `mama` since they don't need their own group.

---

## 5. Suggested Build Order (for Claude Code)

1. **Bot skeleton** — client setup, single `/mama` command tree with subcommand groups, cog structure
2. **Music cog** — self-contained, no external API key needed, good first working feature
3. **Meme cog** — simple, quick win
4. **Poll cog** — needs reaction listener + SQLite wired up
5. **Valorant cog** — needs HenrikDev key + the `valorant_accounts` table for linking, but no background task since stats are on-demand only; build last since it's the only piece with an external API dependency

Each feature as its own `cog` file keeps things modular so Claude Code can work on one at a time without touching the others.
