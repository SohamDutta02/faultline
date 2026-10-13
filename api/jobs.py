"""One background job at a time, with an append-only event log readers can tail.

The toolserver world and the proxy's fault config are process-wide, so two
trials can't run side by side; a second job is refused while one is running.
"""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field


class Busy(Exception):
    def __init__(self, job: "Job"):
        super().__init__(f"{job.kind} {job.title} is running")
        self.job = job


@dataclass
class Job:
    kind: str
    title: str
    params: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    progress: dict | None = None
    result: dict | None = None
    events: list = field(default_factory=list)
    stop_requested: threading.Event = field(default_factory=threading.Event)

    def emit(self, type_: str, **data):
        self.events.append({"seq": len(self.events), "type": type_, "t": time.time(), **data})

    def summary(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "progress": self.progress,
            "result": self.result,
        }


class JobManager:
    keep = 50

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None

    def running(self) -> Job | None:
        job = self.current
        return job if job is not None and job.status == "running" else None

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def start(self, kind: str, title: str, params: dict, target: Callable[[Job], None]) -> Job:
        with self.lock:
            if self.running():
                raise Busy(self.current)
            job = Job(kind, title, params)
            self.jobs[job.id] = job
            self.current = job
            for old in list(self.jobs)[: -self.keep]:
                del self.jobs[old]
        threading.Thread(target=self._run, args=(job, target), daemon=True, name=f"job-{job.id}").start()
        return job

    def _run(self, job: Job, target: Callable[[Job], None]):
        try:
            target(job)
            if job.status == "running":
                job.status = "done"
        except Exception as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.emit("error", message=job.error)
        finally:
            job.finished_at = time.time()
            job.emit("end", status=job.status)
