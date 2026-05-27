#!/usr/bin/env python3
"""
Replay helper skill for event analysis.

Scans recent readings and prints candidate rows that would match high-signal
thresholds before cooldown is considered. Covers all 8 event types.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta

THRESHOLDS = {
    "EXTREME_WIND": {"wind_speed_10m_gte": 60},
    "HEAVY_PRECIPITATION": {"precipitation_gte": 5},
    "EXTREME_COLD": {"apparent_temperature_lte": -25},
    "EXTREME_HEAT": {"apparent_temperature_gte": 35},
    "RAPID_TEMP_CHANGE": {"temp_delta_gte": 5, "rate_per_hour_gte": 1.5, "requires_history": 2},
    "CROSS_CITY_TEMP_DIVERGENCE": {"cross_city_diff_gte": 15},
    "CITY_TEMP_ANOMALY": {"baseline_deviation_gte": {"Ottawa": 10, "Toronto": 9, "Vancouver": 7}},
    "SEVERE_WEATHER_CODE": {"weather_code_gte": 65},
}


def _has_readings_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='readings' LIMIT 1"
    ).fetchone()
    return row is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay notable event candidates.")
    parser.add_argument("--db", required=True, help="Path to sqlite database file.")
    parser.add_argument("--limit", type=int, default=50, help="Rows per city to replay.")
    parser.add_argument("--hours", type=int, default=0, help="Lookback window in hours (0 = use --limit only).")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    result: dict = {
        "thresholds_used": THRESHOLDS,
        "candidates": {
            "EXTREME_WIND": [],
            "HEAVY_PRECIPITATION": [],
            "EXTREME_COLD": [],
            "EXTREME_HEAT": [],
            "SEVERE_WEATHER_CODE": [],
            "RAPID_TEMP_CHANGE": [],
            "CROSS_CITY_TEMP_DIVERGENCE": [],
            "CITY_TEMP_ANOMALY": [],
        },
    }

    try:
        if not _has_readings_table(conn):
            print(json.dumps({"error": "Database missing required table: readings"}, indent=2))
            return

        time_filter = ""
        params_extra: tuple = ()
        if args.hours > 0:
            since = (datetime.now(UTC) - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M")
            time_filter = " AND timestamp >= ?"
            params_extra = (since,)

        cities = [r["city"] for r in conn.execute("SELECT DISTINCT city FROM readings ORDER BY city").fetchall()]

        city_latest_temps: dict[str, float] = {}

        for city in cities:
            rows = conn.execute(
                f"SELECT * FROM readings WHERE city = ?{time_filter} ORDER BY timestamp DESC LIMIT ?",
                (city, *params_extra, args.limit),
            ).fetchall()

            if not rows:
                continue

            city_latest_temps[city] = rows[0]["temperature_2m"]

            for row in rows:
                if row["wind_speed_10m"] >= 60:
                    result["candidates"]["EXTREME_WIND"].append(
                        {"city": city, "timestamp": row["timestamp"], "wind_speed_10m": row["wind_speed_10m"]}
                    )
                if row["precipitation"] >= 5:
                    result["candidates"]["HEAVY_PRECIPITATION"].append(
                        {"city": city, "timestamp": row["timestamp"], "precipitation": row["precipitation"]}
                    )
                if row["apparent_temperature"] <= -25:
                    result["candidates"]["EXTREME_COLD"].append(
                        {"city": city, "timestamp": row["timestamp"], "apparent_temperature": row["apparent_temperature"]}
                    )
                if row["apparent_temperature"] >= 35:
                    result["candidates"]["EXTREME_HEAT"].append(
                        {"city": city, "timestamp": row["timestamp"], "apparent_temperature": row["apparent_temperature"]}
                    )
                if row["weather_code"] >= 65:
                    result["candidates"]["SEVERE_WEATHER_CODE"].append(
                        {"city": city, "timestamp": row["timestamp"], "weather_code": row["weather_code"]}
                    )

            # RAPID_TEMP_CHANGE: check consecutive pairs
            temps_ordered = list(reversed(rows))  # oldest first
            for i in range(1, len(temps_ordered)):
                delta = abs(temps_ordered[i]["temperature_2m"] - temps_ordered[i - 1]["temperature_2m"])
                if delta >= 5:
                    result["candidates"]["RAPID_TEMP_CHANGE"].append(
                        {"city": city, "timestamp": temps_ordered[i]["timestamp"], "delta_c": round(delta, 2)}
                    )

            # CITY_TEMP_ANOMALY: compare latest to mean of history
            if len(rows) >= 4:
                temps = [r["temperature_2m"] for r in rows]
                baseline = sum(temps[1:]) / len(temps[1:])
                deviation = abs(temps[0] - baseline)
                threshold = THRESHOLDS["CITY_TEMP_ANOMALY"]["baseline_deviation_gte"].get(city, 10)
                if deviation >= threshold:
                    result["candidates"]["CITY_TEMP_ANOMALY"].append(
                        {"city": city, "timestamp": rows[0]["timestamp"], "deviation_c": round(deviation, 2), "baseline_c": round(baseline, 2)}
                    )

        # CROSS_CITY_TEMP_DIVERGENCE: compare latest temps across cities
        if len(city_latest_temps) >= 2:
            city_list = list(city_latest_temps.items())
            for i in range(len(city_list)):
                for j in range(i + 1, len(city_list)):
                    diff = abs(city_list[i][1] - city_list[j][1])
                    if diff >= 15:
                        result["candidates"]["CROSS_CITY_TEMP_DIVERGENCE"].append(
                            {"cities": [city_list[i][0], city_list[j][0]], "diff_c": round(diff, 2)}
                        )

        print(json.dumps(result, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
