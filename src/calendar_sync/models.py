from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json


@dataclass
class NormalizedEvent:
    summary: str
    start: datetime | date
    end: datetime | date
    is_all_day: bool
    description: str | None = None
    location: str | None = None
    recurrence_rule: str | None = None
    status: str = "CONFIRMED"
    last_modified: datetime | None = None

    # Source identifiers (populated by the respective client)
    icloud_uid: str | None = None
    icloud_etag: str | None = None
    google_event_id: str | None = None
    google_etag: str | None = None

    def content_hash(self) -> str:
        """Hash of the fields that should trigger a sync when changed."""
        fields = {
            "summary": self.summary,
            "description": self.description or "",
            "location": self.location or "",
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "is_all_day": self.is_all_day,
            "recurrence_rule": self.recurrence_rule or "",
            "status": self.status,
        }
        raw = json.dumps(fields, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class SyncPair:
    id: int
    icloud_uid: str
    google_event_id: str
    icloud_etag: str | None
    google_etag: str | None
    content_hash: str
    last_modified: str
    last_synced_at: str
    source_origin: str | None
