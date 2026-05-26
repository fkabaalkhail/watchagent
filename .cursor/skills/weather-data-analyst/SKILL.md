---
name: weather-data-analyst
description: Answers questions about WatchAgent stored readings and events by running local analysis scripts. Use when the user asks for trends, city comparisons, event volume summaries, or replay analysis.
disable-model-invocation: true
---

# Weather Data Analyst

Use this skill to analyze collected WatchAgent data from SQLite. The `disable-model-invocation: true` flag ensures deterministic, script-only execution without LLM interpretation of raw data.

## Inputs expected

- A natural-language question (for example: "compare Ottawa vs Vancouver last 24h")
- Database path (default `data/watchagent.db`)
- Optional lookback window in hours (default: 24)

## Primary command

```bash
python .cursor/skills/weather_data_analysis.py --db data/watchagent.db --question "<question>" --hours 24
```

Supported question keywords: `trend`, `city`, `temperature`, `compare`, `event`, `alert`, `notable`, `summary`.

## Replay command (threshold sanity check)

```bash
python .cursor/skills/replay_event_candidates.py --db data/watchagent.db --limit 100 --hours 48
```

## Output contract

- Return structured JSON from the script first.
- Then provide a concise interpretation:
  - cross-city differences
  - notable trend direction
  - event concentration by type/city

### Example output shape (weather_data_analysis.py)

```json
{
  "question": "compare city trends and event summary",
  "time_window_hours": 24,
  "since_timestamp_utc": "2026-05-25T19:00",
  "analysis": {
    "city_comparison": {
      "latest_temperatures": {
        "Ottawa": {"timestamp": "2026-05-26T18:00", "temp_c": 22.5},
        "Toronto": {"timestamp": "2026-05-26T18:00", "temp_c": 24.1},
        "Vancouver": {"timestamp": "2026-05-26T18:00", "temp_c": 17.3}
      },
      "temperature_spread_c": 6.8
    },
    "city_trends": {
      "Ottawa": {"reading_count": 24, "temp_min_c": 14.2, "temp_max_c": 23.1, "temp_avg_c": 18.6, "wind_avg_kmh": 12.4, "precip_total_mm": 0.0}
    },
    "event_summary": {
      "total_events": 3,
      "events_by_type": [{"RAPID_TEMP_CHANGE": 2}, {"EXTREME_WIND": 1}],
      "events_by_city": [{"Ottawa": 2}, {"Toronto": 1}]
    }
  }
}
```

### Example output shape (replay_event_candidates.py)

```json
{
  "thresholds_used": {"EXTREME_WIND": {"wind_speed_10m_gte": 60}, "...": "..."},
  "candidates": {
    "EXTREME_WIND": [{"city": "Ottawa", "timestamp": "...", "wind_speed_10m": 65.2}],
    "RAPID_TEMP_CHANGE": [{"city": "Toronto", "timestamp": "...", "delta_c": 6.1}],
    "CROSS_CITY_TEMP_DIVERGENCE": [{"cities": ["Ottawa", "Vancouver"], "diff_c": 16.3}]
  }
}
```

## Failure handling

- If required tables are missing: `{"status": "error", "error": "Database missing required tables: readings, events"}`.
- If tables exist but no data collected: `{"status": "no_data_collected_yet", "message": "..."}`.
- Do not invent analysis when the database is empty.
