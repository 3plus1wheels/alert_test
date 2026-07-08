import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.tomorrow.io/v4"
TIMEZONE = ZoneInfo("Asia/Bangkok")


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def env_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def parse_number(name: str) -> float:
    value = env_value(name)
    try:
        return float(value)
    except ValueError:
        fail(f"{name} must be numeric, got {value!r}")


def format_number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def numeric_value(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None

    if number.is_integer():
        return int(number)
    return number


def local_time(raw_time: str | None) -> str:
    if not raw_time:
        return "unknown-time"

    try:
        parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        return raw_time

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(TIMEZONE).isoformat()


def point_from_item(item: dict[str, Any]) -> tuple[str | None, int | float] | None:
    values = item.get("values")
    if not isinstance(values, dict):
        values = item.get("eventValues")
    if not isinstance(values, dict):
        values = item

    probability = numeric_value(values.get("precipitationProbability"))
    if probability is None:
        return None

    time_value = item.get("time") or item.get("startTime") or item.get("validTime")
    if time_value is not None:
        time_value = str(time_value)

    return time_value, probability


def points_from_items(items: list[Any]) -> list[tuple[str | None, int | float]]:
    points: list[tuple[str | None, int | float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        point = point_from_item(item)
        if point is not None:
            points.append(point)
    return points


def collect_hourly_points(payload: Any) -> list[tuple[str | None, int | float]]:
    if not isinstance(payload, dict):
        return []

    points: list[tuple[str | None, int | float]] = []
    roots = [payload.get("timelines")]
    data = payload.get("data")
    if isinstance(data, dict):
        roots.append(data.get("timelines"))

    for root in roots:
        if isinstance(root, dict):
            hourly = root.get("hourly") or root.get("1h")
            if isinstance(hourly, list):
                points.extend(points_from_items(hourly))
        elif isinstance(root, list):
            for timeline in root:
                if not isinstance(timeline, dict):
                    continue
                timestep = str(timeline.get("timestep") or timeline.get("name") or "").lower()
                if timestep and timestep not in {"1h", "hourly"}:
                    continue
                intervals = timeline.get("intervals")
                if isinstance(intervals, list):
                    points.extend(points_from_items(intervals))

    return points


def get_forecast(api_key: str, location: str) -> Any:
    response = requests.get(
        f"{API_BASE}/weather/forecast",
        params={
            "location": location,
            "timesteps": "1h",
            "units": "metric",
            "apikey": api_key,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )

    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text}

    if response.status_code >= 400:
        print("ERROR: Tomorrow.io forecast request failed.")
        print(f"HTTP status: {response.status_code}")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        raise SystemExit(1)

    return body


def main() -> None:
    load_dotenv(BASE_DIR / ".env")

    api_key = env_value("TOMORROW_API_KEY")
    lat = parse_number("HANOI_LAT")
    lon = parse_number("HANOI_LON")
    threshold = parse_number("ALERT_THRESHOLD")
    if threshold.is_integer():
        threshold = int(threshold)

    payload = get_forecast(api_key, f"{lat},{lon}")
    points = collect_hourly_points(payload)
    if not points:
        print("No hourly precipitationProbability values found in forecast response.")
        print("Raw top-level keys:", ", ".join(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
        raise SystemExit(1)

    max_probability = max(probability for _, probability in points)
    above_threshold = [(time_value, probability) for time_value, probability in points if probability > threshold]

    print(f"Hanoi precipitationProbability max next forecast window: {format_number(max_probability)}%")
    if above_threshold:
        print(f"Slots above {format_number(threshold)}%:")
        for time_value, probability in above_threshold:
            print(f"- {local_time(time_value)} => {format_number(probability)}%")
        print("Alert likely to trigger if Tomorrow.io monitoring and webhook delivery are active.")
    else:
        print(f"No hourly slots above {format_number(threshold)}%.")
        print("Real alert not likely to trigger soon, but local webhook handling can still pass.")


if __name__ == "__main__":
    main()
