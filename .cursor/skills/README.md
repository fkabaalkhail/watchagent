# WatchAgent Cursor Skills

This folder contains executable project skills for Cursor agents.

## `weather_data_analysis.py`

Primary graded analysis skill. It reads stored readings and events from SQLite and returns structured JSON containing:

- city trends over a configurable lookback window
- cross-city latest temperature spread
- event volume breakdown by type and city

Parameters:
- `--db` (required): path to SQLite database
- `--question` (required): natural language question (keywords: trend, city, compare, event, summary)
- `--hours` (optional, default 24): lookback window in hours

Example:

```bash
python .cursor/skills/weather_data_analysis.py --db data/watchagent.db --question "compare city trends and event summary" --hours 48
```

Sample output (empty DB):
```json
{"question": "...", "status": "no_data_collected_yet", "message": "Tables exist but no readings have been stored."}
```

## `replay_event_candidates.py`

Utility skill to inspect raw readings for candidate event triggers before cooldown suppression. Covers all 8 event types including temporal (RAPID_TEMP_CHANGE), comparative (CROSS_CITY_TEMP_DIVERGENCE), city-calibrated (CITY_TEMP_ANOMALY), and WMO condition-based (SEVERE_WEATHER_CODE).

Parameters:
- `--db` (required): path to SQLite database
- `--limit` (optional, default 50): rows per city to replay
- `--hours` (optional, default 0): lookback window in hours (0 = use --limit only)

Example:

```bash
python .cursor/skills/replay_event_candidates.py --db data/watchagent.db --limit 100 --hours 48
```

Sample output (empty DB):
```json
{"error": "Database missing required table: readings"}
```
