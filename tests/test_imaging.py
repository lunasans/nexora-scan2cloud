import re
from datetime import datetime

from PIL import Image, ImageDraw

from scan2cloud import imaging
from scan2cloud.config import Profile

WHEN = datetime(2026, 10, 22, 14, 5, 9)


def blank_page(size=(850, 1100)):
    img = Image.new("L", size, 250)
    # Schatten am Rand, wie bei Einzugsscannern
    ImageDraw.Draw(img).rectangle((0, 0, 20, size[1]), fill=40)
    return img


def text_page(size=(850, 1100)):
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    for y in range(100, 900, 30):
        d.rectangle((100, y, 700, y + 8), fill=0)
    return img


def test_blank_detection():
    assert imaging.is_blank(blank_page())
    assert not imaging.is_blank(text_page())


def test_remove_blank_pages():
    pages = [text_page(), blank_page(), text_page()]
    assert len(imaging.remove_blank_pages(pages, 0.5)) == 2


def test_file_name_template_and_numbering(tmp_path):
    p = Profile(name="Rechnung", filename_template="{datum}_{profil}_{nr}")
    assert imaging.unique_base_name(p, tmp_path, ".pdf", WHEN) == "2026-10-22_Rechnung_001"
    (tmp_path / "2026-10-22_Rechnung_001.pdf").touch()
    assert imaging.unique_base_name(p, tmp_path, ".pdf", WHEN) == "2026-10-22_Rechnung_002"


def test_template_without_nr_gets_suffix(tmp_path):
    p = Profile(name="Brief", filename_template="{profil}-{jahr}")
    assert imaging.unique_base_name(p, tmp_path, ".pdf", WHEN) == "Brief-2026"
    (tmp_path / "Brief-2026.pdf").touch()
    assert imaging.unique_base_name(p, tmp_path, ".pdf", WHEN) == "Brief-2026_2"


def test_invalid_characters_and_unknown_placeholder(tmp_path):
    p = Profile(name="A/B", filename_template="{profil}:{unbekannt}")
    assert imaging.unique_base_name(p, tmp_path, ".pdf", WHEN) == "A_B_{unbekannt}"


def test_remote_folder_placeholders():
    p = Profile(name="Rechnung", remote_folder="Scans\\Rechnungen/{jahr}/{monat}/")
    assert imaging.remote_folder_for(p, WHEN) == "/Scans/Rechnungen/2026/10"


def test_write_multipage_pdf(tmp_path):
    p = Profile(file_format="pdf", color_mode="bw", dpi=300)
    files = imaging.write_output([text_page(), text_page()], p, tmp_path, WHEN)
    assert len(files) == 1 and files[0].suffix == ".pdf"
    data = files[0].read_bytes()
    assert data.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page(?!s)", data)) == 2


def test_write_jpgs(tmp_path):
    p = Profile(name="Foto", file_format="jpg", color_mode="color")
    files = imaging.write_output([text_page(), text_page()], p, tmp_path, WHEN)
    assert [f.name for f in files] == ["2026-10-22_Foto_001_S1.jpg", "2026-10-22_Foto_001_S2.jpg"]
    with Image.open(files[0]) as im:
        assert im.mode == "RGB"
    # nächster Scan bekommt eine neue Nummer
    files = imaging.write_output([text_page()], p, tmp_path, WHEN)
    assert files[0].name == "2026-10-22_Foto_002.jpg"
