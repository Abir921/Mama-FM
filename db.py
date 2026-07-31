"""SQLite access layer (aiosqlite)."""

import os

import aiosqlite

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mama.db")

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
    linked_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id              INTEGER PRIMARY KEY,
    poll_default_duration INTEGER NOT NULL DEFAULT 60   -- minutes
);
"""


async def init() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


def connect() -> aiosqlite.Connection:
    """Usage: async with db.connect() as conn: ..."""
    return aiosqlite.connect(DB_PATH)
