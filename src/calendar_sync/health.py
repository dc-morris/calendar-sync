import json
import logging
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from calendar_sync.db import SyncDB

logger = logging.getLogger(__name__)

_db_ref: SyncDB | None = None
_sync_interval: int = 300

EVENTS_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calendar Sync</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f0f1a;
    color: #e0e0e0;
    padding: 2rem;
    max-width: 900px;
    margin: 0 auto;
}
h1 { font-size: 1.4rem; font-weight: 300; letter-spacing: 0.1em;
     text-transform: uppercase; color: #fff; margin-bottom: 0.5rem; }
.meta { font-size: 0.8rem; color: #666; margin-bottom: 2rem; }
.meta .ok { color: #5cdd8b; }
.meta .err { color: #e06c75; }
.tabs { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; }
.tab { padding: 0.4rem 1rem; border-radius: 8px; font-size: 0.85rem; cursor: pointer;
       background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
       color: #aaa; transition: all 0.2s; }
.tab.active { background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.2); color: #fff; }
.day-header { font-size: 0.85rem; color: #888; padding: 0.75rem 0 0.25rem;
              border-bottom: 1px solid rgba(255,255,255,0.06); margin-bottom: 0.5rem; }
.event { display: flex; gap: 1rem; padding: 0.6rem 0.75rem; border-radius: 10px;
         margin-bottom: 0.25rem; transition: background 0.15s; }
.event:hover { background: rgba(255,255,255,0.04); }
.event-time { min-width: 100px; font-size: 0.8rem; color: #888;
              font-family: "SF Mono", "Fira Code", monospace; padding-top: 0.1rem; }
.event-details { flex: 1; }
.event-summary { font-size: 0.95rem; color: #fff; }
.event-location { font-size: 0.75rem; color: #666; margin-top: 0.15rem; }
.event-badge { font-size: 0.65rem; padding: 0.1rem 0.4rem; border-radius: 4px;
               margin-left: 0.5rem; vertical-align: middle; }
.badge-icloud { background: rgba(86,180,233,0.15); color: #56b4e9; }
.badge-google { background: rgba(244,104,0,0.15); color: #f46800; }
.all-day { color: #d19a66; }
.empty { text-align: center; padding: 3rem; color: #555; }
a { color: #888; text-decoration: none; }
a:hover { color: #fff; }
</style>
</head>
<body>
<a href="/">&larr; macmini.internal</a>
<h1>Calendar Sync</h1>
<div class="meta" id="meta">Loading...</div>
<div class="tabs">
    <div class="tab active" data-view="upcoming">Upcoming</div>
    <div class="tab" data-view="past">Past</div>
    <div class="tab" data-view="all">All</div>
</div>
<div id="events"></div>
<script>
let allEvents = [];
let currentView = 'upcoming';

document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        currentView = t.dataset.view;
        render();
    });
});

async function load() {
    try {
        const [statusRes, eventsRes] = await Promise.all([
            fetch('status'), fetch('events')
        ]);
        const status = await statusRes.json();
        const events = await eventsRes.json();
        allEvents = events;

        const m = document.getElementById('meta');
        const cls = status.last_sync_status === 'success' ? 'ok' : 'err';
        const ago = timeAgo(status.last_sync);
        m.innerHTML = `<span class="${cls}">${status.synced_events} events synced</span> &middot; last sync ${ago}`;
        render();
    } catch(e) {
        document.getElementById('meta').textContent = 'Failed to load';
    }
}

function render() {
    const el = document.getElementById('events');
    const now = new Date().toISOString();
    let filtered = allEvents;
    if (currentView === 'upcoming') filtered = allEvents.filter(e => e.end_time >= now);
    else if (currentView === 'past') filtered = allEvents.filter(e => e.end_time < now).reverse();

    if (!filtered.length) { el.innerHTML = '<div class="empty">No events</div>'; return; }

    let html = '';
    let lastDay = '';
    for (const e of filtered) {
        const day = formatDay(e.start_time, e.is_all_day);
        if (day !== lastDay) { html += `<div class="day-header">${day}</div>`; lastDay = day; }

        const time = e.is_all_day ? '<span class="all-day">All day</span>' : formatTime(e.start_time, e.end_time);
        const badge = e.source_origin === 'icloud'
            ? '<span class="event-badge badge-icloud">iCloud</span>'
            : '<span class="event-badge badge-google">Google</span>';
        const loc = e.location ? `<div class="event-location">${esc(e.location)}</div>` : '';

        html += `<div class="event">
            <div class="event-time">${time}</div>
            <div class="event-details">
                <div class="event-summary">${esc(e.summary)}${badge}</div>
                ${loc}
            </div>
        </div>`;
    }
    el.innerHTML = html;
}

function formatDay(iso, allDay) {
    const d = allDay ? new Date(iso + 'T00:00:00') : new Date(iso);
    const now = new Date();
    const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
    const ds = d.toDateString();
    if (ds === now.toDateString()) return 'Today';
    if (ds === tomorrow.toDateString()) return 'Tomorrow';
    return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

function formatTime(start, end) {
    const s = new Date(start);
    const e = new Date(end);
    const fmt = t => t.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    return fmt(s) + ' &ndash; ' + fmt(e);
}

function timeAgo(iso) {
    if (!iso) return 'never';
    const diff = Date.now() - new Date(iso + 'Z').getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + 'h ago';
    return Math.floor(hrs / 24) + 'd ago';
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

load();
setInterval(load, 60000);
</script>
</body>
</html>"""


class HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                self._handle_health()
            elif self.path == "/status":
                self._handle_status()
            elif self.path == "/events":
                self._handle_events()
            elif self.path == "/":
                self._handle_page()
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
        except Exception:
            logger.exception("Health handler error")
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _handle_health(self) -> None:
        last_run = _db_ref.last_sync_run() if _db_ref else None
        healthy = True
        body: dict = {"status": "ok"}

        if last_run:
            body["last_sync"] = last_run.get("completed_at") or last_run.get("started_at")
            body["last_sync_status"] = last_run.get("status")
            if last_run.get("status") == "error":
                healthy = False
                body["status"] = "error"
                body["error"] = last_run.get("error_message")

        self._send_json(200 if healthy else 503, body)

    def _handle_status(self) -> None:
        if not _db_ref:
            self._send_json(503, {"error": "not ready"})
            return

        last_run = _db_ref.last_sync_run()
        recent = _db_ref.recent_sync_runs(10)
        pairs = _db_ref.pair_count()

        body = {
            "synced_events": pairs,
            "last_sync": None,
            "recent_runs": [
                {
                    "time": r.get("completed_at") or r.get("started_at"),
                    "status": r.get("status"),
                    "created": r.get("events_created", 0),
                    "updated": r.get("events_updated", 0),
                    "deleted": r.get("events_deleted", 0),
                    "error": r.get("error_message"),
                }
                for r in recent
            ],
        }

        if last_run:
            body["last_sync"] = last_run.get("completed_at") or last_run.get("started_at")
            body["last_sync_status"] = last_run.get("status")

        self._send_json(200, body)

    def _handle_events(self) -> None:
        if not _db_ref:
            self._send_json(503, [])
            return
        events = _db_ref.get_events()
        self._send_json(200, events)

    def _handle_page(self) -> None:
        data = EVENTS_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code: int, body: dict | list) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


def start_health_server(port: int, db: SyncDB, sync_interval: int = 300) -> None:
    global _db_ref, _sync_interval
    _db_ref = db
    _sync_interval = sync_interval

    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server started on port %d", port)
