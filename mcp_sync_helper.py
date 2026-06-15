"""
mcp_sync_helper.py — Append Strava MCP data to activities + laps CSVs.

Usage:
    python3 mcp_sync_helper.py <input.json>

Input JSON format:
{
  "activities": [
    {
      "id": "12345",
      "name": "Morning Run",
      "description": "...",
      "sport_type": "Run",
      "start_local": "2026-06-10T07:05:00",
      "summary": {
        "distance": 6999,          // meters
        "moving_time": 2706,       // seconds
        "elapsed_time": 2787,
        "elevation_gain": 7,       // meters
        "avg_speed": 2.59,         // m/s
        "max_speed": 3.56,         // m/s
        "relative_effort": 18,
        "total_calories": 466,
        "avg_cadence": 81.4,
        "kudos_count": 24
      }
    }
  ],
  "performance": {
    "12345": {
      "average_heartrate": 133.8,
      "max_heartrate": 150,
      "laps": [
        {
          "moving_time": 2706,
          "elapsed_time": 2787,
          "distance": 6999.79,    // meters
          "elevation_gain": 11.8, // meters
          "max_speed": 3.56,      // m/s
          "avg_hr": 133.8,
          "max_hr": 150,
          "avg_cadence": 81.48
        }
      ]
    }
  }
}

The "performance" key is optional — only needs entries for runs where laps are desired.
"""

import csv
import json
import os
import re
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVITIES_CSV = os.path.join(BASE_DIR, "strava_activities.csv")
LAPS_CSV = os.path.join(BASE_DIR, "strava_laps.csv")

ACTIVITIES_FIELDNAMES = [
    "date", "name", "type", "sport_type", "distance_miles", "moving_time",
    "elapsed_time", "pace_per_mile", "total_elevation_gain_ft",
    "average_heartrate", "max_heartrate", "average_speed_mph", "max_speed_mph",
    "suffer_score", "kudos", "calories", "description", "strava_id",
]

LAPS_FIELDNAMES = [
    "activity_date", "activity_name", "activity_type", "activity_total_miles",
    "activity_strava_id", "lap_number", "lap_name", "lap_distance_miles",
    "lap_moving_time", "lap_elapsed_time", "lap_pace_per_mile",
    "lap_avg_speed_mph", "lap_max_speed_mph", "lap_avg_heartrate",
    "lap_max_heartrate", "lap_elevation_gain_ft", "lap_avg_cadence",
]

QUALITY_KEYWORDS = [
    "ffrt", "fartlek", "yasso", "tempo", "threshold", "workout",
    "interval", "ladder", "michigan", "matrix", "lumberjack",
    "relay", "blastoff", "half time", "race", "13.1", "half",
    "10k", "5k", "shuffle", "soldier", "hidden gem",
]


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def meters_to_miles(m):
    if not m:
        return 0.0
    return round(float(m) / 1609.344, 2)


def seconds_to_hms(s):
    if not s:
        return "0:00:00"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def pace_per_mile(dist_m, moving_s):
    if not dist_m or not moving_s:
        return ""
    miles = float(dist_m) / 1609.344
    if miles < 0.01:
        return ""
    spm = float(moving_s) / miles
    mins, secs = divmod(int(spm), 60)
    return f"{mins}:{secs:02d}"


def format_date(dt_str):
    """Convert ISO datetime string to 'YYYY-MM-DD HH:MM'."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return dt_str[:16].replace("T", " ")


def is_quality_run(name):
    if re.search(r'\d+:\d{2}:\d{2}', name):
        return True
    nl = name.lower()
    return any(k in nl for k in QUALITY_KEYWORDS)


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_existing_activity_ids():
    if not os.path.exists(ACTIVITIES_CSV):
        return set()
    with open(ACTIVITIES_CSV, newline="") as f:
        return {row.get("strava_id", "") for row in csv.DictReader(f)}


def load_existing_lap_activity_ids():
    if not os.path.exists(LAPS_CSV):
        return set()
    with open(LAPS_CSV, newline="") as f:
        return {row.get("activity_strava_id", "") for row in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def build_activity_row(a, perf=None):
    summary = a.get("summary", {})
    dist_m = summary.get("distance", 0) or 0
    moving_s = summary.get("moving_time", 0) or 0
    avg_hr = ""
    max_hr = ""
    if perf:
        avg_hr = perf.get("average_heartrate", "")
        max_hr = perf.get("max_heartrate", "")
    sport = a.get("sport_type", "")
    return {
        "date": format_date(a.get("start_local", "")),
        "name": a.get("name", ""),
        "type": sport,  # 'type' and 'sport_type' are the same in modern Strava
        "sport_type": sport,
        "distance_miles": meters_to_miles(dist_m),
        "moving_time": seconds_to_hms(moving_s),
        "elapsed_time": seconds_to_hms(summary.get("elapsed_time", 0)),
        "pace_per_mile": pace_per_mile(dist_m, moving_s),
        "total_elevation_gain_ft": round((summary.get("elevation_gain", 0) or 0) * 3.28084, 1),
        "average_heartrate": avg_hr,
        "max_heartrate": max_hr,
        "average_speed_mph": round((summary.get("avg_speed", 0) or 0) * 2.23694, 2),
        "max_speed_mph": round((summary.get("max_speed", 0) or 0) * 2.23694, 2),
        "suffer_score": summary.get("relative_effort", ""),
        "kudos": summary.get("kudos_count", 0),
        "calories": summary.get("total_calories", ""),
        "description": (a.get("description") or "").replace("\n", " "),
        "strava_id": str(a.get("id", "")),
    }


def build_lap_rows(activity_id, a, laps):
    """Build lap CSV rows from MCP get_activity_performance laps list."""
    summary = a.get("summary", {})
    dist_m = summary.get("distance", 0) or 0
    sport = a.get("sport_type", "")
    rows = []
    for i, lap in enumerate(laps):
        lap_dist_m = lap.get("distance", 0) or 0
        lap_moving = lap.get("moving_time", 0) or 0
        # avg_speed not in MCP laps — derive from distance/time
        avg_speed_ms = (lap_dist_m / lap_moving) if lap_moving > 0 else 0
        rows.append({
            "activity_date": format_date(a.get("start_local", "")),
            "activity_name": a.get("name", ""),
            "activity_type": sport,
            "activity_total_miles": meters_to_miles(dist_m),
            "activity_strava_id": str(activity_id),
            "lap_number": i + 1,
            "lap_name": f"Lap {i + 1}",
            "lap_distance_miles": meters_to_miles(lap_dist_m),
            "lap_moving_time": seconds_to_hms(lap_moving),
            "lap_elapsed_time": seconds_to_hms(lap.get("elapsed_time", 0)),
            "lap_pace_per_mile": pace_per_mile(lap_dist_m, lap_moving),
            "lap_avg_speed_mph": round(avg_speed_ms * 2.23694, 2),
            "lap_max_speed_mph": round((lap.get("max_speed", 0) or 0) * 2.23694, 2),
            "lap_avg_heartrate": lap.get("avg_hr", ""),
            "lap_max_heartrate": lap.get("max_hr", ""),
            "lap_elevation_gain_ft": round((lap.get("elevation_gain", 0) or 0) * 3.28084, 1),
            "lap_avg_cadence": lap.get("avg_cadence", ""),
        })
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mcp_sync_helper.py <input.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    activities = data.get("activities", [])
    performance = data.get("performance", {})

    existing_act_ids = load_existing_activity_ids()
    existing_lap_ids = load_existing_lap_activity_ids()

    new_activities = [a for a in activities if str(a.get("id", "")) not in existing_act_ids]
    act_count = 0
    lap_count = 0
    lap_act_count = 0

    # Append activities
    if new_activities:
        file_exists = os.path.exists(ACTIVITIES_CSV)
        with open(ACTIVITIES_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ACTIVITIES_FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            for a in new_activities:
                aid = str(a.get("id", ""))
                perf = performance.get(aid)
                writer.writerow(build_activity_row(a, perf))
                act_count += 1

    # Append laps
    file_exists = os.path.exists(LAPS_CSV)
    lap_rows_to_write = []
    for a in new_activities:
        aid = str(a.get("id", ""))
        if aid in existing_lap_ids:
            continue
        sport = a.get("sport_type", "")
        name = a.get("name", "")
        if sport != "Run" and "run" not in sport.lower():
            continue
        perf = performance.get(aid, {})
        laps = perf.get("laps", [])
        if laps:
            lap_rows_to_write.extend(build_lap_rows(aid, a, laps))
            lap_act_count += 1
            lap_count += len(laps)

    if lap_rows_to_write:
        with open(LAPS_CSV, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LAPS_FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            for row in lap_rows_to_write:
                writer.writerow(row)

    print(f"  Appended {act_count} activities -> strava_activities.csv")
    print(f"  Appended {lap_count} laps across {lap_act_count} runs -> strava_laps.csv")


if __name__ == "__main__":
    main()
