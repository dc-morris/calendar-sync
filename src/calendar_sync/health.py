import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from calendar_sync.db import SyncDB

logger = logging.getLogger(__name__)

_db_ref: SyncDB | None = None
_sync_interval: int = 300


class HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                self._handle_health()
            elif self.path == "/status":
                self._handle_status()
            else:
                self.send_response(404)
                self.end_headers()
        except Exception:
            logger.exception("Health handler error")
            self.send_response(500)
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

    def _send_json(self, code: int, body: dict) -> None:
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

    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server started on port %d", port)
