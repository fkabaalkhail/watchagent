# WatchAgent: Weather Monitor & AI Assistant

WatchAgent polls live weather for Ottawa, Toronto, and Vancouver, stores deduplicated readings, detects notable weather events, and exposes both datasets through a FastAPI HTTP API.

## System Overview

The service is designed around one core question: _what deserves attention_?

- The poller fetches Open-Meteo `current` weather data on a schedule.
- Readings are stored only when the `(city, timestamp)` pair is new.
- New readings are evaluated by an event engine that balances useful sensitivity and noise suppression.
- API endpoints provide recent readings and detected events for monitoring and review.

## Architecture

![Architecture Diagram](docs/architecture.png)

## Why FastAPI

FastAPI was selected because:

- it has first-class async support, which the background poller and aiosqlite DB calls require natively — Flask would need additional extensions for async
- its automatic query parameter validation (via Pydantic) keeps the `/readings` and `/events` contracts explicit without manual parsing
- it provides fast setup for a small service without the overhead of Django's ORM/admin layer, which this project does not need

## Event Detection Design

### Detection logic

The engine combines temporal, absolute, comparative, and city-calibrated signals:

- `RAPID_TEMP_CHANGE`: absolute temp shift >= 5 C over recent readings
- `EXTREME_WIND`: wind >= 60 km/h
- `HEAVY_PRECIPITATION`: precipitation >= 5 mm/hr
- `EXTREME_COLD`: apparent temperature <= -25 C
- `EXTREME_HEAT`: apparent temperature >= 35 C
- `CROSS_CITY_TEMP_DIVERGENCE`: city temp differs by >= 15 C versus another monitored city
- `CITY_TEMP_ANOMALY`: current city temp departs from that city's recent baseline by a city-specific threshold

### Noise control strategy

- Every event type has a cooldown window, so persistent conditions do not emit every hour.
- Event firing requires context (recent history) for change/anomaly signals.
- Thresholds for city anomaly differ by city (`Vancouver` stricter, `Ottawa` less strict) to reflect different climates.

### Event record shape

Each event stores:

- `city`, `timestamp`, `event_type`, `severity`
- human-readable `description` (what happened and why it was notable)
- JSON `details` (machine-readable supporting values)

## API Reference

### `GET /health`

Returns service status and storage counts.

```bash
curl "http://localhost:8000/health"
```

```json
{ "status": "ok", "readings_stored": 142, "events_stored": 7 }
```

### `GET /readings?city=Ottawa&limit=50`

Returns stored readings, most recent first.

```bash
curl "http://localhost:8000/readings?city=Ottawa&limit=50"
```

```json
{
  "readings": [
    {
      "id": 42, "city": "Ottawa", "timestamp": "2026-05-26T18:00",
      "temperature_2m": 22.5, "apparent_temperature": 20.1,
      "precipitation": 0.0, "wind_speed_10m": 14.3, "weather_code": 2
    }
  ]
}
```

### `GET /events?city=Ottawa&limit=50`

Returns detected events, most recent first.

```bash
curl "http://localhost:8000/events?city=Ottawa&limit=50"
```

```json
{
  "events": [
    {
      "id": 3, "city": "Ottawa", "timestamp": "2026-05-26T14:00",
      "event_type": "RAPID_TEMP_CHANGE", "severity": "info",
      "description": "Temperature rose 6.2°C over recent readings (from 16.3°C to 22.5°C)",
      "details": "{\"delta\": 6.2, \"from\": 16.3, \"to\": 22.5}",
      "created_at": "2026-05-26 14:01:23"
    }
  ]
}
```

## Local Development

### Prerequisites

- Python 3.11+
- Docker + Docker Compose

### Run with Docker (challenge path)

```bash
git clone <your-repo>
cd watchagent
cp .env.example .env
docker compose up --build
```

API will be available at [http://localhost:8000](http://localhost:8000).

### Run without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python main.py
```

## Environment Variables

See `.env.example`:

- `POLL_INTERVAL_SECONDS`: poll interval in seconds
- `DATABASE_PATH`: sqlite db path
- `API_HOST`, `API_PORT`: HTTP bind settings
- `MAX_FETCH_RETRIES`: retries per city fetch before giving up
- `FETCH_TIMEOUT_SECONDS`: HTTP timeout per attempt
- `ENABLE_POLLER`: enables background poller (set `false` in some tests/dev scenarios)

## Tests

Run unit tests:

```bash
pytest -q
```

Coverage focus:

- deduplication by city+timestamp
- event detection semantics and cooldown suppression
- `/health`, `/readings`, `/events` response shape

All tests mock external dependencies by avoiding live Open-Meteo calls.

## CI Pipeline

GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push to `main` with:

1. **Test job**: installs dependencies and runs `pytest -q`
2. **Build job**: runs `docker build -t watchagent:ci .`

## Cursor Setup

The repository includes a committed `.cursor/` folder as a graded deliverable.

### Rules

1. `.cursor/rules/poller-failure-logging.mdc`
   - Encodes poller resilience and logging behavior for failed API calls.
   - Prevents fragile implementations that crash on transient upstream errors.

2. `.cursor/rules/event-record-shape.mdc`
   - Enforces event record schema and anti-noise behavior (cooldowns + rationale fields).
   - Keeps event output analyzable and consistent as logic evolves.

3. `.cursor/rules/test-isolation-and-dedup.mdc`
   - Enforces mocked external API usage in unit tests and poll-level dedup coverage.
   - Keeps tests deterministic and aligned to the challenge's dedup expectation.

### Custom Agent

`.cursor/agents/event-signal-reviewer.md`

- Scoped reviewer agent for event logic quality only.
- Helps calibrate signal/noise trade-offs and identify missing false-positive/false-negative tests.

### Skills

1. `.cursor/skills/weather_data_analysis.py` (primary graded skill)
   - Executable data analysis script that queries readings/events from SQLite.
   - Returns structured JSON for trend summaries, city comparison, and event breakdown.
   - Example:
     ```bash
     python .cursor/skills/weather_data_analysis.py --db data/watchagent.db --question "compare city trends and event summary"
     ```

2. `.cursor/skills/replay_event_candidates.py`
   - Replays recent readings to inspect candidate threshold matches.
   - Useful for calibrating event definitions against collected data.

3. `.cursor/skills/weather-data-analyst/SKILL.md`
   - Cursor skill definition that operationalizes the analysis scripts for repeatable agent use.
   - Documents input expectations, output format, and failure handling for empty databases.

## Open-Meteo Source

Data source: [https://api.open-meteo.com/v1/forecast](https://api.open-meteo.com/v1/forecast)  
Cities monitored:

- Ottawa (`45.42`, `-75.69`)
- Toronto (`43.70`, `-79.42`)
- Vancouver (`49.25`, `-123.12`)
