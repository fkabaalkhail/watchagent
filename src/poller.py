import asyncio
import logging
import httpx
from src.config import (
    CITIES,
    POLL_INTERVAL_SECONDS,
    MAX_FETCH_RETRIES,
    FETCH_TIMEOUT_SECONDS,
)
from src.database import get_db, insert_reading
from src.events import evaluate_events

logger = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
PARAMS_TEMPLATE = {
    "current": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code",
    "wind_speed_unit": "kmh",
    "timezone": "auto",
}


async def fetch_weather(client: httpx.AsyncClient, city: str, coords: dict) -> dict | None:
    params = {**PARAMS_TEMPLATE, "latitude": coords["lat"], "longitude": coords["lon"]}
    for attempt in range(1, MAX_FETCH_RETRIES + 2):
        try:
            resp = await client.get(API_URL, params=params, timeout=FETCH_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()["current"]
            return {
                "timestamp": data["time"],
                "temperature_2m": data["temperature_2m"],
                "apparent_temperature": data["apparent_temperature"],
                "precipitation": data["precipitation"],
                "wind_speed_10m": data["wind_speed_10m"],
                "weather_code": data["weather_code"],
            }
        except (httpx.HTTPError, KeyError) as exc:
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "n/a"
            logger.warning(
                "poll fetch failed city=%s status=%s retry=%d/%d error=%s",
                city,
                status_code,
                attempt,
                MAX_FETCH_RETRIES + 1,
                exc,
            )
            if attempt >= MAX_FETCH_RETRIES + 1:
                return None
            await asyncio.sleep(1)
    return None


async def poll_once(client: httpx.AsyncClient):
    db = await get_db()
    try:
        for city, coords in CITIES.items():
            data = await fetch_weather(client, city, coords)
            if data is None:
                continue
            inserted = await insert_reading(db, city, data)
            if inserted:
                logger.info("New reading stored for %s at %s", city, data["timestamp"])
                await evaluate_events(db, city, data)
            else:
                logger.debug("Duplicate reading skipped for %s at %s", city, data["timestamp"])
    finally:
        await db.close()


async def run_poller():
    logger.info("Poller starting, interval=%ds", POLL_INTERVAL_SECONDS)
    async with httpx.AsyncClient() as client:
        while True:
            await poll_once(client)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
