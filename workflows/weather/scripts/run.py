#!/usr/bin/env python3
"""
Weather workflow — fetches current conditions and forecasts.
Uses Open-Meteo (weather data) and Nominatim/OpenStreetMap (geocoding).
No API keys required.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR    = Path(__file__).resolve().parent
WORKFLOW_DIR  = SCRIPT_DIR.parent
PROJECT_ROOT  = WORKFLOW_DIR.parent.parent
LOCATIONS_FILE = WORKFLOW_DIR / "locations.json"

# ─── API endpoints ───────────────────────────────────────────────────────────

NOMINATIM_URL  = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ─── WMO weather code descriptions ───────────────────────────────────────────

WMO_DESCRIPTIONS = {
    0:  "Clear sky",
    1:  "Mainly clear",
    2:  "Partly cloudy",
    3:  "Overcast",
    45: "Fog",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Light freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}

WIND_COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

# ─── Formatting helpers ───────────────────────────────────────────────────────

def c_to_f(celsius):
    return round(celsius * 9 / 5 + 32)


def fmt_temp(celsius):
    if celsius is None:
        return "N/A"
    return f"{round(celsius)}°C ({c_to_f(celsius)}°F)"


def fmt_wind(speed_mph, direction_deg):
    if speed_mph is None or direction_deg is None:
        return "N/A"
    idx = round(direction_deg / 22.5) % 16
    return f"{WIND_COMPASS[idx]} {round(speed_mph)} mph"


def wmo_desc(code):
    if code is None:
        return "Unknown"
    return WMO_DESCRIPTIONS.get(int(code), f"Code {int(code)}")


def fmt_date_long(dt):
    """e.g. Wednesday 3 Jun 2026"""
    return f"{dt.strftime('%A')} {dt.day} {dt.strftime('%b %Y')}"


def fmt_date_short(dt):
    """e.g. Wed 3 Jun"""
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"


# ─── Environment ─────────────────────────────────────────────────────────────

def load_env():
    env_path = PROJECT_ROOT / ".env"
    result = {}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


# ─── Saved locations ─────────────────────────────────────────────────────────

def load_locations():
    if not LOCATIONS_FILE.exists():
        with LOCATIONS_FILE.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write("{}\n")
    return json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))


def save_locations(locs):
    with LOCATIONS_FILE.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(locs, indent=2) + "\n")


# ─── API calls ───────────────────────────────────────────────────────────────

def http_get_json(url, params, headers=None):
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e


def geocode(query, user_agent):
    data = http_get_json(
        NOMINATIM_URL,
        {"q": query, "format": "json", "limit": 1, "addressdetails": 1},
        headers={"User-Agent": user_agent},
    )
    if not data:
        raise ValueError(f"Location not found: {query!r}")
    r = data[0]
    return float(r["lat"]), float(r["lon"]), r["display_name"]


def fetch_weather(lat, lon, days):
    return http_get_json(
        OPEN_METEO_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weathercode,windspeed_10m,winddirection_10m",
            "daily": "temperature_2m_max,temperature_2m_min,weathercode",
            "hourly": "weathercode,windspeed_10m,winddirection_10m",
            "wind_speed_unit": "mph",
            "temperature_unit": "celsius",
            "timezone": "auto",
            "forecast_days": days,
        },
    )


# ─── Hourly condition grouping ────────────────────────────────────────────────

def _circular_mean(degrees_list):
    valid = [d for d in degrees_list if d is not None]
    if not valid:
        return None
    sin_sum = sum(math.sin(math.radians(d)) for d in valid)
    cos_sum = sum(math.cos(math.radians(d)) for d in valid)
    return math.degrees(math.atan2(sin_sum, cos_sum)) % 360


def _finalise_group(cur):
    valid_speeds = [s for s in cur["speeds"] if s is not None]
    return {
        "start":      cur["start"],
        "code":       cur["code"],
        "wind_speed": sum(valid_speeds) / len(valid_speeds) if valid_speeds else None,
        "wind_dir":   _circular_mean(cur["dirs"]),
    }


def group_hourly(weather, date_str, start_hour=6, end_hour=22):
    """
    Group hourly data for date_str into blocks of consecutive identical WMO codes.
    Only considers hours between start_hour (inclusive) and end_hour (exclusive).
    Returns list of group dicts: {start, code, wind_speed, wind_dir}.
    """
    times  = weather["hourly"]["time"]
    codes  = weather["hourly"]["weathercode"]
    speeds = weather["hourly"]["windspeed_10m"]
    dirs   = weather["hourly"]["winddirection_10m"]

    day_hours = [
        {"hour": int(t[11:13]), "code": codes[i], "speed": speeds[i], "dir": dirs[i]}
        for i, t in enumerate(times)
        if t.startswith(date_str) and start_hour <= int(t[11:13]) < end_hour
    ]

    if not day_hours:
        return []

    first = day_hours[0]
    cur = {"start": first["hour"], "code": first["code"],
           "speeds": [first["speed"]], "dirs": [first["dir"]]}
    groups = []

    for entry in day_hours[1:]:
        if entry["code"] == cur["code"]:
            cur["speeds"].append(entry["speed"])
            cur["dirs"].append(entry["dir"])
        else:
            groups.append(_finalise_group(cur))
            cur = {"start": entry["hour"], "code": entry["code"],
                   "speeds": [entry["speed"]], "dirs": [entry["dir"]]}
    groups.append(_finalise_group(cur))
    return groups


def format_groups(groups):
    """Return indented text lines describing each condition block."""
    lines = []
    for i, g in enumerate(groups):
        desc = wmo_desc(g["code"])
        wind = fmt_wind(g["wind_speed"], g["wind_dir"])

        if len(groups) == 1:
            label = "All day"
        elif i == 0:
            label = f"Until {groups[i + 1]['start']:02d}:00"
        elif i == len(groups) - 1:
            label = f"After {g['start']:02d}:00"
        else:
            label = f"{g['start']:02d}:00-{groups[i + 1]['start']:02d}:00"

        lines.append(f"  {label:<20} {desc}, {wind}")
    return lines


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_list(env):
    default = env.get("WEATHER_DEFAULT_LOCATION", "(not set - add WEATHER_DEFAULT_LOCATION to .env)")
    locs = load_locations()
    print(f"Default location: {default}")
    if locs:
        print("\nSaved locations:")
        for name, query in sorted(locs.items()):
            print(f"  {name:<20} {query}")
    else:
        print("No saved locations yet.")


def cmd_save(name, query, dry_run):
    key = name.strip().lower()
    if dry_run:
        print(f"[DRY RUN] Would save: '{key}' = '{query}'")
        return
    locs = load_locations()
    locs[key] = query
    save_locations(locs)
    print(f"Saved: '{key}' = '{query}'")


def cmd_delete(name, dry_run):
    key = name.strip().lower()
    locs = load_locations()
    if key not in locs:
        print(f"No saved location named '{key}'.")
        return
    if dry_run:
        print(f"[DRY RUN] Would delete: '{key}'")
        return
    del locs[key]
    save_locations(locs)
    print(f"Deleted saved location: '{key}'")


def cmd_weather(location_query, days, env, dry_run):
    locs = load_locations()
    user_agent = f"BookDragon-Weather/1.0 ({env.get('USER_EMAIL', 'unknown')})"

    if not location_query:
        location_query = env.get("WEATHER_DEFAULT_LOCATION", "")
    if not location_query:
        print("Error: no location given and WEATHER_DEFAULT_LOCATION is not set in .env.", file=sys.stderr)
        sys.exit(1)

    # Resolve saved location name to its stored query string
    resolved = locs.get(location_query.strip().lower(), location_query)

    if dry_run:
        print(f"[DRY RUN] Would geocode: {resolved!r}")
        print(f"[DRY RUN] Would fetch {days} day(s) of weather from Open-Meteo")
        return

    try:
        lat, lon, display_name = geocode(resolved, user_agent)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        weather = fetch_weather(lat, lon, days)
    except RuntimeError as exc:
        print(f"Error fetching weather: {exc}", file=sys.stderr)
        sys.exit(1)

    # Trim display name to city/region/country
    parts = [p.strip() for p in display_name.split(",")]
    short_name = ", ".join(parts[:3])

    cur       = weather["current"]
    daily     = weather["daily"]
    dates     = daily["time"]
    highs     = daily["temperature_2m_max"]
    lows      = daily["temperature_2m_min"]
    day_codes = daily["weathercode"]

    today_str = dates[0]
    today_dt  = datetime.strptime(today_str, "%Y-%m-%d")

    print(f"\nWeather for {short_name}")
    print("-" * 52)

    if days == 1:
        # ── Today only ────────────────────────────────────
        print(f"Today: {fmt_date_long(today_dt)}\n")
        print(f"Current:   {fmt_temp(cur['temperature_2m'])} - {wmo_desc(cur['weathercode'])}")
        print(f"High/Low:  {fmt_temp(highs[0])} / {fmt_temp(lows[0])}")
        print(f"Wind:      {fmt_wind(cur['windspeed_10m'], cur['winddirection_10m'])}")

        groups = group_hourly(weather, today_str)
        if len(groups) > 1:
            print("\nConditions through the day:")
            for line in format_groups(groups):
                print(line)
        print()

    else:
        # ── Multi-day ─────────────────────────────────────
        today_label = fmt_date_short(today_dt)
        print(f"Today ({today_label}):  High {fmt_temp(highs[0])}, Low {fmt_temp(lows[0])}")

        groups = group_hourly(weather, today_str)
        if not groups:
            pass
        elif len(groups) == 1:
            g = groups[0]
            print(f"  {wmo_desc(g['code'])}, {fmt_wind(g['wind_speed'], g['wind_dir'])}")
        else:
            for line in format_groups(groups):
                print(line)
        print()

        for i in range(1, len(dates)):
            date_str = dates[i]
            date_dt  = datetime.strptime(date_str, "%Y-%m-%d")
            label    = fmt_date_short(date_dt)
            high     = fmt_temp(highs[i])
            low      = fmt_temp(lows[i])

            groups = group_hourly(weather, date_str)
            if len(groups) <= 1:
                desc = wmo_desc(day_codes[i])
                wind_str = (f", {fmt_wind(groups[0]['wind_speed'], groups[0]['wind_dir'])}"
                            if groups else "")
                print(f"{label}:  High {high}, Low {low} - {desc}{wind_str}")
            else:
                print(f"{label}:  High {high}, Low {low}")
                for line in format_groups(groups):
                    print(line)
            print()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    # Ensure UTF-8 output on Windows terminals that default to cp1252
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Get weather and forecasts via Open-Meteo and Nominatim.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run.py                              default location, today
  python run.py --days 7                     7-day forecast for default location
  python run.py --location "Edinburgh"       one-off by place name
  python run.py --location "Houston, Texas"  works worldwide
  python run.py --location "RG26 4YB"        UK postcode
  python run.py --save work "EC1A 1BB"       save a named location
  python run.py --location work              query saved location
  python run.py --delete work                remove saved location
  python run.py --list                       list all saved locations
  python run.py --dry-run                    preview without API calls
""",
    )
    parser.add_argument("--location", "-l",
                        help="Postcode, place name, or saved location name")
    parser.add_argument("--days", "-d", type=int, default=1,
                        help="Number of forecast days (default: 1)")
    parser.add_argument("--save", nargs=2, metavar=("NAME", "QUERY"),
                        help="Save a named location: --save NAME 'place or postcode'")
    parser.add_argument("--delete", metavar="NAME",
                        help="Delete a saved location by name")
    parser.add_argument("--list", action="store_true",
                        help="List default location and all saved locations")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned actions without making any API calls")

    args = parser.parse_args()
    env  = load_env()

    if args.list:
        cmd_list(env)
    elif args.save:
        cmd_save(args.save[0], args.save[1], args.dry_run)
    elif args.delete:
        cmd_delete(args.delete, args.dry_run)
    else:
        cmd_weather(args.location, args.days, env, args.dry_run)


if __name__ == "__main__":
    main()
