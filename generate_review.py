#!/usr/bin/env python3
"""
Chicago Marathon 2026 — Weekly Review Generator
================================================
Reads strava_activities.csv + strava_laps.csv, analyzes the current
training week against the plan, and injects HTML into dashboard.html.

Run automatically on Thu mornings (after Wed FFRT) and Sat afternoons
(after long run). Can also be run manually any time.
"""

import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVITIES_FILE = os.path.join(SCRIPT_DIR, "strava_activities.csv")
LAPS_FILE = os.path.join(SCRIPT_DIR, "strava_laps.csv")
DASHBOARD_FILE = os.path.join(SCRIPT_DIR, "dashboard.html")

# ── Training context ────────────────────────────────────────────────────────

BLOCK_START = datetime(2026, 6, 1)
MARATHON_DATE = datetime(2026, 10, 11)

TARGET_PACES = {
    "easy":     ("9:45", "10:15"),
    "mp":       ("9:05", "9:10"),
    "gmp":      ("8:55", "9:00"),
    "hmp":      ("8:20", "8:25"),
    "10k":      ("7:50", "8:05"),
    "5k":       ("7:30", "7:45"),
    "recovery": ("10:30", "11:00"),
}

# Realistic easy-run pace used to convert durations → mileage estimates.
# Kevin's easy runs sit around 10:30/mi, so 45min easy ≈ 4.3mi (not 4.5).
EASY_PACE_SEC = 630  # 10:30/mi

# Keywords that mark a run as a quality/FFRT session. FFRP = Fleet Feet
# Racing Performance = same as FFRT. "funnel" covers Funnel of Fun workouts.
WORKOUT_KEYWORDS = [
    "ffrt", "ffrp", "fartlek", "yasso", "tempo", "threshold", "workout",
    "interval", "ladder", "michigan", "matrix", "lumberjack", "relay",
    "blastoff", "half time", "funnel", "progression", "sandwich",
]


def est_miles_from_minutes(minutes, pace_sec=EASY_PACE_SEC):
    """Convert an easy-run duration (minutes) to a mileage estimate."""
    return minutes * 60.0 / pace_sec


WEEKLY_PLAN = {
    1:  {"miles": 32,  "phase": "Race",     "key": "Half Time Fartlek + Chicago 13.1"},
    2:  {"miles": 27,  "phase": "Recovery", "key": "Post-race recovery — easy all week, no FFRT, 10-12mi Sat"},
    3:  {"miles": 36,  "phase": "Base",     "key": "Funnel of Fun + 12mi easy"},
    4:  {"miles": 38,  "phase": "Base",     "key": "Yasso 800s + 13mi w/ MP surges"},
    5:  {"miles": 34,  "phase": "Recovery", "key": "2min on/off + 11mi easy"},
    6:  {"miles": 41,  "phase": "Build",    "key": "Progression + 3x3 Matrix + 14mi"},
    7:  {"miles": 41,  "phase": "Build",    "key": "Ladder + 15mi w/ MP/float"},
    8:  {"miles": 45,  "phase": "Build",    "key": "Michigan + 16mi 3x2@MP"},
    9:  {"miles": 37,  "phase": "Recovery", "key": "Lumberjack + 13mi easy"},
    10: {"miles": 52,  "phase": "Peak",     "key": "HMP tempo + 1K Sandwich + 18mi"},
    11: {"miles": 50,  "phase": "Peak",     "key": "10min threshold + 19mi 4x3@MP"},
    12: {"miles": 36,  "phase": "Recovery", "key": "5K Partner Relay + 13mi easy"},
    13: {"miles": 51,  "phase": "Peak",     "key": "Modified Tempo + 18mi 3x4@MP"},
    14: {"miles": 53,  "phase": "Peak",     "key": "Threshold + 20.5mi w/ 10@GMP"},
    15: {"miles": 39,  "phase": "Peak",     "key": "In & Out Tempo + Hidden Gem Half"},
    16: {"miles": 53,  "phase": "Peak",     "key": "Yassos + 20mi opt 4@MP"},
    17: {"miles": 48,  "phase": "Taper 1",  "key": "2mi reps + 19mi 8@MP+2@HMP"},
    18: {"miles": 33,  "phase": "Taper 2",  "key": "Root Beer Float + 11mi easy"},
    19: {"miles": 17,  "phase": "Race Week","key": "Pre-race 3@MP + Chicago Marathon"},
}

# Day-by-day schedule for weeks rendered dynamically (past + current weeks).
# Each entry: (Day, Run, Notes, planned_miles, kind)
#   planned_miles — estimate at realistic pace; None for rest days
#   kind          — easy | quality | long | rest | race  (drives the dot + column)
# Easy planned miles use EASY_PACE_SEC (10:30/mi); workout/long use plan mileage.
WEEK_SCHEDULE = {
    1: [
        ("Mon", "45–55min easy + HAF",        "",                              4.8,  "easy"),
        ("Tue", "45min easy",                  "",                              4.3,  "easy"),
        ("Wed", "Half Time Fartlek (FFRT)",    "8mi quality.",                  8.0,  "quality"),
        ("Thu", "REST",                        "",                              None, "rest"),
        ("Fri", "20min + strides",             "Pre-race shakeout.",            2.0,  "easy"),
        ("Sat", "REST",                        "Lay out race kit. Hydrate. Sleep.", None, "rest"),
        ("Sun", "CHICAGO 13.1 — FULL SEND",    "1:52:21 (8:27/mi).",            13.1, "race"),
    ],
    2: [
        ("Mon", "20–25min easy or rest",       "No strides. HAF fine if upper body.", 2.0,  "easy"),
        ("Tue", "45–50min easy",               "Still no strides. Just aerobic.",     4.5,  "easy"),
        ("Wed", "40–50min easy",               "Instead of FFRT. 10:00–10:30 pace.",  4.3,  "easy"),
        ("Thu", "REST / optional Lagree",      "",                                    None, "rest"),
        ("Fri", "50min easy + HAF",            "Back to normal.",                     4.8,  "easy"),
        ("Sat", "10–12mi easy",                "The priority. Arrive fresh.",         11.0, "long"),
        ("Sun", "REST",                        "",                                    None, "rest"),
    ],
    3: [
        ("Mon", "50min easy + HAF",            "",                                    4.8,  "easy"),
        ("Tue", "65min easy ★ — first extended Tue", "",                              6.2,  "easy"),
        ("Wed", "Funnel of Fun: 15@GMP+10, 10@HMP, 1–3x5@10K — FFRT + HAF", "",       8.0,  "quality"),
        ("Thu", "REST · Optional Lagree",      "",                                    None, "rest"),
        ("Fri", "50min easy + HAF",            "",                                    4.8,  "easy"),
        ("Sat", "11–13mi easy",                "",                                    12.0, "long"),
        ("Sun", "REST or recovery · Ricky Byrdsong 8K/5K option", "",                 None, "rest"),
    ],
    4: [
        ("Mon", "55min easy + 6 strides + HAF","",                                    5.2,  "easy"),
        ("Tue", "75min easy ★",                "",                                    7.1,  "easy"),
        ("Wed", "Yasso 800s: 8–10x800 @ 3:50–3:55 — FFRT + HAF", "",                  8.0,  "quality"),
        ("Thu", "REST · Optional Lagree",      "",                                    None, "rest"),
        ("Fri", "50min easy + HAF",            "",                                    4.8,  "easy"),
        ("Sat", "12–14mi w/ MP surges (0.25mi/mi for 5–7mi) — FFRT Lincoln Sq", "",   13.0, "long"),
        ("Sun", "REST or recovery",            "",                                    None, "rest"),
    ],
    5: [
        ("Mon", "50min easy + 6 strides + HAF", "", 4.8, "easy"),
        ("Tue", "55min easy — recovery week, don't extend", "", 5.2, "easy"),
        ("Wed", "2min on/off: 6-12x2min @ threshold, 2min @ LR pace — FFRT + HAF", "", 8.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "45min easy + HAF", "", 4.3, "easy"),
        ("Sat", "10-12mi easy · 4 on the 4th race option (Elmhurst)", "", 11.0, "long"),
        ("Sun", "REST or recovery", "", None, "rest"),
    ],
    6: [
        ("Mon", "Progression: 3-5mi current MP → GMP + HAF", "", 7.0, "quality"),
        ("Tue", "80min easy ★ — run by HR in heat", "", 7.6, "easy"),
        ("Wed", "3x3 Matrix: 2-3 sets of 4x300@mile, 100m jog — FFRT + HAF", "", 7.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy", "", 5.2, "easy"),
        ("Sat", "14mi easy (opt last 4@CMP)", "", 14.0, "long"),
        ("Sun", "REST or recovery", "", None, "rest"),
    ],
    7: [
        ("Mon", "55min easy + HAF", "", 5.2, "easy"),
        ("Tue", "90min easy ★ — first 90-min Tuesday", "", 8.6, "easy"),
        ("Wed", "Ladder: 6-10x200, 3-5x400, 1-2x800 (200m recovery) — FFRT + HAF", "", 7.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy", "", 5.2, "easy"),
        ("Sat", "15mi w/ 4x(1mi@GMP, 1mi@GMP+30 float)", "", 15.0, "long"),
        ("Sun", "REST or recovery", "", None, "rest"),
    ],
    8: [
        ("Mon", "55min easy + 6 strides + HAF · First 50-mile week!", "", 5.2, "easy"),
        ("Tue", "100min easy ★", "", 9.5, "easy"),
        ("Wed", "Michigan: 1mi@10K, 2K tempo, 1200@5K, 2K tempo, 800@3K, 2K tempo, 400 AUG — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy + HAF — monitor for Sat", "", 5.2, "easy"),
        ("Sat", "16mi w/ 3x2@MP (1mi EZ recovery) · fuel practice", "", 16.0, "long"),
        ("Sun", "REST or recovery", "", None, "rest"),
    ],
    9: [
        ("Mon", "50min easy + HAF · Cutback week", "", 4.8, "easy"),
        ("Tue", "65min easy ★ — don't extend", "", 6.2, "easy"),
        ("Wed", "Lumberjack: alternating circuits — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "45min easy", "", 4.3, "easy"),
        ("Sat", "13mi easy", "", 13.0, "long"),
        ("Sun", "REST or recovery", "", None, "rest"),
    ],
    10: [
        ("Mon", "HMP tempo 3-4mi + HAF", "", 7.0, "quality"),
        ("Tue", "2:05 easy ★", "", 11.9, "easy"),
        ("Wed", "1K Sandwich: HMP-1K@5K-HMP — FFRT + HAF", "", 10.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy", "", 5.2, "easy"),
        ("Sat", "18mi w/ MP progression", "", 18.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    11: [
        ("Mon", "55min easy + 6 strides + HAF", "", 5.2, "easy"),
        ("Tue", "2:05 easy ★", "", 11.9, "easy"),
        ("Wed", "10min threshold — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy", "", 5.2, "easy"),
        ("Sat", "19mi w/ 4x3@MP — Waterfall Glen", "", 19.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    12: [
        ("Mon", "50min easy + 6 strides + HAF", "", 4.8, "easy"),
        ("Tue", "75min easy ★", "", 7.1, "easy"),
        ("Wed", "5K Partner Relay — FFRT + HAF", "", 7.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "45min easy", "", 4.3, "easy"),
        ("Sat", "13mi easy", "", 13.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    13: [
        ("Mon", "55min easy + 8 strides + HAF", "", 5.2, "easy"),
        ("Tue", "2:15 easy ★", "", 12.9, "easy"),
        ("Wed", "Modified Tempo — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy", "", 5.2, "easy"),
        ("Sat", "18mi w/ 3x4@MP — Busse Woods", "", 18.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    14: [
        ("Mon", "55min easy + HAF", "", 5.2, "easy"),
        ("Tue", "2:15 easy ★", "", 12.9, "easy"),
        ("Wed", "Threshold — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "55min easy", "", 5.2, "easy"),
        ("Sat", "20.5mi w/ 10@GMP — THE BIG ONE", "", 20.5, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    15: [
        ("Mon", "50min easy + HAF", "", 4.8, "easy"),
        ("Tue", "75min easy ★", "", 7.1, "easy"),
        ("Wed", "In & Out Tempo — FFRT + HAF", "", 8.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "45min easy", "", 4.3, "easy"),
        ("Sat", "Hidden Gem Half Marathon — FULL SEND", "", 15.0, "race"),
        ("Sun", "REST", "", None, "rest"),
    ],
    16: [
        ("Mon", "HMP tempo 3-4mi + HAF", "", 7.0, "quality"),
        ("Tue", "2:05 easy ★", "", 11.9, "easy"),
        ("Wed", "Yasso 800s — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST · Optional Lagree", "", None, "rest"),
        ("Fri", "50min easy", "", 4.8, "easy"),
        ("Sat", "20mi (opt last 4@MP) — FF group run · last 20-miler", "", 20.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    17: [
        ("Mon", "55min easy + 8 strides + HAF", "", 5.2, "easy"),
        ("Tue", "1:45 easy ★", "", 10.0, "easy"),
        ("Wed", "2mi reps + 200s — FFRT + HAF", "", 9.0, "quality"),
        ("Thu", "REST", "", None, "rest"),
        ("Fri", "50min easy", "", 4.8, "easy"),
        ("Sat", "19mi w/ 8@MP + 2@HMP — Going South", "", 19.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    18: [
        ("Mon", "50min easy + light HAF", "", 4.8, "easy"),
        ("Tue", "55min easy", "", 5.2, "easy"),
        ("Wed", "Root Beer Float — FFRT + light HAF", "", 8.0, "quality"),
        ("Thu", "REST", "", None, "rest"),
        ("Fri", "45min easy", "", 4.3, "easy"),
        ("Sat", "11mi easy", "", 11.0, "long"),
        ("Sun", "REST", "", None, "rest"),
    ],
    19: [
        ("Mon", "50min easy — course viz w/ FFRT", "", 4.8, "easy"),
        ("Tue", "35min easy", "", 3.3, "easy"),
        ("Wed", "Pre-race: 3@MP + 4x200@5K (6mi)", "", 6.0, "quality"),
        ("Thu", "REST or cross train", "", None, "rest"),
        ("Fri", "REST", "", None, "rest"),
        ("Sat", "20-30min shakeout", "", 2.4, "easy"),
        ("Sun", "🏆 CHICAGO MARATHON · RELENTLESS · Sub-3:55", "", None, "race"),
    ],
}

# ── Helpers ─────────────────────────────────────────────────────────────────

def pace_to_seconds(pace_str):
    """'9:05' → 545"""
    if not pace_str or ':' not in pace_str:
        return None
    parts = pace_str.split(':')
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def seconds_to_pace(s):
    """545 → '9:05'"""
    if not s or s <= 0:
        return "—"
    return f"{int(s)//60}:{int(s)%60:02d}"


def current_training_week():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if today < BLOCK_START:
        return 0
    days_in = (today - BLOCK_START).days
    return min(days_in // 7 + 1, 19)


def week_date_range(week_num):
    start = BLOCK_START + timedelta(weeks=week_num - 1)
    end = start + timedelta(days=6)
    return start, end


def load_activities():
    if not os.path.exists(ACTIVITIES_FILE):
        return []
    with open(ACTIVITIES_FILE, newline="") as f:
        return list(csv.DictReader(f))


def load_laps():
    if not os.path.exists(LAPS_FILE):
        return []
    with open(LAPS_FILE, newline="") as f:
        return list(csv.DictReader(f))


def deduplicate_activities(activities):
    """Remove duplicate rows by strava_id, keeping first occurrence."""
    seen = set()
    result = []
    for a in activities:
        rid = a.get("strava_id", "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        result.append(a)
    return result


def runs_in_range(activities, start, end):
    result = []
    for a in activities:
        if a.get("type") != "Run":
            continue
        try:
            d = datetime.strptime(a["date"][:10], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        if start <= d <= end:
            result.append(a)
    return sorted(result, key=lambda x: x["date"])


def laps_for_activity(laps, strava_id):
    return [l for l in laps if str(l.get("activity_strava_id", "")) == str(strava_id)]


def classify_run(run):
    """Guess the effort type from name and pace."""
    name = run.get("name", "").lower()
    dist = float(run.get("distance_miles", 0) or 0)
    pace_s = pace_to_seconds(run.get("pace_per_mile", ""))

    race_kw = ["race", "marathon", "half", "10k", "5k", "shuffle", "soldier", "hidden gem"]
    if any(k in name for k in race_kw + WORKOUT_KEYWORDS):
        return "quality"
    if dist >= 12:
        return "long"
    if pace_s and pace_s < pace_to_seconds("10:00"):
        return "quality"
    return "easy"


def pace_zone_label(pace_s):
    if not pace_s:
        return ("—", "muted")
    if pace_s <= pace_to_seconds("8:05"):
        return ("10K or faster", "blue")
    if pace_s <= pace_to_seconds("8:25"):
        return ("HMP zone", "blue")
    if pace_s <= pace_to_seconds("9:10"):
        return ("MP zone", "yellow")
    if pace_s <= pace_to_seconds("10:15"):
        return ("Easy zone", "green")
    return ("Recovery zone", "muted")


def hr_comment(hr):
    try:
        hr = float(hr)
    except (TypeError, ValueError):
        return ""
    if hr < 130:
        return "Very low HR — pure recovery"
    if hr < 140:
        return "Easy aerobic"
    if hr < 150:
        return "Solid aerobic"
    if hr < 158:
        return "Tempo / MP zone"
    if hr < 165:
        return "Hard effort"
    return "Near max — race effort"


def parse_time_from_name(name):
    """Extract race time like '1:52:21' from activity name."""
    m = re.search(r'(\d+):(\d{2}):(\d{2})', name)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def format_duration(seconds):
    """Format seconds as H:MM:SS."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def predict_marathon_from_half(half_seconds):
    """Riegel formula: t2 = t1 * (d2/d1)^1.06"""
    return half_seconds * (26.2 / 13.1) ** 1.06


def is_race_activity(run):
    """True if name contains a clock time (e.g. '1:52:21') or race keywords."""
    name = run.get("name", "")
    if re.search(r'\d+:\d{2}:\d{2}', name):
        return True
    nl = name.lower()
    return any(k in nl for k in ["race", "shuffle", "soldier", "hidden gem"])


def is_workout_activity(run):
    nl = run.get("name", "").lower()
    return any(k in nl for k in WORKOUT_KEYWORDS)


# ── Weekly mileage history for the block ────────────────────────────────────

def weekly_miles_in_block(activities):
    result = {}
    week = current_training_week()
    for w in range(1, week + 1):
        start, end = week_date_range(w)
        runs = runs_in_range(activities, start, end)
        miles = sum(float(r.get("distance_miles", 0) or 0) for r in runs)
        result[w] = miles
    return result


# ── HTML generators ─────────────────────────────────────────────────────────

COLORS = {
    "green":  ("var(--accent3)", "rgba(78,203,136,.15)"),
    "blue":   ("var(--accent2)", "rgba(91,142,240,.15)"),
    "yellow": ("var(--accent4)", "rgba(240,192,64,.15)"),
    "orange": ("var(--accent)",  "rgba(224,92,46,.15)"),
    "muted":  ("var(--muted)",   "rgba(122,122,149,.12)"),
}


def badge(text, color="muted"):
    fg, bg = COLORS.get(color, COLORS["muted"])
    return f'<span style="display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;background:{bg};color:{fg};">{text}</span>'


def stat_tile(label, value, sub="", color="green"):
    fg, _ = COLORS.get(color, COLORS["green"])
    return f'''<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;">
  <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:4px;">{label}</div>
  <div style="font-size:26px;font-weight:700;letter-spacing:-.02em;color:{fg};">{value}</div>
  {"<div style='font-size:12px;color:var(--muted);margin-top:2px;'>" + sub + "</div>" if sub else ""}
</div>'''


def lap_split_chart_html(laps):
    """Horizontal bar chart of lap paces, faster = longer bar."""
    meaningful = [l for l in laps if float(l.get("lap_distance_miles", 0) or 0) >= 0.5]
    if not meaningful:
        return ""
    paces_s = [pace_to_seconds(l.get("lap_pace_per_mile", "")) for l in meaningful]
    valid = [p for p in paces_s if p and 360 < p < 720]
    if not valid:
        return ""
    slowest = max(valid)
    fastest = min(valid)
    pace_range = max(slowest - fastest, 20)

    rows = ""
    for i, l in enumerate(meaningful):
        p = pace_to_seconds(l.get("lap_pace_per_mile", ""))
        if not p:
            continue
        try:
            hr = float(l.get("lap_avg_heartrate", "") or 0)
            hr_str = f"HR {hr:.0f}" if hr else ""
        except (TypeError, ValueError):
            hr_str = ""
        bar_pct = max(8, min(100, (slowest - p) / pace_range * 88 + 12))
        _, zcol = pace_zone_label(p)
        fg, _ = COLORS.get(zcol, COLORS["muted"])
        rows += (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px;">'
            f'<span style="width:18px;color:var(--muted);text-align:right;flex-shrink:0;">{i+1}</span>'
            f'<div style="flex:1;background:var(--surface2);border-radius:3px;height:7px;">'
            f'<div style="width:{bar_pct:.0f}%;height:100%;background:{fg};border-radius:3px;opacity:.85;"></div>'
            f'</div>'
            f'<span style="width:40px;font-weight:600;color:{fg};font-variant-numeric:tabular-nums;">{seconds_to_pace(p)}</span>'
            f'<span style="width:48px;font-size:11px;color:var(--muted);">{hr_str}</span>'
            f'</div>'
        )
    return f'<div style="margin-top:10px;">{rows}</div>'


def race_deep_dive_html(run, run_laps):
    """Detailed race breakdown with lap chart and marathon prediction."""
    name = run.get("name", "")
    dist = float(run.get("distance_miles", 0) or 0)
    pace = run.get("pace_per_mile", "—")
    hr = run.get("average_heartrate", "—")

    official_s = parse_time_from_name(name)
    official_str = format_duration(official_s) if official_s else "—"

    # Pacing narrative
    meaningful = [l for l in run_laps if float(l.get("lap_distance_miles", 0) or 0) >= 0.5]
    pacing_note = ""
    if len(meaningful) >= 4:
        half = len(meaningful) // 2
        fh = [pace_to_seconds(l.get("lap_pace_per_mile","")) for l in meaningful[:half]]
        sh = [pace_to_seconds(l.get("lap_pace_per_mile","")) for l in meaningful[half:]]
        fh_v = [p for p in fh if p]
        sh_v = [p for p in sh if p]
        if fh_v and sh_v:
            fh_avg = sum(fh_v) / len(fh_v)
            sh_avg = sum(sh_v) / len(sh_v)
            diff = fh_avg - sh_avg
            fh_str = seconds_to_pace(int(fh_avg))
            sh_str = seconds_to_pace(int(sh_avg))
            if diff > 20:
                pacing_note = (f"Strong negative split — first half avg {fh_str}/mi, second half "
                               f"{sh_str}/mi. Great pacing discipline.")
            elif diff > 5:
                pacing_note = f"Slight negative split ({fh_str} → {sh_str}/mi). Solid controlled effort."
            elif diff > -5:
                pacing_note = "Even splits from start to finish."
            else:
                pacing_note = (f"Positive split — faded from {fh_str} to {sh_str}/mi. "
                               f"Common in hot or hilly conditions.")

    # Marathon prediction (half marathon only)
    pred_html = ""
    if 12 <= dist <= 14.5 and official_s:
        pred_s = predict_marathon_from_half(official_s)
        pred_time = format_duration(int(pred_s))
        pred_pace_str = seconds_to_pace(int(pred_s / 26.2))
        target_s = pace_to_seconds("8:58") * 26.2  # sub-3:55
        gap = pred_s - target_s
        if gap < -120:
            pred_color = "var(--accent3)"
            verdict = (f"Predicts <strong style=\"color:var(--accent3);\">{pred_time}</strong> "
                       f"({pred_pace_str}/mi) — comfortably under your sub-3:55 target. "
                       f"Strong fitness baseline for Week 1.")
        elif gap < 90:
            pred_color = "var(--accent3)"
            verdict = (f"Predicts <strong style=\"color:var(--accent3);\">{pred_time}</strong> "
                       f"({pred_pace_str}/mi) — right at your sub-3:55 target. "
                       f"With 18 weeks of structured training ahead, you're in great position.")
        elif gap < 360:
            pred_color = "var(--accent4)"
            verdict = (f"Predicts <strong style=\"color:var(--accent4);\">{pred_time}</strong> "
                       f"({pred_pace_str}/mi) — a few minutes off goal. Very closeable over 18 weeks.")
        else:
            pred_color = "var(--accent)"
            verdict = (f"Predicts <strong style=\"color:var(--accent);\">{pred_time}</strong> "
                       f"({pred_pace_str}/mi) — some work to do, but 18 weeks is a long runway.")
        pred_html = (
            '<div style="margin-top:12px;padding:12px;background:var(--surface2);border-radius:8px;">'
            '<div style="font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;'
            'color:var(--muted);margin-bottom:6px;">Marathon Prediction (Riegel Formula)</div>'
            f'<div style="font-size:13px;color:var(--muted);line-height:1.6;">{verdict}</div>'
            '<div style="font-size:11px;color:var(--muted);margin-top:5px;opacity:.6;">'
            'Directional signal — heat, course, and race-day execution all affect the real number.</div>'
            '</div>'
        )

    pacing_div = (f'<div style="font-size:13px;color:var(--muted);margin-bottom:10px;line-height:1.6;">'
                  f'{pacing_note}</div>') if pacing_note else ""
    chart = lap_split_chart_html(run_laps)

    return (
        '<div style="background:var(--surface);border:1px solid var(--border);'
        'border-radius:12px;padding:20px;margin-bottom:16px;">'
        '<div style="font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;'
        'color:var(--accent);margin-bottom:6px;">Race Breakdown</div>'
        f'<div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:12px;">{name}</div>'
        '<div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px;">'
        f'<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:2px;">Time</div>'
        f'<div style="font-size:22px;font-weight:700;color:var(--text);">{official_str}</div></div>'
        f'<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:2px;">Avg Pace</div>'
        f'<div style="font-size:22px;font-weight:700;color:var(--accent2);">{pace}/mi</div></div>'
        f'<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:2px;">Avg HR</div>'
        f'<div style="font-size:22px;font-weight:700;color:var(--text);">{hr}</div></div>'
        f'<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:2px;">Distance</div>'
        f'<div style="font-size:22px;font-weight:700;color:var(--text);">{dist:.2f}mi</div></div>'
        '</div>'
        f'{pacing_div}'
        f'{chart}'
        f'{pred_html}'
        '</div>'
    )


def workout_interval_chart_html(laps):
    """Interval-aware lap chart for workouts — shows ALL laps, labels hard/easy/WU/CD."""
    laps = [l for l in laps if float(l.get("lap_distance_miles", 0) or 0) >= 0.05]
    if not laps:
        return ""

    paces_s = [pace_to_seconds(l.get("lap_pace_per_mile", "")) for l in laps]
    valid = [p for p in paces_s if p and 300 < p < 900]
    if not valid:
        return ""

    slowest = max(valid)
    fastest = min(valid)
    pace_range = max(slowest - fastest, 30)

    # Classify each lap
    def lap_type(i, p):
        if p is None:
            return "muted", "—"
        hard_thresh = pace_to_seconds("9:20")
        if i == 0 and p > hard_thresh:
            return "muted", "WU"
        if i == len(laps) - 1 and p > hard_thresh:
            return "muted", "CD"
        if p <= hard_thresh:
            return "blue", "Hard"
        return "green", "Easy"

    rows = ""
    hard_num = 0
    for i, l in enumerate(laps):
        p = pace_to_seconds(l.get("lap_pace_per_mile", ""))
        dist = float(l.get("lap_distance_miles", 0) or 0)
        try:
            hr = float(l.get("lap_avg_heartrate", "") or 0)
            hr_str = f"HR {hr:.0f}" if hr else ""
        except (TypeError, ValueError):
            hr_str = ""

        col, label = lap_type(i, p)
        if label == "Hard":
            hard_num += 1
            label = f"Hard {hard_num}"
        fg, _ = COLORS.get(col, COLORS["muted"])

        bar_pct = max(8, min(100, (slowest - p) / pace_range * 88 + 12)) if p else 8
        pace_str = seconds_to_pace(p) if p else "—"

        rows += (
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;font-size:12px;">'
            f'<span style="width:46px;color:{fg};font-size:10px;font-weight:600;text-align:right;'
            f'flex-shrink:0;">{label}</span>'
            f'<div style="flex:1;background:var(--surface2);border-radius:3px;height:7px;">'
            f'<div style="width:{bar_pct:.0f}%;height:100%;background:{fg};border-radius:3px;opacity:.85;"></div>'
            f'</div>'
            f'<span style="width:40px;font-weight:600;color:{fg};font-variant-numeric:tabular-nums;">{pace_str}</span>'
            f'<span style="width:36px;font-size:11px;color:var(--muted);">{dist:.2f}mi</span>'
            f'<span style="width:46px;font-size:11px;color:var(--muted);">{hr_str}</span>'
            f'</div>'
        )
    return f'<div style="margin-top:10px;">{rows}</div>'


def workout_deep_dive_html(run, run_laps):
    """Structured workout breakdown with interval lap chart."""
    name = run.get("name", "")
    dist = float(run.get("distance_miles", 0) or 0)
    pace = run.get("pace_per_mile", "—")
    hr = run.get("average_heartrate", "—")

    all_laps = [l for l in run_laps if float(l.get("lap_distance_miles", 0) or 0) >= 0.05]
    hard_paces = []
    for l in all_laps:
        p = pace_to_seconds(l.get("lap_pace_per_mile", ""))
        if p and p <= pace_to_seconds("9:20"):
            hard_paces.append(p)

    effort_note = ""
    if hard_paces:
        avg_hard = sum(hard_paces) / len(hard_paces)
        fastest_hard = min(hard_paces)
        effort_note = (f"{len(hard_paces)} hard effort{'s' if len(hard_paces)>1 else ''} — "
                       f"avg {seconds_to_pace(int(avg_hard))}/mi, "
                       f"fastest {seconds_to_pace(fastest_hard)}/mi.")
    elif not all_laps:
        effort_note = "No lap data recorded — overall stats only."

    chart = workout_interval_chart_html(run_laps)
    effort_div = (f'<div style="font-size:13px;color:var(--muted);margin-bottom:10px;line-height:1.6;">'
                  f'{effort_note}</div>') if effort_note else ""

    return (
        '<div style="background:var(--surface);border:1px solid var(--border);'
        'border-radius:12px;padding:20px;margin-bottom:16px;">'
        '<div style="font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;'
        'color:var(--accent2);margin-bottom:6px;">Workout Breakdown</div>'
        f'<div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:12px;">{name}</div>'
        '<div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px;">'
        f'<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:2px;">Distance</div>'
        f'<div style="font-size:22px;font-weight:700;color:var(--text);">{dist:.1f}mi</div></div>'
        f'<div><div style="font-size:10px;color:var(--muted);text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:2px;">Avg HR</div>'
        f'<div style="font-size:22px;font-weight:700;color:var(--text);">{hr}</div></div>'
        '</div>'
        f'{effort_div}'
        f'{chart}'
        '</div>'
    )


def highlight_box(title, body, color="orange"):
    fg, bg = COLORS.get(color, COLORS["orange"])
    return f'''<div style="background:{bg};border:1px solid {fg.replace('var','').replace('(','').replace(')','') if 'var' not in fg else fg}20;border:1px solid {fg};border-radius:10px;padding:16px 20px;margin-bottom:14px;">
  <div style="font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:{fg};margin-bottom:6px;">{title}</div>
  <div style="font-size:13px;color:var(--muted);line-height:1.7;">{body}</div>
</div>'''


def run_row_html(run, laps):
    date = run.get("date", "")[:10]
    name = run.get("name", "Unknown")
    dist = float(run.get("distance_miles", 0) or 0)
    pace = run.get("pace_per_mile", "—")
    hr = run.get("average_heartrate", "—")
    pace_s = pace_to_seconds(pace)
    zone, zcol = pace_zone_label(pace_s)
    hr_note = hr_comment(hr)
    fg, _ = COLORS.get(zcol, COLORS["muted"])

    lap_html = ""
    if laps:
        # Show meaningful laps (filter tiny transition laps < 0.05mi)
        meaningful = [l for l in laps if float(l.get("lap_distance_miles", 0) or 0) >= 0.1]
        if len(meaningful) > 1:
            lap_rows = ""
            for l in meaningful[:12]:
                lp = l.get("lap_pace_per_mile", "—")
                ld = float(l.get("lap_distance_miles", 0) or 0)
                lhr = l.get("lap_avg_heartrate", "")
                try:
                    lhr = float(lhr)
                    lhr_str = f" · HR {lhr:.0f}" if lhr else ""
                except (TypeError, ValueError):
                    lhr_str = ""
                lname = l.get("lap_name", "")
                lap_rows += f'<div style="padding:3px 0;font-size:12px;color:var(--muted);">Lap {l.get("lap_number","")}: <strong style="color:var(--text)">{ld:.2f}mi @ {lp}</strong>{lhr_str} {lname}</div>'
            lap_html = f'<div style="margin-top:8px;padding:8px 12px;background:var(--surface2);border-radius:6px;">{lap_rows}</div>'

    return f'''<div style="padding:12px 0;border-bottom:1px solid rgba(46,46,61,.5);">
  <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;">
    <span style="font-size:11px;color:var(--muted);width:58px;flex-shrink:0;">{date}</span>
    <span style="flex:1;font-size:13px;">{name}</span>
    <span style="font-size:13px;color:var(--muted);">{dist:.1f}mi</span>
    <span style="font-size:13px;font-weight:600;color:{fg};font-variant-numeric:tabular-nums;">{pace}/mi</span>
    <span style="font-size:12px;color:var(--muted);">HR {hr}</span>
    {badge(zone, zcol)}
  </div>
  {lap_html}
</div>'''


# Workout-specific pace guide keyed by substring match on key_session / activity name
WORKOUT_DETAILS = {
    "20x400": {
        "headline": "20 × 400m Repeats",
        "what": "20 quarter-mile repeats at a hard, controlled effort with short jog recoveries.",
        "paces": "Rep pace: <strong>7:15/mi → each 400 in ~1:49</strong>. Jog 200m (~90 sec) between each. Easy warmup + cooldown miles around the set.",
        "why": "Volume repetition work builds raw speed economy — you're teaching your legs to run fast efficiently before the block layers in marathon-specific fatigue. This is the speed deposit that pays dividends in miles 20–26.",
    },
    "funnel": {
        "headline": "Funnel of Fun",
        "what": "Descending-interval fartlek: longer hard efforts at the start, shorter and faster toward the end.",
        "paces": "Hard segments: threshold <strong>8:18/mi</strong> on the long efforts, pushing toward interval <strong>7:39/mi</strong> on the short ones. Float: easy 9:57–10:56.",
        "why": "Works multiple energy systems in one session. The long-to-short structure builds threshold capacity early and sharpens turnover at the end — great for aerobic ceiling development.",
    },
    "yasso": {
        "headline": "Yasso 800s",
        "what": "800m repeats where the goal time in min:sec matches your marathon goal in hrs:min.",
        "paces": "Target: <strong>3:52 per 800m</strong> (matching 3:52 VDOT equiv). Jog 3:52 recovery between each. 8–10 reps for full benefit.",
        "why": "Classic marathon fitness benchmark. Completing 10 at goal pace is one of the strongest predictors of marathon readiness. Each rep builds VO2max and trains your body at race-equivalent effort.",
    },
    "michigan": {
        "headline": "Michigan",
        "what": "Alternating marathon pace and half marathon pace miles sustained for several miles.",
        "paces": "MP miles: <strong>8:52/mi</strong>. HMP miles: <strong>8:34/mi</strong>. No full recovery between — just shift gears. Easy warmup + cooldown.",
        "why": "The hardest single workout in the block. Forces your body to hold marathon pace while fatigued and change gears — exactly what miles 20–26 demand when you need to respond to surges or hold form.",
    },
    "lumberjack": {
        "headline": "Lumberjack",
        "what": "Progressive fartlek — alternating MP and easy efforts building across the run.",
        "paces": "MP surges: <strong>8:52/mi</strong>. Easy float: 10:00–10:30. Build the MP segments over time.",
        "why": "Teaches your body to run marathon pace on tired legs — the core adaptation that separates finishing from racing the back half of a marathon.",
    },
    "ladder": {
        "headline": "Ladder Workout",
        "what": "Intervals ascending and/or descending in distance — building to a peak then coming back down.",
        "paces": "Long efforts: threshold <strong>8:18/mi</strong>. Short efforts: interval <strong>7:39/mi</strong>. Easy float between each.",
        "why": "Develops aerobic range across multiple energy systems. You're training from threshold (long reps) down to VO2max (short reps) in one session — builds the engine capacity that supports marathon pace.",
    },
    "matrix": {
        "headline": "3×3 Matrix",
        "what": "3 sets of 3 miles, each mile in the set at a different pace: easy → MP → HMP.",
        "paces": "Easy miles: 9:57–10:56. MP miles: <strong>8:52/mi</strong>. HMP miles: <strong>8:34/mi</strong>. No recovery between sets.",
        "why": "Builds marathon-specific neuromuscular patterning — your legs learn to shift between paces while maintaining form under fatigue. Great precursor to race execution.",
    },
    "half time": {
        "headline": "Half Time Fartlek",
        "what": "Alternating hard/easy efforts in a structured fartlek.",
        "paces": "Hard: interval pace <strong>7:39/mi</strong>. Float: easy 9:57–10:56.",
        "why": "Sharpens top-end speed and recovery between efforts. The fartlek structure keeps it feel-based — good for early block when the goal is stimulus, not precision.",
    },
    "progression": {
        "headline": "Progression Run",
        "what": "Starts easy and systematically builds to a hard finish — finish the last miles at MP or HMP.",
        "paces": "First half: easy 10:00+. Middle: 9:57–10:30. Finish miles: MP <strong>8:52</strong> or HMP <strong>8:34/mi</strong>.",
        "why": "Directly simulates marathon race execution — going out controlled and building. The physiological load at the end (fast on tired legs) is one of the most specific marathon adaptations you can train.",
    },
    "tempo": {
        "headline": "Tempo Run",
        "what": "Sustained effort at threshold pace — comfortably hard, not a sprint.",
        "paces": "Threshold: <strong>8:18/mi</strong>. Warm up 1–2mi easy, cool down 1mi easy. Hold the pace for 20–40 min.",
        "why": "Raises your lactate threshold — the fastest pace you can sustain aerobically. Every improvement here directly raises your marathon pace ceiling.",
    },
    "threshold": {
        "headline": "Threshold Workout",
        "what": "Threshold intervals or sustained run at comfortably hard effort.",
        "paces": "Threshold: <strong>8:18/mi</strong>. Recovery between reps at easy 9:57–10:56.",
        "why": "Same adaptation as tempo — lactate threshold gains translate directly to marathon pace ceiling. The intervals allow slightly more volume at the target intensity.",
    },
    "relay": {
        "headline": "5K Partner Relay",
        "what": "Alternating 5K-pace efforts — your rest is your partner's work.",
        "paces": "5K effort: <strong>7:52/mi</strong>. Recovery during partner's turn (full rest = better quality).",
        "why": "Develops VO2max with built-in recovery, making hard efforts sustainable over a long session. More total volume at fast pace than a standard interval workout.",
    },
    "hidden gem": {
        "headline": "Hidden Gem Half",
        "what": "Tune-up half marathon race in the peak block.",
        "paces": "Race it: target sub-1:50 on fresh legs, or treat as a hard long workout if fatigued. Your HMP is <strong>8:34/mi</strong>.",
        "why": "Mid-block fitness check that sharpens race-day execution. Gives a data point on where fitness is heading into taper — and usually delivers a confidence boost.",
    },
    "2min": {
        "headline": "2-Minute On/Off Fartlek",
        "what": "Alternating 2-minute hard / 2-minute easy efforts — recovery week version.",
        "paces": "Hard: somewhere between threshold <strong>8:18</strong> and interval <strong>7:39/mi</strong> — go by feel. Easy: 10:00+.",
        "why": "Keeps the neuromuscular system stimulated during a recovery week without the cumulative fatigue of a full FFRT session. Maintains sharpness while the body absorbs the previous block.",
    },
    "1k sandwich": {
        "headline": "1K Sandwich",
        "what": "1K repeats sandwiched between MP miles — combines marathon pace volume with fast intervals.",
        "paces": "MP miles: <strong>8:52/mi</strong>. 1K reps: interval pace → <strong>7:39/mi</strong> (≈ 4:45 per km).",
        "why": "Peak-block workout that trains speed while aerobically loaded. The MP miles before and after the 1Ks simulate late-race conditions when you need to surge or hold form.",
    },
    "root beer float": {
        "headline": "Root Beer Float",
        "what": "Taper workout: alternating easy float miles with short, snappy pickups.",
        "paces": "Float miles: easy 9:57+. Pickups: mile pace or faster, <strong>7:15/mi</strong> for 20–30 sec. Stay relaxed.",
        "why": "Keeps your legs sharp and neuromuscular system primed during taper without adding fatigue. The goal is feel — loose, fast, in control.",
    },
}


def _find_workout_details(key_session_str):
    """Match a key session string to a WORKOUT_DETAILS entry."""
    kl = key_session_str.lower()
    for keyword, details in WORKOUT_DETAILS.items():
        if keyword in kl:
            return details
    return None


def _workout_sentence(run, laps, lead="", capitalize=False):
    """One sentence describing a quality session with pace-vs-target analysis."""
    name = run.get("name", "")
    dist = float(run.get("distance_miles", 0) or 0)
    pace = run.get("pace_per_mile", "—")
    hr = run.get("average_heartrate", "")
    try:
        hr_str = f", HR {int(float(hr))}" if hr else ""
    except (TypeError, ValueError):
        hr_str = ""
    rl = laps_for_activity(laps, run.get("strava_id", ""))
    paces = [pace_to_seconds(l.get("lap_pace_per_mile", "")) for l in rl]
    paces = [p for p in paces if p]
    s = f"{lead}{name}, covered {dist:.1f}mi at {pace}/mi avg{hr_str}."
    if paces:
        quality = [p for p in paces if p < pace_to_seconds("8:52")]   # at/under MP
        if quality:
            sub_hmp = sum(1 for p in quality if p < pace_to_seconds("8:34"))
            fastest_str = seconds_to_pace(min(paces))
            rep = "rep" if len(quality) == 1 else "reps"
            if sub_hmp:
                s += (f" {len(quality)} {rep} at marathon pace or faster, {sub_hmp} of them under HMP "
                      f"(8:34), topping out at {fastest_str}/mi — the speed is firing.")
            else:
                s += f" {len(quality)} {rep} at marathon pace or quicker, fastest {fastest_str}/mi."
    return s


def _long_run_pace_note(pace, hr):
    """Short note comparing long-run pace to the easy zone + HR read."""
    ps = pace_to_seconds(pace)
    parts = []
    try:
        if hr:
            parts.append(f"HR {int(float(hr))}")
    except (TypeError, ValueError):
        pass
    if ps:
        if ps < pace_to_seconds("9:57"):
            parts.append("a touch quicker than easy")
        elif ps <= pace_to_seconds("10:56"):
            parts.append("right in the easy zone")
        else:
            parts.append("controlled and aerobic")
    return ", ".join(parts) if parts else "—"


def _mileage_takeaway(actual, target, finished):
    """One sentence on mileage vs target."""
    try:
        tgt = float(target)
    except (TypeError, ValueError):
        tgt = 0
    pct = (actual / tgt * 100) if tgt else 0
    if tgt and actual >= tgt * 0.98:
        return f"Mileage came in at {actual:.1f} against a {target}mi target — right on plan."
    if not finished:
        return f"{actual:.1f}mi in so far against a {target}mi target — week's still going."
    if pct >= 90:
        return f"{actual:.1f}mi against a {target}mi target — close enough; absorb it and move on."
    return f"{actual:.1f}mi against a {target}mi target — ran a little light, bank it and stay consistent."


def generate_exec_summary_html(week_num, phase, week_runs, laps, race_run, workout_run, long_run, actual_miles, target_miles):
    """Generate a headline + narrative executive summary for the week."""
    week_finished = datetime.now() > (BLOCK_START + timedelta(weeks=week_num))

    # ── Headline + narrative logic ───────────────────────────────────────────
    if race_run:
        race_name = race_run.get("name", "")
        race_pace = race_run.get("pace_per_mile", "—")
        race_hr   = race_run.get("average_heartrate", "—")
        race_dist = float(race_run.get("distance_miles", 0) or 0)
        official_s = parse_time_from_name(race_name)
        time_str = format_duration(official_s) if official_s else race_pace + "/mi"

        headline = f"Fitness Check Complete: Sub-3:55 Is Within Range"
        if official_s and official_s < pace_to_seconds("8:34") * 13.1:
            headline = "Race Fitness Confirmed: The Block Starts Strong"

        workout_note = ""
        if workout_run and workout_run.get("strava_id") != race_run.get("strava_id"):
            w_pace = workout_run.get("pace_per_mile", "—")
            w_laps = laps_for_activity(laps, workout_run.get("strava_id", ""))
            fast = [l for l in w_laps if pace_to_seconds(l.get("lap_pace_per_mile", "")) and
                    pace_to_seconds(l.get("lap_pace_per_mile", "")) < pace_to_seconds("8:30")]
            fastest_lap = min(fast, key=lambda l: pace_to_seconds(l.get("lap_pace_per_mile", "")), default=None)
            fastest_str = f" Fastest interval: {fastest_lap.get('lap_pace_per_mile','')}/mi." if fastest_lap else ""
            workout_note = (f" Tuesday's workout at {w_pace}/mi avg showed the speed is there.{fastest_str}")

        narrative = (
            f"Week 1 delivered exactly what it was designed to do: a fitness snapshot before the build begins. "
            f"{workout_note} "
            f"The Chicago 13.1 on Saturday — {time_str} at {race_pace}/mi, HR {race_hr} in warm June conditions — "
            f"translates to a VDOT marathon equivalent of 3:52:35. That's already 2+ minutes faster than your 3:55 goal. "
            f"The fitness gap isn't speed or aerobic capacity. It's muscular endurance in miles 17–26. "
            f"Everything in this block is designed to close that gap."
        )

    elif long_run and float(long_run.get("distance_miles", 0) or 0) >= 14:
        lr_dist = float(long_run.get("distance_miles", 0) or 0)
        lr_pace = long_run.get("pace_per_mile", "—")
        lr_hr   = long_run.get("average_heartrate", "—")
        headline = f"{lr_dist:.0f}-Miler Done: Muscular Endurance Building"
        narrative = (
            f"The long run is the backbone of marathon training, and this week's {lr_dist:.1f}-miler at {lr_pace}/mi "
            f"({_long_run_pace_note(lr_pace, lr_hr)}) is exactly the stimulus that builds the fatigue resistance you need for miles 17–26. "
        )
        if workout_run:
            narrative += _workout_sentence(workout_run, laps, lead="Earlier in the week, ")
            narrative += " Two different stressors, one week, one adaptation."
        else:
            narrative += "Keep showing up to the FFRT midweek — the combination of speed work and long mileage is where the gains come from."

    elif workout_run:
        w_dist = float(workout_run.get("distance_miles", 0) or 0)

        headline = f"Workout + Long Run: Both Boxes Checked" if (long_run and float(long_run.get("distance_miles",0) or 0) >= 10) else "Workout Week: Quality Over Quantity"
        if phase == "Peak":
            headline = "Peak Block: Hard Week in the Books"
        elif phase == "Recovery":
            headline = "Recovery Week: Sharp but Fresh"

        # Sentence 1: the workout, with pace-vs-target analysis
        narrative = _workout_sentence(workout_run, laps, lead="The key session, ", capitalize=True) + " "
        # Sentence 2: the long run
        if long_run and float(long_run.get("distance_miles", 0) or 0) >= 10:
            lr_dist = float(long_run.get("distance_miles", 0) or 0)
            lr_pace = long_run.get("pace_per_mile", "—")
            lr_hr   = long_run.get("average_heartrate", "—")
            narrative += (
                f"The {lr_dist:.1f}mi long run at {lr_pace}/mi ({_long_run_pace_note(lr_pace, lr_hr)}) covered the aerobic side — "
                f"time on feet building the miles-17–26 endurance that's the whole point of this block. "
            )
        elif not long_run:
            narrative += "Long run still to come this weekend — that's where the week's volume comes together. "
        # Sentence 3: takeaway vs target
        narrative += _mileage_takeaway(actual_miles, target_miles, week_finished)

    else:
        # Generic — easy/recovery week, possibly with a long run but no quality session
        headline = f"Week {week_num}: {phase} Phase Underway"
        if long_run and float(long_run.get("distance_miles", 0) or 0) >= 10:
            lr_dist = float(long_run.get("distance_miles", 0) or 0)
            lr_pace = long_run.get("pace_per_mile", "—")
            lr_hr   = long_run.get("average_heartrate", "—")
            headline = f"Easy Week Anchored by a {lr_dist:.0f}-Miler"
            narrative = (
                f"No hard session this week by design — the work was aerobic. The {lr_dist:.1f}mi long run at {lr_pace}/mi "
                f"({_long_run_pace_note(lr_pace, lr_hr)}) was the centerpiece, with easy mileage filling in around it. "
                f"{_mileage_takeaway(actual_miles, target_miles, week_finished)}"
            )
        else:
            narrative = (
                f"{_mileage_takeaway(actual_miles, target_miles, week_finished)} "
                f"Easy weeks build the aerobic base that quality sessions run on. Don't skip the easy stuff."
            )

    return f'''
<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px;">
  <div style="font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;">Executive Summary</div>
  <div style="font-size:18px;font-weight:700;color:var(--text);margin-bottom:10px;">{headline}</div>
  <p style="font-size:13px;color:var(--muted);line-height:1.6;margin:0;">{narrative.strip()}</p>
</div>'''


def generate_whats_next_html(week_num, today):
    """Preview the upcoming session with paces, context-aware by day of week."""
    day = today.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    is_midweek = day == 3  # Thursday (post-FFRT, pre-long-run)
    is_weekend  = day >= 4  # Fri/Sat/Sun — post-long-run or race day

    plan = WEEKLY_PLAN.get(week_num, {})
    next_plan = WEEKLY_PLAN.get(week_num + 1, {})

    if is_midweek:
        # Preview the long run coming this weekend
        title = "Up Next: Long Run This Weekend"
        lr_miles = plan.get("miles", "?")  # rough guide only
        # Try to pull long run target from key session
        key = plan.get("key", "")
        # Extract miles from key if present (e.g. "15mi w/ MP/float")
        import re as _re
        lr_match = _re.search(r'(\d+)mi', key)
        lr_target = lr_match.group(0) if lr_match else "long"
        narrative = (
            f"Quality session is done — now the long run. This week's long run target is <strong>{lr_target}</strong>. "
            f"Run it easy: <strong>9:57 – 10:56/mi</strong>, HR in the 130s–low 140s. "
            f"The goal is time on feet and aerobic adaptation, not pace. "
            f"If the plan calls for MP miles at the end, lock into <strong>8:52/mi</strong> for those only."
        )
        detail = _find_workout_details(key)

    else:
        # Weekend — preview next week
        if week_num >= 19:
            return ""
        next_week = week_num + 1
        title = f"Up Next: Week {next_week}"
        next_key = next_plan.get("key", "")
        next_miles = next_plan.get("miles", "?")
        next_phase = next_plan.get("phase", "")
        detail = _find_workout_details(next_key)
        schedule = WEEK_SCHEDULE.get(next_week)

        if schedule:
            # Show day-by-day table
            narrative = f"Week {next_week} ({next_miles}mi · {next_phase}): <strong>{next_key}</strong>"
            rows = "".join(
                f'<tr>'
                f'<td style="padding:6px 10px 6px 0;font-weight:600;color:var(--text);white-space:nowrap;">{entry[0]}</td>'
                f'<td style="padding:6px 10px;color:var(--muted);">{entry[1]}</td>'
                f'<td style="padding:6px 0;font-size:12px;color:var(--muted);opacity:.7;">{entry[2]}</td>'
                f'</tr>'
                for entry in schedule
            )
            pace_block = (
                f'<div style="background:var(--surface2);border-radius:8px;padding:12px;margin:12px 0;">'
                f'<table style="width:100%;border-collapse:collapse;font-size:13px;">{rows}</table>'
                f'</div>'
                f'<p style="font-size:13px;color:var(--muted);margin:0;">Easy pace all week: <strong>9:57 – 10:56/mi</strong>. Saturday long run is the priority — arrive fresh.</p>'
            )
        elif detail:
            narrative = (
                f"Next week ({next_miles}mi · {next_phase}): <strong>{detail['headline']}</strong>. "
                f"{detail['what']}"
            )
            pace_block = (
                f'<div style="background:var(--surface2);border-radius:8px;padding:12px;margin:12px 0;font-size:13px;">'
                f'<div style="font-weight:600;margin-bottom:4px;">Paces</div>'
                f'<div style="color:var(--muted);">{detail["paces"]}</div>'
                f'</div>'
                f'<p style="font-size:13px;color:var(--muted);margin:0;"><strong>Why it matters:</strong> {detail["why"]}</p>'
            )
        else:
            narrative = f"Next week: <strong>{next_key}</strong> — {next_miles}mi {next_phase} week."
            pace_block = f'<p style="font-size:13px;color:var(--muted);">Target easy pace: <strong>9:57 – 10:56/mi</strong>. MP work at <strong>8:52/mi</strong>.</p>'

    if is_midweek:
        pace_block = (
            f'<div style="background:var(--surface2);border-radius:8px;padding:12px;margin:12px 0;font-size:13px;">'
            f'<div style="font-weight:600;margin-bottom:4px;">Long Run Paces</div>'
            f'<div style="color:var(--muted);">Easy: <strong>9:57 – 10:56/mi</strong> · '
            f'MP surges (if in plan): <strong>8:52/mi</strong></div>'
            f'</div>'
        )

    return f'''
<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-top:16px;">
  <div style="font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;">{title}</div>
  <p style="font-size:13px;color:var(--muted);line-height:1.6;margin:0 0 4px;">{narrative}</p>
  {pace_block}
</div>'''


def generate_week_review_html(activities, laps):
    today = datetime.now()
    week_num = current_training_week()

    if week_num == 0:
        return '<div style="padding:40px;text-align:center;color:var(--muted);">Block hasn\'t started yet.</div>'

    start, end = week_date_range(week_num)
    plan = WEEKLY_PLAN.get(week_num, {})
    target_miles = plan.get("miles", "?")
    phase = plan.get("phase", "")
    key_session = plan.get("key", "")

    week_runs_raw = runs_in_range(activities, start, end)
    # Deduplicate by strava_id
    seen_ids = set()
    week_runs = []
    for r in week_runs_raw:
        rid = r.get("strava_id", "")
        if rid and rid in seen_ids:
            continue
        if rid:
            seen_ids.add(rid)
        week_runs.append(r)

    actual_miles = sum(float(r.get("distance_miles", 0) or 0) for r in week_runs)
    run_count = len(week_runs)

    # Also pull HAF (weight training) sessions this week
    wt_sessions = [a for a in activities if a.get("type") == "WeightTraining"
                   and start <= datetime.strptime(a["date"][:10], "%Y-%m-%d") <= end]

    # Miles tracking
    pct = (actual_miles / target_miles * 100) if target_miles else 0
    miles_color = "green" if pct >= 85 else ("yellow" if pct >= 60 else "orange")

    # Find race, workout, quality (for insights), long run
    race_run = None
    quality_run = None
    workout_run = None
    long_run = None
    for r in week_runs:
        d = float(r.get("distance_miles", 0) or 0)
        name_l = r.get("name", "").lower()
        if is_race_activity(r) and d >= 3:
            race_run = r
        if is_workout_activity(r):
            workout_run = r
        if any(k in name_l for k in WORKOUT_KEYWORDS):
            quality_run = r
        if d >= 10:
            long_run = r

    # Build run rows
    run_rows = ""
    for r in week_runs:
        r_laps = laps_for_activity(laps, r.get("strava_id", ""))
        run_rows += run_row_html(r, r_laps)

    if not run_rows:
        run_rows = '<div style="padding:20px;color:var(--muted);text-align:center;">No runs logged this week yet.</div>'

    # Insights
    insights = []

    # Miles insight — only flag as under target if the week is actually finished
    week_finished = today > end
    remaining_days = (end - today).days + 1

    if actual_miles >= target_miles:
        insights.append(("green", "Weekly target hit", f"{actual_miles:.1f}mi vs {target_miles}mi planned. On track."))
    elif week_finished:
        if pct >= 80:
            insights.append(("yellow", "Just under target", f"{actual_miles:.1f}mi of {target_miles}mi planned ({pct:.0f}%). Close enough — absorb and move on."))
        else:
            insights.append(("orange", "Under target", f"{actual_miles:.1f}mi vs {target_miles}mi planned. Short week — factor this into next week."))
    else:
        insights.append(("muted", f"{remaining_days} day{'s' if remaining_days != 1 else ''} left this week", f"{actual_miles:.1f}mi logged so far, {max(0, target_miles - actual_miles):.1f}mi remaining to hit {target_miles}mi target."))

    # Quality session insight
    if quality_run:
        q_pace = quality_run.get("pace_per_mile", "—")
        q_hr = quality_run.get("average_heartrate", "—")
        q_laps = laps_for_activity(laps, quality_run.get("strava_id", ""))
        fast_laps = [l for l in q_laps if pace_to_seconds(l.get("lap_pace_per_mile","")) and
                     pace_to_seconds(l.get("lap_pace_per_mile","")) < pace_to_seconds("9:00")
                     if l.get("lap_pace_per_mile","")]
        if fast_laps:
            fastest = min(fast_laps, key=lambda l: pace_to_seconds(l.get("lap_pace_per_mile","")))
            insights.append(("blue", "Quality session logged",
                f"FFRT workout done. Fastest lap: {fastest.get('lap_pace_per_mile','—')}/mi. Overall avg {q_pace}/mi at HR {q_hr}."))
        else:
            insights.append(("blue", "Quality session logged",
                f"FFRT workout in the books. Avg {q_pace}/mi at HR {q_hr}."))

    # Long run insight
    if long_run:
        lr_dist = float(long_run.get("distance_miles", 0) or 0)
        lr_pace = long_run.get("pace_per_mile", "—")
        lr_hr = long_run.get("average_heartrate", "—")
        insights.append(("orange", "Long run complete",
            f"{lr_dist:.1f}mi at {lr_pace}/mi, HR {lr_hr}. {'Strong effort.' if float(lr_hr or 0) < 148 else 'HR was elevated — check conditions and fueling.'}"))

    # HAF insight
    if wt_sessions:
        insights.append(("muted", f"Heavy AF: {len(wt_sessions)} session{'s' if len(wt_sessions)>1 else ''}",
            "Strength work logged alongside running. Keep monitoring fatigue going into hard sessions."))

    insight_html = "".join(highlight_box(t, b, c) for c, t, b in insights)

    # Deep dives — race first, then workout if it's a different activity
    deep_dive_html = ""
    if race_run:
        r_laps = laps_for_activity(laps, race_run.get("strava_id", ""))
        deep_dive_html += race_deep_dive_html(race_run, r_laps)
    if workout_run and workout_run.get("strava_id") != (race_run or {}).get("strava_id"):
        w_laps = laps_for_activity(laps, workout_run.get("strava_id", ""))
        deep_dive_html += workout_deep_dive_html(workout_run, w_laps)

    # Executive summary + What's Next
    exec_summary_html = generate_exec_summary_html(
        week_num, phase, week_runs, laps, race_run, workout_run, long_run, actual_miles, target_miles
    )
    whats_next_html = generate_whats_next_html(week_num, today)

    # Days to marathon
    days_to_race = (MARATHON_DATE - today).days

    updated = today.strftime("%A, %B %d at %-I:%M %p")

    return f'''
  <div style="margin-bottom:28px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">
    <h1>Week {week_num} in Review</h1>
    <span style="display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;background:rgba(122,122,149,.15);color:var(--muted);">{phase}</span>
    <span style="font-size:13px;color:var(--muted);">{start.strftime("%b %d")} – {end.strftime("%b %d")}</span>
    <span style="margin-left:auto;font-size:12px;color:var(--muted);">{days_to_race} days to Chicago</span>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px;">
    {stat_tile("Miles this week", f"{actual_miles:.1f}", f"Target: {target_miles}mi", miles_color)}
    {stat_tile("Runs", str(run_count), f"HAF: {len(wt_sessions)} sessions", "blue")}
    {stat_tile("Key session", "✓" if quality_run else "—", key_session[:40] + "…" if len(key_session) > 40 else key_session, "green" if quality_run else "muted")}
    {stat_tile("Long run", f"{float(long_run.get('distance_miles',0)):.1f}mi" if long_run else "—", long_run.get("pace_per_mile","") + "/mi" if long_run else "Not yet", "orange" if long_run else "muted")}
  </div>

  {exec_summary_html}

  {whats_next_html}

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px;">
    <div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;">
        <h3 style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:14px;">This week's runs</h3>
        {run_rows}
      </div>
    </div>
    <div>
      {deep_dive_html}
      <div style="margin-bottom:16px;">{insight_html}</div>
    </div>
  </div>

  <div style="margin-top:20px;font-size:11px;color:var(--muted);text-align:right;">Updated {updated}</div>
'''


def generate_block_summary_html(activities, laps):
    today = datetime.now()
    week_num = current_training_week()

    if week_num == 0:
        return '<div style="padding:40px;text-align:center;color:var(--muted);">Block hasn\'t started yet.</div>'

    days_to_race = (MARATHON_DATE - today).days
    weekly_miles = weekly_miles_in_block(activities)

    # Build mileage bar chart
    max_miles = max(weekly_miles.values()) if weekly_miles else 1
    bar_max = max(max_miles, 40)

    bar_rows = ""
    for w in sorted(weekly_miles):
        miles = weekly_miles[w]
        target = WEEKLY_PLAN.get(w, {}).get("miles", 0)
        pct = miles / bar_max * 100
        tpct = target / bar_max * 100
        on_track = miles >= target * 0.85
        color = "var(--accent3)" if on_track else "var(--accent4)" if miles >= target * 0.6 else "var(--accent)"
        phase = WEEKLY_PLAN.get(w, {}).get("phase", "")
        bar_rows += f'''<div style="display:flex;align-items:center;gap:8px;margin-bottom:7px;font-size:12px;">
  <span style="width:36px;color:var(--muted);text-align:right;">Wk {w}</span>
  <div style="flex:1;position:relative;background:var(--surface2);border-radius:3px;height:10px;">
    <div style="width:{pct:.1f}%;height:100%;background:{color};border-radius:3px;"></div>
    <div style="position:absolute;top:0;left:{tpct:.1f}%;width:2px;height:100%;background:rgba(255,255,255,.2);"></div>
  </div>
  <span style="width:36px;font-weight:600;color:var(--text);">{miles:.0f}mi</span>
  <span style="width:60px;font-size:11px;color:var(--muted);">{phase}</span>
</div>'''

    # Total miles so far
    total_miles = sum(weekly_miles.values())
    total_runs = len([a for a in activities if a.get("type") == "Run"
                      and a.get("date", "") >= BLOCK_START.strftime("%Y-%m-%d")])

    # Pace trend: get average easy run pace by week
    pace_trend = {}
    for w in range(1, week_num + 1):
        start, end = week_date_range(w)
        week_runs = runs_in_range(activities, start, end)
        easy_paces = []
        for r in week_runs:
            p = pace_to_seconds(r.get("pace_per_mile", ""))
            dist = float(r.get("distance_miles", 0) or 0)
            if p and p > pace_to_seconds("10:00") and dist >= 3:
                easy_paces.append(p)
        if easy_paces:
            pace_trend[w] = sum(easy_paces) / len(easy_paces)

    # Quality sessions hit. Only count *finished* weeks that actually had a
    # quality session on the plan — recovery weeks with no FFRT and the current
    # in-progress week (before its workout day) shouldn't count against attendance.
    quality_hit = 0
    quality_total = 0
    for w in range(1, week_num + 1):
        start, end = week_date_range(w)
        sched = WEEK_SCHEDULE.get(w, [])
        planned_quality = any(e[4] == "quality" for e in sched) if sched else True
        week_done = today > end
        if not planned_quality or not week_done:
            continue
        quality_total += 1
        week_runs = runs_in_range(activities, start, end)
        for r in week_runs:
            name = r.get("name", "").lower()
            if any(k in name for k in WORKOUT_KEYWORDS):
                quality_hit += 1
                break

    # HAF sessions in block
    haf_in_block = [a for a in activities if a.get("type") == "WeightTraining"
                    and a.get("date", "") >= BLOCK_START.strftime("%Y-%m-%d")]

    # Highlights and watch-fors
    highlights = []
    watch_fors = []

    # Mileage trend
    if week_num >= 2:
        prev = weekly_miles.get(week_num - 1, 0)
        curr = weekly_miles.get(week_num, 0)
        if curr > prev * 1.1:
            highlights.append(f"Mileage is building well — up {curr-prev:.1f}mi from last week")
        avg_miles = total_miles / week_num
        plan_avg = sum(WEEKLY_PLAN.get(w, {}).get("miles", 0) for w in range(1, week_num + 1)) / week_num
        if avg_miles >= plan_avg * 0.9:
            highlights.append(f"Averaging {avg_miles:.1f}mi/week through {week_num} weeks — on pace with the plan")
        else:
            watch_fors.append(f"Averaging {avg_miles:.1f}mi/week vs {plan_avg:.1f}mi planned — running a bit light")

    # Quality session consistency
    if quality_total > 0:
        q_pct = quality_hit / quality_total * 100
        if q_pct >= 80:
            highlights.append(f"Quality sessions: {quality_hit}/{quality_total} weeks hit — consistent FFRT attendance")
        else:
            watch_fors.append(f"Quality sessions: only {quality_hit}/{quality_total} weeks with a FFRT workout logged")

    # HAF consistency
    if week_num >= 2:
        haf_per_week = len(haf_in_block) / week_num
        if haf_per_week >= 2:
            highlights.append(f"Heavy AF: averaging {haf_per_week:.1f} sessions/week — strength base solid")
        else:
            watch_fors.append(f"Heavy AF frequency is down ({haf_per_week:.1f}/week) — keep the strength work consistent")

    highlights_html = "".join(
        f'<div style="display:flex;gap:8px;margin-bottom:8px;font-size:13px;color:var(--muted);"><span style="color:var(--accent3);">✓</span>{h}</div>'
        for h in (highlights or ["Keep stacking the weeks — the data will tell the story."])
    )
    watchfor_html = "".join(
        f'<div style="display:flex;gap:8px;margin-bottom:8px;font-size:13px;color:var(--muted);"><span style="color:var(--accent4);">→</span>{w}</div>'
        for w in (watch_fors or ["Nothing critical to flag yet."])
    )

    # ── Weekly recap archive ────────────────────────────────────────────────
    # Generate exec summaries for all completed weeks (not the current partial week)
    recap_cards = ""
    for w in range(1, week_num + 1):
        w_start, w_end = week_date_range(w)
        w_finished = today > w_end
        w_plan = WEEKLY_PLAN.get(w, {})
        w_phase = w_plan.get("phase", "")
        w_key   = w_plan.get("key", "")
        w_target = w_plan.get("miles", "?")

        w_runs_raw = runs_in_range(activities, w_start, w_end)
        seen = set()
        w_runs = []
        for r in w_runs_raw:
            rid = r.get("strava_id", "")
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            w_runs.append(r)

        if not w_runs and not w_finished:
            continue  # skip current week if no data yet

        w_actual = sum(float(r.get("distance_miles", 0) or 0) for r in w_runs)
        w_race = next((r for r in w_runs if is_race_activity(r) and float(r.get("distance_miles", 0) or 0) >= 3), None)
        w_workout = next((r for r in w_runs if is_workout_activity(r)), None)
        w_long = next((r for r in sorted(w_runs, key=lambda r: float(r.get("distance_miles", 0) or 0), reverse=True)
                       if float(r.get("distance_miles", 0) or 0) >= 10), None)

        # Get exec summary (strip outer wrapper to re-wrap with week header)
        summary_html = generate_exec_summary_html(w, w_phase, w_runs, laps, w_race, w_workout, w_long, w_actual, w_target)

        miles_pct = w_actual / w_target * 100 if w_target else 0
        miles_color = "var(--accent3)" if miles_pct >= 85 else "var(--accent4)" if miles_pct >= 60 else "var(--accent)"
        status_badge = (
            f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;'
            f'background:rgba(122,122,149,.15);color:var(--muted);">{w_phase}</span>'
        )
        recap_cards += f'''
<div style="margin-bottom:12px;">
  <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;">
    <span style="font-size:13px;font-weight:700;color:var(--text);">Week {w}</span>
    {status_badge}
    <span style="font-size:12px;color:var(--muted);">{w_start.strftime("%b %d")} – {w_end.strftime("%b %d")}</span>
    <span style="margin-left:auto;font-size:12px;font-weight:600;color:{miles_color};">{w_actual:.0f} / {w_target}mi</span>
  </div>
  {summary_html}
</div>'''

    updated = today.strftime("%A, %B %d at %-I:%M %p")

    progress_card = generate_block_progress_html(week_num, activities)

    return f'''
  <div style="margin-bottom:28px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">
    <h1>The Block So Far</h1>
    <span style="font-size:13px;color:var(--muted);">Week {week_num} of 19 · {days_to_race} days to Chicago</span>
  </div>

  {progress_card}

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px;">
    {stat_tile("Total miles", f"{total_miles:.0f}", f"Week {week_num} of 19", "orange")}
    {stat_tile("Total runs", str(total_runs), f"Since Jun 1", "blue")}
    {stat_tile("Quality sessions", f"{quality_hit}/{quality_total}", "Weeks with FFRT workout", "green" if quality_hit/max(quality_total,1) >= .8 else "yellow")}
    {stat_tile("HAF sessions", str(len(haf_in_block)), "Since block start", "muted")}
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;">
      <h3 style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:14px;">Weekly Mileage · Bar = actual, line = target</h3>
      {bar_rows}
      <div style="font-size:11px;color:var(--muted);margin-top:8px;">
        <span style="display:inline-block;width:10px;height:10px;background:var(--accent3);border-radius:2px;margin-right:4px;"></span>On/above target
        <span style="display:inline-block;width:10px;height:10px;background:var(--accent4);border-radius:2px;margin:0 4px 0 12px;"></span>Slightly under
        <span style="display:inline-block;width:10px;height:10px;background:var(--accent);border-radius:2px;margin:0 4px 0 12px;"></span>Short week
      </div>
    </div>
    <div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px;">
        <h3 style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:14px;">What's going well</h3>
        {highlights_html}
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;">
        <h3 style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--accent4);margin-bottom:14px;">Pay attention to</h3>
        {watchfor_html}
      </div>
    </div>
  </div>

  <div style="margin-top:24px;">
    <div style="font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:16px;">Weekly Recaps</div>
    {recap_cards if recap_cards else '<div style="color:var(--muted);font-size:13px;">No completed weeks yet.</div>'}
  </div>

  <div style="margin-top:20px;font-size:11px;color:var(--muted);text-align:right;">Updated {updated}</div>
'''


# ── Where I'm At ─────────────────────────────────────────────────────────────

def generate_where_im_at_html(activities, laps):
    today = datetime.now()
    week_num = current_training_week()
    days_to_race = (MARATHON_DATE - today).days

    # Recent runs — last 10, deduplicated
    all_runs = sorted([a for a in activities if a.get("type") == "Run"],
                      key=lambda x: x.get("date", ""), reverse=True)
    recent_runs = all_runs[:10]

    # Check if the Chicago 13.1 has been completed
    chicago_13 = next(
        (r for r in all_runs
         if "13.1" in r.get("name", "") and r.get("date", "").startswith("2026-06")),
        None
    )

    # Last 4 weeks avg miles
    cutoff = (today - timedelta(weeks=4)).strftime("%Y-%m-%d")
    last4 = [a for a in activities if a.get("type") == "Run" and a.get("date", "")[:10] >= cutoff]
    avg_weekly = sum(float(a.get("distance_miles", 0) or 0) for a in last4) / 4

    # Block miles so far
    block_miles = sum(float(a.get("distance_miles", 0) or 0) for a in activities
                      if a.get("type") == "Run" and a.get("date", "") >= BLOCK_START.strftime("%Y-%m-%d"))

    # ── Intro ──────────────────────────────────────────────────────────────
    if week_num == 0:
        intro = "Block starts June 1. Here's the picture heading in."
    elif week_num == 1 and chicago_13:
        t = parse_time_from_name(chicago_13.get("name", ""))
        result_str = f"{format_duration(t)} ({chicago_13.get('pace_per_mile','—')}/mi)" if t else chicago_13.get("pace_per_mile","—") + "/mi"
        intro = f"Week 1 is done. Chicago 13.1 result: <strong>{result_str}</strong>. Here's the picture heading into the build."
    elif week_num == 1:
        intro = "Week 1 underway. Here's the picture heading into the training block."
    else:
        intro = f"Week {week_num} of 19 · {block_miles:.0f} block miles logged · {days_to_race} days to Chicago."

    # ── Recent runs ────────────────────────────────────────────────────────
    run_rows_html = ""
    for r in recent_runs:
        raw_date = r.get("date", "")[:10]
        try:
            dt = datetime.strptime(raw_date, "%Y-%m-%d")
            date_str = dt.strftime("%b %-d")
        except Exception:
            date_str = raw_date
        name = r.get("name", "")
        dist = float(r.get("distance_miles", 0) or 0)
        pace = r.get("pace_per_mile", "—")
        hr = r.get("average_heartrate", "")
        hr_str = str(int(float(hr))) if hr else "—"

        bdg = ""
        if is_race_activity(r) and dist >= 3:
            bdg = ' <span class="badge badge-yellow">RACE</span>'
        elif is_workout_activity(r):
            bdg = ' <span class="badge badge-blue">FFRT</span>'

        run_rows_html += (
            f'<div class="run-row">'
            f'<div class="run-date">{date_str}</div>'
            f'<div class="run-name">{name}{bdg}</div>'
            f'<div class="run-dist">{dist:.1f}mi</div>'
            f'<div class="run-pace">{pace}</div>'
            f'<div class="run-hr">{hr_str}</div>'
            f'</div>'
        )

    # ── Race result / upcoming box ─────────────────────────────────────────
    if chicago_13:
        official_s = parse_time_from_name(chicago_13.get("name", ""))
        official_str = format_duration(official_s) if official_s else "—"
        race_pace = chicago_13.get("pace_per_mile", "—")
        race_hr = chicago_13.get("average_heartrate", "—")

        pred_html = ""
        if official_s:
            # VDOT equivalent from 1:52:21 half = 3:52:35 marathon (8:52/mi)
            pred_html = (f' VDOT marathon equivalent: '
                         f'<strong style="color:var(--accent3);">3:52:35</strong> (8:52/mi) — '
                         f'faster than your 3:55 goal. Train at 8:52 MP, race conservatively at 8:58.')

        next_week = week_num + 1
        next_plan = WEEKLY_PLAN.get(next_week, {})
        next_key = next_plan.get("key", "")
        next_miles = next_plan.get("miles", "")
        next_up = f" Week {next_week}: {next_miles}mi — {next_key}." if next_key else ""

        race_box = (
            '<div class="highlight green">'
            f'<div class="highlight-title">Chicago 13.1 — {official_str} ✓</div>'
            f'<p>{race_pace}/mi avg · HR {race_hr} · Paces are set for the block.{pred_html}{next_up}</p>'
            '</div>'
        )
    else:
        race_box = (
            '<div class="highlight yellow">'
            '<div class="highlight-title">Upcoming: Chicago 13.1 — Race Day</div>'
            '<p>Target sub-1:50 (8:24/mi). Go conservative for the first 5K, build through mile 8, '
            'race the back half. Whatever you run sets your training paces for the block.</p>'
            '</div>'
        )

    # ── Training paces (VDOT from Chicago 13.1 — 1:52:21) ─────────────────
    # VDOT calculator output: easy 9:57-10:56, marathon 8:52, threshold 8:18,
    # interval 7:39, repetition 7:15. HM equiv = 1:52:21 (8:34/mi), 10K = 8:10, 5K = 7:52.
    if chicago_13:
        easy_lo, easy_hi  = "9:57", "10:56"
        rec_lo,  rec_hi   = "11:00", "11:30"
        mp_str            = "8:52"          # VDOT marathon pace (equiv is 3:52:35)
        hmp_str           = "8:34"          # VDOT HM pace
        threshold_str     = "8:18"          # VDOT threshold/tempo
        interval_str      = "7:39"          # VDOT interval
        rep_str           = "7:15"          # VDOT repetition
        tenk_str          = "8:10"          # VDOT 10K equiv pace
        fivek_str         = "7:52"          # VDOT 5K equiv pace
        pace_note = '<p style="font-size:11px; margin-top:12px;">VDOT from Chicago 13.1 (1:52:21). Equiv marathon: 3:52:35.</p>'
    else:
        easy_lo, easy_hi  = "9:45", "10:15"
        rec_lo,  rec_hi   = "10:30", "11:00"
        mp_str            = "9:05"
        hmp_str           = "8:20 – 8:25"
        threshold_str     = "8:10 – 8:15"
        interval_str      = "7:45 – 7:55"
        rep_str           = "7:15 – 7:25"
        tenk_str          = "7:55 – 8:05"
        fivek_str         = "7:35 – 7:45"
        pace_note = '<p style="font-size:11px; margin-top:12px;">Will update after Chicago 13.1 result.</p>'

    # Race-equivalent strip (shown above the pace table)
    def _equiv_tile(label, value, color):
        return (f'<div style="flex:1;min-width:84px;background:var(--surface2);border-radius:8px;padding:9px 8px;text-align:center;">'
                f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:3px;">{label}</div>'
                f'<div style="font-size:17px;font-weight:700;color:{color};">{value}</div></div>')
    if chicago_13:
        race_equiv_html = (
            '<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;">'
            + _equiv_tile("Marathon equiv", "3:52:35", "var(--accent3)")
            + _equiv_tile("Half (actual)", "1:52:21", "var(--accent2)")
            + _equiv_tile("Goal", "3:55", "var(--accent4)")
            + '</div>'
        )
    else:
        race_equiv_html = ""

    updated = today.strftime("%A, %B %d at %-I:%M %p")

    return f'''
  <div style="margin-bottom: 28px;">
    <h1>Where I'm At</h1>
    <p>{intro}</p>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;align-items:start;">
    <div class="grid-2" style="gap:12px;">
      <div class="card-sm">
        <div class="stat-label">London Result</div>
        <div class="stat-value orange">4:19:37</div>
        <div class="stat-sub">Best since 2018 PR</div>
      </div>
      <div class="card-sm">
        <div class="stat-label">Chicago 13.1</div>
        <div class="stat-value blue">{parse_time_from_name(chicago_13.get("name","")) and format_duration(parse_time_from_name(chicago_13.get("name",""))) or "TBD"}</div>
        <div class="stat-sub">{"8:27/mi · HR 166 · Jun 7" if chicago_13 else "Jun 7 — race day"}</div>
      </div>
      <div class="card-sm">
        <div class="stat-label">Last 4 Weeks Avg</div>
        <div class="stat-value green">{avg_weekly:.0f}mi</div>
        <div class="stat-sub">{"Week 1 underway" if week_num <= 1 else f"Wk {week_num} of 19"}</div>
      </div>
      <div class="card-sm">
        <div class="stat-label">Chicago Target</div>
        <div class="stat-value yellow">3:55</div>
        <div class="stat-sub">8:58/mi · 24min PR</div>
      </div>
    </div>
    <div class="card">
      <h3>Training Paces &amp; Race Equivalents</h3>
      {race_equiv_html}
      <div class="pace-row"><div class="pace-name"><span class="dot dot-easy"></span>Easy / Aerobic</div>
          <div class="pace-val" style="color:var(--easy)">{easy_lo} – {easy_hi}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot dot-easy"></span>Recovery</div>
          <div class="pace-val" style="color:var(--muted)">{rec_lo} – {rec_hi}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot" style="background:var(--accent4)"></span>Marathon Pace (VDOT equiv)</div>
          <div class="pace-val" style="color:var(--accent4)">{mp_str}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot dot-quality"></span>Half Marathon Pace</div>
          <div class="pace-val" style="color:var(--accent2)">{hmp_str}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot dot-quality"></span>Threshold / Tempo</div>
          <div class="pace-val" style="color:var(--accent2)">{threshold_str}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot dot-quality"></span>10K Pace</div>
          <div class="pace-val" style="color:var(--accent2)">{tenk_str}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot dot-quality"></span>5K / Interval</div>
          <div class="pace-val" style="color:var(--accent2)">{fivek_str} / {interval_str}</div></div>
        <div class="pace-row"><div class="pace-name"><span class="dot dot-quality"></span>Repetition</div>
          <div class="pace-val" style="color:var(--accent2)">{rep_str}</div></div>
        {pace_note}
    </div>
  </div>

  <div class="grid-2" style="margin-bottom: 20px;">
    <div class="card">
      <h3>Recent Runs</h3>
      {run_rows_html}
    </div>
    <div class="card">
      <h3>What the Data Says</h3>
      <div class="highlight blue" style="margin-bottom: 12px;">
        <div class="highlight-title">Aerobic Base: Strong</div>
        <p>Soldier Field 10 at 9:04/mi with HR 157 on May 23 — 4 weeks post-London — is a meaningful fitness signal. Easy runs sitting 10:15–10:45 at HR 130–140, exactly where they should be.</p>
      </div>
      <div class="highlight orange" style="margin-bottom: 12px;">
        <div class="highlight-title">Muscular Endurance: The Gap</div>
        <p>London confirmed: you can run 16 miles at marathon pace. Miles 17–26 are where the legs give out — not the lungs. Tuesday mid-week long runs (building to 12–14mi) directly target this.</p>
      </div>
      <div class="highlight green" style="margin-bottom: 0;">
        <div class="highlight-title">Speed: There</div>
        <p>Shamrock Shuffle 39:44 (7:25/mi). Half Time Fartlek closing at 7:30. Speed system is healthy — the issue is sustaining it past mile 18, not generating it.</p>
      </div>
    </div>
  </div>

  {race_box}

  <hr>

  <div class="card" style="margin-bottom: 20px;">
    <h3>Heavy AF — The Strength Picture</h3>
    <div class="grid-2" style="gap: 16px;">
      <div>
        <p style="font-size:13px; margin-bottom:12px;">Lifting has been consistent through every training block — typically 2–3x/week alongside running. London block produced real concurrent strength gains:</p>
        <div class="pace-row"><div class="pace-name">Squat 2RM (Mar 2026)</div><div class="pace-val" style="color:var(--accent3)">195 lb</div></div>
        <div class="pace-row"><div class="pace-name">Current frequency</div><div class="pace-val" style="color:var(--accent4)">~3x/week</div></div>
        <div class="pace-row"><div class="pace-name">Lagree / Solidcore</div><div class="pace-val" style="color:var(--accent2)">Thursdays (opt)</div></div>
      </div>
      <div>
        <div class="highlight green">
          <div class="highlight-title">Goal: Maintain Through Peak</div>
          <p>Keep 2–3 HAF sessions/week through Week 10. Drop to 1–2 sessions in peak weeks. No new PRs during taper — just maintenance and movement quality.</p>
        </div>
      </div>
    </div>
  </div>

  <div style="margin-top:20px;font-size:11px;color:var(--muted);text-align:right;">Updated {updated}</div>
'''


# ── Chicago Plan dynamic sections ────────────────────────────────────────────

def generate_plan_progress_html(week_num, activities=None):
    days_to_go = (MARATHON_DATE - datetime.now()).days
    return f'<div style="margin-bottom:4px;font-size:13px;color:var(--muted);">Week {week_num} of 19 &middot; {days_to_go} days to Chicago</div>'


def generate_block_progress_html(week_num, activities):
    """Card with time + mileage progress bars for the Block So Far page."""
    days_total = (MARATHON_DATE - BLOCK_START).days
    days_done  = (datetime.now() - BLOCK_START).days
    time_pct   = max(0, min(100, days_done / days_total * 100))
    days_to_go = (MARATHON_DATE - datetime.now()).days

    total_planned = sum(v["miles"] for v in WEEKLY_PLAN.values())
    actual_miles = 0.0
    for a in (activities or []):
        if a.get("type") == "Run":
            try:
                d = datetime.strptime(a["date"][:10], "%Y-%m-%d")
                if BLOCK_START <= d:
                    actual_miles += float(a.get("distance_miles", 0) or 0)
            except Exception:
                pass
    mile_pct   = max(0, min(100, actual_miles / total_planned * 100)) if total_planned else 0
    mile_color = "var(--accent4)" if mile_pct >= 100 else "var(--accent2)"

    return f'''<div class="card" style="margin-bottom:20px;">
    <div style="display:flex;justify-content:space-between;margin-bottom:10px;">
      <span style="font-size:13px;font-weight:600;">Block Progress</span>
      <span style="font-size:12px;color:var(--muted);">Week {week_num} of 19 &middot; {days_to_go} days to go</span>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
      <span style="font-size:11px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase;">Time</span>
      <span style="font-size:11px;color:var(--muted);">Jun 1 &rarr; Oct 11</span>
    </div>
    <div class="progress-wrap" style="margin-bottom:12px;">
      <div class="progress-bar" style="width:{time_pct:.1f}%;"></div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
      <span style="font-size:11px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase;">Mileage</span>
      <span style="font-size:12px;font-weight:600;color:var(--text);">{actual_miles:.0f} <span style="color:var(--muted);font-weight:400;">/ {total_planned} mi planned</span> <span style="color:var(--accent2);margin-left:6px;">{mile_pct:.1f}%</span></span>
    </div>
    <div class="progress-wrap">
      <div class="progress-bar" style="width:{mile_pct:.1f}%;background:{mile_color};"></div>
    </div>
  </div>'''


def generate_plan_week_html(week_num, activities):
    """Generate a week block for the Chicago Plan, dynamically marking done days."""
    plan = WEEKLY_PLAN.get(week_num, {})
    phase = plan.get("phase", "Base")
    phase_class = {
        "Race": "phase-race", "Base": "phase-base", "Build": "phase-build",
        "Recovery": "phase-recovery", "Peak": "phase-peak",
        "Taper 1": "phase-taper", "Taper 2": "phase-taper", "Race Week": "phase-race",
    }.get(phase, "phase-base")
    target_miles = plan.get("miles", "?")

    w_start, w_end = week_date_range(week_num)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Build lookups: date string → best run activity, and total miles that day
    # (Kevin sometimes splits a run into two activities; the day total sums them.)
    run_by_date = {}
    miles_by_date = {}
    for a in activities:
        if a.get("type") != "Run":
            continue
        d = a.get("date", "")[:10]
        dist = float(a.get("distance_miles", 0) or 0)
        miles_by_date[d] = miles_by_date.get(d, 0.0) + dist
        if d not in run_by_date or dist > float(run_by_date[d].get("distance_miles", 0) or 0):
            run_by_date[d] = a

    CHECK = '<span class="check"><svg viewBox="0 0 10 10" fill="none"><polyline points="2,5 4,7 8,3" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>'

    schedule = WEEK_SCHEDULE.get(week_num)
    if schedule:
        # Use the custom day schedule. Tuple = (day, desc, notes, planned_miles, kind)
        days = []
        for i, entry in enumerate(schedule):
            day_name, run_desc, notes, planned, kind = entry
            date = w_start + timedelta(days=i)
            date_str = date.strftime("%b %-d")
            date_key = date.strftime("%Y-%m-%d")
            is_past = date < today
            run = run_by_date.get(date_key)
            is_rest = kind == "rest"

            dot = f"dot-{kind}"
            # Mark a day done once it's in the past OR has a logged activity.
            is_done = is_past or (run is not None)
            done_class = " done" if is_done else ""
            check = CHECK if is_done else ""

            # Miles column: actuals win; past unlogged days show a dash (don't
            # pass off an estimate as completed); future days show the estimate.
            if run is not None:
                miles_str = f"{miles_by_date.get(date_key, 0.0):.1f}mi ✓"
            elif is_rest or is_past or planned is None:
                miles_str = "—" if (is_rest or is_past) else ""
            elif kind == "easy":
                miles_str = f"~{planned:.1f}mi"
            else:
                # whole-number for workouts/long runs unless it has a fraction
                miles_str = f"{planned:.1f}mi" if planned % 1 else f"{planned:.0f}mi"

            note_html = f' <span style="font-size:11px;color:var(--muted);opacity:.7;">· {notes}</span>' if notes else ""
            days.append(
                f'<div class="day-row{done_class}"><div class="day-date">{date_str}</div>'
                f'<div class="day-name">{day_name}</div>'
                f'<div class="day-run">{check}<span class="dot {dot}"></span>{run_desc}{note_html}</div>'
                f'<div class="day-miles">{miles_str}</div></div>'
            )
        rows_html = "\n      ".join(days)
    else:
        # No schedule defined — leave a static placeholder
        rows_html = f'<!-- week {week_num} static -->'

    week_title = f"{w_start.strftime('%b %-d')}–{w_end.strftime('%-d')}"

    return f'''<div class="week-block">
      <div class="week-header">
        <span class="week-num">Wk {week_num}</span>
        <span class="week-title">{week_title}</span>
        <span class="phase-tag {phase_class}">{phase}</span>
        <span class="week-miles">~{target_miles}mi</span>
      </div>
      {rows_html}
    </div>'''


# ── Dashboard injection ──────────────────────────────────────────────────────

def inject_html(dashboard_path, marker, content):
    with open(dashboard_path, "r") as f:
        html = f.read()

    start_marker = f"<!-- {marker}_START -->"
    end_marker = f"<!-- {marker}_END -->"
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL
    )
    replacement = f"{start_marker}\n{content}\n{end_marker}"
    new_html, count = pattern.subn(replacement, html)
    if count == 0:
        print(f"  WARNING: marker {marker} not found in dashboard")
        return

    with open(dashboard_path, "w") as f:
        f.write(new_html)
    print(f"  Injected {marker}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n=== Chicago Marathon 2026 — Weekly Review Generator ===")
    print(f"  Today: {datetime.now().strftime('%A, %B %d, %Y')}")
    print(f"  Training week: {current_training_week()}")

    # Try to sync Strava first — works from Mac terminal, skips silently in sandbox
    import subprocess, shutil
    sync_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strava_export_v2.py")
    if os.path.exists(sync_script):
        print("  Syncing Strava...")
        result = subprocess.run(
            ["python3", sync_script],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            # Print just the summary lines (skip warnings)
            for line in result.stdout.splitlines():
                if any(k in line for k in ["Appended", "Found", "Done", "INCREMENTAL", "FULL"]):
                    print(f"    {line.strip()}")
        else:
            print("  (Strava sync skipped — not reachable from this environment)")

    activities = deduplicate_activities(load_activities())
    laps = load_laps()
    print(f"  Loaded {len(activities)} activities, {len(laps)} laps")

    print("\n  Generating Week in Review...")
    review_html = generate_week_review_html(activities, laps)
    inject_html(DASHBOARD_FILE, "WEEK_REVIEW", review_html)

    print("  Generating Block Summary...")
    block_html = generate_block_summary_html(activities, laps)
    inject_html(DASHBOARD_FILE, "BLOCK_SUMMARY", block_html)

    print("  Generating Where I'm At...")
    where_at_html = generate_where_im_at_html(activities, laps)
    inject_html(DASHBOARD_FILE, "WHERE_AT", where_at_html)

    print("  Generating Chicago Plan (progress bar + dynamic weeks)...")
    cur_week = current_training_week()
    inject_html(DASHBOARD_FILE, "PLAN_PROGRESS", generate_plan_progress_html(cur_week))
    for w in WEEK_SCHEDULE:
        inject_html(DASHBOARD_FILE, f"PLAN_WEEK_{w}", generate_plan_week_html(w, activities))

    print(f"\n  Dashboard updated: {DASHBOARD_FILE}")

    # Auto-push to GitHub Pages
    import subprocess, shutil
    if shutil.which("git"):
        repo_dir = os.path.dirname(os.path.abspath(DASHBOARD_FILE))

        # Clear stale lock file if present (can be left by a crashed git process)
        lock_file = os.path.join(repo_dir, ".git", "index.lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                print("  Cleared stale .git/index.lock")
            except OSError as e:
                print(f"  Warning: could not remove index.lock: {e}")

        subprocess.run(["git", "-C", repo_dir, "add",
                        "dashboard.html", "strava_activities.csv", "strava_laps.csv"],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.email", "kdaliva@gmail.com"],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.name", "Kevin"],
                       capture_output=True, text=True)
        commit = subprocess.run(
            ["git", "-C", repo_dir, "commit", "-m",
             f"dashboard update {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
            capture_output=True, text=True
        )
        if commit.returncode != 0:
            err = commit.stderr.strip() or commit.stdout.strip()
            if "nothing to commit" in err or "nothing added" in err:
                print("  GitHub: no changes to push.")
            else:
                print(f"  GitHub commit failed: {err}")
        else:
            push = subprocess.run(
                ["git", "-C", repo_dir, "push"],
                capture_output=True, text=True
            )
            if push.returncode == 0:
                print("  GitHub Pages: pushed ✓")
            else:
                print(f"  GitHub push failed: {push.stderr.strip()}")
    else:
        print("  (git not found — skipping GitHub push)")


if __name__ == "__main__":
    main()
