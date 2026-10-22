"""Einstellungen und Profile – gespeichert als JSON in %APPDATA%\\Scan2Cloud.

Das Cloud-Passwort wird nicht in der JSON-Datei abgelegt, sondern in der
Windows-Anmeldeinformationsverwaltung (über ``keyring``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from scan2cloud import APP_NAME

log = logging.getLogger(__name__)

KEYRING_SERVICE = APP_NAME

COLOR_MODES = {"color": "Farbe", "gray": "Graustufen", "bw": "Schwarz-Weiß"}
FORMATS = {"pdf": "PDF (mehrseitig)", "jpg": "JPG (je Seite eine Datei)"}
RESOLUTIONS = [150, 200, 300, 400, 600]
CLOUD_TYPES = {
    "nextcloud": "Nextcloud",
    "webdav": "WebDAV (allgemein)",
    "folder": "Lokaler Sync-Ordner",
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def default_output_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


@dataclass
class Profile:
    name: str = "Standard"
    dpi: int = 300
    color_mode: str = "gray"
    duplex: bool = True
    remove_blank: bool = True
    # Anteil "nicht-weißer" Pixel, unter dem eine Seite als leer gilt (in Prozent)
    blank_threshold: float = 0.5
    file_format: str = "pdf"
    jpeg_quality: int = 85
    filename_template: str = "{datum}_{profil}_{nr}"
    remote_folder: str = "/Scans"


@dataclass
class CloudSettings:
    type: str = "nextcloud"
    url: str = ""
    username: str = ""
    sync_folder: str = ""
    verify_tls: bool = True


@dataclass
class WatchSettings:
    enabled: bool = False
    folder: str = ""
    profile: str = "Standard"


@dataclass
class Settings:
    profiles: list[Profile] = field(default_factory=lambda: [
        Profile(),
        Profile(name="Rechnung", color_mode="bw", remote_folder="/Scans/Rechnungen"),
        Profile(name="Brief", color_mode="gray", remote_folder="/Scans/Briefe"),
        Profile(name="Foto", dpi=600, color_mode="color", duplex=False,
                remove_blank=False, file_format="jpg", jpeg_quality=92,
                remote_folder="/Scans/Fotos"),
    ])
    last_profile: str = "Standard"
    scanner_name: str = ""
    show_scanner_ui: bool = False
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    keep_local_copy: bool = True
    cloud: CloudSettings = field(default_factory=CloudSettings)
    watch: WatchSettings = field(default_factory=WatchSettings)

    def profile(self, name: str) -> Profile:
        for p in self.profiles:
            if p.name == name:
                return p
        return self.profiles[0]


def _from_dict(cls, data: dict):
    """Baut eine Dataclass aus einem Dict; unbekannte Schlüssel werden ignoriert,
    fehlende bekommen den Standardwert (wichtig für spätere Versionen)."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def settings_from_dict(data: dict) -> Settings:
    s = _from_dict(Settings, {k: v for k, v in data.items()
                               if k not in ("profiles", "cloud", "watch")})
    if data.get("profiles"):
        s.profiles = [_from_dict(Profile, p) for p in data["profiles"]]
    s.cloud = _from_dict(CloudSettings, data.get("cloud", {}))
    s.watch = _from_dict(WatchSettings, data.get("watch", {}))
    return s


def config_path() -> Path:
    return app_dir() / "config.json"


def load(path: Path | None = None) -> Settings:
    path = path or config_path()
    if not path.exists():
        return Settings()
    try:
        return settings_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as e:
        log.error("Einstellungen konnten nicht gelesen werden (%s) – nutze Standard", e)
        return Settings()


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)


def get_password(username: str) -> str:
    if not username:
        return ""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, username) or ""
    except Exception as e:  # kein Schlüsselbund verfügbar
        log.warning("Passwort konnte nicht gelesen werden: %s", e)
        return ""


def set_password(username: str, password: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, username, password)
