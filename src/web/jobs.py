"""Simple in-memory background job tracker (bounded memory)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

# Keep Railway RAM low: drop finished jobs after TTL / hard cap
_MAX_JOBS = 40
_JOB_TTL = timedelta(hours=1)


@dataclass
class Job:
    id: str
    type: str
    status: str = "pending"  # pending | running | done | error
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def _prune_locked(self) -> None:
        now = datetime.utcnow()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.status in ("done", "error")
            and job.finished_at
            and (now - job.finished_at) > _JOB_TTL
        ]
        for jid in expired:
            del self._jobs[jid]

        if len(self._jobs) <= _MAX_JOBS:
            return
        # Drop oldest finished first, then oldest overall
        finished = sorted(
            (
                (j.finished_at or j.created_at, jid)
                for jid, j in self._jobs.items()
                if j.status in ("done", "error")
            ),
            key=lambda x: x[0],
        )
        for _, jid in finished:
            if len(self._jobs) <= _MAX_JOBS:
                break
            self._jobs.pop(jid, None)

    def create(self, job_type: str) -> Job:
        job = Job(id=str(uuid.uuid4())[:8], type=job_type)
        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    def run(self, job: Job, fn: Callable[[], Any], message: str = "Processing..."):
        def _worker():
            job.status = "running"
            job.message = message
            try:
                result = fn()
                job.result = result if isinstance(result, dict) else {"data": result}
                job.status = "done"
                job.message = "Selesai"
            except Exception as e:
                job.status = "error"
                job.message = str(e)
            finally:
                job.finished_at = datetime.utcnow()
                with self._lock:
                    self._prune_locked()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def to_dict(self, job: Job) -> dict:
        return {
            "id": job.id,
            "type": job.type,
            "status": job.status,
            "message": job.message,
            "result": job.result,
            "created_at": job.created_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }


job_manager = JobManager()