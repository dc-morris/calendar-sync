# calendar-sync

Bidirectional sync between Apple iCloud Calendar and Google Calendar. Runs as a lightweight Docker container, syncing events every 5 minutes.

Built for self-hosted homelab setups — comes with a health endpoint for uptime monitoring and a web UI to browse synced events.

## How it works

A sync loop runs on a configurable interval (default 5 minutes):

1. **Fetch** all events from both iCloud (via CalDAV) and Google Calendar (via REST API) within a configurable time window (default: 90 days back, 365 days forward)
2. **Diff** against known sync pairs stored in SQLite
3. **Propagate** creates, updates, and deletes in both directions
4. **Store** event details for the web UI

### Duplicate prevention

Three layers prevent events from being duplicated:

- **Sync pairs table** — Every synced event gets a mapping record (`icloud_uid ↔ google_event_id`). Known pairs are skipped on every cycle. This is the primary guard.
- **Pending changes table** — When we write an event to one side, we record it with a TTL. On the next cycle, we recognise it as our own write and skip it. This prevents sync loops where a newly created event bounces back and forth.
- **Fuzzy matching on initial sync** — On first run (or after a crash with no pair record), before creating an event we search the target calendar for an existing event with the same summary and start time. If found, we link the pair instead of creating a duplicate.

### Conflict resolution

If the same event is modified on both sides between sync cycles, the version with the most recent `last-modified` timestamp wins.

### Recurring events

Master events with `RRULE` are synced intact — the recurrence rule is preserved rather than expanding into individual instances. `EXDATE` (deleted occurrences) are synced. Individual instance modifications (e.g. changing just one occurrence) are not currently handled.

## Setup

### Prerequisites

- Docker and Docker Compose
- An Apple ID with an [app-specific password](https://support.apple.com/en-gb/102654)
- A Google Cloud project with the [Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com) enabled and OAuth2 Desktop credentials

### 1. Get a Google refresh token

Create OAuth2 credentials (Application type: Desktop app) in the Google Cloud Console, download `client_secret.json`, then run:

```bash
pip install google-auth-oauthlib
python scripts/google_auth.py client_secret.json
```

This opens a browser for authorisation and prints the credentials to add to your `.env`.

> **Note:** If you see "app not verified", add your Google account as a test user under APIs & Services → OAuth consent screen → Test users.

### 2. Find your Google Calendar ID

In [Google Calendar](https://calendar.google.com), click the three dots next to your target calendar → Settings and sharing → Integrate calendar → Calendar ID.

It looks like `abc123@group.calendar.google.com`. For your primary calendar, use `primary`.

### 3. Configure

Copy the example env file and fill in your values:

```bash
cp infra/.env.example infra/.env
```

| Variable | Description |
|----------|-------------|
| `ICLOUD_USERNAME` | Your Apple ID email |
| `ICLOUD_APP_PASSWORD` | App-specific password from appleid.apple.com |
| `ICLOUD_CALENDAR_NAME` | Calendar name in iCloud (e.g. `Family`) |
| `GOOGLE_CLIENT_ID` | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `GOOGLE_REFRESH_TOKEN` | From step 1 |
| `GOOGLE_CALENDAR_ID` | From step 2 |
| `SYNC_INTERVAL_SECONDS` | How often to sync (default: `300`) |
| `SYNC_LOOKBACK_DAYS` | How far back to sync (default: `90`) |
| `SYNC_LOOKAHEAD_DAYS` | How far forward to sync (default: `365`) |
| `DB_PATH` | SQLite database path (default: `/data/calendar_sync.db`) |
| `HEALTH_PORT` | Health/UI server port (default: `8080`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |
| `NTFY_URL` | Optional [ntfy](https://ntfy.sh) endpoint for error alerts |
| `NTFY_TOKEN` | Optional ntfy auth token |

### 4. Deploy

```bash
docker volume create calendar_sync_data
cd infra
docker compose up -d
```

Check the logs to verify the first sync:

```bash
docker logs -f calendar-sync
```

## Web UI

The container serves a simple web interface:

- **`/`** — Event browser with upcoming/past/all tabs
- **`/health`** — Health check endpoint (JSON, suitable for uptime monitoring)
- **`/status`** — Sync status with recent run history (JSON)
- **`/events`** — All synced events (JSON)

To expose it through a reverse proxy, route to port 8080 on the container. For example, with Caddy:

```
route /calendar-sync* {
    uri strip_prefix /calendar-sync
    reverse_proxy calendar-sync:8080
}
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

## Architecture

```
src/calendar_sync/
  main.py           — Entry point: sync loop + health server
  config.py         — Pydantic settings from environment variables
  sync_engine.py    — Core bidirectional sync algorithm
  icloud_client.py  — CalDAV wrapper for iCloud
  google_client.py  — Google Calendar API wrapper
  db.py             — SQLite schema and queries
  models.py         — NormalizedEvent dataclass with content hashing
  health.py         — HTTP server: health, status, events API, and web UI
```

## Limitations

- Individual recurring event instance modifications (changing just one occurrence) are not synced — only the master event and deleted occurrences
- Initial sync fuzzy matching uses summary + start time, so two genuinely different events with identical names and times could be incorrectly linked
- Google refresh tokens can expire if unused for 6 months or if access is revoked — the app will log an error and alert via ntfy if configured
- iCloud CalDAV rate limiting may apply with very aggressive sync intervals

## License

MIT
