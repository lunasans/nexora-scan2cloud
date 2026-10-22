"""Verarbeitungsablauf: Seiten → (leere entfernen) → Datei → Zielordner → Upload.

Hier wird in Version 2 die Texterkennung und das automatische Einsortieren
(Regeln, lokale KI über LM Studio) eingehängt – siehe ``choose_folder``.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageSequence

from scan2cloud import imaging
from scan2cloud.config import Profile, Settings
from scan2cloud.uploads import UploadQueue

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_EXTS = {".pdf"}


@dataclass
class Result:
    files: list[Path]
    folder: str
    removed_blank: int = 0


def choose_folder(profile: Profile, files: list[Path], when: datetime) -> str:
    """Ermittelt den Cloud-Zielordner. Version 1: Ordner aus dem Profil."""
    return imaging.remote_folder_for(profile, when)


def process_pages(pages: list[Image.Image], profile: Profile, settings: Settings,
                  queue: UploadQueue, when: datetime | None = None) -> Result:
    when = when or datetime.now()
    removed = 0
    if profile.remove_blank:
        kept = imaging.remove_blank_pages(pages, profile.blank_threshold)
        removed = len(pages) - len(kept)
        pages = kept
    files = imaging.write_output(pages, profile, Path(settings.output_dir), when)
    folder = choose_folder(profile, files, when)
    for f in files:
        queue.add(f, folder)
    return Result(files, folder, removed)


def load_image_file(path: Path) -> list[Image.Image]:
    with Image.open(path) as im:
        pages = []
        for frame in ImageSequence.Iterator(im):
            frame.load()
            pages.append(frame.copy())
        return pages


def process_file(path: Path, profile: Profile, settings: Settings,
                 queue: UploadQueue) -> Result:
    """Verarbeitet eine Datei aus dem überwachten Ordner."""
    when = datetime.fromtimestamp(path.stat().st_mtime)
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return process_pages(load_image_file(path), profile, settings, queue, when)
    if ext in PDF_EXTS:
        out_dir = Path(settings.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = imaging.unique_base_name(profile, out_dir, ".pdf", when)
        dest = out_dir / f"{base}.pdf"
        shutil.copy2(path, dest)
        folder = choose_folder(profile, [dest], when)
        queue.add(dest, folder)
        return Result([dest], folder)
    raise ValueError(f"Dateityp {ext} wird nicht unterstützt")
