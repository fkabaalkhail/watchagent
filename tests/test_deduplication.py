import pytest

from src import database
from src import poller


@pytest.mark.asyncio
async def test_duplicate_reading_is_skipped(tmp_path, monkeypatch):
    db_path = tmp_path / "dedup.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))

    db = await database.get_db()
    try:
        reading = {
            "timestamp": "2026-01-01T10:00",
            "temperature_2m": -2.0,
            "apparent_temperature": -6.0,
            "precipitation": 0.0,
            "wind_speed_10m": 12.0,
            "weather_code": 3,
        }

        first = await database.insert_reading(db, "Ottawa", reading)
        second = await database.insert_reading(db, "Ottawa", reading)
        rows = await database.get_readings(db, city="Ottawa", limit=10)

        assert first is True
        assert second is False
        assert len(rows) == 1
        assert rows[0]["timestamp"] == "2026-01-01T10:00"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_duplicate_weather_payload_across_two_polls_stores_once_per_city(tmp_path, monkeypatch):
    db_path = tmp_path / "dedup_poll.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))

    async def fake_fetch_weather(_client, _city, _coords):
        # Same upstream payload returned on both poll cycles.
        return {
            "timestamp": "2026-01-01T10:00",
            "temperature_2m": -2.0,
            "apparent_temperature": -6.0,
            "precipitation": 0.0,
            "wind_speed_10m": 12.0,
            "weather_code": 3,
        }

    async def fake_evaluate_events(_db, _city, _current):
        return None

    monkeypatch.setattr(poller, "fetch_weather", fake_fetch_weather)
    monkeypatch.setattr(poller, "evaluate_events", fake_evaluate_events)

    # First poll stores one reading per city.
    await poller.poll_once(client=None)
    # Second poll receives the same timestamp; should deduplicate.
    await poller.poll_once(client=None)

    db = await database.get_db()
    try:
        rows = await database.get_readings(db, limit=20)
        assert len(rows) == 3

        by_city = {row["city"]: row for row in rows}
        assert set(by_city.keys()) == {"Ottawa", "Toronto", "Vancouver"}
        assert all(row["timestamp"] == "2026-01-01T10:00" for row in rows)
    finally:
        await db.close()
