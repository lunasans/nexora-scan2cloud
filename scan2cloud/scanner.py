"""Ansteuerung des Scanners über TWAIN (Canon P-208II und andere).

Wichtig: Der Canon-Treiber ist ein 32-Bit-TWAIN-Treiber. Das Programm muss
deshalb mit 32-Bit-Python laufen bzw. als 32-Bit-EXE gebaut werden.
TWAIN muss außerdem im Haupt-Thread der Oberfläche aufgerufen werden.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

from scan2cloud.config import Profile

log = logging.getLogger(__name__)


class ScannerError(Exception):
    pass


def _twain():
    if sys.platform != "win32":
        raise ScannerError("Scannen über TWAIN geht nur unter Windows")
    if sys.maxsize > 2**32:
        raise ScannerError(
            "Dieses Programm läuft als 64-Bit-Version. Der Canon-Treiber ist ein "
            "32-Bit-TWAIN-Treiber – bitte die 32-Bit-Version von Scan2Cloud verwenden.")
    try:
        import twain
    except ImportError as e:
        raise ScannerError("Paket 'pytwain' fehlt") from e
    return twain


def _source_manager(twain, parent):
    try:
        return twain.SourceManager(parent, ProductName="Scan2Cloud",
                                   Manufacturer="Scan2Cloud", ProductFamily="Scan2Cloud")
    except twain.exceptions.SMLoadFileFailed as e:
        raise ScannerError("TWAIN ist nicht verfügbar (twain_32.dll fehlt)") from e


def list_scanners(parent) -> list[str]:
    twain = _twain()
    sm = _source_manager(twain, parent)
    try:
        return sm.source_list
    finally:
        sm.close()


def _set(src, twain, cap, type_id, value, what: str) -> None:
    """Setzt eine Einstellung. Unterstützt der Treiber sie nicht, wird das nur
    protokolliert – der Scan läuft trotzdem."""
    try:
        src.set_capability(cap, type_id, value)
    except Exception as e:
        log.info("Scanner unterstützt '%s' nicht (%s)", what, e)


def _apply_profile(src, twain, profile: Profile) -> None:
    pixel = {"bw": twain.TWPT_BW, "gray": twain.TWPT_GRAY, "color": twain.TWPT_RGB}
    _set(src, twain, twain.ICAP_UNITS, twain.TWTY_UINT16, twain.TWUN_INCHES, "Einheit")
    _set(src, twain, twain.ICAP_PIXELTYPE, twain.TWTY_UINT16,
         pixel.get(profile.color_mode, twain.TWPT_GRAY), "Farbmodus")
    _set(src, twain, twain.ICAP_XRESOLUTION, twain.TWTY_FIX32, float(profile.dpi), "Auflösung X")
    _set(src, twain, twain.ICAP_YRESOLUTION, twain.TWTY_FIX32, float(profile.dpi), "Auflösung Y")
    _set(src, twain, twain.CAP_FEEDERENABLED, twain.TWTY_BOOL, True, "Einzug")
    _set(src, twain, twain.CAP_AUTOFEED, twain.TWTY_BOOL, True, "Automatischer Einzug")
    _set(src, twain, twain.CAP_DUPLEXENABLED, twain.TWTY_BOOL, bool(profile.duplex), "Duplex")
    _set(src, twain, twain.CAP_XFERCOUNT, twain.TWTY_INT16, -1, "Seitenanzahl")
    _set(src, twain, twain.CAP_INDICATORS, twain.TWTY_BOOL, False, "Fortschrittsanzeige")
    # Papierformat automatisch erkennen und schief eingezogene Seiten geraderücken
    _set(src, twain, twain.ICAP_SUPPORTEDSIZES, twain.TWTY_UINT16, twain.TWSS_NONE, "Papierformat")
    _set(src, twain, twain.ICAP_AUTOMATICBORDERDETECTION, twain.TWTY_BOOL, True, "Randerkennung")
    _set(src, twain, twain.ICAP_AUTOMATICDESKEW, twain.TWTY_BOOL, True, "Geraderücken")


def scan(parent, profile: Profile, scanner_name: str = "", show_ui: bool = False
         ) -> tuple[list[Image.Image], str, str]:
    """Scannt alle Seiten im Einzug. Gibt (Seiten, Scannername, Warnung) zurück.

    Ist kein Scanner gewählt, zeigt TWAIN ein Auswahlfenster an.
    """
    twain = _twain()
    sm = _source_manager(twain, parent)
    pages: list[Image.Image] = []
    warning = ""
    tmpdir = tempfile.mkdtemp(prefix="scan2cloud_")
    try:
        try:
            src = sm.open_source(scanner_name or None)
        except Exception as e:
            raise ScannerError(
                f"Scanner '{scanner_name}' konnte nicht geöffnet werden. Ist er eingeschaltet, "
                f"per USB verbunden und der Auto-Start-Schalter auf OFF? ({e})") from e
        if src is None:
            raise ScannerError("Keine Scannerauswahl getroffen")
        name = src.name
        try:
            _apply_profile(src, twain, profile)

            def after(image, more: int) -> None:
                bmp = os.path.join(tmpdir, f"page{len(pages) + 1:03d}.bmp")
                image.save(bmp)
                with Image.open(bmp) as im:
                    im.load()
                    pages.append(im.copy())
                os.remove(bmp)

            try:
                src.acquire_natively(after=after, show_ui=show_ui, modal=show_ui)
            except twain.exceptions.DSTransferCancelled:
                pass
            except twain.exceptions.TwainError as e:
                if isinstance(e, twain.exceptions.PaperJam):
                    reason = "Papierstau"
                elif isinstance(e, twain.exceptions.PaperDoubleFeedError):
                    reason = "Doppeleinzug erkannt"
                else:
                    reason = f"Scan fehlgeschlagen: {e}"
                if not pages:
                    raise ScannerError(f"{reason}. Liegt Papier im Einzug?") from e
                warning = f"{reason} – die {len(pages)} bereits gescannten Seiten werden gespeichert"
                log.warning(warning)
        finally:
            src.close()
    finally:
        sm.close()
        try:
            Path(tmpdir).rmdir()
        except OSError:
            pass
    return pages, name, warning
