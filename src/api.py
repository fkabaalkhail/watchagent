import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Query

from src.config import ENABLE_POLLER
from src.database import count_events, count_readings, get_db, get_events, get_readings
from src.poller import run_poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_poller()) if ENABLE_POLLER else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="WatchAgent", lifespan=lifespan)


@app.get("/health")
async def health():
    db = await get_db()
    try:
        return {
            "status": "ok",
            "readings_stored": await count_readings(db),
            "events_stored": await count_events(db),
        }
    finally:
        await db.close()


@app.get("/readings")
async def readings(city: str | None = Query(None), limit: int = Query(50, ge=1, le=1000)):
    db = await get_db()
    try:
        return {"readings": await get_readings(db, city=city, limit=limit)}
    finally:
        await db.close()


@app.get("/events")
async def events(city: str | None = Query(None), limit: int = Query(50, ge=1, le=1000)):
    db = await get_db()
    try:
        return {"events": await get_events(db, city=city, limit=limit)}
    finally:
        await db.close()
