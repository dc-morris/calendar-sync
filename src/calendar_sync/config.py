from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # iCloud CalDAV
    icloud_username: str
    icloud_app_password: str
    icloud_caldav_url: str = "https://caldav.icloud.com"
    icloud_calendar_name: str = "Family"

    # Google Calendar
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    google_calendar_id: str

    # Sync settings
    sync_interval_seconds: int = 300
    sync_lookback_days: int = 90
    sync_lookahead_days: int = 365

    # Storage
    db_path: str = "/data/calendar_sync.db"

    # Health
    health_port: int = 8080

    # Logging
    log_level: str = "INFO"

    # Optional ntfy alerts
    ntfy_url: str | None = None
    ntfy_token: str | None = None
