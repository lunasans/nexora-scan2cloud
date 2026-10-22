# Scan2Cloud

Scannen, sinnvoll benennen und automatisch in die Cloud hochladen – mit Oberfläche, für Windows.
Entwickelt für den **Canon imageFORMULA P-208II**, funktioniert aber mit jedem TWAIN-Scanner.

## Funktionen (Version 0.1)

- **Scannen per Klick** über TWAIN – ohne das Canon-Programm
- **Profile** (z. B. „Rechnung“, „Brief“, „Foto“) mit
  - Auflösung (150–600 dpi), Farbe / Graustufen / Schwarz-Weiß
  - beidseitig (Duplex), **leere Seiten automatisch entfernen**
  - PDF (mehrseitig) oder JPG
  - Dateiname nach Vorlage, z. B. `{datum}_{profil}_{nr}` → `2026-10-22_Rechnung_001.pdf`
  - Zielordner in der Cloud, auch mit Platzhaltern: `/Scans/Rechnungen/{jahr}`
- **Automatischer Upload** zu **Nextcloud** oder einem anderen **WebDAV**-Server –
  oder Kopie in einen **lokalen Sync-Ordner** (funktioniert mit jeder Cloud, die ein Sync-Programm hat)
- **Upload-Warteschlange**: ohne Internet geht nichts verloren, es wird automatisch erneut
  versucht (auch nach einem Neustart des Programms). Vorhandene Dateien werden nie überschrieben.
- **Ordner überwachen**: Alternative ohne Treiber – Scans aus *CaptureOnTouch Lite* werden aus
  einem Ordner abgeholt, nach Profil benannt und hochgeladen
- Passwort sicher in der **Windows-Anmeldeinformationsverwaltung**

## Einrichtung

### 1. Scanner-Treiber (für „Scannen per Klick“)

Im Auslieferungszustand arbeitet der P-208II im *Plug-and-Scan*-Modus: Dann kann nur Canons
eigenes Programm *CaptureOnTouch Lite* scannen. Damit Scan2Cloud den Scanner direkt ansteuern kann:

1. Den **ISIS/TWAIN-Treiber für den P-208II** von der Canon-Supportseite installieren
   (Windows 10/11, Administratorrechte nötig).
2. Den **Auto-Start-Schalter** an der Rückseite des Scanners auf **OFF** stellen.
3. Scanner neu einstecken, Scan2Cloud starten → **Scanner wählen…**

Ohne Treiber geht der Weg über den Reiter **Ordner überwachen** (siehe unten).

> Der Canon-Treiber ist ein **32-Bit**-Treiber. Scan2Cloud wird deshalb als 32-Bit-Programm gebaut –
> das ist Absicht.

### 2. Cloud

Reiter **Cloud**:

| Art | Was eintragen |
|---|---|
| Nextcloud | Server-Adresse (z. B. `https://cloud.example.de`), Benutzername, **App-Passwort** |
| WebDAV (allgemein) | vollständige WebDAV-Adresse, Benutzername, Passwort |
| Lokaler Sync-Ordner | den Ordner, den das Sync-Programm deiner Cloud überwacht |

Ein App-Passwort legst du in Nextcloud unter *Einstellungen → Sicherheit → Neues App-Passwort* an.
Mit **Verbindung testen** prüfen, dann **Speichern**.

### 3. Profile

Im Reiter **Profile** Auflösung, Farbmodus, Dateiname und Zielordner festlegen.
**Auswählen…** zeigt die Ordner direkt aus der Cloud an.

Platzhalter für Dateiname und Zielordner:

| Platzhalter | Beispiel |
|---|---|
| `{datum}` | 2026-10-22 |
| `{zeit}` | 14-05-09 |
| `{jahr}` `{monat}` `{tag}` | 2026, 10, 22 |
| `{profil}` | Rechnung |
| `{nr}` | 001, 002 … (fortlaufend, damit nichts überschrieben wird) |

### Ohne Treiber: Ordner überwachen

1. In *CaptureOnTouch Lite* als Speicherort einen festen Ordner wählen, z. B. `C:\Scans\Eingang`.
2. In Scan2Cloud im Reiter **Ordner überwachen** diesen Ordner und ein Profil wählen, Haken bei
   *Überwachung aktiv*, **Übernehmen**.
3. Neue Scans werden umbenannt, hochgeladen und das Original nach `verarbeitet\` verschoben.

## Programm bekommen

**Fertige EXE:** Jeder Push baut über GitHub Actions automatisch eine 32-Bit-Version.
Unter *Actions → Test & Build → letzter Lauf → Artifacts* „Scan2Cloud-windows-32bit“ herunterladen,
entpacken und `Scan2Cloud.exe` starten.

**Selbst bauen:** 32-Bit-Python 3.12 von python.org installieren, dann `build.bat` ausführen.
Ergebnis: `dist\Scan2Cloud\Scan2Cloud.exe`.

**Entwickeln:**

```bat
py -3.12-32 -m venv .venv32
.venv32\Scripts\pip install -e .[dev]
.venv32\Scripts\python -m scan2cloud
.venv32\Scripts\python -m pytest
```

## Wo liegt was?

| | |
|---|---|
| Einstellungen | `%APPDATA%\Scan2Cloud\config.json` |
| Upload-Warteschlange | `%APPDATA%\Scan2Cloud\queue.json` |
| Protokoll | `%APPDATA%\Scan2Cloud\scan2cloud.log` |
| Gescannte Dateien | `Dokumente\Scan2Cloud` (änderbar im Reiter *Allgemein*) |

## Wie es weitergeht

Siehe [docs/ROADMAP.md](docs/ROADMAP.md) – als Nächstes: Texterkennung und **automatisches
Einsortieren** in den richtigen Ordner, mit eigenen Regeln und der lokalen KI (LM Studio).
