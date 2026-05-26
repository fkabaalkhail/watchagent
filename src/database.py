import aiosqlite
import os
from src.config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    temperature_2m REAL,
    apparent_temperature REAL,
    precipitation REAL,
    wind_speed_10m REAL,
    weather_code INTEGER,
    UNIQUE(city, timestamp)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    return db


async def insert_reading(db: aiosqlite.Connection, city: str, data: dict) -> bool:
    """Insert a reading if its timestamp is new for that city. Returns True if inserted."""
    try:
        await db.execute(
            """INSERT INTO readings (city, timestamp, temperature_2m, apparent_temperature,
               precipitation, wind_speed_10m, weather_code)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (city, data["timestamp"], data["temperature_2m"], data["apparent_temperature"],
             data["precipitation"], data["wind_speed_10m"], data["weather_code"]),
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def insert_event(db: aiosqlite.Connection, event: dict):
    await db.execute(
        """INSERT INTO events (city, timestamp, event_type, description, severity, details)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event["city"], event["timestamp"], event["event_type"],
         event["description"], event["severity"], event.get("details", "")),
    )
    await db.commit()


async def get_readings(db: aiosqlite.Connection, city: str | None = None, limit: int = 50) -> list[dict]:
    query = "SELECT * FROM readings"
    params: list = []
    if city:
        query += " WHERE city = ?"
        params.append(city)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_events(db: aiosqlite.Connection, city: str | None = None, limit: int = 50) -> list[dict]:
    query = "SELECT * FROM events"
    params: list = []
    if city:
        query += " WHERE city = ?"
        params.append(city)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def count_readings(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT COUNT(*) FROM readings")
    row = await cursor.fetchone()
    return row[0]


async def count_events(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("SELECT COUNT(*) FROM events")
    row = await cursor.fetchone()
    return row[0]


async def get_recent_readings(db: aiosqlite.Connection, city: str, n: int = 6) -> list[dict]:
    """Get the N most recent readings for a city, ordered oldest to newest."""
    cursor = await db.execute(
        "SELECT * FROM readings WHERE city = ? ORDER BY timestamp DESC LIMIT ?",
        (city, n),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in reversed(rows)]


async def get_latest_event(db: aiosqlite.Connection, city: str, event_type: str) -> dict | None:
    cursor = await db.execute(
        "SELECT * FROM events WHERE city = ? AND event_type = ? ORDER BY timestamp DESC LIMIT 1",
        (city, event_type),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None
