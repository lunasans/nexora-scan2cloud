"""Warteschlange, Ablauf und Ordnerüberwachung zusammen."""

from datetime import datetime

from PIL import Image, ImageDraw

from scan2cloud import config, pipeline
from scan2cloud.cloud import CloudError, FolderTarget, WebDavTarget
from scan2cloud.config import CloudSettings, Profile, Settings
from scan2cloud.uploads import FAILED, UploadQueue, retry_delay
from scan2cloud.watcher import FolderWatcher

from conftest import PASSWORD, USER


def page():
    img = Image.new("L", (400, 500), 255)
    ImageDraw.Draw(img).rectangle((50, 50, 350, 300), fill=0)
    return img


def dav_queue(tmp_path, dav, keep=True):
    cs = CloudSettings(type="nextcloud", url=dav.base, username=USER)
    return UploadQueue(tmp_path / "queue.json",
                       lambda: WebDavTarget.from_settings(cs, PASSWORD),
                       keep_local=lambda: keep)


def test_retry_delay_grows_and_is_capped():
    assert [retry_delay(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert retry_delay(50) == 30 * 60


def test_scan_to_cloud(tmp_path, dav_server):
    s = Settings(output_dir=str(tmp_path / "out"))
    q = dav_queue(tmp_path, dav_server)
    p = Profile(name="Brief", remote_folder="/Scans/Briefe/{jahr}")
    blank = Image.new("L", (400, 500), 255)
    res = pipeline.process_pages([page(), blank, page()], p, s, q,
                                 when=datetime(2026, 3, 1))
    assert res.removed_blank == 1
    assert res.folder == "/Scans/Briefe/2026"
    q.process_due()
    assert q.pending() == []
    assert "Scans/Briefe/2026/2026-03-01_Brief_001.pdf" in dav_server.files
    assert res.files[0].exists()  # lokale Kopie behalten


def test_failed_upload_is_retried_and_survives_restart(tmp_path, dav_server):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF")
    dav_server.fail_puts = 1
    q = dav_queue(tmp_path, dav_server, keep=False)
    job = q.add(f, "/Scans")
    q.process_due(now=1000)
    assert job.attempts == 1 and job.next_try == 1030 and "503" in job.last_error
    # Programm neu gestartet: Auftrag ist noch da
    q2 = dav_queue(tmp_path, dav_server, keep=False)
    assert [j.name for j in q2.pending()] == ["a.pdf"]
    q2.process_due(now=1010)  # noch nicht fällig
    assert "Scans/a.pdf" not in dav_server.files
    q2.process_due(now=1031)
    assert dav_server.files["Scans/a.pdf"] == b"%PDF"
    assert not f.exists()  # lokale Kopie gelöscht
    assert q2.pending() == []


def test_wrong_password_stops_retrying(tmp_path, dav_server):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    cs = CloudSettings(type="nextcloud", url=dav_server.base, username=USER)
    q = UploadQueue(tmp_path / "q.json", lambda: WebDavTarget.from_settings(cs, "falsch"))
    job = q.add(f, "/")
    q.process_due()
    assert job.status == FAILED
    q.retry()
    assert q.pending()[0].status != FAILED


def test_missing_config_marks_failed(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")

    def factory():
        raise CloudError("Keine Server-Adresse eingetragen", retry=False)

    q = UploadQueue(tmp_path / "q.json", factory)
    q.add(f, "/")
    q.process_due()
    assert q.pending()[0].status == FAILED


def test_watch_folder(tmp_path):
    inbox, cloud = tmp_path / "inbox", tmp_path / "cloud"
    inbox.mkdir()
    cloud.mkdir()
    s = Settings(output_dir=str(tmp_path / "out"))
    q = UploadQueue(tmp_path / "q.json", lambda: FolderTarget(str(cloud)))
    p = Profile(name="Rechnung", remote_folder="/Rechnungen")
    w = FolderWatcher(inbox, lambda path: pipeline.process_file(path, p, s, q))

    page().save(inbox / "scan0001.jpg")
    (inbox / "scan0002.pdf").write_bytes(b"%PDF-1.4 test")
    assert w.poll() == []           # erster Blick: Dateien könnten noch geschrieben werden
    assert len(w.poll()) == 2       # unverändert → fertig
    assert sorted(x.name for x in (inbox / "verarbeitet").iterdir()) == \
        ["scan0001.jpg", "scan0002.pdf"]
    q.process_due()
    uploaded = sorted(x.name for x in (cloud / "Rechnungen").iterdir())
    assert len(uploaded) == 2 and all("Rechnung" in n for n in uploaded)


def test_watch_folder_bad_file_goes_to_error(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "kaputt.jpg").write_bytes(b"kein bild")
    s = Settings(output_dir=str(tmp_path / "out"))
    q = UploadQueue(tmp_path / "q.json", lambda: FolderTarget(str(tmp_path)))
    w = FolderWatcher(inbox, lambda path: pipeline.process_file(path, Profile(), s, q))
    w.poll()
    w.poll()
    assert (inbox / "fehler" / "kaputt.jpg").exists()


def test_settings_roundtrip_and_forward_compat(tmp_path):
    s = Settings()
    s.profiles[0].dpi = 600
    s.cloud.url = "https://cloud.example.de"
    path = tmp_path / "config.json"
    config.save(s, path)
    loaded = config.load(path)
    assert loaded.profiles[0].dpi == 600 and loaded.cloud.url == "https://cloud.example.de"
    # unbekannte Schlüssel (z. B. von neueren Versionen) stören nicht
    path.write_text('{"neu": 1, "profiles": [{"name": "X", "neu": 2}], "cloud": {}}')
    loaded = config.load(path)
    assert loaded.profiles[0].name == "X" and loaded.profiles[0].dpi == 300
    path.write_text("kaputt")
    assert config.load(path).profiles  # Standard statt Absturz
