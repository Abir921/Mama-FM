"""SQLite access layer (aiosqlite)."""

import os

import aiosqlite

# Overridable so containers can point at a mounted volume that outlives them.
DB_PATH = os.getenv("MAMA_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mama.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    poll_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    guild_id    INTEGER NOT NULL,
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,          -- JSON list of option strings
    created_by  INTEGER NOT NULL,
    created_at  TEXT NOT NULL,          -- ISO timestamp (UTC)
    close_at    TEXT,                   -- ISO timestamp (UTC), NULL = no auto-close
    closed      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS valorant_accounts (
    discord_user_id INTEGER PRIMARY KEY,
    riot_name       TEXT NOT NULL,
    riot_tag        TEXT NOT NULL,
    region          TEXT NOT NULL,
    linked_at       TEXT NOT NULL,
    puuid           TEXT,                        -- stable id; names/tags can change
    platform        TEXT NOT NULL DEFAULT 'pc'
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id              INTEGER PRIMARY KEY,
    poll_default_duration INTEGER NOT NULL DEFAULT 60   -- minutes
);
"""


# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add
# them to a database that already exists, so apply them separately.
MIGRATIONS = {
    "valorant_accounts": {
        "puuid": "ALTER TABLE valorant_accounts ADD COLUMN puuid TEXT",
        "platform": "ALTER TABLE valorant_accounts ADD COLUMN platform TEXT NOT NULL DEFAULT 'pc'",
    },
}


async def init() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for column, ddl in columns.items():
                if column not in existing:
                    await conn.execute(ddl)
        await conn.commit()


def connect() -> aiosqlite.Connection:
    """Usage: async with db.connect() as conn: ..."""
    return aiosqlite.connect(DB_PATH)
