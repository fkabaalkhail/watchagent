import logging
import json
from datetime import datetime, timedelta
from src.database import get_recent_readings, insert_event, get_latest_event

logger = logging.getLogger(__name__)

CITY_ANOMALY_THRESHOLDS = {
    "Ottawa": 10.0,
    "Toronto": 9.0,
    "Vancouver": 7.0,
}

EVENT_COOLDOWN_HOURS = {
    "RAPID_TEMP_CHANGE": 2,
    "EXTREME_WIND": 3,
    "HEAVY_PRECIPITATION": 3,
    "EXTREME_COLD": 4,
    "EXTREME_HEAT": 4,
    "CROSS_CITY_TEMP_DIVERGENCE": 6,
    "CITY_TEMP_ANOMALY": 4,
}


def _timestamp_to_dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


async def _passes_cooldown(db, event: dict) -> bool:
    last_event = await get_latest_event(db, event["city"], event["event_type"])
    if not last_event:
        return True
    current_ts = _timestamp_to_dt(event["timestamp"])
    previous_ts = _timestamp_to_dt(last_event["timestamp"])
    cooldown = timedelta(hours=EVENT_COOLDOWN_HOURS.get(event["event_type"], 1))
    return current_ts - previous_ts >= cooldown


async def evaluate_events(db, city: str, current: dict):
    """Run all detection rules against the new reading for a city."""
    history = await get_recent_readings(db, city, n=6)
    events = []

    events += _check_rapid_temp_change(city, current, history)
    events += _check_extreme_wind(city, current)
    events += _check_heavy_precipitation(city, current)
    events += _check_extreme_cold(city, current)
    events += _check_extreme_heat(city, current)
    events += _check_city_temp_anomaly(city, current, history)
    events += await _check_cross_city_divergence(db, city, current)

    for event in events:
        if await _passes_cooldown(db, event):
            await insert_event(db, event)
            logger.info("Event detected: [%s] %s in %s", event["event_type"], event["description"], city)
        else:
            logger.debug("Event suppressed by cooldown: %s (%s)", event["event_type"], city)


def _check_rapid_temp_change(city: str, current: dict, history: list[dict]) -> list[dict]:
    if len(history) < 2:
        return []
    oldest_temp = history[0]["temperature_2m"]
    current_temp = current["temperature_2m"]
    delta = current_temp - oldest_temp
    if abs(delta) >= 5.0:
        direction = "rose" if delta > 0 else "dropped"
        return [{
            "city": city,
            "timestamp": current["timestamp"],
            "event_type": "RAPID_TEMP_CHANGE",
            "severity": "warning" if abs(delta) >= 8.0 else "info",
            "description": f"Temperature {direction} {abs(delta):.1f}°C over recent readings (from {oldest_temp}°C to {current_temp}°C)",
            "details": json.dumps({"delta": delta, "from": oldest_temp, "to": current_temp}),
        }]
    return []


def _check_extreme_wind(city: str, current: dict) -> list[dict]:
    wind = current["wind_speed_10m"]
    if wind >= 60.0:
        severity = "critical" if wind >= 90.0 else "warning"
        return [{
            "city": city,
            "timestamp": current["timestamp"],
            "event_type": "EXTREME_WIND",
            "severity": severity,
            "description": f"Wind speed at {wind} km/h",
            "details": json.dumps({"wind_speed_10m": wind}),
        }]
    return []


def _check_heavy_precipitation(city: str, current: dict) -> list[dict]:
    precip = current["precipitation"]
    if precip >= 5.0:
        severity = "critical" if precip >= 15.0 else "warning"
        return [{
            "city": city,
            "timestamp": current["timestamp"],
            "event_type": "HEAVY_PRECIPITATION",
            "severity": severity,
            "description": f"Precipitation at {precip} mm/hr",
            "details": json.dumps({"precipitation": precip}),
        }]
    return []


def _check_extreme_cold(city: str, current: dict) -> list[dict]:
    apparent = current["apparent_temperature"]
    if apparent <= -25.0:
        severity = "critical" if apparent <= -35.0 else "warning"
        return [{
            "city": city,
            "timestamp": current["timestamp"],
            "event_type": "EXTREME_COLD",
            "severity": severity,
            "description": f"Apparent temperature at {apparent}°C — frostbite risk",
            "details": json.dumps({"apparent_temperature": apparent}),
        }]
    return []


def _check_extreme_heat(city: str, current: dict) -> list[dict]:
    apparent = current["apparent_temperature"]
    if apparent >= 35.0:
        severity = "critical" if apparent >= 40.0 else "warning"
        return [{
            "city": city,
            "timestamp": current["timestamp"],
            "event_type": "EXTREME_HEAT",
            "severity": severity,
            "description": f"Apparent temperature at {apparent}°C — heat warning",
            "details": json.dumps({"apparent_temperature": apparent}),
        }]
    return []


def _check_city_temp_anomaly(city: str, current: dict, history: list[dict]) -> list[dict]:
    if len(history) < 4:
        return []
    baseline = sum(r["temperature_2m"] for r in history[:-1]) / max(len(history) - 1, 1)
    delta = current["temperature_2m"] - baseline
    threshold = CITY_ANOMALY_THRESHOLDS.get(city, 8.0)
    if abs(delta) < threshold:
        return []

    direction = "above" if delta > 0 else "below"
    return [{
        "city": city,
        "timestamp": current["timestamp"],
        "event_type": "CITY_TEMP_ANOMALY",
        "severity": "warning",
        "description": (
            f"{city} temperature is {abs(delta):.1f}°C {direction} its recent baseline "
            f"({current['temperature_2m']:.1f}°C vs {baseline:.1f}°C)"
        ),
        "details": json.dumps(
            {"current_temp": current["temperature_2m"], "baseline": baseline, "delta": delta}
        ),
    }]


async def _check_cross_city_divergence(db, city: str, current: dict) -> list[dict]:
    """Compare current city's temperature against the latest reading from other cities."""
    from src.config import CITIES
    events = []
    for other_city in CITIES:
        if other_city == city:
            continue
        rows = await get_recent_readings(db, other_city, n=1)
        if not rows:
            continue
        other_temp = rows[-1]["temperature_2m"]
        delta = abs(current["temperature_2m"] - other_temp)
        if delta >= 15.0:
            events.append({
                "city": city,
                "timestamp": current["timestamp"],
                "event_type": "CROSS_CITY_TEMP_DIVERGENCE",
                "severity": "info",
                "description": f"{city} ({current['temperature_2m']}°C) vs {other_city} ({other_temp}°C) — {delta:.1f}°C difference",
                "details": json.dumps({"city_a": city, "city_b": other_city, "delta": delta}),
            })
    return events
