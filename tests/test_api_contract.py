import asyncio

from fastapi.testclient import TestClient

from src import api, database


async def _seed(db_path: str):
    database.DATABASE_PATH = db_path
    db = await database.get_db()
    try:
        await database.insert_reading(
            db,
            "Ottawa",
            {
                "timestamp": "2026-01-01T10:00",
                "temperature_2m": 1.0,
                "apparent_temperature": -1.0,
                "precipitation": 0.0,
                "wind_speed_10m": 10.0,
                "weather_code": 2,
            },
        )
        await database.insert_event(
            db,
            {
                "city": "Ottawa",
                "timestamp": "2026-01-01T10:00",
                "event_type": "TEST_EVENT",
                "description": "seeded event",
                "severity": "info",
                "details": "{}",
            },
        )
    finally:
        await db.close()


def test_api_shapes_for_health_readings_and_events(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    monkeypatch.setattr(api, "ENABLE_POLLER", False)
    asyncio.run(_seed(db_path))

    with TestClient(api.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert set(body.keys()) == {"status", "readings_stored", "events_stored"}
        assert body["status"] == "ok"
        assert body["readings_stored"] == 1
        assert body["events_stored"] == 1

        readings = client.get("/readings", params={"city": "Ottawa", "limit": 50})
        assert readings.status_code == 200
        readings_body = readings.json()
        assert "readings" in readings_body
        assert len(readings_body["readings"]) == 1
        assert readings_body["readings"][0]["city"] == "Ottawa"
        assert readings_body["readings"][0]["timestamp"] == "2026-01-01T10:00"

        events = client.get("/events", params={"city": "Ottawa", "limit": 50})
        assert events.status_code == 200
        events_body = events.json()
        assert "events" in events_body
        assert len(events_body["events"]) == 1
        assert events_body["events"][0]["event_type"] == "TEST_EVENT"
