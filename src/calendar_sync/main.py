import logging
import time
import sys
from calendar_sync.config import Settings
from calendar_sync.db import SyncDB
from calendar_sync.health import start_health_server
from calendar_sync.sync_engine import SyncEngine

logger = logging.getLogger("calendar_sync")


def main() -> None:
    config = Settings()  # type: ignore[call-arg]

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info("Starting calendar-sync (interval: %ds)", config.sync_interval_seconds)

    db = SyncDB(config.db_path)
    start_health_server(config.health_port, db, config.sync_interval_seconds)

    engine = SyncEngine(config, db)

    while True:
        try:
            engine.run()
        except Exception:
            logger.exception("Sync cycle failed, will retry next interval")
        time.sleep(config.sync_interval_seconds)


if __name__ == "__main__":
    main()
