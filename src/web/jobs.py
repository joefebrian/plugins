"""Simple in-memory background job tracker."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional


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

    def create(self, job_type: str) -> Job:
        job = Job(id=str(uuid.uuid4())[:8], type=job_type)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
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