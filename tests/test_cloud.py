import pytest

from scan2cloud.cloud import CloudError, FolderTarget, WebDavTarget
from scan2cloud.config import CloudSettings

from conftest import PASSWORD, USER


def target(dav, password=PASSWORD):
    cs = CloudSettings(type="nextcloud", url=dav.base, username=USER)
    return WebDavTarget.from_settings(cs, password)


def test_nextcloud_url_is_built():
    cs = CloudSettings(type="nextcloud", url="cloud.example.de/", username="Anna Müller")
    t = WebDavTarget.from_settings(cs, "x")
    assert t.base_url == "https://cloud.example.de/remote.php/dav/files/Anna%20M%C3%BCller"


def test_connection_ok_and_wrong_password(dav_server):
    assert target(dav_server).test() == "Verbindung erfolgreich"
    with pytest.raises(CloudError) as e:
        target(dav_server, "falsch").test()
    assert e.value.retry is False


def test_upload_creates_folders_and_avoids_overwrite(dav_server, tmp_path):
    f = tmp_path / "Rechnung.pdf"
    f.write_bytes(b"%PDF-1")
    t = target(dav_server)
    assert t.upload(f, "/Scans/Rechnungen/2026", "Rechnung.pdf") == \
        "/Scans/Rechnungen/2026/Rechnung.pdf"
    assert t.upload(f, "/Scans/Rechnungen/2026", "Rechnung.pdf") == \
        "/Scans/Rechnungen/2026/Rechnung_2.pdf"
    assert dav_server.files["Scans/Rechnungen/2026/Rechnung.pdf"] == b"%PDF-1"


def test_umlauts_in_paths(dav_server, tmp_path):
    f = tmp_path / "Brief.pdf"
    f.write_bytes(b"x")
    target(dav_server).upload(f, "/Ärzte & Co", "Brief über Größe.pdf")
    assert "Ärzte & Co/Brief über Größe.pdf" in dav_server.files


def test_list_folders(dav_server):
    dav_server.dirs |= {"Scans", "Scans/Briefe", "Scans/Rechnungen", "Fotos"}
    t = target(dav_server)
    assert t.list_folders("/") == ["/Fotos", "/Scans"]
    assert t.list_folders("/Scans") == ["/Scans/Briefe", "/Scans/Rechnungen"]


def test_unreachable_server_is_retryable():
    t = WebDavTarget("http://127.0.0.1:1/dav", "a", "b")
    with pytest.raises(CloudError) as e:
        t.test()
    assert e.value.retry is True


def test_folder_target(tmp_path):
    root = tmp_path / "Cloud"
    root.mkdir()
    f = tmp_path / "a.pdf"
    f.write_bytes(b"1")
    t = FolderTarget(str(root))
    assert t.upload(f, "/Scans/Briefe", "a.pdf") == "/Scans/Briefe/a.pdf"
    assert t.upload(f, "/Scans/Briefe", "a.pdf") == "/Scans/Briefe/a_2.pdf"
    assert t.list_folders("/") == ["/Scans"]
    assert t.list_folders("/Scans") == ["/Scans/Briefe"]


def test_folder_target_missing_root_is_retryable(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"1")
    with pytest.raises(CloudError) as e:
        FolderTarget(str(tmp_path / "fehlt")).upload(f, "/", "a.pdf")
    assert e.value.retry is True
