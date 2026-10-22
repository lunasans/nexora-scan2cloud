"""Hauptfenster von Scan2Cloud (tkinter)."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from scan2cloud import APP_NAME, __version__, config, imaging, pipeline, scanner
from scan2cloud.cloud import make_target
from scan2cloud.config import CLOUD_TYPES, COLOR_MODES, FORMATS, RESOLUTIONS, Profile
from scan2cloud.uploads import FAILED, UploadQueue
from scan2cloud.watcher import FolderWatcher

log = logging.getLogger(__name__)

PLACEHOLDER_HELP = ("Platzhalter: {datum} {zeit} {jahr} {monat} {tag} {profil} {nr}\n"
                    "Auch im Zielordner möglich, z. B. /Scans/Rechnungen/{jahr}")


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


class GuiLogHandler(logging.Handler):
    def __init__(self, app: "App"):
        super().__init__(logging.INFO)
        self.app = app

    def emit(self, record):
        msg = self.format(record)
        self.app.call_soon(lambda: self.app.append_log(msg))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = config.load()
        self._events: queue.Queue = queue.Queue()
        self.watcher: FolderWatcher | None = None

        root.title(f"{APP_NAME} {__version__}")
        root.geometry("940x640")
        root.minsize(760, 520)

        self.uploads = UploadQueue(
            config.app_dir() / "queue.json",
            target_factory=lambda: make_target(self.settings.cloud),
            keep_local=lambda: self.settings.keep_local_copy,
            on_change=lambda job: self.call_soon(self.refresh_jobs),
        )

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._build_scan_tab()
        self._build_profiles_tab()
        self._build_cloud_tab()
        self._build_watch_tab()
        self._build_general_tab()

        self.status = tk.StringVar(value="Bereit")
        ttk.Label(root, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=4)

        handler = GuiLogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)

        self.update_profile_summary()
        self.refresh_jobs()
        self.uploads.start()
        self.apply_watch_settings()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self._drain_events)

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------
    def call_soon(self, fn) -> None:
        """Führt ``fn`` im GUI-Thread aus (aus Hintergrund-Threads aufrufbar)."""
        self._events.put(fn)

    def _drain_events(self) -> None:
        try:
            while True:
                self._events.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(200, self._drain_events)

    def run_in_background(self, work, done=None, error=None) -> None:
        def runner():
            try:
                result = work()
            except Exception as e:  # an den GUI-Thread weiterreichen
                log.debug("Hintergrundaufgabe fehlgeschlagen", exc_info=True)
                self.call_soon(lambda e=e: (error or self.show_error)(e))
            else:
                if done:
                    self.call_soon(lambda: done(result))
        threading.Thread(target=runner, daemon=True).start()

    def show_error(self, e: Exception) -> None:
        self.status.set(f"Fehler: {e}")
        messagebox.showerror(APP_NAME, str(e), parent=self.root)

    def save_settings(self) -> None:
        config.save(self.settings)

    def append_log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    @staticmethod
    def _row(parent, row: int, label: str, widget, hint: str = "") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        if hint:
            ttk.Label(parent, text=hint, foreground="gray").grid(
                row=row, column=2, sticky="w", padx=(10, 0))

    def profile_names(self) -> list[str]:
        return [p.name for p in self.settings.profiles]

    def refresh_profile_lists(self) -> None:
        names = self.profile_names()
        self.scan_profile_cb["values"] = names
        self.watch_profile_cb["values"] = names
        if self.scan_profile.get() not in names:
            self.scan_profile.set(names[0])
        if self.watch_profile.get() not in names:
            self.watch_profile.set(names[0])
        self.update_profile_summary()

    # ------------------------------------------------------------------
    # Reiter "Scannen"
    # ------------------------------------------------------------------
    def _build_scan_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Scannen")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Profil:").pack(side="left")
        self.scan_profile = tk.StringVar(value=self.settings.last_profile)
        self.scan_profile_cb = ttk.Combobox(top, textvariable=self.scan_profile, state="readonly",
                                            values=self.profile_names(), width=22)
        self.scan_profile_cb.pack(side="left", padx=6)
        self.scan_profile_cb.bind("<<ComboboxSelected>>", lambda e: self.update_profile_summary())
        self.scan_button = ttk.Button(top, text="▶  Scannen", command=self.do_scan, width=16)
        self.scan_button.pack(side="left", padx=12, ipady=6)

        self.scanner_label = tk.StringVar()
        ttk.Button(top, text="Scanner wählen…", command=self.choose_scanner).pack(side="right")
        ttk.Label(top, textvariable=self.scanner_label).pack(side="right", padx=8)
        self._update_scanner_label()

        self.profile_summary = tk.StringVar()
        ttk.Label(tab, textvariable=self.profile_summary, foreground="gray").pack(
            fill="x", pady=(8, 10))

        box = ttk.LabelFrame(tab, text="Uploads", padding=6)
        box.pack(fill="both", expand=True)
        cols = ("datei", "ziel", "status", "info")
        self.jobs_tree = ttk.Treeview(box, columns=cols, show="headings", height=7)
        for col, text, width in (("datei", "Datei", 230), ("ziel", "Zielordner", 200),
                                 ("status", "Status", 80), ("info", "Info", 300)):
            self.jobs_tree.heading(col, text=text)
            self.jobs_tree.column(col, width=width, stretch=col in ("info", "datei"))
        self.jobs_tree.pack(fill="both", expand=True)
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Erneut versuchen", command=self.retry_selected).pack(side="left")
        ttk.Button(btns, text="Aus Liste entfernen", command=self.remove_selected).pack(
            side="left", padx=6)
        ttk.Button(btns, text="Ausgabeordner öffnen", command=self.open_output_dir).pack(
            side="right")

        logbox = ttk.LabelFrame(tab, text="Protokoll", padding=6)
        logbox.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(logbox, height=7, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _update_scanner_label(self) -> None:
        self.scanner_label.set(f"Scanner: {self.settings.scanner_name or '(noch nicht gewählt)'}")

    def update_profile_summary(self) -> None:
        p = self.settings.profile(self.scan_profile.get())
        parts = [f"{p.dpi} dpi", COLOR_MODES.get(p.color_mode, p.color_mode),
                 "beidseitig" if p.duplex else "einseitig",
                 "leere Seiten entfernen" if p.remove_blank else "",
                 p.file_format.upper(), f"→ {p.remote_folder}"]
        self.profile_summary.set(" · ".join(x for x in parts if x))

    def choose_scanner(self) -> None:
        try:
            names = scanner.list_scanners(self.root)
        except scanner.ScannerError as e:
            self.show_error(e)
            return
        if not names:
            messagebox.showwarning(APP_NAME, "Kein TWAIN-Scanner gefunden.\n\n"
                                   "Ist der Canon-Treiber (ISIS/TWAIN) installiert und der "
                                   "Scanner eingeschaltet?", parent=self.root)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Scanner wählen")
        dlg.transient(self.root)
        lb = tk.Listbox(dlg, width=50, height=min(len(names), 10))
        for n in names:
            lb.insert("end", n)
        lb.pack(padx=10, pady=10)
        if self.settings.scanner_name in names:
            lb.selection_set(names.index(self.settings.scanner_name))

        def ok():
            sel = lb.curselection()
            if sel:
                self.settings.scanner_name = names[sel[0]]
                self.save_settings()
                self._update_scanner_label()
            dlg.destroy()

        lb.bind("<Double-Button-1>", lambda e: ok())
        ttk.Button(dlg, text="Übernehmen", command=ok).pack(pady=(0, 10))
        dlg.grab_set()

    def do_scan(self) -> None:
        profile = self.settings.profile(self.scan_profile.get())
        self.settings.last_profile = profile.name
        self.save_settings()
        self.scan_button.state(["disabled"])
        self.status.set(f"Scanne mit Profil „{profile.name}“ …")
        self.root.update_idletasks()
        try:
            # TWAIN muss im GUI-Thread laufen
            pages, name, warning = scanner.scan(self.root, profile, self.settings.scanner_name,
                                                self.settings.show_scanner_ui)
        except scanner.ScannerError as e:
            self.scan_button.state(["!disabled"])
            self.show_error(e)
            return
        except Exception as e:
            log.exception("Unerwarteter Scanfehler")
            self.scan_button.state(["!disabled"])
            self.show_error(e)
            return
        if name and name != self.settings.scanner_name:
            self.settings.scanner_name = name
            self.save_settings()
            self._update_scanner_label()
        if warning:
            messagebox.showwarning(APP_NAME, warning, parent=self.root)
        if not pages:
            self.scan_button.state(["!disabled"])
            self.status.set("Keine Seiten gescannt")
            return
        self.status.set(f"{len(pages)} Seiten gescannt – speichere …")

        def done(result: pipeline.Result):
            self.scan_button.state(["!disabled"])
            msg = f"Gespeichert: {', '.join(f.name for f in result.files)} → {result.folder}"
            if result.removed_blank:
                msg += f" ({result.removed_blank} leere Seiten entfernt)"
            if not result.files:
                msg = "Alle Seiten waren leer – nichts gespeichert"
            log.info(msg)
            self.status.set(msg)

        def failed(e):
            self.scan_button.state(["!disabled"])
            self.show_error(e)

        self.run_in_background(
            lambda: pipeline.process_pages(pages, profile, self.settings, self.uploads),
            done, failed)

    def refresh_jobs(self) -> None:
        tree = self.jobs_tree
        tree.delete(*tree.get_children())
        jobs = self.uploads.pending()
        for job in jobs:
            if job.status == FAILED:
                info = job.last_error
            elif job.last_error:
                wait = max(0, int(job.next_try - datetime.now().timestamp()))
                info = f"{job.last_error} – neuer Versuch in {wait} s"
            else:
                info = "wird hochgeladen …"
            tree.insert("", "end", iid=job.id, values=(job.name, job.folder, job.status, info))
        if not jobs:
            tree.insert("", "end", iid="_empty",
                        values=("Alles hochgeladen ✓", "", "", ""))

    def _selected_jobs(self) -> list[str]:
        return [i for i in self.jobs_tree.selection() if i != "_empty"]

    def retry_selected(self) -> None:
        ids = self._selected_jobs()
        if not ids:
            self.uploads.retry()
        for i in ids:
            self.uploads.retry(i)
        self.refresh_jobs()

    def remove_selected(self) -> None:
        ids = self._selected_jobs()
        if ids and messagebox.askyesno(
                APP_NAME, "Ausgewählte Uploads aus der Liste entfernen?\n"
                "Die Dateien bleiben im Ausgabeordner erhalten.", parent=self.root):
            for i in ids:
                self.uploads.remove(i)
            self.refresh_jobs()

    def open_output_dir(self) -> None:
        path = Path(self.settings.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)

    # ------------------------------------------------------------------
    # Reiter "Profile"
    # ------------------------------------------------------------------
    def _build_profiles_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Profile")

        left = ttk.Frame(tab)
        left.pack(side="left", fill="y")
        self.profile_list = tk.Listbox(left, width=22, exportselection=False)
        self.profile_list.pack(fill="y", expand=True)
        self.profile_list.bind("<<ListboxSelect>>", lambda e: self.load_profile_form())
        b = ttk.Frame(left)
        b.pack(fill="x", pady=6)
        ttk.Button(b, text="Neu", command=self.new_profile, width=7).pack(side="left")
        ttk.Button(b, text="Kopie", command=self.copy_profile, width=7).pack(side="left", padx=3)
        ttk.Button(b, text="Löschen", command=self.delete_profile, width=7).pack(side="left")

        form = ttk.Frame(tab, padding=(16, 0, 0, 0))
        form.pack(side="left", fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        self.pf = {
            "name": tk.StringVar(), "dpi": tk.StringVar(), "color_mode": tk.StringVar(),
            "duplex": tk.BooleanVar(), "remove_blank": tk.BooleanVar(),
            "blank_threshold": tk.DoubleVar(), "file_format": tk.StringVar(),
            "jpeg_quality": tk.IntVar(), "filename_template": tk.StringVar(),
            "remote_folder": tk.StringVar(),
        }
        r = 0
        self._row(form, r, "Name", ttk.Entry(form, textvariable=self.pf["name"]))
        r += 1
        self._row(form, r, "Auflösung (dpi)", ttk.Combobox(
            form, textvariable=self.pf["dpi"], values=[str(x) for x in RESOLUTIONS],
            state="readonly", width=8), "300 dpi reicht für Dokumente")
        r += 1
        self._row(form, r, "Farbmodus", ttk.Combobox(
            form, textvariable=self.pf["color_mode"], values=list(COLOR_MODES.values()),
            state="readonly"))
        r += 1
        self._row(form, r, "Beidseitig (Duplex)", ttk.Checkbutton(form, variable=self.pf["duplex"]))
        r += 1
        self._row(form, r, "Leere Seiten entfernen",
                  ttk.Checkbutton(form, variable=self.pf["remove_blank"]))
        r += 1
        self._row(form, r, "Leer-Schwelle (%)", ttk.Spinbox(
            form, textvariable=self.pf["blank_threshold"], from_=0.05, to=5.0, increment=0.05,
            width=8), "höher = mehr Seiten gelten als leer")
        r += 1
        self._row(form, r, "Dateiformat", ttk.Combobox(
            form, textvariable=self.pf["file_format"], values=list(FORMATS.values()),
            state="readonly"))
        r += 1
        self._row(form, r, "JPG-Qualität", ttk.Spinbox(
            form, textvariable=self.pf["jpeg_quality"], from_=50, to=100, width=8))
        r += 1
        self._row(form, r, "Dateiname", ttk.Entry(form, textvariable=self.pf["filename_template"]))
        r += 1
        folder_row = ttk.Frame(form)
        folder_row.columnconfigure(0, weight=1)
        ttk.Entry(folder_row, textvariable=self.pf["remote_folder"]).grid(row=0, column=0,
                                                                           sticky="ew")
        ttk.Button(folder_row, text="Auswählen…", command=self.pick_remote_folder).grid(
            row=0, column=1, padx=(6, 0))
        self._row(form, r, "Zielordner (Cloud)", folder_row)
        r += 1
        ttk.Label(form, text=PLACEHOLDER_HELP, foreground="gray", justify="left").grid(
            row=r, column=1, columnspan=2, sticky="w", pady=(2, 6))
        r += 1
        self.name_preview = tk.StringVar()
        ttk.Label(form, textvariable=self.name_preview).grid(row=r, column=1, columnspan=2,
                                                            sticky="w")
        for key in ("name", "filename_template", "remote_folder", "file_format"):
            self.pf[key].trace_add("write", lambda *a: self.update_name_preview())
        r += 1
        ttk.Button(form, text="Profil speichern", command=self.save_profile).grid(
            row=r, column=1, sticky="w", pady=12)

        self.fill_profile_list()

    def fill_profile_list(self, select: str | None = None) -> None:
        self.profile_list.delete(0, "end")
        names = self.profile_names()
        for n in names:
            self.profile_list.insert("end", n)
        idx = names.index(select) if select in names else 0
        self.profile_list.selection_set(idx)
        self.load_profile_form()

    def _current_profile_index(self) -> int:
        sel = self.profile_list.curselection()
        return sel[0] if sel else 0

    def load_profile_form(self) -> None:
        p = self.settings.profiles[self._current_profile_index()]
        self.pf["name"].set(p.name)
        self.pf["dpi"].set(str(p.dpi))
        self.pf["color_mode"].set(COLOR_MODES.get(p.color_mode, COLOR_MODES["gray"]))
        self.pf["duplex"].set(p.duplex)
        self.pf["remove_blank"].set(p.remove_blank)
        self.pf["blank_threshold"].set(p.blank_threshold)
        self.pf["file_format"].set(FORMATS.get(p.file_format, FORMATS["pdf"]))
        self.pf["jpeg_quality"].set(p.jpeg_quality)
        self.pf["filename_template"].set(p.filename_template)
        self.pf["remote_folder"].set(p.remote_folder)

    def _profile_from_form(self) -> Profile:
        def key_for(mapping, label, default):
            return next((k for k, v in mapping.items() if v == label), default)
        try:
            threshold = float(self.pf["blank_threshold"].get())
            quality = int(self.pf["jpeg_quality"].get())
        except (tk.TclError, ValueError) as e:
            raise ValueError("Bitte gültige Zahlen für Schwelle und JPG-Qualität eingeben") from e
        return Profile(
            name=self.pf["name"].get().strip() or "Profil",
            dpi=int(self.pf["dpi"].get() or 300),
            color_mode=key_for(COLOR_MODES, self.pf["color_mode"].get(), "gray"),
            duplex=self.pf["duplex"].get(),
            remove_blank=self.pf["remove_blank"].get(),
            blank_threshold=max(0.01, min(threshold, 20.0)),
            file_format=key_for(FORMATS, self.pf["file_format"].get(), "pdf"),
            jpeg_quality=max(10, min(quality, 100)),
            filename_template=self.pf["filename_template"].get().strip() or "{datum}_{profil}_{nr}",
            remote_folder=self.pf["remote_folder"].get().strip() or "/",
        )

    def update_name_preview(self) -> None:
        try:
            p = self._profile_from_form()
        except ValueError:
            return
        values = imaging.placeholders(p)
        values.update(nr="001", nr_suffix="")
        name = imaging.sanitize(imaging.render(p.filename_template, values))
        self.name_preview.set(
            f"Beispiel: {imaging.remote_folder_for(p).rstrip('/')}/{name}.{p.file_format}")

    def save_profile(self) -> None:
        try:
            p = self._profile_from_form()
        except ValueError as e:
            self.show_error(e)
            return
        idx = self._current_profile_index()
        old_name = self.settings.profiles[idx].name
        if p.name != old_name and p.name in self.profile_names():
            self.show_error(ValueError(f"Ein Profil „{p.name}“ gibt es schon"))
            return
        self.settings.profiles[idx] = p
        if self.settings.watch.profile == old_name:
            self.settings.watch.profile = p.name
        if self.scan_profile.get() == old_name:
            self.scan_profile.set(p.name)
        self.save_settings()
        self.fill_profile_list(p.name)
        self.refresh_profile_lists()
        self.status.set(f"Profil „{p.name}“ gespeichert")

    def _unique_profile_name(self, base: str) -> str:
        name, n = base, 1
        while name in self.profile_names():
            n += 1
            name = f"{base} {n}"
        return name

    def new_profile(self) -> None:
        p = Profile(name=self._unique_profile_name("Neues Profil"))
        self.settings.profiles.append(p)
        self.save_settings()
        self.fill_profile_list(p.name)
        self.refresh_profile_lists()

    def copy_profile(self) -> None:
        src = self.settings.profiles[self._current_profile_index()]
        p = replace(src, name=self._unique_profile_name(src.name + " Kopie"))
        self.settings.profiles.append(p)
        self.save_settings()
        self.fill_profile_list(p.name)
        self.refresh_profile_lists()

    def delete_profile(self) -> None:
        if len(self.settings.profiles) <= 1:
            self.show_error(ValueError("Das letzte Profil kann nicht gelöscht werden"))
            return
        idx = self._current_profile_index()
        name = self.settings.profiles[idx].name
        if messagebox.askyesno(APP_NAME, f"Profil „{name}“ löschen?", parent=self.root):
            del self.settings.profiles[idx]
            self.save_settings()
            self.fill_profile_list()
            self.refresh_profile_lists()

    def pick_remote_folder(self) -> None:
        FolderPicker(self, self.pf["remote_folder"])

    # ------------------------------------------------------------------
    # Reiter "Cloud"
    # ------------------------------------------------------------------
    def _build_cloud_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Cloud")
        tab.columnconfigure(1, weight=1)
        cs = self.settings.cloud
        self.cf_type = tk.StringVar(value=CLOUD_TYPES.get(cs.type, CLOUD_TYPES["nextcloud"]))
        self.cf_url = tk.StringVar(value=cs.url)
        self.cf_user = tk.StringVar(value=cs.username)
        self.cf_pass = tk.StringVar(value=config.get_password(cs.username))
        self.cf_verify = tk.BooleanVar(value=cs.verify_tls)
        self.cf_sync = tk.StringVar(value=cs.sync_folder)

        type_cb = ttk.Combobox(tab, textvariable=self.cf_type, values=list(CLOUD_TYPES.values()),
                               state="readonly")
        self._row(tab, 0, "Art der Cloud", type_cb)
        self.cf_widgets_dav = []
        e_url = ttk.Entry(tab, textvariable=self.cf_url)
        self._row(tab, 1, "Server-Adresse", e_url)
        self.url_hint = tk.StringVar()
        ttk.Label(tab, textvariable=self.url_hint, foreground="gray").grid(
            row=2, column=1, sticky="w")
        e_user = ttk.Entry(tab, textvariable=self.cf_user)
        self._row(tab, 3, "Benutzername", e_user)
        e_pass = ttk.Entry(tab, textvariable=self.cf_pass, show="•")
        self._row(tab, 4, "App-Passwort", e_pass)
        ttk.Label(tab, foreground="gray", justify="left", text=(
            "Tipp: In Nextcloud unter Einstellungen → Sicherheit → „Neues App-Passwort“\n"
            "ein eigenes Passwort für Scan2Cloud anlegen. Es wird sicher in der\n"
            "Windows-Anmeldeinformationsverwaltung gespeichert.")).grid(
            row=5, column=1, sticky="w", pady=(0, 6))
        c_verify = ttk.Checkbutton(tab, text="Zertifikat prüfen (empfohlen)",
                                   variable=self.cf_verify)
        c_verify.grid(row=6, column=1, sticky="w")
        self.cf_widgets_dav = [e_url, e_user, e_pass, c_verify]

        sync_row = ttk.Frame(tab)
        sync_row.columnconfigure(0, weight=1)
        e_sync = ttk.Entry(sync_row, textvariable=self.cf_sync)
        e_sync.grid(row=0, column=0, sticky="ew")
        b_sync = ttk.Button(sync_row, text="…", width=3, command=lambda: self._browse(self.cf_sync))
        b_sync.grid(row=0, column=1, padx=(6, 0))
        self._row(tab, 7, "Sync-Ordner", sync_row,)
        ttk.Label(tab, foreground="gray", justify="left", text=(
            "Nur für „Lokaler Sync-Ordner“: ein Ordner, den das Sync-Programm deiner Cloud\n"
            "überwacht. Scan2Cloud kopiert die Dateien dorthin, der Rest läuft automatisch.")
        ).grid(row=8, column=1, sticky="w")
        self.cf_widgets_sync = [e_sync, b_sync]

        btns = ttk.Frame(tab)
        btns.grid(row=9, column=1, sticky="w", pady=14)
        ttk.Button(btns, text="Speichern", command=self.save_cloud).pack(side="left")
        ttk.Button(btns, text="Verbindung testen", command=self.test_cloud).pack(
            side="left", padx=8)

        type_cb.bind("<<ComboboxSelected>>", lambda e: self._update_cloud_fields())
        self._update_cloud_fields()

    def _cloud_type_key(self) -> str:
        return next((k for k, v in CLOUD_TYPES.items() if v == self.cf_type.get()), "nextcloud")

    def _update_cloud_fields(self) -> None:
        kind = self._cloud_type_key()
        for w in self.cf_widgets_dav:
            w.state(["disabled"] if kind == "folder" else ["!disabled"])
        for w in self.cf_widgets_sync:
            w.state(["!disabled"] if kind == "folder" else ["disabled"])
        self.url_hint.set({
            "nextcloud": "z. B. https://cloud.example.de (ohne /remote.php/…)",
            "webdav": "vollständige WebDAV-Adresse, z. B. https://example.de/dav/",
            "folder": "",
        }[kind])

    def _cloud_from_form(self) -> config.CloudSettings:
        return config.CloudSettings(
            type=self._cloud_type_key(), url=self.cf_url.get().strip(),
            username=self.cf_user.get().strip(), sync_folder=self.cf_sync.get().strip(),
            verify_tls=self.cf_verify.get())

    def save_cloud(self) -> None:
        cs = self._cloud_from_form()
        if cs.type != "folder" and cs.username:
            try:
                config.set_password(cs.username, self.cf_pass.get())
            except Exception as e:
                self.show_error(RuntimeError(f"Passwort konnte nicht gespeichert werden: {e}"))
                return
        self.settings.cloud = cs
        self.save_settings()
        self.uploads.retry()
        self.status.set("Cloud-Einstellungen gespeichert")

    def test_cloud(self) -> None:
        cs, password = self._cloud_from_form(), self.cf_pass.get()
        self.status.set("Teste Verbindung …")

        def done(msg):
            self.status.set(msg)
            messagebox.showinfo(APP_NAME, msg, parent=self.root)

        self.run_in_background(lambda: make_target(cs, password).test(), done)

    # ------------------------------------------------------------------
    # Reiter "Ordner überwachen"
    # ------------------------------------------------------------------
    def _build_watch_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Ordner überwachen")
        tab.columnconfigure(1, weight=1)
        ws = self.settings.watch
        ttk.Label(tab, justify="left", text=(
            "Für Scans ohne Treiber, z. B. mit CaptureOnTouch Lite (Auto-Start-Schalter ON):\n"
            "Speichere die Scans dort in einen festen Ordner. Scan2Cloud holt neue Dateien ab,\n"
            "benennt sie nach dem gewählten Profil und lädt sie hoch. Die Originale werden in\n"
            "den Unterordner „verarbeitet“ verschoben.")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self.watch_enabled = tk.BooleanVar(value=ws.enabled)
        self.watch_folder = tk.StringVar(value=ws.folder)
        self.watch_profile = tk.StringVar(value=ws.profile)
        self._row(tab, 1, "Überwachung aktiv", ttk.Checkbutton(tab, variable=self.watch_enabled))
        row = ttk.Frame(tab)
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.watch_folder).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="…", width=3, command=lambda: self._browse(self.watch_folder)).grid(
            row=0, column=1, padx=(6, 0))
        self._row(tab, 2, "Ordner", row)
        self.watch_profile_cb = ttk.Combobox(tab, textvariable=self.watch_profile, state="readonly",
                                             values=self.profile_names())
        self._row(tab, 3, "Profil anwenden", self.watch_profile_cb,
                  "Dateiname und Zielordner kommen aus dem Profil")
        self.watch_state = tk.StringVar()
        ttk.Label(tab, textvariable=self.watch_state).grid(row=4, column=1, sticky="w", pady=6)
        ttk.Button(tab, text="Übernehmen", command=self.save_watch).grid(
            row=5, column=1, sticky="w", pady=8)

    def save_watch(self) -> None:
        self.settings.watch = config.WatchSettings(
            enabled=self.watch_enabled.get(), folder=self.watch_folder.get().strip(),
            profile=self.watch_profile.get())
        self.save_settings()
        self.apply_watch_settings()

    def apply_watch_settings(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        ws = self.settings.watch
        if not ws.enabled:
            self.watch_state.set("Überwachung ist aus")
            return
        if not ws.folder:
            self.watch_state.set("⚠ Kein Ordner gewählt")
            return

        def handle(path: Path):
            profile = self.settings.profile(self.settings.watch.profile)
            result = pipeline.process_file(path, profile, self.settings, self.uploads)
            log.info("Aus Ordner übernommen: %s → %s", path.name,
                     ", ".join(f.name for f in result.files))

        self.watcher = FolderWatcher(Path(ws.folder), handle)
        try:
            self.watcher.start()
        except FileNotFoundError as e:
            self.watcher = None
            self.watch_state.set(f"⚠ {e}")
            return
        self.watch_state.set(f"✓ Überwache {ws.folder}")
        log.info("Ordnerüberwachung gestartet: %s", ws.folder)

    # ------------------------------------------------------------------
    # Reiter "Allgemein"
    # ------------------------------------------------------------------
    def _build_general_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="Allgemein")
        tab.columnconfigure(1, weight=1)
        s = self.settings
        self.gf_output = tk.StringVar(value=s.output_dir)
        self.gf_keep = tk.BooleanVar(value=s.keep_local_copy)
        self.gf_ui = tk.BooleanVar(value=s.show_scanner_ui)
        row = ttk.Frame(tab)
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.gf_output).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="…", width=3, command=lambda: self._browse(self.gf_output)).grid(
            row=0, column=1, padx=(6, 0))
        self._row(tab, 0, "Ausgabeordner", row)
        self._row(tab, 1, "Lokale Kopie behalten",
                  ttk.Checkbutton(tab, variable=self.gf_keep), "sonst nach dem Upload löschen")
        self._row(tab, 2, "Canon-Einstellungsfenster zeigen",
                  ttk.Checkbutton(tab, variable=self.gf_ui),
                  "für Sonderfälle; sonst gelten die Profile")
        btns = ttk.Frame(tab)
        btns.grid(row=3, column=1, sticky="w", pady=14)
        ttk.Button(btns, text="Speichern", command=self.save_general).pack(side="left")
        ttk.Button(btns, text="Logdatei öffnen",
                   command=lambda: open_path(config.app_dir() / "scan2cloud.log")).pack(
            side="left", padx=8)
        ttk.Label(tab, foreground="gray", text=f"Einstellungen: {config.config_path()}").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(20, 0))

    def save_general(self) -> None:
        self.settings.output_dir = self.gf_output.get().strip() or str(config.default_output_dir())
        self.settings.keep_local_copy = self.gf_keep.get()
        self.settings.show_scanner_ui = self.gf_ui.get()
        self.save_settings()
        self.status.set("Einstellungen gespeichert")

    def _browse(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(parent=self.root, initialdir=var.get() or str(Path.home()))
        if path:
            var.set(str(Path(path)))

    # ------------------------------------------------------------------
    def on_close(self) -> None:
        pending = len(self.uploads.pending())
        if pending and not messagebox.askyesno(
                APP_NAME, f"Es warten noch {pending} Uploads.\n"
                "Trotzdem beenden? (Sie werden beim nächsten Start fortgesetzt.)",
                parent=self.root):
            return
        self.uploads.stop()
        if self.watcher:
            self.watcher.stop()
        self.root.destroy()


class FolderPicker:
    """Kleines Fenster zum Auswählen eines Cloud-Ordners."""

    def __init__(self, app: App, var: tk.StringVar):
        self.app, self.var = app, var
        self.path = imaging.remote_folder_for(Profile(remote_folder=var.get().split("{")[0]))
        self.win = tk.Toplevel(app.root)
        self.win.title("Cloud-Ordner wählen")
        self.win.transient(app.root)
        self.label = tk.StringVar()
        ttk.Label(self.win, textvariable=self.label).pack(fill="x", padx=10, pady=(10, 4))
        self.lb = tk.Listbox(self.win, width=60, height=14)
        self.lb.pack(fill="both", expand=True, padx=10)
        self.lb.bind("<Double-Button-1>", lambda e: self.open_selected())
        b = ttk.Frame(self.win)
        b.pack(fill="x", padx=10, pady=10)
        ttk.Button(b, text="⬆ Nach oben", command=self.up).pack(side="left")
        ttk.Button(b, text="Diesen Ordner nehmen", command=self.ok).pack(side="right")
        self.load()
        self.win.grab_set()

    def load(self) -> None:
        self.label.set(f"{self.path}   (lädt …)")
        self.lb.delete(0, "end")
        path = self.path

        def done(folders):
            if not self.win.winfo_exists() or path != self.path:
                return
            self.label.set(self.path)
            self.folders = folders
            for f in folders:
                self.lb.insert("end", "📁 " + f.rsplit("/", 1)[-1])

        def failed(e):
            if self.win.winfo_exists():
                self.label.set(f"{self.path}   – Fehler: {e}")

        self.app.run_in_background(
            lambda: make_target(self.app.settings.cloud).list_folders(path), done, failed)

    def open_selected(self) -> None:
        sel = self.lb.curselection()
        if sel:
            self.path = self.folders[sel[0]]
            self.load()

    def up(self) -> None:
        self.path = self.path.rsplit("/", 1)[0] or "/"
        self.load()

    def ok(self) -> None:
        self.var.set(self.path)
        self.win.destroy()


def setup_logging() -> None:
    config.app_dir().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(config.app_dir() / "scan2cloud.log", encoding="utf-8")],
    )


def main() -> None:
    setup_logging()
    root = tk.Tk()
    try:
        import sv_ttk  # modernes Windows-11-Aussehen, falls installiert
        sv_ttk.set_theme("light")
    except ImportError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
