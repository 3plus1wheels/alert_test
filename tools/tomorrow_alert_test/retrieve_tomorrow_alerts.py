import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.tomorrow.io/v4"


class ApiError(Exception):
    def __init__(self, method: str, path: str, status_code: int, body: Any, headers: dict[str, str]):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body
        self.headers = headers
        super().__init__(f"{method} {path} failed with HTTP {status_code}")


class TomorrowClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def get(self, path: str) -> Any:
        response = self.session.get(
            f"{API_BASE}{path}",
            params={"apikey": self.api_key},
            timeout=30,
        )

        try:
            body: Any = response.json()
        except ValueError:
            body = {"text": response.text}

        if response.status_code >= 400:
            raise ApiError("GET", path, response.status_code, body, dict(response.headers))

        return body


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def env_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def collection(payload: Any, plural_key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    data = payload.get("data", payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    value = data.get(plural_key)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]

    singular_key = plural_key[:-1]
    value = data.get(singular_key)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]

    return []


def resource(payload: Any, singular_key: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    data = payload.get("data", payload)
    if isinstance(data, dict):
        value = data.get(singular_key)
        if isinstance(value, dict):
            return value
        if "id" in data or "_id" in data:
            return data
    return {}


def resource_id(item: dict[str, Any]) -> str:
    for key in ("id", "_id"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def resource_name(item: dict[str, Any]) -> str:
    value = item.get("name")
    return str(value) if value is not None else ""


def resource_status(item: dict[str, Any]) -> str:
    for key in ("isActive", "active", "status", "state"):
        value = item.get(key)
        if value is not None:
            return str(value)
    return "unknown"


def alert_matches(alert: dict[str, Any], alert_id: str | None, name: str | None) -> bool:
    if alert_id and resource_id(alert) != alert_id:
        return False
    if name and resource_name(alert) != name:
        return False
    return True


def print_api_error(prefix: str, error: ApiError) -> None:
    print(f"ERROR: {prefix}")
    print(f"Request: {error.method} {API_BASE}{error.path}")
    print(f"HTTP status: {error.status_code}")
    print("Response:")
    print(json.dumps(error.body, indent=2, ensure_ascii=False))

    rate_headers = {
        name: value
        for name, value in error.headers.items()
        if "rate" in name.lower() or name.lower() in {"retry-after", "x-retry-after"}
    }
    if rate_headers:
        print("Rate-limit headers:")
        print(json.dumps(rate_headers, indent=2, ensure_ascii=False))


def print_alert_summary(alert: dict[str, Any]) -> None:
    alert_id = resource_id(alert) or "no-id"
    name = resource_name(alert) or "<unnamed>"
    status = resource_status(alert)
    insight = alert.get("insight", "unknown")
    print(f"- {name} ({alert_id}) active/status={status} insight={insight}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List existing Tomorrow.io alerts and retrieve alert details by documented alertId endpoint.",
    )
    parser.add_argument(
        "--alert-id",
        help="Only retrieve this alert ID. Uses GET /alerts/{alertId}.",
    )
    parser.add_argument(
        "--name",
        help="Only retrieve alerts whose name exactly matches this value.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Only print list summaries from GET /alerts; do not fetch each alert detail.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON for the final result.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(BASE_DIR / ".env")

    args = parse_args()
    client = TomorrowClient(env_value("TOMORROW_API_KEY"))

    try:
        alerts = collection(client.get("/alerts"), "alerts")
    except ApiError as error:
        print_api_error("Could not list existing Tomorrow.io alerts.", error)
        raise SystemExit(1)

    matching_alerts = [alert for alert in alerts if alert_matches(alert, args.alert_id, args.name)]

    if not matching_alerts:
        if args.raw:
            print("[]")
        else:
            print(f"Existing alerts found: {len(alerts)}")
            if args.alert_id or args.name:
                print("Matching alerts: 0")
            print("No matching alerts.")
        return

    if args.raw and args.summary:
        print(json.dumps(matching_alerts, indent=2, ensure_ascii=False))
        return

    if not args.raw:
        print(f"Existing alerts found: {len(alerts)}")
        if args.alert_id or args.name:
            print(f"Matching alerts: {len(matching_alerts)}")

        for alert in matching_alerts:
            print_alert_summary(alert)

    if args.summary:
        return

    detailed_alerts: list[dict[str, Any]] = []
    for alert in matching_alerts:
        alert_id = resource_id(alert)
        if not alert_id:
            if args.raw:
                detailed_alerts.append({"error": "missing alert id", "alert": alert})
            else:
                print("Skipping detail retrieval for alert without an id.")
            continue

        try:
            detail_payload = client.get(f"/alerts/{alert_id}")
        except ApiError as error:
            print_api_error(f"Could not retrieve alert {alert_id!r}.", error)
            raise SystemExit(1)

        detail = resource(detail_payload, "alert")
        detailed_alerts.append(detail if detail else {"id": alert_id, "response": detail_payload})

    if args.raw:
        print(json.dumps(detailed_alerts, indent=2, ensure_ascii=False))
        return

    print()
    print("Retrieved alert details:")
    for alert in detailed_alerts:
        print_alert_summary(alert)


if __name__ == "__main__":
    main()
