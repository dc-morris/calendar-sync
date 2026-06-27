import logging
from datetime import date, datetime, timezone
from icalendar import Calendar, Event as ICalEvent
import caldav
from calendar_sync.models import NormalizedEvent

logger = logging.getLogger(__name__)


class ICloudClient:
    def __init__(self, username: str, app_password: str, caldav_url: str, calendar_name: str):
        self.calendar_name = calendar_name
        self._username = username
        self._app_password = app_password
        self._caldav_url = caldav_url
        self._client: caldav.DAVClient | None = None
        self._calendar: caldav.Calendar | None = None

    def _get_client(self) -> caldav.DAVClient:
        if self._client is None:
            self._client = caldav.DAVClient(
                url=self._caldav_url,
                username=self._username,
                password=self._app_password,
                timeout=60,
            )
        return self._client

    def _reset_connection(self) -> None:
        """Discard both the cached calendar and the underlying HTTP session."""
        self._calendar = None
        self._client = None

    def _get_calendar(self) -> caldav.Calendar:
        if self._calendar is None:
            try:
                principal = self._get_client().principal()
                calendars = principal.calendars()
            except Exception:
                self._reset_connection()
                raise
            for cal in calendars:
                if cal.name == self.calendar_name:
                    self._calendar = cal
                    break
            if self._calendar is None:
                available = [c.name for c in calendars]
                raise ValueError(
                    f"Calendar '{self.calendar_name}' not found. Available: {available}"
                )
            logger.info("Connected to iCloud calendar: %s", self.calendar_name)
        return self._calendar

    def fetch_events(self, start: datetime, end: datetime) -> list[NormalizedEvent]:
        cal = self._get_calendar()
        try:
            results = cal.date_search(start=start, end=end, expand=False)
        except Exception:
            # Drop both the calendar and HTTP session so the next cycle gets a
            # fresh connection. iCloud can silently invalidate a calendar URL or
            # leave the connection pool in a broken state after a timeout.
            self._reset_connection()
            raise
        events = []
        for item in results:
            try:
                event = self._parse_event(item)
                if event:
                    events.append(event)
            except Exception:
                logger.exception("Failed to parse iCloud event: %s", item.url)
        logger.info("Fetched %d events from iCloud", len(events))
        return events

    def _parse_event(self, item: caldav.CalendarObjectResource) -> NormalizedEvent | None:
        ical = Calendar.from_ical(item.data)
        for component in ical.walk():
            if component.name != "VEVENT":
                continue

            summary = str(component.get("SUMMARY", ""))
            description = component.get("DESCRIPTION")
            if description:
                description = str(description)
            location = component.get("LOCATION")
            if location:
                location = str(location)

            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if not dtstart:
                return None

            start_val = dtstart.dt
            end_val = dtend.dt if dtend else start_val
            is_all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)

            rrule = component.get("RRULE")
            recurrence_rule = None
            if rrule:
                recurrence_rule = rrule.to_ical().decode()

            last_modified = component.get("LAST-MODIFIED")
            lm_dt = None
            if last_modified:
                lm_dt = last_modified.dt
                if lm_dt.tzinfo is None:
                    lm_dt = lm_dt.replace(tzinfo=timezone.utc)

            status = str(component.get("STATUS", "CONFIRMED"))
            uid = str(component.get("UID", ""))

            return NormalizedEvent(
                summary=summary,
                description=description,
                location=location,
                start=start_val,
                end=end_val,
                is_all_day=is_all_day,
                recurrence_rule=recurrence_rule,
                status=status,
                last_modified=lm_dt,
                icloud_uid=uid,
                icloud_etag=item.props.get("{DAV:}getetag") if hasattr(item, "props") else None,
            )
        return None

    def create_event(self, event: NormalizedEvent) -> str:
        """Create an event in iCloud. Returns the UID."""
        import uuid
        uid = str(uuid.uuid4())
        vcal = self._build_vcalendar(event, uid)
        cal = self._get_calendar()
        cal.save_event(vcal)
        logger.info("Created iCloud event: %s (%s)", event.summary, uid)
        return uid

    def update_event(self, icloud_uid: str, event: NormalizedEvent) -> None:
        """Update an existing iCloud event."""
        cal = self._get_calendar()
        vcal = self._build_vcalendar(event, icloud_uid)
        try:
            existing = cal.event_by_uid(icloud_uid)
            existing.data = vcal
            existing.save()
            logger.info("Updated iCloud event: %s (%s)", event.summary, icloud_uid)
        except (caldav.error.NotFoundError, caldav.error.ReportError) as e:
            # UID lookup can fail with 412 Precondition Failed on iCloud —
            # fall back to saving directly (CalDAV PUT with the same UID overwrites)
            logger.warning("iCloud event_by_uid failed for %s (%s), saving directly", icloud_uid, e)
            cal.save_event(vcal)
            logger.info("Saved iCloud event directly: %s (%s)", event.summary, icloud_uid)

    def delete_event(self, icloud_uid: str) -> None:
        """Delete an event from iCloud."""
        cal = self._get_calendar()
        try:
            existing = cal.event_by_uid(icloud_uid)
            existing.delete()
            logger.info("Deleted iCloud event: %s", icloud_uid)
        except (caldav.error.NotFoundError, caldav.error.ReportError):
            logger.warning("iCloud event not found for deletion: %s", icloud_uid)

    def _build_vcalendar(self, event: NormalizedEvent, uid: str) -> str:
        cal = Calendar()
        cal.add("prodid", "-//calendar-sync//EN")
        cal.add("version", "2.0")

        vevent = ICalEvent()
        vevent.add("uid", uid)
        vevent.add("summary", event.summary)
        if event.description:
            vevent.add("description", event.description)
        if event.location:
            vevent.add("location", event.location)

        if event.is_all_day:
            vevent.add("dtstart", event.start)
            vevent.add("dtend", event.end)
        else:
            start = event.start
            end = event.end
            if isinstance(start, datetime) and start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if isinstance(end, datetime) and end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            vevent.add("dtstart", start)
            vevent.add("dtend", end)

        if event.recurrence_rule:
            from icalendar import vRecur
            vevent.add("rrule", vRecur.from_ical(event.recurrence_rule))

        vevent.add("status", event.status)
        now = datetime.now(timezone.utc)
        vevent.add("dtstamp", now)
        vevent.add("last-modified", now)

        cal.add_component(vevent)
        return cal.to_ical().decode()
