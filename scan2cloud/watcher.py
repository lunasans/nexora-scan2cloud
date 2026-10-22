"""Überwacht einen Ordner (z. B. den Speicherort von CaptureOnTouch Lite) und
meldet neue Scans, sobald sie fertig geschrieben sind."""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Callable

from scan2cloud.pipeline import IMAGE_EXTS, PDF_EXTS

log = logging.getLogger(__name__)

DONE_SUBDIR = "verarbeitet"
ERROR_SUBDIR = "fehler"


class FolderWatcher:
    def __init__(self, folder: Path, handler: Callable[[Path], None],
                 interval: float = 2.0):
        self.folder = Path(folder)
        self.handler = handler
        self.interval = interval
        self._sizes: dict[Path, tuple[int, float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _candidates(self) -> list[Path]:
        exts = IMAGE_EXTS | PDF_EXTS
        return sorted(p for p in self.folder.iterdir()
                      if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("~"))

    def poll(self) -> list[Path]:
        """Ein Durchlauf. Eine Datei gilt als fertig, wenn Größe und Änderungszeit
        bei zwei Durchläufen hintereinander gleich geblieben sind."""
        ready = []
        current = {}
        for p in self._candidates():
            try:
                st = p.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            if st.st_size > 0 and self._sizes.get(p) == sig:
                ready.append(p)
            else:
                current[p] = sig
        self._sizes = current
        for p in ready:
            self._handle(p)
        return ready

    def _handle(self, path: Path) -> None:
        try:
            self.handler(path)
            self._move(path, DONE_SUBDIR)
        except Exception:
            log.exception("Datei %s konnte nicht verarbeitet werden", path.name)
            self._move(path, ERROR_SUBDIR)

    def _move(self, path: Path, subdir: str) -> None:
        dest_dir = self.folder / subdir
        dest_dir.mkdir(exist_ok=True)
        dest, n = dest_dir / path.name, 1
        while dest.exists():
            n += 1
            dest = dest_dir / f"{path.stem}_{n}{path.suffix}"
        try:
            shutil.move(str(path), dest)
        except OSError as e:
            log.error("Datei %s konnte nicht verschoben werden: %s", path.name, e)

    def start(self) -> None:
        if not self.folder.is_dir():
            raise FileNotFoundError(f"Ordner {self.folder} existiert nicht")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except Exception:
                log.exception("Fehler bei der Ordnerüberwachung")
