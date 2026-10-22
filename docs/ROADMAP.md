# Roadmap

## Version 1 – Scannen & Hochladen ✅

- TWAIN-Scan (Canon P-208II), Profile, leere Seiten entfernen, PDF/JPG
- Upload zu Nextcloud/WebDAV oder Sync-Ordner, Warteschlange mit Wiederholungen
- Ordnerüberwachung für Scans ohne Treiber

## Version 2 – Automatisch einsortieren

Ablauf: **Scan → Texterkennung → Einordnen → Dateiname → Zielordner → Upload**

Eingehängt wird das in `pipeline.choose_folder()`.

1. **Texterkennung (OCR)** mit der eingebauten Windows-OCR (`Windows.Media.Ocr`, offline, Deutsch).
   Das PDF bekommt eine unsichtbare Textebene und wird dadurch durchsuchbar.
2. **Datum aus dem Dokument** lesen (Rechnungsdatum statt Scandatum) → `{dokdatum}`.
3. **Eigene Regeln** (haben Vorrang), z. B.
   - enthält „Telekom“ und „Rechnung“ → `/Finanzen/Telekom/{jahr}`, Titel `Telekom_Rechnung`
   - enthält „Lohnabrechnung“ → `/Arbeit/Gehalt/{jahr}`
4. **Lokale KI über LM Studio** für alles, wofür keine Regel passt
   - Schnittstelle: `http://localhost:1234/v1/chat/completions` (OpenAI-kompatibel)
   - Modell frei wählbar (z. B. Qwen); Denkmodus wird abgeschaltet bzw. `<think>`-Blöcke entfernt
   - Antwort als festes JSON-Schema (`response_format: json_schema`):
     `{"ordner": "...", "titel": "...", "datum": "YYYY-MM-DD", "sicherheit": 0.0–1.0}`
   - Die KI bekommt die **Liste der vorhandenen Cloud-Ordner** und darf nur daraus wählen
     (oder ausdrücklich einen neuen Unterordner vorschlagen)
   - Unter der Sicherheits-Schwelle → Ordner **„Eingang“** + Bestätigung in der Oberfläche
   - LM Studio nicht gestartet → Dokument geht trotzdem in den „Eingang“, nichts geht verloren
   - Optional: Modelle mit Bildverständnis bekommen zusätzlich die erste Seite als Bild
5. **Reiter „Eingang“**: Vorschau, Vorschlag mit einem Klick bestätigen oder korrigieren.

## Version 3 – Lernen

- Aus Korrekturen im Eingang neue Regeln vorschlagen
- Korrigierte Beispiele als „Few-Shot“-Beispiele an die KI geben
- Suche über alle gescannten Dokumente (OCR-Text)
