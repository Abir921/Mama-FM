# Hosting Mama FM 24/7

The bot only exists while its process runs. To keep it online without leaving
your PC on, it needs to live on a machine that is always on.

This guide uses **Oracle Cloud Always Free**, which is genuinely free
indefinitely (not a trial) and large enough to run Lavalink. Everything here
works on any Linux box, so the same steps apply to a VPS or a Raspberry Pi.

---

## Before you start: the YouTube caveat

YouTube blocks datacenter IP ranges far more aggressively than home
connections. On a cloud server, YouTube playback is **more likely** to fail
with `This video requires login` even with the OAuth token configured.

The bot handles this: it falls back to SoundCloud and posts a notice rather
than going silent. But if flawless YouTube playback matters more than uptime,
a Raspberry Pi at home is the better host — it keeps your residential IP.

---

## 1. Create the server

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com). A card is required
   for identity verification; the Always Free resources are not charged.
   Choose a home region close to you — it cannot be changed later.
2. **Compute → Instances → Create instance**
   - Image: **Ubuntu 22.04** (or 24.04)
   - Shape: **Ampere A1 (ARM)**, 2 OCPU / 12 GB RAM is well within the free
     allowance. If A1 capacity is unavailable, retry later or in another
     availability domain — this is common.
   - Add your SSH public key when prompted.
3. Note the public IP.

> Oracle may reclaim Always Free compute instances that stay idle. A Discord
> bot with an open gateway connection generally counts as active, but do not
> treat this box as the only copy of anything important.

No inbound ports need opening. The bot makes outbound connections only, and
Lavalink is never exposed to the internet.

## 2. Install Docker

```bash
ssh ubuntu@YOUR_SERVER_IP

sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
```

Log out and back in so the group change applies.

## 3. Get the code

```bash
git clone https://github.com/Abir921/Mama-FM.git
cd Mama-FM
```

## 4. Create the .env

`.env` is gitignored, so it is not in the clone — copy your values across.
**Never commit this file.**

```bash
cp .env.example .env
nano .env
```

Fill in:

| Key | Notes |
|---|---|
| `DISCORD_TOKEN` | required |
| `GUILD_ID` | **leave empty** — global commands work in every server |
| `HENRIK_API_KEY` | for Valorant |
| `YOUTUBE_REFRESH_TOKEN` | reuse your existing token, or omit and re-link (see below) |
| `LAVALINK_PASSWORD` | pick anything; Lavalink is not internet-facing |
| `MUSIC_SOURCE` | `youtube`, or `soundcloud` if YouTube keeps failing on the server |

Do not set `LAVALINK_URI` — Compose points the bot at the Lavalink container
automatically.

## 5. Start it

```bash
docker compose up -d
docker compose logs -f
```

Wait for `Logged in as Mama FM` and `Active in: ...`. Ctrl-C stops following
the logs; it does not stop the bot.

`restart: unless-stopped` means both services come back automatically after a
crash **and** after a server reboot.

## 6. Verify

```bash
docker compose ps          # both services should be Up
docker compose logs bot    # gateway connection, synced commands
```

Then run `/mama meme` in Discord — it needs no external service beyond one
public API, so it is the quickest end-to-end check.

---

## Re-linking YouTube OAuth on the server

If you skip `YOUTUBE_REFRESH_TOKEN`, Lavalink prints a fresh device code on
first start:

```bash
docker compose logs lavalink | grep -i "enter code"
```

Visit [google.com/device](https://www.google.com/device) with that code, using
a **burner Google account** — never your main one. Lavalink then logs the new
refresh token; put it in `.env` and `docker compose up -d` to persist it.

## Updating

```bash
git pull
docker compose up -d --build
```

## Backing up

Poll records and Riot ID links live in a Docker volume.

```bash
docker compose cp bot:/data/mama.db ./mama-backup.db
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| Bot offline in Discord | `docker compose ps`; check `docker compose logs bot` |
| `/mama` missing in a server | `GUILD_ID` is set — clear it and restart, global commands work everywhere |
| Music silent, everything else fine | Lavalink unhealthy: `docker compose logs lavalink` |
| `This video requires login` | Datacenter IP blocked. Set `MUSIC_SOURCE=soundcloud` |
| Valorant commands report no key | `HENRIK_API_KEY` missing from `.env` |
| Out of memory | Give Lavalink room — 2 GB RAM minimum for both services |
