import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from calendar_sync.db import SyncDB

logger = logging.getLogger(__name__)

_db_ref: SyncDB | None = None
_sync_interval: int = 300


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

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

        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress request logs


def start_health_server(port: int, db: SyncDB, sync_interval: int = 300) -> None:
    global _db_ref, _sync_interval
    _db_ref = db
    _sync_interval = sync_interval

    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server started on port %d", port)
