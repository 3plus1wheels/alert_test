import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.tomorrow.io/v4"
WEBHOOK_PATH = "/webhooks/tomorrow/rain-alert"


class ApiError(Exception):
    def __init__(self, method: str, path: str, status_code: int, body: Any, headers: dict[str, str]):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body
        self.headers = headers
        super().__init__(f"{method} {path} failed with HTTP {status_code}")


@dataclass(frozen=True)
class Config:
    api_key: str
    public_webhook_base_url: str
    webhook_secret: str
    hanoi_lat: float
    hanoi_lon: float
    alert_threshold: int | float
    alert_name: str
    insight_name: str
    location_name: str

    @property
    def condition(self) -> str:
        return f"precipitationProbability > {format_number(self.alert_threshold)}"

    @property
    def webhook_base_path(self) -> str:
        return f"{self.public_webhook_base_url}{WEBHOOK_PATH}"

    @property
    def webhook_url_template(self) -> str:
        return f"{self.webhook_base_path}?secret=<WEBHOOK_SECRET>"


class TomorrowClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        request_params = {"apikey": self.api_key}
        if params:
            request_params.update(params)

        response = self.session.request(
            method,
            f"{API_BASE}{path}",
            params=request_params,
            json=json_body,
            timeout=30,
        )

        if response.status_code == 204:
            body: Any = {}
        else:
            try:
                body = response.json()
            except ValueError:
                body = {"text": response.text}

        if response.status_code >= 400:
            raise ApiError(method, path, response.status_code, body, dict(response.headers))

        return body


def format_number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)


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


def load_config() -> Config:
    load_dotenv(BASE_DIR / ".env")

    base_url = env_value("PUBLIC_WEBHOOK_BASE_URL").rstrip("/")
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not parsed.netloc:
        fail("PUBLIC_WEBHOOK_BASE_URL must be a public https:// URL.")
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        fail("PUBLIC_WEBHOOK_BASE_URL must be public HTTPS; localhost is not enough.")

    threshold = parse_number("ALERT_THRESHOLD")
    if threshold.is_integer():
        threshold = int(threshold)

    return Config(
        api_key=env_value("TOMORROW_API_KEY"),
        public_webhook_base_url=base_url,
        webhook_secret=env_value("WEBHOOK_SECRET"),
        hanoi_lat=parse_number("HANOI_LAT"),
        hanoi_lon=parse_number("HANOI_LON"),
        alert_threshold=threshold,
        alert_name=env_value("ALERT_NAME"),
        insight_name=env_value("INSIGHT_NAME"),
        location_name=env_value("LOCATION_NAME"),
    )


def print_summary(config: Config) -> None:
    print("Provider: Tomorrow.io")
    print("Plan target: Free only")
    print(f"Location: Hanoi, Vietnam, {config.hanoi_lat},{config.hanoi_lon}")
    print(f"Condition: {config.condition}")
    print(f"Webhook: {config.webhook_base_path}")
    print("Webhook with secret for Tomorrow.io UI:")
    print(f"  {config.webhook_url_template}")
    print("Resources intended: 1 location, 1 custom insight, 1 alert")
    print("Note: Tomorrow.io alert API docs do not expose a webhook URL field.")
    print("If your account UI requires webhook setup, use the URL above and replace <WEBHOOK_SECRET> locally.")
    print()


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
        if "id" in data:
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


def find_by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    matches = [item for item in items if resource_name(item) == name]
    if len(matches) > 1:
        print(f"Warning: multiple resources named {name!r}; reusing first match.")
    return matches[0] if matches else None


def describe_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"
    return ", ".join(f"{resource_name(item) or '<unnamed>'} ({resource_id(item) or 'no-id'})" for item in items)


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

    body_text = json.dumps(error.body, ensure_ascii=False).lower()
    if error.status_code == 429:
        print()
        print("Rate-limit note:")
        print("Tomorrow.io Free API limits are low. Wait for the current rate-limit window to reset, then rerun.")
        return

    if error.status_code in {400, 402, 403, 409} or "limit" in body_text or "plan" in body_text:
        print()
        print("Free-plan note:")
        print("Tomorrow.io Free plan allows one monitored location and one weather-based alert.")
        print("If this is a resource-limit rejection, delete the extra Tomorrow.io location/alert and rerun.")


def ensure_existing_resource_ok(
    resource_kind: str,
    target_name: str,
    target_resource: dict[str, Any] | None,
    existing: list[dict[str, Any]],
) -> None:
    if target_resource or not existing:
        return

    fail(
        f"Free-plan guard stopped creation of another {resource_kind}. "
        f"Expected exact name {target_name!r}, but existing {resource_kind}(s) are: {describe_items(existing)}. "
        "Delete or rename the existing Tomorrow.io resource, then rerun."
    )


def create_location(client: TomorrowClient, config: Config) -> dict[str, Any]:
    payload = {
        "name": config.location_name,
        "geometry": {
            "type": "Point",
            "coordinates": [config.hanoi_lon, config.hanoi_lat],
        },
        "tags": ["codex", "hanoi-rain-alert-poc"],
    }
    response = client.request("POST", "/locations", json_body=payload)
    created = resource(response, "location")
    if not created:
        fail("Location create response did not include a location object.")
    return created


def insight_base_payload(config: Config) -> dict[str, Any]:
    return {
        "name": config.insight_name,
        "description": f"POC insight for Hanoi: {config.condition}. Free-plan one-alert test.",
        "severity": "minor",
        "tags": ["codex", "hanoi-rain-alert-poc"],
    }


def insight_conditions(config: Config) -> dict[str, Any]:
    return {
        "type": "OPERATOR",
        "content": {
            "operator": "GREATER",
        },
        "children": [
            {
                "type": "PARAMETER",
                "content": {
                    "parameter": "precipitationProbability",
                },
            },
            {
                "type": "CONST",
                "content": {
                    "const": config.alert_threshold,
                },
            },
        ],
    }


def is_invalid_rules_error(error: ApiError) -> bool:
    body_text = json.dumps(error.body, ensure_ascii=False).lower()
    return error.status_code == 400 and "rules" in body_text and "not valid" in body_text


def create_insight(client: TomorrowClient, config: Config) -> dict[str, Any]:
    rules_payload = insight_base_payload(config)
    rules_payload["rules"] = config.condition

    try:
        response = client.request("POST", "/insights", json_body=rules_payload)
    except ApiError as rules_error:
        if not is_invalid_rules_error(rules_error):
            raise

        print("Rules language rejected precipitationProbability rule; retrying with documented AST conditions.")
        conditions_payload = insight_base_payload(config)
        conditions_payload["conditions"] = json.dumps(
            insight_conditions(config),
            separators=(",", ":"),
        )
        try:
            response = client.request("POST", "/insights", json_body=conditions_payload)
        except ApiError as conditions_error:
            print("AST conditions retry also failed.")
            print("Likely cause: precipitationProbability is available in forecast data, but not accepted as an Insights rule parameter for this account/API.")
            raise conditions_error from rules_error

    created = resource(response, "insight")
    if not created:
        fail("Insight create response did not include an insight object.")
    return created


def create_alert(client: TomorrowClient, config: Config, insight_id: str) -> dict[str, Any]:
    payload = {
        "name": config.alert_name,
        "insight": insight_id,
        "isActive": True,
        "notifications": json.dumps(
            [
                {"type": "START"},
                {"type": "END"},
            ],
            separators=(",", ":"),
        ),
    }
    response = client.request("POST", "/alerts", json_body=payload)
    created = resource(response, "alert")
    if not created:
        fail("Alert create response did not include an alert object.")
    return created


def validate_existing_insight(insight: dict[str, Any], config: Config) -> None:
    rules = insight.get("rules")
    if rules is not None and str(rules).strip() != config.condition:
        fail(
            f"Existing insight {config.insight_name!r} has rules {rules!r}, "
            f"not {config.condition!r}. Delete or rename it before rerunning."
        )


def validate_existing_alert(alert: dict[str, Any], config: Config, insight_id: str) -> None:
    existing_insight = alert.get("insight")
    if existing_insight is not None and str(existing_insight) != insight_id:
        fail(
            f"Existing alert {config.alert_name!r} points to insight {existing_insight!r}, "
            f"not {insight_id!r}. Delete or rename it before rerunning."
        )


def activate_alert_if_needed(client: TomorrowClient, alert: dict[str, Any]) -> None:
    if alert.get("isActive") is not False:
        return

    alert_id = resource_id(alert)
    if not alert_id:
        fail("Cannot activate existing alert because it has no id.")
    client.request("POST", f"/alerts/{alert_id}/activate")
    print("Activated existing alert.")


def link_location(client: TomorrowClient, alert_id: str, location_id: str) -> None:
    try:
        client.request("POST", f"/alerts/{alert_id}/locations/link", json_body={"locations": [location_id]})
        print("Linked alert to Hanoi location.")
    except ApiError as error:
        body_text = json.dumps(error.body, ensure_ascii=False).lower()
        if "already" in body_text and "link" in body_text:
            print("Location already linked to alert.")
            return
        raise


def main() -> None:
    config = load_config()
    print_summary(config)

    client = TomorrowClient(config.api_key)

    try:
        locations = collection(client.request("GET", "/locations"), "locations")
        insights = collection(client.request("GET", "/insights"), "insights")
        alerts = collection(client.request("GET", "/alerts"), "alerts")
    except ApiError as error:
        print_api_error("Could not list existing Tomorrow.io resources.", error)
        raise SystemExit(1)

    print(f"Existing locations: {describe_items(locations)}")
    print(f"Existing insights: {describe_items(insights)}")
    print(f"Existing alerts: {describe_items(alerts)}")
    print()

    location = find_by_name(locations, config.location_name)
    ensure_existing_resource_ok("location", config.location_name, location, locations)

    alert = find_by_name(alerts, config.alert_name)
    ensure_existing_resource_ok("alert", config.alert_name, alert, alerts)

    status: dict[str, str] = {}

    try:
        if location:
            status["location"] = "reused"
            print(f"Reusing location: {config.location_name} ({resource_id(location)})")
        else:
            location = create_location(client, config)
            status["location"] = "created"
            print(f"Created location: {config.location_name} ({resource_id(location)})")

        insight = find_by_name(insights, config.insight_name)
        if insight:
            validate_existing_insight(insight, config)
            status["insight"] = "reused"
            print(f"Reusing insight: {config.insight_name} ({resource_id(insight)})")
        else:
            insight = create_insight(client, config)
            status["insight"] = "created"
            print(f"Created insight: {config.insight_name} ({resource_id(insight)})")

        insight_id = resource_id(insight)
        location_id = resource_id(location)
        if not insight_id:
            fail("Insight has no id.")
        if not location_id:
            fail("Location has no id.")

        if alert:
            validate_existing_alert(alert, config, insight_id)
            status["alert"] = "reused"
            print(f"Reusing alert: {config.alert_name} ({resource_id(alert)})")
        else:
            alert = create_alert(client, config, insight_id)
            status["alert"] = "created"
            print(f"Created alert: {config.alert_name} ({resource_id(alert)})")

        alert_id = resource_id(alert)
        if not alert_id:
            fail("Alert has no id.")

        activate_alert_if_needed(client, alert)
        link_location(client, alert_id, location_id)

    except ApiError as error:
        print_api_error("Tomorrow.io rejected setup request.", error)
        raise SystemExit(1)

    print()
    print("Done.")
    print(f"Location {status['location']}: {config.location_name} ({resource_id(location)})")
    print(f"Insight {status['insight']}: {config.insight_name} ({resource_id(insight)})")
    print(f"Alert {status['alert']}: {config.alert_name} ({resource_id(alert)})")
    print(f"Webhook path: {WEBHOOK_PATH}")
    print(f"Webhook destination template: {config.webhook_url_template}")
    print("API key was not printed.")


if __name__ == "__main__":
    main()
