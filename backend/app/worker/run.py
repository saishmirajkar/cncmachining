"""Worker entry point: RQ worker on the ``cnc`` queue plus the CAM engine heartbeat.

    python -m app.worker.run [--with-scheduler]
"""

from __future__ import annotations

import sys

from redis import Redis
from rq import Worker

from app.core.config import get_settings
from app.worker.heartbeat import start_publisher
from app.worker.queue import QUEUE_NAME


def main() -> None:
    start_publisher()
    Worker([QUEUE_NAME], connection=Redis.from_url(get_settings().redis_url)).work(
        with_scheduler="--with-scheduler" in sys.argv)


if __name__ == "__main__":
    main()
