# Tomorrow.io Hanoi Rain Alert POC

Minimal one-shot test for a Tomorrow.io Free plan weather alert:

- one location: Hanoi, Vietnam
- one custom insight: `precipitationProbability > 70`
- one alert linked to that location
- one public HTTPS webhook receiver

This is only a proof of concept. It does not include flood routing, dashboards, paid providers, multi-location monitoring, SMS/email/push, or production auth.

## Files

- `app.py`: FastAPI webhook receiver and local simulation endpoints.
- `setup_tomorrow_alert.py`: Creates or reuses the Tomorrow.io location, insight, alert, and location link.
- `inspect_tomorrow_alerts.py`: Checks Hanoi hourly forecast values for `precipitationProbability`.
- `data/alerts.jsonl`: Created at runtime when webhook or simulated events arrive.

## Setup

PowerShell:

```powershell
cd C:\Users\w\Desktop\alert_test\tools\tomorrow_alert_test
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If `py` or `python` is not available in this Codex workspace, use the bundled runtime:

```powershell
& 'C:\Users\w\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Bash:

```bash
cd tools/tomorrow_alert_test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env`:

```bash
TOMORROW_API_KEY=replace_me
PUBLIC_WEBHOOK_BASE_URL=https://replace-me.example.com
WEBHOOK_SECRET=dev-secret-change-me
HANOI_LAT=21.0278
HANOI_LON=105.8342
ALERT_THRESHOLD=70
ALERT_NAME=codex-hanoi-rain-probability-gt-70
INSIGHT_NAME=codex-rain-probability-gt-70
LOCATION_NAME=codex-hanoi-test-location
```

Do not commit `.env`. The API key is never printed by the scripts.

## Run Local Webhook Receiver

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

Webhook URL path:

```txt
/webhooks/tomorrow/rain-alert
```

Webhook URL with shared secret:

```txt
https://your-public-host.example.com/webhooks/tomorrow/rain-alert?secret=YOUR_WEBHOOK_SECRET
```

The webhook accepts a correct `secret` query parameter or `X-Webhook-Secret` header. If Tomorrow.io cannot send either during POC testing, missing secret is accepted with a warning. Wrong secret is rejected.

## Run As Docker Container

This is the easiest way to keep the webhook receiver running in the background.

```bash
cd tools/tomorrow_alert_test
docker compose up -d --build
```

PowerShell:

```powershell
cd C:\Users\w\Desktop\alert_test\tools\tomorrow_alert_test
docker compose up -d --build
```

Useful commands:

```bash
docker compose logs -f
docker compose restart
docker compose down
docker compose exec tomorrow-alert-test python setup_tomorrow_alert.py
docker compose exec tomorrow-alert-test python inspect_tomorrow_alerts.py
```

The container uses `restart: unless-stopped`, so Docker will restart it after normal Docker/Desktop restarts unless you run `docker compose down`.

Alert storage is a host bind mount:

```txt
Host:      C:\Users\w\Desktop\alert_test\tools\tomorrow_alert_test\data\alerts.jsonl
Container: /app/data/alerts.jsonl
```

That keeps alerts visible on the host and persistent across container restarts and image rebuilds.

## Public HTTPS Tunnel

Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

Then set:

```bash
PUBLIC_WEBHOOK_BASE_URL=https://the-generated-tunnel-url
```

Free alternatives: ngrok free tunnel or localtunnel. Localhost alone will not prove Tomorrow.io delivery.

Keep the tunnel outside the app container for this POC:

```bash
cloudflared tunnel --url http://localhost:8000
```

Quick `trycloudflare.com` tunnel URLs can change when restarted. Use a stable Cloudflare tunnel token later if you need a permanent URL.

## Create Or Reuse Tomorrow.io Resources

```bash
python setup_tomorrow_alert.py
```

The script:

- lists existing locations, insights, and alerts before creating anything
- reuses exact matching names
- stops if a different existing location or alert would exceed the Free plan shape
- creates the custom insight with `rules: "precipitationProbability > 70"`
- retries once with documented AST `conditions` encoded as a JSON string if Tomorrow.io rejects the rules string
- creates the alert with `START` and `END` notifications encoded as a JSON string
- links the alert to the Hanoi location

If both insight formats fail, Tomorrow.io is likely allowing `precipitationProbability` in forecast responses but not as an Insights alert parameter for this account/API. In that case, this exact no-polling rain-probability alert is not accepted by Tomorrow.io, and `inspect_tomorrow_alerts.py` is the fallback proof for forecast availability.

Important Tomorrow.io docs gap: the documented `POST /v4/alerts` schema does not expose a webhook URL field. If your Tomorrow.io account requires webhook destination setup in the UI, use:

```txt
https://your-public-host.example.com/webhooks/tomorrow/rain-alert?secret=YOUR_WEBHOOK_SECRET
```

## Forecast Preflight

```bash
python inspect_tomorrow_alerts.py
```

It prints the max returned hourly `precipitationProbability`, all slots above `ALERT_THRESHOLD`, and whether a real alert is likely soon.

## Local Webhook Test

```bash
curl -X POST http://localhost:8000/alerts/simulate
curl http://localhost:8000/alerts/latest
```

PowerShell:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/alerts/simulate
Invoke-RestMethod http://localhost:8000/alerts/latest
```

`POST /alerts/simulate` writes a fake Hanoi event with:

```json
{"simulated": true, "precipitationProbability": 71}
```

This only proves local webhook handling. Real delivery is proven only when Tomorrow.io posts to `/webhooks/tomorrow/rain-alert`.

## Success Criteria

1. FastAPI app runs locally.
2. Public HTTPS tunnel reaches the local app.
3. `setup_tomorrow_alert.py` creates or reuses one Hanoi location, one custom insight, and one active alert.
4. `POST /alerts/simulate` writes to `data/alerts.jsonl`.
5. `GET /alerts/latest` returns the newest event.
6. Real Tomorrow.io webhook event is saved when `precipitationProbability > 70`.
7. If no real event fires, `inspect_tomorrow_alerts.py` shows forecast values and local handling still passes.

## References

- Free plan: https://www.tomorrow.io/weather-api/
- Free API rate limits: https://support.tomorrow.io/hc/en-us/articles/20273728362644-Free-API-Plan-Rate-Limits
- Alerts: https://docs.tomorrow.io/reference/post-alerts
- Insights: https://docs.tomorrow.io/reference/post-insights
- Locations: https://docs.tomorrow.io/reference/post-locations
- Forecast: https://docs.tomorrow.io/reference/weather-forecast
- Data layers: https://docs.tomorrow.io/reference/weather-data-layers
