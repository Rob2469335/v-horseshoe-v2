from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from infrastructure.config.settings import get_settings
from infrastructure.runtime.feature_flags import get_feature_flags


JobFn = Callable[[], None]


@dataclass
class RegisteredJob:
    name: str
    interval_seconds: int
    fn: JobFn
    last_started_at: float | None = None
    last_finished_at: float | None = None
    last_error: str | None = None


@dataclass
class BackgroundJobRunner:
    jobs: list[RegisteredJob] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)

    def register(self, name: str, interval_seconds: int, fn: JobFn) -> None:
        self.jobs.append(RegisteredJob(name=name, interval_seconds=interval_seconds, fn=fn))

    def start(self) -> None:
        settings = get_settings()
        flags = get_feature_flags()

        if not settings.jobs_enabled or not flags.background_jobs:
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="phase6-background-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def snapshot(self) -> list[dict]:
        return [
            {
                "name": job.name,
                "interval_seconds": job.interval_seconds,
                "last_started_at": job.last_started_at,
                "last_finished_at": job.last_finished_at,
                "last_error": job.last_error,
            }
            for job in self.jobs
        ]

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.time()
            for job in self.jobs:
                should_run = job.last_started_at is None or (now - job.last_started_at) >= job.interval_seconds
                if not should_run:
                    continue

                job.last_started_at = time.time()
                try:
                    job.fn()
                    job.last_error = None
                except Exception as exc:
                    job.last_error = str(exc)
                finally:
                    job.last_finished_at = time.time()

            time.sleep(1)



def register_default_jobs(runner: BackgroundJobRunner) -> BackgroundJobRunner:
    settings = get_settings()

    def health_snapshot_job() -> None:
        return None

    def cache_cleanup_job() -> None:
        return None

    runner.register("health_snapshot", settings.jobs_poll_seconds, health_snapshot_job)
    runner.register("cache_cleanup", max(settings.jobs_poll_seconds, 60), cache_cleanup_job)
    return runner

