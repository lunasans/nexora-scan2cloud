"""Upload-Warteschlange mit automatischen Wiederholungen.

Die Warteschlange wird in ``queue.json`` gespeichert. Was beim Beenden des
Programms noch nicht hochgeladen war, wird beim nächsten Start fortgesetzt.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from scan2cloud.cloud import CloudError

log = logging.getLogger(__name__)

MAX_DELAY = 30 * 60

WAITING = "wartet"
FAILED = "fehler"
DONE = "fertig"


@dataclass
class Job:
    local_path: str
    folder: str
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = WAITING
    attempts: int = 0
    next_try: float = 0.0
    last_error: str = ""
    remote_path: str = ""


def retry_delay(attempts: int) -> float:
    """30 s, 1 min, 2 min, 4 min … höchstens 30 min."""
    return min(30 * 2 ** max(attempts - 1, 0), MAX_DELAY)


class UploadQueue:
    def __init__(self, path: Path, target_factory: Callable[[], object],
                 keep_local: Callable[[], bool] = lambda: True,
                 on_change: Callable[[Job], None] = lambda job: None):
        self.path = path
        self.target_factory = target_factory
        self.keep_local = keep_local
        self.on_change = on_change
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.jobs: list[Job] = self._load()

    # --- Speicherung -------------------------------------------------------
    def _load(self) -> list[Job]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [Job(**j) for j in data]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError) as e:
            log.error("Warteschlange konnte nicht gelesen werden: %s", e)
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(j) for j in self.jobs if j.status != DONE],
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # --- Öffentliche Methoden ---------------------------------------------
    def add(self, local_path: Path, folder: str, name: str | None = None) -> Job:
        job = Job(str(local_path), folder, name or Path(local_path).name)
        with self._lock:
            self.jobs.append(job)
            self._save()
        self.on_change(job)
        self._wake.set()
        return job

    def retry(self, job_id: str | None = None) -> None:
        """Fehlgeschlagene (oder alle wartenden) Aufträge sofort erneut versuchen."""
        with self._lock:
            for j in self.jobs:
                if j.status != DONE and (job_id is None or j.id == job_id):
                    j.status, j.next_try = WAITING, 0.0
            self._save()
        self._wake.set()

    def remove(self, job_id: str) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.id != job_id]
            self._save()

    def pending(self) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.status != DONE]

    # --- Verarbeitung ------------------------------------------------------
    def process_due(self, now: float | None = None) -> int:
        """Lädt alle fälligen Aufträge hoch. Gibt die Anzahl der Versuche zurück."""
        now = time.time() if now is None else now
        with self._lock:
            due = [j for j in self.jobs if j.status == WAITING and j.next_try <= now]
        if not due:
            return 0
        try:
            target = self.target_factory()
        except CloudError as e:
            for j in due:
                self._failed(j, e, now)
            return len(due)
        for job in due:
            if self._stop.is_set():
                break
            self._upload(target, job, now)
        return len(due)

    def _upload(self, target, job: Job, now: float) -> None:
        local = Path(job.local_path)
        if not local.exists():
            self._failed(job, CloudError(f"Datei fehlt: {local}", retry=False), now)
            return
        try:
            job.remote_path = target.upload(local, job.folder, job.name)
        except CloudError as e:
            self._failed(job, e, now)
            return
        except Exception as e:  # unerwartet – trotzdem später erneut versuchen
            log.exception("Upload-Fehler")
            self._failed(job, CloudError(str(e)), now)
            return
        job.status, job.last_error = DONE, ""
        with self._lock:
            self.jobs = [j for j in self.jobs if j.id != job.id]
            self._save()
        if not self.keep_local():
            try:
                local.unlink()
            except OSError as e:
                log.warning("Lokale Datei konnte nicht gelöscht werden: %s", e)
        log.info("Hochgeladen: %s → %s", local.name, job.remote_path)
        self.on_change(job)

    def _failed(self, job: Job, err: CloudError, now: float) -> None:
        job.attempts += 1
        job.last_error = str(err)
        if err.retry:
            job.next_try = now + retry_delay(job.attempts)
        else:
            job.status = FAILED
        with self._lock:
            self._save()
        log.warning("Upload von %s fehlgeschlagen: %s", job.name, err)
        self.on_change(job)

    # --- Hintergrund-Thread ------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="uploads", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_due()
            except Exception:
                log.exception("Fehler in der Upload-Warteschlange")
            self._wake.wait(timeout=10)
            self._wake.clear()
