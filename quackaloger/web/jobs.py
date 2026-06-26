"""Single-worker job queue for the web UI.

The organizer is synchronous and mutates the filesystem, so jobs run strictly
one at a time in a background daemon thread (this also respects Audible/TMDB
rate limits). While a job runs, the worker installs the UI event sink so log
lines and progress stream live to the browser over SSE.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Callable, Optional

from quackaloger.ui import ui

_MAX_LOG_LINES = 1000


class Job:
    def __init__(self, job_id: str, kind: str, library_id: str, label: str):
        self.id = job_id
        self.kind = kind            # "scan" | "execute"
        self.library_id = library_id
        self.label = label
        self.status = "queued"      # queued | running | done | error
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.log: list = []         # [{seq, level, text}]
        self.progress: Optional[dict] = None
        self.plan_id: Optional[str] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self._seq = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "library_id": self.library_id,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "plan_id": self.plan_id,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
        }


class JobManager:
    def __init__(self):
        self._q: "queue.Queue" = queue.Queue()
        self._jobs: dict = {}
        self._lock = threading.Lock()
        self._active_library: Optional[str] = None
        self._worker = threading.Thread(target=self._run, name="quack-worker", daemon=True)
        self._started = False

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._worker.start()

    def submit(
        self,
        kind: str,
        library_id: str,
        label: str,
        fn: Callable[["Job"], None],
    ) -> Job:
        """Queue *fn* (called as ``fn(job)``) and return the Job handle."""
        job = Job(uuid.uuid4().hex[:12], kind, library_id, label)
        with self._lock:
            self._jobs[job.id] = job
        self._q.put((job, fn))
        return job

    # -- queries (used by routes + watcher) -----------------------------

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def recent_jobs(self, limit: int = 25) -> list:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def library_busy(self, library_id: str) -> bool:
        """True if a job for this library is running or still queued."""
        with self._lock:
            if self._active_library == library_id:
                return True
            for job in self._jobs.values():
                if job.library_id == library_id and job.status in ("queued", "running"):
                    return True
        return False

    def snapshot(self, job_id: str, since_seq: int = 0) -> Optional[dict]:
        """Status + any log lines newer than *since_seq* (for SSE polling)."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        new = [e for e in job.log if e["seq"] > since_seq]
        last_seq = job.log[-1]["seq"] if job.log else since_seq
        return {
            "status": job.status,
            "progress": job.progress,
            "plan_id": job.plan_id,
            "result": job.result,
            "error": job.error,
            "log": new,
            "last_seq": last_seq,
        }

    # -- internals ------------------------------------------------------

    def _append(self, job: Job, level: str, text: str) -> None:
        job._seq += 1
        job.log.append({"seq": job._seq, "level": level, "text": text})
        if len(job.log) > _MAX_LOG_LINES:
            del job.log[: len(job.log) - _MAX_LOG_LINES]

    def _sink_for(self, job: Job) -> Callable[[dict], None]:
        def sink(event: dict) -> None:
            etype = event.get("type")
            if etype == "progress":
                job.progress = {
                    "desc": event.get("desc"),
                    "completed": event.get("completed"),
                    "total": event.get("total"),
                }
            elif etype == "phase":
                self._append(job, "phase", f"Phase {event.get('n')}: {event.get('title')}")
            else:
                self._append(job, event.get("level", "info"), event.get("text", ""))
        return sink

    def _run(self) -> None:
        while True:
            job, fn = self._q.get()
            with self._lock:
                self._active_library = job.library_id
            job.status = "running"
            job.started_at = time.time()
            ui.set_sink(self._sink_for(job))
            try:
                fn(job)
                if job.status == "running":
                    job.status = "done"
            except Exception as e:  # noqa: BLE001 - surface any failure to the UI
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                self._append(job, "error", job.error)
            finally:
                ui.clear_sink()
                job.finished_at = time.time()
                with self._lock:
                    self._active_library = None
                self._q.task_done()


# Module-level singleton
manager = JobManager()
