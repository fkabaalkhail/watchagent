import pytest

from src import database
from src.events import evaluate_events


def _reading(ts: str, temp: float, apparent: float, precip: float, wind: float, code: int = 3) -> dict:
    return {
        "timestamp": ts,
        "temperature_2m": temp,
        "apparent_temperature": apparent,
        "precipitation": precip,
        "wind_speed_10m": wind,
        "weather_code": code,
    }


@pytest.mark.asyncio
async def test_event_engine_detects_expected_signals_and_avoids_false_positive(tmp_path, monkeypatch):
    db_path = tmp_path / "events.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))
    db = await database.get_db()

    try:
        # Historical Ottawa context (quiet baseline).
        for ts, temp in [
            ("2026-01-01T00:00", -4.0),
            ("2026-01-01T01:00", -5.0),
            ("2026-01-01T02:00", -5.0),
            ("2026-01-01T03:00", -4.0),
        ]:
            await database.insert_reading(db, "Ottawa", _reading(ts, temp, temp - 2, 0.0, 10.0))

        # Other cities at the same timestamp for cross-city comparison.
        await database.insert_reading(db, "Toronto", _reading("2026-01-01T04:00", 2.0, 0.0, 0.0, 8.0))
        await database.insert_reading(db, "Vancouver", _reading("2026-01-01T04:00", -1.0, -2.0, 0.0, 9.0))

        current = _reading("2026-01-01T04:00", -20.0, -30.0, 6.0, 72.0)
        await database.insert_reading(db, "Ottawa", current)
        await evaluate_events(db, "Ottawa", current)

        rows = await database.get_events(db, city="Ottawa", limit=50)
        event_types = [event["event_type"] for event in rows]

        assert "RAPID_TEMP_CHANGE" in event_types
        assert "EXTREME_WIND" in event_types
        assert "HEAVY_PRECIPITATION" in event_types
        assert "EXTREME_COLD" in event_types
        assert "CITY_TEMP_ANOMALY" in event_types
        assert "EXTREME_HEAT" not in event_types
        assert event_types.count("CROSS_CITY_TEMP_DIVERGENCE") >= 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_event_cooldown_prevents_repeated_extreme_alerts(tmp_path, monkeypatch):
    db_path = tmp_path / "cooldown.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))
    db = await database.get_db()

    try:
        for idx in range(4):
            ts = f"2026-01-01T0{idx}:00"
            await database.insert_reading(db, "Ottawa", _reading(ts, -10.0, -12.0, 0.0, 15.0))

        first = _reading("2026-01-01T04:00", -20.0, -30.0, 0.0, 70.0)
        await database.insert_reading(db, "Ottawa", first)
        await evaluate_events(db, "Ottawa", first)

        second = _reading("2026-01-01T05:00", -21.0, -31.0, 0.0, 71.0)
        await database.insert_reading(db, "Ottawa", second)
        await evaluate_events(db, "Ottawa", second)

        events = await database.get_events(db, city="Ottawa", limit=50)
        extreme_cold_count = sum(1 for event in events if event["event_type"] == "EXTREME_COLD")
        extreme_wind_count = sum(1 for event in events if event["event_type"] == "EXTREME_WIND")

        assert extreme_cold_count == 1
        assert extreme_wind_count == 1
    finally:
        await db.close()
