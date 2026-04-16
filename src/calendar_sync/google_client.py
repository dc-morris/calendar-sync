import logging
from datetime import date, datetime, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from calendar_sync.models import NormalizedEvent

logger = logging.getLogger(__name__)


class GoogleClient:
    def __init__(
        self, client_id: str, client_secret: str, refresh_token: str, calendar_id: str
    ):
        self.calendar_id = calendar_id
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        self.service = build("calendar", "v3", credentials=creds)

    def fetch_events(self, start: datetime, end: datetime) -> list[NormalizedEvent]:
        events = []
        page_token = None
        while True:
            resp = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=False,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in resp.get("items", []):
                try:
                    event = self._parse_event(item)
                    if event:
                        events.append(event)
                except Exception:
                    logger.exception("Failed to parse Google event: %s", item.get("id"))

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        logger.info("Fetched %d events from Google Calendar", len(events))
        return events

    def _parse_event(self, item: dict) -> NormalizedEvent | None:
        if item.get("status") == "cancelled":
            return None

        summary = item.get("summary", "")
        description = item.get("description")
        location = item.get("location")

        start_raw = item.get("start", {})
        end_raw = item.get("end", {})

        if "date" in start_raw:
            is_all_day = True
            start_val = date.fromisoformat(start_raw["date"])
            end_val = date.fromisoformat(end_raw.get("date", start_raw["date"]))
        elif "dateTime" in start_raw:
            is_all_day = False
            start_val = datetime.fromisoformat(start_raw["dateTime"])
            end_val = datetime.fromisoformat(end_raw.get("dateTime", start_raw["dateTime"]))
        else:
            return None

        recurrence = item.get("recurrence")
        recurrence_rule = None
        if recurrence:
            for rule in recurrence:
                if rule.startswith("RRULE:"):
                    recurrence_rule = rule[6:]
                    break

        updated = item.get("updated")
        last_modified = None
        if updated:
            last_modified = datetime.fromisoformat(updated)
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)

        status = item.get("status", "confirmed").upper()

        return NormalizedEvent(
            summary=summary,
            description=description,
            location=location,
            start=start_val,
            end=end_val,
            is_all_day=is_all_day,
            recurrence_rule=recurrence_rule,
            status=status,
            last_modified=last_modified,
            google_event_id=item["id"],
            google_etag=item.get("etag"),
        )

    def create_event(self, event: NormalizedEvent) -> str:
        """Create an event in Google Calendar. Returns the event ID."""
        body = self._build_event_body(event)
        result = self.service.events().insert(
            calendarId=self.calendar_id, body=body
        ).execute()
        event_id = result["id"]
        logger.info("Created Google event: %s (%s)", event.summary, event_id)
        return event_id

    def update_event(self, google_event_id: str, event: NormalizedEvent) -> None:
        """Update an existing Google Calendar event."""
        body = self._build_event_body(event)
        self.service.events().update(
            calendarId=self.calendar_id, eventId=google_event_id, body=body
        ).execute()
        logger.info("Updated Google event: %s (%s)", event.summary, google_event_id)

    def delete_event(self, google_event_id: str) -> None:
        """Delete an event from Google Calendar."""
        try:
            self.service.events().delete(
                calendarId=self.calendar_id, eventId=google_event_id
            ).execute()
            logger.info("Deleted Google event: %s", google_event_id)
        except Exception as e:
            if hasattr(e, "resp") and e.resp.status == 410:  # type: ignore[union-attr]
                logger.warning("Google event already deleted: %s", google_event_id)
            else:
                raise

    def _build_event_body(self, event: NormalizedEvent) -> dict:
        body: dict = {"summary": event.summary}

        if event.description:
            body["description"] = event.description
        if event.location:
            body["location"] = event.location

        if event.is_all_day:
            body["start"] = {"date": event.start.isoformat()}
            body["end"] = {"date": event.end.isoformat()}
        else:
            start = event.start
            end = event.end
            if isinstance(start, datetime) and start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if isinstance(end, datetime) and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            body["start"] = {"dateTime": start.isoformat()}
            body["end"] = {"dateTime": end.isoformat()}

        if event.recurrence_rule:
            body["recurrence"] = [f"RRULE:{event.recurrence_rule}"]

        body["status"] = event.status.lower()

        return body

    def find_existing_event(self, summary: str, start: datetime | date) -> str | None:
        """Find an event by summary and start time (for duplicate detection on initial sync)."""
        if isinstance(start, date) and not isinstance(start, datetime):
            time_min = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
            time_max = datetime(start.year, start.month, start.day, 23, 59, 59, tzinfo=timezone.utc)
        else:
            time_min = start.replace(second=0, microsecond=0) if isinstance(start, datetime) else start  # type: ignore[union-attr]
            time_max = time_min

        resp = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                q=summary,
                maxResults=10,
            )
            .execute()
        )
        for item in resp.get("items", []):
            if item.get("summary", "").strip() == summary.strip():
                return item["id"]
        return None
