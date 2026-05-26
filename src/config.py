import os

CITIES = {
    "Ottawa": {"lat": 45.42, "lon": -75.69},
    "Toronto": {"lat": 43.70, "lon": -79.42},
    "Vancouver": {"lat": 49.25, "lon": -123.12},
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/watchagent.db")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
MAX_FETCH_RETRIES = int(os.getenv("MAX_FETCH_RETRIES", "1"))
FETCH_TIMEOUT_SECONDS = int(os.getenv("FETCH_TIMEOUT_SECONDS", "10"))
ENABLE_POLLER = _env_bool("ENABLE_POLLER", True)
