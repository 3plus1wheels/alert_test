import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ALERTS_PATH = DATA_DIR / "alerts.jsonl"

load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tomorrow_alert_test")

app = FastAPI(title="Tomorrow.io Hanoi Rain Alert POC")

SAFE_HEADER_NAMES = {
    "accept",
    "content-type",
    "cf-connecting-ip",
    "cf-ipcountry",
    "cf-ray",
    "host",
    "user-agent",
    "x-correlation-id",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
    "x-request-id",
}

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-webhook-secret",
}

NOTIFICATION_TYPES = {"PRIOR", "START", "END", "PUBLISH"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered in SENSITIVE_HEADER_NAMES:
            continue
        if lowered in SAFE_HEADER_NAMES:
            headers[lowered] = value
    return headers


def walk_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_items(child)


def normalized_key(key: Any) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


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


def extract_precipitation_probability(payload: Any) -> int | float | None:
    for key, value in walk_items(payload):
        if normalized_key(key) in {"precipitationprobability", "precipprobability"}:
            number = numeric_value(value)
            if number is not None:
                return number
    return None


def extract_notification_type(payload: Any) -> str | None:
    for key, value in walk_items(payload):
        if normalized_key(key) not in {"type", "notificationtype"}:
            continue
        if not isinstance(value, str):
            continue
        candidate = value.upper()
        if candidate in NOTIFICATION_TYPES:
            return candidate
    return None


def extract_event_time(payload: Any, names: set[str]) -> str | None:
    target_names = {normalized_key(name) for name in names}
    for key, value in walk_items(payload):
        if normalized_key(key) not in target_names:
            continue
        if isinstance(value, str):
            return value
    return None


def parsed_metadata(payload: Any) -> dict[str, Any]:
    return {
        "notificationType": extract_notification_type(payload),
        "eventStart": extract_event_time(
            payload,
            {"start", "startTime", "eventStart", "eventStartTime", "validFrom"},
        ),
        "eventEnd": extract_event_time(
            payload,
            {"end", "endTime", "eventEnd", "eventEndTime", "validTo", "expiresTime"},
        ),
        "precipitationProbability": extract_precipitation_probability(payload),
    }


def save_event(payload: Any, request: Request | None = None, simulated: bool = False) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "receivedAtUtc": utc_now_iso(),
        "simulated": simulated,
        "safeHeaders": safe_headers(request) if request else {},
        "parsed": parsed_metadata(payload),
        "raw": payload,
    }
    with ALERTS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    return event


def expected_secret() -> str:
    return os.getenv("WEBHOOK_SECRET", "").strip()


def validate_webhook_secret(request: Request) -> None:
    expected = expected_secret()
    query_secret = request.query_params.get("secret")
    header_secret = request.headers.get("x-webhook-secret")
    supplied = [value.strip() for value in (query_secret, header_secret) if value]

    if not expected:
        logger.warning("WEBHOOK_SECRET unset; accepting webhook for POC compatibility.")
        return

    if supplied:
        if any(hmac.compare_digest(value, expected) for value in supplied):
            return
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    logger.warning("Webhook secret missing; accepting webhook for POC compatibility.")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/webhooks/tomorrow/rain-alert")
async def receive_tomorrow_rain_alert(request: Request) -> dict[str, bool]:
    validate_webhook_secret(request)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Expected JSON body") from exc

    save_event(payload, request=request)
    return {"ok": True, "saved": True}


@app.get("/alerts/latest")
def latest_alert() -> dict[str, Any]:
    if not ALERTS_PATH.exists():
        return {"ok": True, "latest": None}

    latest_line = None
    with ALERTS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                latest_line = line.strip()

    if latest_line is None:
        return {"ok": True, "latest": None}

    try:
        latest = json.loads(latest_line)
    except json.JSONDecodeError:
        latest = {"rawLine": latest_line}

    return {"ok": True, "latest": latest}


@app.post("/alerts/simulate")
async def simulate_alert(request: Request) -> dict[str, Any]:
    now = utc_now_iso()
    fake_payload = {
        "simulated": True,
        "type": "START",
        "location": {
            "name": "Hanoi",
            "lat": 21.0278,
            "lon": 105.8342,
        },
        "event": {
            "startTime": now,
            "endTime": None,
            "values": {
                "precipitationProbability": 71,
            },
        },
        "precipitationProbability": 71,
    }
    event = save_event(fake_payload, request=request, simulated=True)
    return {"ok": True, "saved": True, "simulated": True, "latest": event}
