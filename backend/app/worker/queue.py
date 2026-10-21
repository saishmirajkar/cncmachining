"""Background job dispatch.

* ``rq`` – Redis Queue; a separate ``rq worker cnc`` process executes tasks (production).
* ``thread`` – in-process thread pool (single-process development without a worker).
* ``inline`` – synchronous execution (tests).

Tasks are idempotent with respect to their database row: each task re-reads the row, exits if it is no longer
in the expected state, and never creates a second job for the same idempotency key.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_executor: ThreadPoolExecutor | None = None
QUEUE_NAME = "cnc"


def _tasks():
    from app.worker import tasks

    return tasks


def enqueue(task_name: str, *args, job_id: str | None = None, timeout_s: int = 600) -> str:
    s = get_settings()
    backend = s.queue_backend
    if backend == "inline":
        getattr(_tasks(), task_name)(*args)
        return job_id or "inline"
    if backend == "thread":
        global _executor
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cnc-task")
        fn = getattr(_tasks(), task_name)

        def run():
            try:
                fn(*args)
            except Exception:  # pragma: no cover - logged
                log.exception("task_failed", task=task_name)

        _executor.submit(run)
        return job_id or "thread"
    if backend == "rq":
        from redis import Redis
        from rq import Queue, Retry

        q = Queue(QUEUE_NAME, connection=Redis.from_url(s.redis_url))
        job = q.enqueue(f"app.worker.tasks.{task_name}", *args, job_id=job_id, job_timeout=timeout_s + 60,
                        retry=Retry(max=1), result_ttl=86400, failure_ttl=7 * 86400)
        return job.id
    raise RuntimeError(f"unknown queue backend {backend}")
