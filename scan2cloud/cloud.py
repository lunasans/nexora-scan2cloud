"""Cloud-Ziele: Nextcloud/WebDAV und lokaler Sync-Ordner.

Jedes Ziel bietet dieselben Methoden, damit der Rest des Programms nicht
wissen muss, wohin hochgeladen wird.
"""

from __future__ import annotations

import posixpath
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import requests

from scan2cloud.config import CloudSettings, get_password

TIMEOUT = 30
_DAV = "{DAV:}"


class CloudError(Exception):
    """Fehler beim Hochladen. ``retry`` sagt, ob ein erneuter Versuch sinnvoll ist
    (z. B. kein Internet) oder nicht (z. B. falsches Passwort)."""

    def __init__(self, message: str, retry: bool = True):
        super().__init__(message)
        self.retry = retry


def _split(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


def _numbered(name: str, n: int) -> str:
    stem, ext = posixpath.splitext(name)
    return f"{stem}_{n}{ext}"


class WebDavTarget:
    def __init__(self, base_url: str, username: str, password: str, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify_tls
        self.session.headers["User-Agent"] = "Scan2Cloud"

    @classmethod
    def from_settings(cls, cs: CloudSettings, password: str | None = None) -> "WebDavTarget":
        if not cs.url:
            raise CloudError("Keine Server-Adresse eingetragen", retry=False)
        url = cs.url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if cs.type == "nextcloud" and "/remote.php/" not in url:
            url = f"{url}/remote.php/dav/files/{quote(cs.username, safe='')}"
        if password is None:
            password = get_password(cs.username)
        return cls(url, cs.username, password, cs.verify_tls)

    def _url(self, path: str) -> str:
        parts = _split(path)
        return self.base_url + "/" + "/".join(quote(p, safe="") for p in parts)

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        try:
            r = self.session.request(method, self._url(path), timeout=TIMEOUT, **kw)
        except requests.exceptions.SSLError as e:
            raise CloudError(f"Zertifikatsfehler: {e}", retry=False) from e
        except requests.RequestException as e:
            raise CloudError(f"Server nicht erreichbar: {e}") from e
        if r.status_code == 401:
            raise CloudError("Anmeldung fehlgeschlagen – Benutzername oder App-Passwort prüfen",
                             retry=False)
        if r.status_code == 403:
            raise CloudError("Zugriff verweigert (403)", retry=False)
        if r.status_code == 507:
            raise CloudError("Cloud-Speicher ist voll (507)", retry=False)
        if r.status_code >= 500:
            raise CloudError(f"Serverfehler {r.status_code}")
        return r

    def test(self) -> str:
        r = self._request("PROPFIND", "/", headers={"Depth": "0"})
        if r.status_code == 207:
            return "Verbindung erfolgreich"
        if r.status_code == 404:
            raise CloudError("Adresse nicht gefunden (404) – Server-Adresse prüfen", retry=False)
        raise CloudError(f"Unerwartete Antwort vom Server: {r.status_code}", retry=False)

    def exists(self, path: str) -> bool:
        r = self._request("PROPFIND", path, headers={"Depth": "0"})
        if r.status_code == 404:
            return False
        if r.status_code == 207:
            return True
        raise CloudError(f"Unerwartete Antwort beim Prüfen von {path}: {r.status_code}")

    def ensure_folder(self, folder: str) -> None:
        current = ""
        for part in _split(folder):
            current += "/" + part
            r = self._request("MKCOL", current)
            # 201 = angelegt, 405 = existiert schon
            if r.status_code not in (201, 405):
                raise CloudError(f"Ordner {current} konnte nicht angelegt werden "
                                 f"({r.status_code})", retry=r.status_code == 409)

    def list_folders(self, folder: str = "/") -> list[str]:
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                '<d:resourcetype/></d:prop></d:propfind>')
        r = self._request("PROPFIND", folder, headers={"Depth": "1"}, data=body)
        if r.status_code != 207:
            raise CloudError(f"Ordner konnten nicht gelesen werden ({r.status_code})")
        base_path = unquote(urlsplit(self.base_url).path).rstrip("/")
        own = "/" + "/".join(_split(folder))
        result = []
        for resp in ET.fromstring(r.content).iter(_DAV + "response"):
            href = resp.findtext(_DAV + "href") or ""
            if resp.find(f".//{_DAV}collection") is None:
                continue
            path = unquote(urlsplit(href).path).rstrip("/")
            if path.startswith(base_path):
                path = path[len(base_path):]
            path = "/" + "/".join(_split(path))
            if path != own:
                result.append(path)
        return sorted(result, key=str.lower)

    def upload(self, local: Path, folder: str, name: str) -> str:
        """Lädt ``local`` nach ``folder/name`` hoch. Existiert die Datei schon,
        wird ``_2``, ``_3`` … angehängt. Gibt den Zielpfad zurück."""
        self.ensure_folder(folder)
        folder = "/" + "/".join(_split(folder))
        target, n = name, 1
        while self.exists(posixpath.join(folder, target)):
            n += 1
            target = _numbered(name, n)
        remote = posixpath.join(folder, target)
        with open(local, "rb") as f:
            r = self._request("PUT", remote, data=f)
        if r.status_code not in (200, 201, 204):
            raise CloudError(f"Hochladen fehlgeschlagen ({r.status_code})")
        return remote


class FolderTarget:
    """Kopiert in einen Ordner, den der Sync-Client der Cloud überwacht."""

    def __init__(self, root: str):
        if not root:
            raise CloudError("Kein Sync-Ordner eingetragen", retry=False)
        self.root = Path(root)

    @classmethod
    def from_settings(cls, cs: CloudSettings, password: str | None = None) -> "FolderTarget":
        return cls(cs.sync_folder)

    def _path(self, folder: str) -> Path:
        return self.root.joinpath(*_split(folder))

    def test(self) -> str:
        if not self.root.is_dir():
            raise CloudError(f"Ordner {self.root} existiert nicht", retry=False)
        return "Sync-Ordner gefunden"

    def list_folders(self, folder: str = "/") -> list[str]:
        base = self._path(folder)
        own = "/" + "/".join(_split(folder))
        return sorted(((own.rstrip("/") + "/" + p.name) for p in base.iterdir() if p.is_dir()),
                      key=str.lower)

    def upload(self, local: Path, folder: str, name: str) -> str:
        if not self.root.is_dir():
            # z. B. externes Laufwerk nicht angeschlossen → später erneut versuchen
            raise CloudError(f"Sync-Ordner {self.root} nicht erreichbar")
        dest_dir = self._path(folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target, n = name, 1
        while (dest_dir / target).exists():
            n += 1
            target = _numbered(name, n)
        shutil.copy2(local, dest_dir / target)
        return "/" + "/".join(_split(folder) + [target])


def make_target(cs: CloudSettings, password: str | None = None):
    if cs.type == "folder":
        return FolderTarget.from_settings(cs)
    return WebDavTarget.from_settings(cs, password)
