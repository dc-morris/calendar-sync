import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from calendar_sync.db import SyncDB
from calendar_sync.models import NormalizedEvent
from calendar_sync.sync_engine import SyncEngine


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = SyncDB(path)
    yield database
    database.conn.close()
    os.unlink(path)


@pytest.fixture
def config():
    return MagicMock(
        icloud_username="test@icloud.com",
        icloud_app_password="xxxx",
        icloud_caldav_url="https://caldav.icloud.com",
        icloud_calendar_name="Family",
        google_client_id="test-id",
        google_client_secret="test-secret",
        google_refresh_token="test-token",
        google_calendar_id="test@group.calendar.google.com",
        sync_interval_seconds=300,
        sync_lookback_days=90,
        sync_lookahead_days=365,
        ntfy_url=None,
        ntfy_token=None,
    )


def make_event(
    summary="Test Event",
    start=None,
    end=None,
    icloud_uid=None,
    google_event_id=None,
    last_modified=None,
):
    now = datetime.now(timezone.utc)
    return NormalizedEvent(
        summary=summary,
        start=start or now + timedelta(hours=1),
        end=end or now + timedelta(hours=2),
        is_all_day=False,
        last_modified=last_modified or now,
        icloud_uid=icloud_uid,
        google_event_id=google_event_id,
    )


@patch("calendar_sync.sync_engine.GoogleClient")
@patch("calendar_sync.sync_engine.ICloudClient")
def test_new_icloud_event_syncs_to_google(mock_icloud_cls, mock_google_cls, db, config):
    engine = SyncEngine(config, db)

    ic_event = make_event(summary="Dinner", icloud_uid="ic-123")
    engine.icloud.fetch_events = MagicMock(return_value=[ic_event])
    engine.google.fetch_events = MagicMock(return_value=[])
    engine.google.find_existing_event = MagicMock(return_value=None)
    engine.google.create_event = MagicMock(return_value="gc-456")

    engine.run()

    engine.google.create_event.assert_called_once()
    pairs = db.get_all_pairs()
    assert len(pairs) == 1
    assert pairs[0].icloud_uid == "ic-123"
    assert pairs[0].google_event_id == "gc-456"


@patch("calendar_sync.sync_engine.GoogleClient")
@patch("calendar_sync.sync_engine.ICloudClient")
def test_new_google_event_syncs_to_icloud(mock_icloud_cls, mock_google_cls, db, config):
    engine = SyncEngine(config, db)

    gc_event = make_event(summary="Meeting", google_event_id="gc-789")
    engine.icloud.fetch_events = MagicMock(return_value=[])
    engine.google.fetch_events = MagicMock(return_value=[gc_event])
    engine.icloud.create_event = MagicMock(return_value="ic-012")

    engine.run()

    engine.icloud.create_event.assert_called_once()
    pairs = db.get_all_pairs()
    assert len(pairs) == 1
    assert pairs[0].icloud_uid == "ic-012"
    assert pairs[0].google_event_id == "gc-789"


@patch("calendar_sync.sync_engine.GoogleClient")
@patch("calendar_sync.sync_engine.ICloudClient")
def test_deleted_from_icloud_deletes_from_google(mock_icloud_cls, mock_google_cls, db, config):
    engine = SyncEngine(config, db)

    # Pre-existing pair
    db.create_pair("ic-100", "gc-200", "hash1", "icloud")

    gc_event = make_event(summary="Old Event", google_event_id="gc-200")
    engine.icloud.fetch_events = MagicMock(return_value=[])  # ic-100 gone
    engine.google.fetch_events = MagicMock(return_value=[gc_event])
    engine.google.delete_event = MagicMock()

    engine.run()

    engine.google.delete_event.assert_called_once_with("gc-200")
    assert len(db.get_all_pairs()) == 0


@patch("calendar_sync.sync_engine.GoogleClient")
@patch("calendar_sync.sync_engine.ICloudClient")
def test_update_propagates_icloud_to_google(mock_icloud_cls, mock_google_cls, db, config):
    engine = SyncEngine(config, db)

    # Use fixed timestamps so hashes are consistent
    fixed_start = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    fixed_end = datetime(2025, 6, 1, 13, 0, tzinfo=timezone.utc)

    original = make_event(
        summary="Original", icloud_uid="ic-1", google_event_id="gc-1",
        start=fixed_start, end=fixed_end,
    )
    original_hash = original.content_hash()
    db.create_pair("ic-1", "gc-1", original_hash, "icloud")

    # iCloud event updated (different summary)
    updated_ic = make_event(
        summary="Updated Title", icloud_uid="ic-1",
        start=fixed_start, end=fixed_end,
    )
    # Google still has original
    original_gc = make_event(
        summary="Original", google_event_id="gc-1",
        start=fixed_start, end=fixed_end,
    )

    engine.icloud.fetch_events = MagicMock(return_value=[updated_ic])
    engine.google.fetch_events = MagicMock(return_value=[original_gc])
    engine.google.update_event = MagicMock()

    engine.run()

    engine.google.update_event.assert_called_once_with("gc-1", updated_ic)


def test_content_hash_changes_on_field_update():
    e1 = make_event(summary="Dinner")
    e2 = make_event(summary="Lunch")
    assert e1.content_hash() != e2.content_hash()


def test_content_hash_stable_for_same_fields():
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    e1 = make_event(summary="Test", start=now, end=now + timedelta(hours=1))
    e2 = make_event(summary="Test", start=now, end=now + timedelta(hours=1))
    assert e1.content_hash() == e2.content_hash()


def test_content_hash_stable_across_timezones():
    """Same moment in different timezone representations should hash the same."""
    utc_start = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
    bst_start = datetime(2025, 6, 1, 11, 0, tzinfo=timezone(timedelta(hours=1)))
    utc_end = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    bst_end = datetime(2025, 6, 1, 13, 0, tzinfo=timezone(timedelta(hours=1)))

    e1 = make_event(summary="Meeting", start=utc_start, end=utc_end)
    e2 = make_event(summary="Meeting", start=bst_start, end=bst_end)
    assert e1.content_hash() == e2.content_hash()


def test_db_pending_changes(db):
    db.record_pending_change("gc-1", "google", "hash1", ttl_seconds=900)
    assert db.is_our_pending_change("google", "gc-1")
    assert not db.is_our_pending_change("icloud", "gc-1")
    assert not db.is_our_pending_change("google", "gc-999")
