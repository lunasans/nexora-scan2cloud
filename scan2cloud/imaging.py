"""Bildverarbeitung: leere Seiten erkennen, Dateinamen bilden, PDF/JPG schreiben."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PIL import Image

from scan2cloud.config import Profile

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_blank(img: Image.Image, threshold_percent: float = 0.5) -> bool:
    """True, wenn weniger als ``threshold_percent`` Prozent der Seite dunkel sind.

    Der Rand wird ignoriert, weil Einzugsscanner dort oft Schatten erzeugen.
    """
    gray = img.convert("L")
    gray.thumbnail((400, 400))
    w, h = gray.size
    mx, my = int(w * 0.05), int(h * 0.05)
    gray = gray.crop((mx, my, w - mx, h - my))
    hist = gray.histogram()
    dark = sum(hist[:200])
    total = sum(hist) or 1
    return dark / total * 100 < threshold_percent


def remove_blank_pages(images: list[Image.Image], threshold_percent: float) -> list[Image.Image]:
    return [im for im in images if not is_blank(im, threshold_percent)]


def apply_color_mode(img: Image.Image, color_mode: str) -> Image.Image:
    """Stellt sicher, dass das Bild im gewünschten Modus vorliegt – auch wenn
    der Treiber die Einstellung ignoriert hat."""
    if color_mode == "bw":
        if img.mode == "1":
            return img
        return img.convert("L").point(lambda v: 255 if v > 160 else 0).convert("1")
    if color_mode == "gray":
        return img if img.mode == "L" else img.convert("L")
    return img if img.mode == "RGB" else img.convert("RGB")


def sanitize(name: str) -> str:
    name = _INVALID_CHARS.sub("_", name).strip(" .")
    return name or "Scan"


def placeholders(profile: Profile, when: datetime | None = None) -> dict[str, str]:
    when = when or datetime.now()
    return {
        "datum": when.strftime("%Y-%m-%d"),
        "zeit": when.strftime("%H-%M-%S"),
        "jahr": when.strftime("%Y"),
        "monat": when.strftime("%m"),
        "tag": when.strftime("%d"),
        "profil": profile.name,
    }


class _SafeDict(dict):
    """Unbekannte Platzhalter bleiben stehen statt einen Fehler auszulösen."""

    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, values: dict[str, str]) -> str:
    return template.format_map(_SafeDict(values))


def remote_folder_for(profile: Profile, when: datetime | None = None) -> str:
    folder = render(profile.remote_folder or "/", placeholders(profile, when))
    folder = "/" + "/".join(sanitize(p) for p in folder.replace("\\", "/").split("/") if p)
    return folder


def unique_base_name(profile: Profile, directory: Path, ext: str,
                     when: datetime | None = None) -> str:
    """Bildet den Dateinamen (ohne Endung) aus der Vorlage. ``{nr}`` wird
    hochgezählt, bis der Name im Ausgabeordner noch frei ist."""
    values = placeholders(profile, when)
    template = profile.filename_template or "{datum}_{profil}_{nr}"
    if "{nr" not in template:
        template += "{nr_suffix}"
    for n in range(1, 10000):
        values["nr"] = f"{n:03d}"
        values["nr_suffix"] = "" if n == 1 else f"_{n}"
        base = sanitize(render(template, values))
        taken = (directory / f"{base}{ext}").exists() or (directory / f"{base}_S1{ext}").exists()
        if not taken:
            return base
    raise RuntimeError("Kein freier Dateiname gefunden")


def write_output(images: list[Image.Image], profile: Profile, directory: Path,
                 when: datetime | None = None) -> list[Path]:
    """Schreibt die Seiten als PDF oder JPG und gibt die erzeugten Dateien zurück."""
    if not images:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    pages = [apply_color_mode(im, profile.color_mode) for im in images]
    if profile.file_format == "pdf":
        base = unique_base_name(profile, directory, ".pdf", when)
        path = directory / f"{base}.pdf"
        pages[0].save(path, "PDF", save_all=True, append_images=pages[1:],
                      resolution=float(profile.dpi))
        return [path]

    base = unique_base_name(profile, directory, ".jpg", when)
    out = []
    for i, page in enumerate(pages, 1):
        suffix = "" if len(pages) == 1 else f"_S{i}"
        path = directory / f"{base}{suffix}.jpg"
        if page.mode == "1":
            page = page.convert("L")
        page.save(path, "JPEG", quality=profile.jpeg_quality, dpi=(profile.dpi, profile.dpi))
        out.append(path)
    return out
