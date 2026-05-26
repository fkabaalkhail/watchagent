#!/usr/bin/env python3
"""
WatchAgent data analysis skill.

Usage examples:
  python .cursor/skills/weather_data_analysis.py --db data/watchagent.db --question "compare city trends last 24h"
  python .cursor/skills/weather_data_analysis.py --db data/watchagent.db --question "event summary"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from statistics import mean


def _rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(query, params)
    return cur.fetchall()


def _has_required_tables(conn: sqlite3.Connection) -> bool:
    rows = _rows(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('readings', 'events')",
    )
    names = {row["name"] for row in rows}
    return {"readings", "events"}.issubset(names)


def _city_temp_summary(conn: sqlite3.Connection, since_iso: str) -> dict:
    rows = _rows(
        conn,
        """
        SELECT city, temperature_2m, apparent_temperature, wind_speed_10m, precipitation, timestamp
        FROM readings
        WHERE timestamp >= ?
        ORDER BY city, timestamp ASC
        """,
        (since_iso,),
    )

    by_city: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_city.setdefault(row["city"], []).append(row)

    out: dict[str, dict] = {}
    for city, city_rows in by_city.items():
        temps = [r["temperature_2m"] for r in city_rows]
        app_temps = [r["apparent_temperature"] for r in city_rows]
        winds = [r["wind_speed_10m"] for r in city_rows]
        precips = [r["precipitation"] for r in city_rows]
        out[city] = {
            "reading_count": len(city_rows),
            "latest_timestamp": city_rows[-1]["timestamp"],
            "temp_min_c": min(temps),
            "temp_max_c": max(temps),
            "temp_avg_c": round(mean(temps), 2),
            "apparent_temp_avg_c": round(mean(app_temps), 2),
            "wind_avg_kmh": round(mean(winds), 2),
            "precip_total_mm": round(sum(precips), 2),
        }
    return out


def _event_summary(conn: sqlite3.Connection, since_iso: str) -> dict:
    total = _rows(conn, "SELECT COUNT(*) AS c FROM events WHERE timestamp >= ?", (since_iso,))[0]["c"]
    by_type = _rows(
        conn,
        """
        SELECT event_type, COUNT(*) AS count
        FROM events
        WHERE timestamp >= ?
        GROUP BY event_type
        ORDER BY count DESC
        """,
        (since_iso,),
    )
    by_city = _rows(
        conn,
        """
        SELECT city, COUNT(*) AS count
        FROM events
        WHERE timestamp >= ?
        GROUP BY city
        ORDER BY count DESC
        """,
        (since_iso,),
    )
    return {
        "total_events": total,
        "events_by_type": [{row["event_type"]: row["count"]} for row in by_type],
        "events_by_city": [{row["city"]: row["count"]} for row in by_city],
    }


def _city_comparison(conn: sqlite3.Connection) -> dict:
    rows = _rows(
        conn,
        """
        SELECT city, timestamp, temperature_2m
        FROM readings
        WHERE (city, timestamp) IN (
            SELECT city, MAX(timestamp) FROM readings GROUP BY city
        )
        ORDER BY city ASC
        """
    )
    latest = {r["city"]: {"timestamp": r["timestamp"], "temp_c": r["temperature_2m"]} for r in rows}
    temps = [r["temperature_2m"] for r in rows]
    spread = max(temps) - min(temps) if temps else 0.0
    return {"latest_temperatures": latest, "temperature_spread_c": round(spread, 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze WatchAgent readings/events.")
    parser.add_argument("--db", required=True, help="Path to sqlite database file.")
    parser.add_argument("--question", required=True, help="Natural language question.")
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Lookback window for trend and event summaries.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    since_iso = since.strftime("%Y-%m-%dT%H:%M")
    question = args.question.lower()

    conn = sqlite3.connect(args.db)
    try:
        if not _has_required_tables(conn):
            print(
                json.dumps(
                    {
                        "question": args.question,
                        "status": "error",
                        "error": "Database missing required tables: readings, events",
                    },
                    indent=2,
                )
            )
            return

        row_count = _rows(conn, "SELECT COUNT(*) AS c FROM readings")[0]["c"]
        if row_count == 0:
            print(
                json.dumps(
                    {
                        "question": args.question,
                        "status": "no_data_collected_yet",
                        "message": "Tables exist but no readings have been stored. Start the poller to collect data.",
                    },
                    indent=2,
                )
            )
            return

        response = {
            "question": args.question,
            "time_window_hours": args.hours,
            "since_timestamp_utc": since_iso,
            "analysis": {},
        }

        response["analysis"]["city_comparison"] = _city_comparison(conn)

        if any(token in question for token in ["trend", "city", "temperature", "compare"]):
            response["analysis"]["city_trends"] = _city_temp_summary(conn, since_iso)

        if any(token in question for token in ["event", "alert", "notable", "summary"]):
            response["analysis"]["event_summary"] = _event_summary(conn, since_iso)

        if not response["analysis"].get("city_trends") and not response["analysis"].get("event_summary"):
            # Default: provide both if the question is broad.
            response["analysis"]["city_trends"] = _city_temp_summary(conn, since_iso)
            response["analysis"]["event_summary"] = _event_summary(conn, since_iso)

        print(json.dumps(response, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
