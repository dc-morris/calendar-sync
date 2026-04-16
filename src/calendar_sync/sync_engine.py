import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from calendar_sync.config import Settings
from calendar_sync.db import SyncDB
from calendar_sync.icloud_client import ICloudClient
from calendar_sync.google_client import GoogleClient
from calendar_sync.models import NormalizedEvent

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(self, config: Settings, db: SyncDB):
        self.config = config
        self.db = db
        self.icloud = ICloudClient(
            username=config.icloud_username,
            app_password=config.icloud_app_password,
            caldav_url=config.icloud_caldav_url,
            calendar_name=config.icloud_calendar_name,
        )
        self.google = GoogleClient(
            client_id=config.google_client_id,
            client_secret=config.google_client_secret,
            refresh_token=config.google_refresh_token,
            calendar_id=config.google_calendar_id,
        )

    def run(self) -> None:
        run_id = self.db.start_sync_run()
        created = 0
        updated = 0
        deleted = 0

        try:
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=self.config.sync_lookback_days)
            end = now + timedelta(days=self.config.sync_lookahead_days)

            icloud_events = self.icloud.fetch_events(start, end)
            google_events = self.google.fetch_events(start, end)

            known_pairs = self.db.get_all_pairs()

            icloud_by_uid = {e.icloud_uid: e for e in icloud_events if e.icloud_uid}
            google_by_id = {e.google_event_id: e for e in google_events if e.google_event_id}

            known_icloud_uids = {p.icloud_uid for p in known_pairs}
            known_google_ids = {p.google_event_id for p in known_pairs}

            # --- Deletions and updates for existing pairs ---
            for pair in known_pairs:
                ic_exists = pair.icloud_uid in icloud_by_uid
                gc_exists = pair.google_event_id in google_by_id

                if not ic_exists and not gc_exists:
                    self.db.delete_pair(pair.id)
                    deleted += 1
                    continue

                if not ic_exists and gc_exists:
                    if not self.db.is_our_pending_change("icloud", pair.icloud_uid):
                        self.google.delete_event(pair.google_event_id)
                        self.db.delete_pair(pair.id)
                        deleted += 1
                    continue

                if ic_exists and not gc_exists:
                    if not self.db.is_our_pending_change("google", pair.google_event_id):
                        self.icloud.delete_event(pair.icloud_uid)
                        self.db.delete_pair(pair.id)
                        deleted += 1
                    continue

                # Both exist — check for updates
                ic_event = icloud_by_uid[pair.icloud_uid]
                gc_event = google_by_id[pair.google_event_id]
                ic_hash = ic_event.content_hash()
                gc_hash = gc_event.content_hash()

                if ic_hash == pair.content_hash and gc_hash == pair.content_hash:
                    continue  # No changes

                if ic_hash != pair.content_hash and gc_hash != pair.content_hash:
                    # Both changed — last modified wins
                    ic_mod = ic_event.last_modified or datetime.min.replace(tzinfo=timezone.utc)
                    gc_mod = gc_event.last_modified or datetime.min.replace(tzinfo=timezone.utc)
                    if ic_mod >= gc_mod:
                        updated += self._propagate_to_google(ic_event, pair)
                    else:
                        updated += self._propagate_to_icloud(gc_event, pair)
                elif ic_hash != pair.content_hash:
                    if not self.db.is_our_pending_change("icloud", pair.icloud_uid):
                        updated += self._propagate_to_google(ic_event, pair)
                elif gc_hash != pair.content_hash:
                    if not self.db.is_our_pending_change("google", pair.google_event_id):
                        updated += self._propagate_to_icloud(gc_event, pair)

            # --- New events from iCloud ---
            for uid, event in icloud_by_uid.items():
                if uid in known_icloud_uids:
                    continue
                google_id = self.google.find_existing_event(event.summary, event.start)
                if google_id:
                    # Link existing events instead of creating duplicate
                    self.db.create_pair(
                        uid, google_id, event.content_hash(), "icloud",
                        icloud_etag=event.icloud_etag,
                    )
                    logger.info("Linked existing pair: %s <-> %s", uid, google_id)
                else:
                    google_id = self.google.create_event(event)
                    self.db.create_pair(
                        uid, google_id, event.content_hash(), "icloud",
                        icloud_etag=event.icloud_etag,
                    )
                    self.db.record_pending_change(
                        google_id, "google", event.content_hash(),
                        ttl_seconds=self.config.sync_interval_seconds * 3,
                    )
                    created += 1

            # --- New events from Google ---
            for gid, event in google_by_id.items():
                if gid in known_google_ids:
                    continue
                # Check if we just created this from iCloud (via pending changes)
                if self.db.is_our_pending_change("google", gid):
                    continue
                icloud_uid = self.icloud.create_event(event)
                self.db.create_pair(
                    icloud_uid, gid, event.content_hash(), "google",
                    google_etag=event.google_etag,
                )
                self.db.record_pending_change(
                    icloud_uid, "icloud", event.content_hash(),
                    ttl_seconds=self.config.sync_interval_seconds * 3,
                )
                created += 1

            self.db.expire_pending_changes()
            self.db.complete_sync_run(run_id, "success", created, updated, deleted)
            logger.info(
                "Sync complete: %d created, %d updated, %d deleted",
                created, updated, deleted,
            )

        except Exception as e:
            logger.exception("Sync failed")
            self.db.complete_sync_run(run_id, "error", created, updated, deleted, str(e))
            self._send_alert(f"Calendar sync failed: {e}")
            raise

    def _propagate_to_google(self, ic_event: NormalizedEvent, pair: object) -> int:
        self.google.update_event(pair.google_event_id, ic_event)  # type: ignore[attr-defined]
        new_hash = ic_event.content_hash()
        self.db.update_pair(pair.id, new_hash, icloud_etag=ic_event.icloud_etag)  # type: ignore[attr-defined]
        self.db.record_pending_change(
            pair.google_event_id, "google", new_hash,  # type: ignore[attr-defined]
            ttl_seconds=self.config.sync_interval_seconds * 3,
        )
        logger.info("Propagated to Google: %s", ic_event.summary)
        return 1

    def _propagate_to_icloud(self, gc_event: NormalizedEvent, pair: object) -> int:
        self.icloud.update_event(pair.icloud_uid, gc_event)  # type: ignore[attr-defined]
        new_hash = gc_event.content_hash()
        self.db.update_pair(pair.id, new_hash, google_etag=gc_event.google_etag)  # type: ignore[attr-defined]
        self.db.record_pending_change(
            pair.icloud_uid, "icloud", new_hash,  # type: ignore[attr-defined]
            ttl_seconds=self.config.sync_interval_seconds * 3,
        )
        logger.info("Propagated to iCloud: %s", gc_event.summary)
        return 1

    def _send_alert(self, message: str) -> None:
        if not self.config.ntfy_url:
            return
        try:
            req = urllib.request.Request(
                self.config.ntfy_url,
                data=message.encode(),
                headers={"Title": "Calendar Sync Error"},
            )
            if self.config.ntfy_token:
                req.add_header("Authorization", f"Bearer {self.config.ntfy_token}")
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            logger.exception("Failed to send ntfy alert")
