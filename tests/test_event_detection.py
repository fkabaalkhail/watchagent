import pytest

from src import database
from src.events import (
    _check_city_temp_anomaly,
    _check_extreme_cold,
    _check_extreme_heat,
    _check_extreme_wind,
    _check_heavy_precipitation,
    _check_rapid_temp_change,
    _check_severe_weather_code,
    evaluate_events,
)


def _reading(ts: str, temp: float, apparent: float, precip: float, wind: float, code: int = 3) -> dict:
    return {
        "timestamp": ts,
        "temperature_2m": temp,
        "apparent_temperature": apparent,
        "precipitation": precip,
        "wind_speed_10m": wind,
        "weather_code": code,
    }


# ---------------------------------------------------------------------------
# Integration test: multiple signals fire together, false-positive guarded
# ---------------------------------------------------------------------------


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
        # False-positive guard: heat must NOT fire when it's -30°C
        assert "EXTREME_HEAT" not in event_types
        assert event_types.count("CROSS_CITY_TEMP_DIVERGENCE") >= 1
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Cooldown: suppression within window, re-fire after expiry
# ---------------------------------------------------------------------------


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

        # 1 hour later — within cooldown window (EXTREME_WIND=3h, EXTREME_COLD=4h)
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


@pytest.mark.asyncio
async def test_event_cooldown_expires_and_allows_refire(tmp_path, monkeypatch):
    """After the cooldown window passes, the same event type should fire again."""
    db_path = tmp_path / "cooldown_expiry.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))
    db = await database.get_db()

    try:
        # Build baseline history
        for idx in range(4):
            ts = f"2026-01-01T0{idx}:00"
            await database.insert_reading(db, "Ottawa", _reading(ts, -10.0, -12.0, 0.0, 15.0))

        # First extreme wind event at T04:00
        first = _reading("2026-01-01T04:00", -10.0, -12.0, 0.0, 70.0)
        await database.insert_reading(db, "Ottawa", first)
        await evaluate_events(db, "Ottawa", first)

        # 4 hours later — EXTREME_WIND cooldown is 3h, so this should fire again
        later = _reading("2026-01-01T08:00", -10.0, -12.0, 0.0, 75.0)
        await database.insert_reading(db, "Ottawa", later)
        await evaluate_events(db, "Ottawa", later)

        events = await database.get_events(db, city="Ottawa", limit=50)
        extreme_wind_events = [e for e in events if e["event_type"] == "EXTREME_WIND"]

        assert len(extreme_wind_events) == 2
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Isolated unit tests for each event type
# ---------------------------------------------------------------------------


class TestRapidTempChange:
    """Time-aware rapid temperature change detection."""

    def test_fires_on_large_fast_change(self):
        history = [
            _reading("2026-01-01T00:00", 10.0, 8.0, 0.0, 5.0),
            _reading("2026-01-01T01:00", 11.0, 9.0, 0.0, 5.0),
            _reading("2026-01-01T02:00", 12.0, 10.0, 0.0, 5.0),
        ]
        current = _reading("2026-01-01T03:00", 20.0, 18.0, 0.0, 5.0)
        events = _check_rapid_temp_change("Ottawa", current, history)
        assert len(events) == 1
        assert events[0]["event_type"] == "RAPID_TEMP_CHANGE"
        assert "rose" in events[0]["description"]

    def test_does_not_fire_on_gradual_change(self):
        """5°C over 6+ hours is normal diurnal variation, not rapid."""
        history = [
            _reading("2026-01-01T00:00", 10.0, 8.0, 0.0, 5.0),
            _reading("2026-01-01T02:00", 11.0, 9.0, 0.0, 5.0),
            _reading("2026-01-01T04:00", 12.0, 10.0, 0.0, 5.0),
        ]
        # 5°C over 7 hours = 0.7°C/hr — below the 1.5°C/hr rate threshold
        current = _reading("2026-01-01T07:00", 15.0, 13.0, 0.0, 5.0)
        events = _check_rapid_temp_change("Ottawa", current, history)
        assert len(events) == 0

    def test_does_not_fire_with_insufficient_history(self):
        history = [_reading("2026-01-01T00:00", 10.0, 8.0, 0.0, 5.0)]
        current = _reading("2026-01-01T01:00", 20.0, 18.0, 0.0, 5.0)
        events = _check_rapid_temp_change("Ottawa", current, history)
        assert len(events) == 0

    def test_severity_escalates_for_large_delta(self):
        history = [
            _reading("2026-01-01T00:00", 0.0, -2.0, 0.0, 5.0),
            _reading("2026-01-01T01:00", 2.0, 0.0, 0.0, 5.0),
        ]
        # 10°C in 2 hours = 5°C/hr, delta >= 8 → warning
        current = _reading("2026-01-01T02:00", 10.0, 8.0, 0.0, 5.0)
        events = _check_rapid_temp_change("Ottawa", current, history)
        assert len(events) == 1
        assert events[0]["severity"] == "warning"


class TestExtremeWind:
    def test_fires_at_60_kmh(self):
        events = _check_extreme_wind("Toronto", _reading("2026-01-01T00:00", 5.0, 3.0, 0.0, 60.0))
        assert len(events) == 1
        assert events[0]["severity"] == "warning"

    def test_critical_at_90_kmh(self):
        events = _check_extreme_wind("Toronto", _reading("2026-01-01T00:00", 5.0, 3.0, 0.0, 95.0))
        assert len(events) == 1
        assert events[0]["severity"] == "critical"

    def test_does_not_fire_below_threshold(self):
        events = _check_extreme_wind("Toronto", _reading("2026-01-01T00:00", 5.0, 3.0, 0.0, 59.9))
        assert len(events) == 0


class TestHeavyPrecipitation:
    def test_fires_at_5mm(self):
        events = _check_heavy_precipitation("Vancouver", _reading("2026-01-01T00:00", 10.0, 9.0, 5.0, 10.0))
        assert len(events) == 1
        assert events[0]["severity"] == "warning"

    def test_critical_at_15mm(self):
        events = _check_heavy_precipitation("Vancouver", _reading("2026-01-01T00:00", 10.0, 9.0, 16.0, 10.0))
        assert len(events) == 1
        assert events[0]["severity"] == "critical"

    def test_does_not_fire_below_threshold(self):
        events = _check_heavy_precipitation("Vancouver", _reading("2026-01-01T00:00", 10.0, 9.0, 4.9, 10.0))
        assert len(events) == 0


class TestExtremeCold:
    def test_fires_at_minus_25(self):
        events = _check_extreme_cold("Ottawa", _reading("2026-01-01T00:00", -28.0, -25.0, 0.0, 10.0))
        assert len(events) == 1
        assert "frostbite" in events[0]["description"]

    def test_critical_at_minus_35(self):
        events = _check_extreme_cold("Ottawa", _reading("2026-01-01T00:00", -38.0, -36.0, 0.0, 10.0))
        assert len(events) == 1
        assert events[0]["severity"] == "critical"

    def test_does_not_fire_above_threshold(self):
        events = _check_extreme_cold("Ottawa", _reading("2026-01-01T00:00", -20.0, -24.9, 0.0, 10.0))
        assert len(events) == 0


class TestExtremeHeat:
    def test_fires_at_35(self):
        events = _check_extreme_heat("Toronto", _reading("2026-07-15T14:00", 37.0, 35.0, 0.0, 5.0))
        assert len(events) == 1
        assert "heat warning" in events[0]["description"]

    def test_does_not_fire_below_threshold(self):
        events = _check_extreme_heat("Toronto", _reading("2026-07-15T14:00", 33.0, 34.9, 0.0, 5.0))
        assert len(events) == 0


class TestSevereWeatherCode:
    def test_fires_on_thunderstorm(self):
        events = _check_severe_weather_code("Ottawa", _reading("2026-06-01T15:00", 25.0, 27.0, 2.0, 30.0, code=95))
        assert len(events) == 1
        assert events[0]["event_type"] == "SEVERE_WEATHER_CODE"
        assert events[0]["severity"] == "critical"
        assert "thunderstorm" in events[0]["description"]

    def test_fires_on_heavy_snow(self):
        events = _check_severe_weather_code("Ottawa", _reading("2026-01-15T10:00", -5.0, -8.0, 3.0, 20.0, code=75))
        assert len(events) == 1
        assert events[0]["severity"] == "warning"
        assert "heavy snowfall" in events[0]["description"]

    def test_fires_on_freezing_rain(self):
        events = _check_severe_weather_code("Toronto", _reading("2026-02-01T08:00", -1.0, -3.0, 2.0, 15.0, code=67))
        assert len(events) == 1
        assert "freezing rain" in events[0]["description"]

    def test_does_not_fire_on_clear_sky(self):
        events = _check_severe_weather_code("Vancouver", _reading("2026-06-01T12:00", 20.0, 19.0, 0.0, 5.0, code=0))
        assert len(events) == 0

    def test_does_not_fire_on_mild_codes(self):
        """WMO codes < 65 (light drizzle, overcast, fog) should not trigger."""
        for code in [0, 1, 2, 3, 45, 48, 51, 53, 55, 61, 63]:
            events = _check_severe_weather_code("Ottawa", _reading("2026-01-01T00:00", 5.0, 3.0, 1.0, 10.0, code=code))
            assert len(events) == 0, f"Should not fire for WMO code {code}"


class TestCityTempAnomaly:
    def test_fires_when_deviation_exceeds_city_threshold(self):
        # Ottawa threshold is 10°C. Baseline ~5°C, current 20°C → delta 15°C
        history = [
            _reading("2026-01-01T00:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T01:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T02:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T03:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T04:00", 5.0, 3.0, 0.0, 5.0),
        ]
        current = _reading("2026-01-01T05:00", 20.0, 18.0, 0.0, 5.0)
        events = _check_city_temp_anomaly("Ottawa", current, history)
        assert len(events) == 1
        assert "above" in events[0]["description"]

    def test_does_not_fire_within_threshold(self):
        # Ottawa threshold is 10°C. Baseline ~5°C, current 12°C → delta 7°C (below 10)
        history = [
            _reading("2026-01-01T00:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T01:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T02:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T03:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T04:00", 5.0, 3.0, 0.0, 5.0),
        ]
        current = _reading("2026-01-01T05:00", 12.0, 10.0, 0.0, 5.0)
        events = _check_city_temp_anomaly("Ottawa", current, history)
        assert len(events) == 0

    def test_vancouver_stricter_threshold(self):
        """Vancouver threshold is 7°C — stricter due to narrower climate range."""
        history = [
            _reading("2026-01-01T00:00", 10.0, 9.0, 0.0, 5.0),
            _reading("2026-01-01T01:00", 10.0, 9.0, 0.0, 5.0),
            _reading("2026-01-01T02:00", 10.0, 9.0, 0.0, 5.0),
            _reading("2026-01-01T03:00", 10.0, 9.0, 0.0, 5.0),
            _reading("2026-01-01T04:00", 10.0, 9.0, 0.0, 5.0),
        ]
        # 8°C deviation — exceeds Vancouver's 7°C threshold but not Ottawa's 10°C
        current = _reading("2026-01-01T05:00", 18.0, 17.0, 0.0, 5.0)
        events = _check_city_temp_anomaly("Vancouver", current, history)
        assert len(events) == 1

    def test_requires_minimum_history(self):
        """Needs at least 4 readings to establish a baseline."""
        history = [
            _reading("2026-01-01T00:00", 5.0, 3.0, 0.0, 5.0),
            _reading("2026-01-01T01:00", 5.0, 3.0, 0.0, 5.0),
        ]
        current = _reading("2026-01-01T02:00", 50.0, 48.0, 0.0, 5.0)
        events = _check_city_temp_anomaly("Ottawa", current, history)
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Cross-city divergence (async, needs DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_city_divergence_fires_on_large_difference(tmp_path, monkeypatch):
    db_path = tmp_path / "divergence.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))
    db = await database.get_db()

    try:
        # Toronto at 20°C, Vancouver at 5°C
        await database.insert_reading(db, "Toronto", _reading("2026-01-01T10:00", 20.0, 18.0, 0.0, 10.0))
        await database.insert_reading(db, "Vancouver", _reading("2026-01-01T10:00", 5.0, 3.0, 0.0, 8.0))

        # Ottawa at -5°C — 25°C difference from Toronto
        current = _reading("2026-01-01T10:00", -5.0, -8.0, 0.0, 12.0)
        await database.insert_reading(db, "Ottawa", current)
        await evaluate_events(db, "Ottawa", current)

        events = await database.get_events(db, city="Ottawa", limit=50)
        divergence_events = [e for e in events if e["event_type"] == "CROSS_CITY_TEMP_DIVERGENCE"]
        assert len(divergence_events) >= 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cross_city_divergence_does_not_fire_when_cities_are_close(tmp_path, monkeypatch):
    db_path = tmp_path / "no_divergence.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))
    db = await database.get_db()

    try:
        await database.insert_reading(db, "Toronto", _reading("2026-06-01T10:00", 22.0, 20.0, 0.0, 10.0))
        await database.insert_reading(db, "Vancouver", _reading("2026-06-01T10:00", 18.0, 17.0, 0.0, 8.0))

        # Ottawa at 20°C — within 15°C of both cities
        current = _reading("2026-06-01T10:00", 20.0, 18.0, 0.0, 12.0)
        await database.insert_reading(db, "Ottawa", current)
        await evaluate_events(db, "Ottawa", current)

        events = await database.get_events(db, city="Ottawa", limit=50)
        divergence_events = [e for e in events if e["event_type"] == "CROSS_CITY_TEMP_DIVERGENCE"]
        assert len(divergence_events) == 0
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Severe weather code integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_severe_weather_code_fires_in_full_pipeline(tmp_path, monkeypatch):
    """Verify SEVERE_WEATHER_CODE integrates with the full evaluate_events pipeline."""
    db_path = tmp_path / "severe.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(db_path))
    db = await database.get_db()

    try:
        # Mild history
        for i in range(4):
            await database.insert_reading(
                db, "Toronto", _reading(f"2026-06-01T0{i}:00", 22.0, 20.0, 0.0, 10.0, code=2)
            )

        # Thunderstorm arrives
        current = _reading("2026-06-01T04:00", 22.0, 20.0, 2.0, 25.0, code=95)
        await database.insert_reading(db, "Toronto", current)
        await evaluate_events(db, "Toronto", current)

        events = await database.get_events(db, city="Toronto", limit=50)
        event_types = [e["event_type"] for e in events]
        assert "SEVERE_WEATHER_CODE" in event_types
        # Should NOT fire extreme wind/precip/cold/heat for these mild values
        assert "EXTREME_WIND" not in event_types
        assert "EXTREME_COLD" not in event_types
        assert "EXTREME_HEAT" not in event_types
    finally:
        await db.close()
