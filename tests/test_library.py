"""The class library, on real files. Skips what needs tools this machine doesn't have."""
import json
import shutil
import subprocess
import zipfile

import pytest

import sources
from engine import Engine
from library import Library, LibraryError, class_label

DETAILS = {"class_name": "Waves", "class_number": "PHYS 101", "subject": "Physics", "doc_type": "Lecture slides"}
needs_pdf = pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs pdftotext")
needs_office = pytest.mark.skipif(not (shutil.which("soffice") and shutil.which("pdftotext")), reason="needs LibreOffice")


def small_pdf(path, pages):
    """Pages of real text, built by hand so the test needs no PDF library."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] /Contents {len(objects)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    path.write_bytes(out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n".encode())
    return path


PAGES = ["Oscillations: a mass on a spring moves back and forth repeating its motion in time forever",
         "Standing waves have nodes that never move and antinodes that move the most of all points",
         "Resonance happens when a driving frequency matches the natural frequency of the system exactly"]


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "data")


def add(library, tmp_path, name="slides.pdf", pages=PAGES, **details):
    return library.add(small_pdf(tmp_path / f"in-{len(list(tmp_path.iterdir()))}.pdf", pages), name, {**DETAILS, **details})


@needs_pdf
def test_a_pdf_is_saved_with_its_text_page_by_page(library, tmp_path):
    entry = add(library, tmp_path)
    assert entry["pages"] == 3 and entry["unit"] == "page" and entry["needs_reading"] == [] and entry["indexed_at"] is None
    assert "nodes" in library.read(entry["id"], [2])["pages"][0]["text"] and class_label(entry) == "PHYS 101 Waves"
    assert library.view_path(entry["id"]).suffix == ".pdf"
    assert library.outline(entry["id"])[2]["first_line"].startswith("Resonance")


@needs_pdf
def test_search_finds_the_right_page_needs_every_word_and_ranks_exact_phrases_first(library, tmp_path):
    entry = add(library, tmp_path)
    found = library.search("antinodes nodes")
    assert found["total"] == 1 and found["hits"][0]["page"] == 2 and found["hits"][0]["file"] == entry["id"] and "nodes" in found["hits"][0]["snippet"]
    assert library.search("antinodes resonance")["total"] == 0
    other = add(library, tmp_path, "other.pdf", ["natural frequency of the system appears on this page once again today",
                                                  "frequency here and natural over there, on a page with other words in it"], class_name="Other", class_number="")
    assert [hit["file"] for hit in library.search("natural frequency", class_name="Other")["hits"]] == [other["id"]] * 2
    assert library.search("natural frequency", file_id=entry["id"])["hits"][0]["page"] == 3
    with pytest.raises(LibraryError, match="words to search"):
        library.search("  !! ")


@needs_pdf
def test_the_same_file_in_a_class_is_not_stored_twice_but_can_be_in_another(library, tmp_path):
    first = add(library, tmp_path)
    assert add(library, tmp_path)["duplicate"] is True and len(library.all()) == 1
    assert add(library, tmp_path, class_name="Other")["id"] != first["id"] and len(library.all()) == 2


@needs_pdf
def test_the_overview_comes_from_each_files_index_and_stays_current(library, tmp_path):
    entry = add(library, tmp_path)
    assert "Not indexed yet" in library.overview("PHYS 101")["overviews"]["PHYS 101 Waves"]
    library.save_index(entry["id"], "Slides on oscillations.", [{"topic": "Standing waves", "pages": "2"}], [])
    text = library.overview("Waves")["overviews"]["PHYS 101 Waves"]
    assert "Slides on oscillations." in text and "Standing waves: pages 2" in text and entry["id"] in text
    assert (library.root / "classes" / "phys-101-waves.md").read_text() == text
    assert library.overview()["classes"] == [{"class": "PHYS 101 Waves", "subject": "Physics", "files": 1, "pages": 3, "not_indexed": 0}]
    assert "No class matches" in library.overview("Chemistry")["note"]

    moved = library.update(entry["id"], {**DETAILS, "class_name": "Vibrations"})
    assert class_label(moved) == "PHYS 101 Vibrations" and not (library.root / "classes" / "phys-101-waves.md").exists()
    library.delete(entry["id"])
    assert library.all() == [] and library.overview()["classes"] == [] and not (library.root / "classes" / "phys-101-vibrations.md").exists()
    assert library.view_path(entry["id"]) is None


def test_a_picture_is_kept_to_read_once_and_its_description_is_saved_for_good(library, tmp_path):
    (tmp_path / "in.png").write_bytes(b"\x89PNG fake")
    entry = library.add(tmp_path / "in.png", "notes.png", {**DETAILS, "doc_type": "Notes"})
    assert entry["needs_reading"] == [1]
    pending = library.pending()[0]
    assert pending["pictures_to_read"] == [1] and pending["path_for_read_tool"].endswith(".png")
    assert library.read(entry["id"], [1])["pages"][0]["picture_not_read_yet"] is True
    saved = library.save_index(entry["id"], "Handwritten notes.", [], [{"page": 1, "text": "F = -kx, written by hand"}])
    assert saved["pictures_not_read_yet"] == [] and library.pending() == []
    page = library.read(entry["id"], [1])["pages"][0]
    assert page["picture_not_read_yet"] is False and "written by hand" in page["text"] and "Claude's description" in page["text"]
    assert library.search("written hand")["total"] == 1
    assert library.stored_text(entry["id"], 1) == "", "a citation can't be checked against Claude's own notes"


def test_a_text_file_is_split_into_parts(library, tmp_path):
    (tmp_path / "in.md").write_text("\n\n".join(f"Paragraph {n} " + "word " * 100 for n in range(30)))
    entry = library.add(tmp_path / "in.md", "notes.md", DETAILS)
    assert entry["unit"] == "part" and entry["pages"] > 3 and "Paragraph 29" in library.read(entry["id"], [entry["pages"]])["pages"][0]["text"]


@pytest.mark.parametrize("name,details,words", [
    ("virus.exe", DETAILS, "isn't supported"),
    ("noext", DETAILS, "isn't supported"),
    ("a.pdf", {"subject": "Physics"}, "name or a number"),
])
def test_what_cannot_be_added_is_refused_and_leaves_nothing_behind(library, tmp_path, name, details, words):
    (tmp_path / "in").write_bytes(b"data")
    with pytest.raises(LibraryError, match=words):
        library.add(tmp_path / "in", name, details)
    assert library.all() == []


@needs_pdf
def test_a_damaged_pdf_is_refused_and_leaves_nothing_behind(library, tmp_path):
    (tmp_path / "in.pdf").write_bytes(b"this is not a pdf")
    with pytest.raises(LibraryError, match="couldn't be read"):
        library.add(tmp_path / "in.pdf", "broken.pdf", DETAILS)
    assert library.all() == [] and not list((library.root / "files").iterdir())


@pytest.mark.parametrize("bad", ["../../etc", "", "ABC", "a" * 12 + "/x", "1234567890123"])
def test_a_file_id_cannot_reach_anywhere_else(library, bad):
    with pytest.raises(LibraryError):
        library.get(bad)
    assert library.view_path(bad) is None


def test_a_file_name_cannot_leave_its_folder(library, tmp_path):
    (tmp_path / "in.txt").write_text("hello there")
    entry = library.add(tmp_path / "in.txt", "../../../evil.txt", DETAILS)
    assert entry["name"] == "evil.txt" and library.view_path(entry["id"]).parent.parent == library.root / "files"


@needs_office
def test_word_and_powerpoint_become_real_pages(library, tmp_path):
    source = tmp_path / "w.txt"
    source.write_text("Word files keep their text after being converted so it can be searched easily today")
    subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{tmp_path}/lo", "--convert-to", "docx", "--outdir", str(tmp_path), str(source)], capture_output=True, timeout=180)
    word = library.add(tmp_path / "w.docx", "w.docx", DETAILS)
    assert word["unit"] == "page" and word["pages"] == 1 and library.view_path(word["id"]).name == "view.pdf"
    assert library.search("converted searched")["hits"][0]["file"] == word["id"]

    pdf = small_pdf(tmp_path / "deck.pdf", ["Slide one talks about oscillations and many other things worth knowing here", "Slide two talks about waves and many other things worth knowing here"])
    subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{tmp_path}/lo", "--infilter=impress_pdf_import", "--convert-to", "pptx", "--outdir", str(tmp_path), str(pdf)], capture_output=True, timeout=180)
    if not (tmp_path / "deck.pptx").exists():
        pytest.skip("this LibreOffice can't make a pptx from a pdf")
    deck = library.add(tmp_path / "deck.pptx", "deck.pptx", DETAILS)
    assert deck["unit"] == "slide" and deck["pages"] == 2 and library.search("waves worth")["hits"][0]["page"] == 2


@needs_pdf
def test_a_citation_is_checked_against_the_files_own_text_on_that_page(library, tmp_path):
    entry = add(library, tmp_path)
    checker = sources.Checker(library.root.parent)
    source = {"title": "Slides", "library": entry["id"], "page": 2, "quote": "nodes that never move and antinodes"}
    result = checker.check(source)
    assert result["status"] == sources.CONFIRMED and result["kind"] == "library" and result["file_id"] == entry["id"] and result["unit"] == "page"
    for changed, words in (({"page": 1}, "isn't on page 1"), ({"page": 9}, "pages 1 to 3"), ({"page": None}, "pages 1 to 3"),
                           ({"library": "000000000000"}, "isn't in the library"), ({"quote": "nodes that always move around"}, "isn't on")):
        with pytest.raises(sources.Refused, match=words):
            checker.check({**source, **changed})


def test_a_handwritten_page_is_labeled_rather_than_confirmed_from_claudes_notes(library, tmp_path):
    (tmp_path / "in.png").write_bytes(b"\x89PNG fake")
    entry = library.add(tmp_path / "in.png", "notes.png", DETAILS)
    library.save_index(entry["id"], "Notes.", [], [{"page": 1, "text": "the force is proportional to displacement"}])
    result = sources.Checker(library.root.parent).check({"title": "Notes", "library": entry["id"], "page": 1, "quote": "the force is proportional to displacement"})
    assert result["status"] == sources.UNCHECKED and "open it to check" in result["note"]


@needs_pdf
def test_indexing_a_file_finishes_its_request_and_retrying_replaces_a_stopped_one(tmp_path):
    events = []
    engine = Engine(tmp_path, lambda *a: None, events.append)
    one = engine.library.add(small_pdf(tmp_path / "a.pdf", PAGES), "a.pdf", DETAILS)
    two = engine.library.add(small_pdf(tmp_path / "b.pdf", PAGES[:2]), "b.pdf", DETAILS)
    view = engine.request_index([one["id"], two["id"]])
    assert all(f["indexing"] for f in view["files"]) and "make-index" in events[-1]["text"] and one["id"] in events[-1]["text"]
    assert engine.home_view()["requests"][0]["form"]["kind"] == "index"

    engine.save_library_index({"file": one["id"], "summary": "S.", "topics": []})
    assert [f["indexing"] for f in engine.library_view()["files"] if f["id"] == one["id"]] == [False]
    assert engine.home_view()["requests"][0]["form"]["files"] == [two["id"]]

    engine.interrupt_requests()
    stopped = next(f for f in engine.library_view()["files"] if f["id"] == two["id"])
    assert "closed" in stopped["index_stopped"] and not stopped["indexing"]
    engine.request_index([two["id"]])
    assert [r["state"] for r in engine.home_view()["requests"]] == ["waiting"]
    engine.delete_library_file(two["id"])
    assert engine.home_view()["requests"] == []


def test_a_form_request_can_name_a_library_class(tmp_path):
    events = []
    engine = Engine(tmp_path, lambda *a: None, events.append)
    engine.request_set({"kind": "study_guide", "topics": "Waves", "library_class": "PHYS 101 Waves"})
    assert "library_overview" in events[-1]["text"] and "PHYS 101 Waves" in events[-1]["text"]
    engine.request_set({"kind": "study_guide", "topics": "Waves"})
    assert "library" not in events[-1]["text"].replace("worksheet page", "").replace("my page", "")


def exported(library, ids):
    return library.root / "exports" / library.export(ids)


@needs_pdf
def test_export_and_import_bring_a_file_and_its_index_to_another_library(library, tmp_path):
    entry = add(library, tmp_path)
    library.save_index(entry["id"], "Slides on oscillations.", [{"topic": "Standing waves", "pages": "2"}], [])
    path = exported(library, [entry["id"]])
    other = Library(tmp_path / "other")
    report = other.import_zip(path)
    assert report == {"added": ["slides.pdf"], "skipped": [], "failed": []}
    moved = other.all()[0]
    assert moved["indexed_at"] and moved["summary"] == "Slides on oscillations." and class_label(moved) == "PHYS 101 Waves"
    assert other.search("antinodes")["hits"][0]["page"] == 2, "the saved text comes along, so it needn't be read again"
    assert "Standing waves: pages 2" in other.overview("PHYS 101")["overviews"]["PHYS 101 Waves"]
    assert other.view_path(moved["id"]).read_bytes() == library.view_path(entry["id"]).read_bytes()
    assert other.import_zip(path) == {"added": [], "skipped": ["slides.pdf"], "failed": []}, "not stored twice"


@needs_pdf
def test_export_holds_only_the_files_asked_for(library, tmp_path):
    keep = add(library, tmp_path)
    secret = add(library, tmp_path, "secret.pdf", ["A private page of notes that should not leave this library for any reason at all"], class_name="Private")
    path = exported(library, [keep["id"]])
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert not any(secret["id"] in name for name in names)
        assert b"Private" not in archive.read("library-export.json") and "server.json" not in names
    with pytest.raises(LibraryError, match="nothing to export"):
        library.export([])


def test_a_picture_descriptions_come_along_and_restoring_keeps_the_id(library, tmp_path):
    (tmp_path / "in.png").write_bytes(b"\x89PNG fake")
    entry = library.add(tmp_path / "in.png", "notes.png", DETAILS)
    library.save_index(entry["id"], "Notes.", [], [{"page": 1, "text": "F = -kx by hand"}])
    path = exported(library, [entry["id"]])
    library.delete(entry["id"])
    assert library.import_zip(path)["added"] == ["notes.png"]
    back = library.get(entry["id"])
    assert back["needs_reading"] == [1] and library.pending() == [] and "by hand" in library.read(entry["id"], [1])["pages"][0]["text"]


def make_zip(path, manifest, files=()):
    with zipfile.ZipFile(path, "w") as archive:
        if manifest is not None:
            archive.writestr("library-export.json", json.dumps(manifest))
        for name, body in files:
            archive.writestr(name, body)
    return path


GOOD = {"id": "abcdefabcdef", "name": "a.txt", "ext": ".txt", "class_name": "C", "pages": 1}


def test_a_hostile_zip_cannot_write_outside_its_folder_or_run_anything(library, tmp_path):
    prefix = "files/abcdefabcdef/"
    path = make_zip(tmp_path / "bad.zip", {"format": 1, "files": [{**GOOD, "name": "../../../evil.txt", "extra": "ignored", "sha256": "lies"}]}, [
        (f"{prefix}original.txt", "hello"), (f"{prefix}pages/00001.txt", "hello"), (f"{prefix}pages/../../../../evil.py", "boom"),
        (f"{prefix}pages/99999.txt", "beyond"), (f"{prefix}meta.json", '{"id": "x"}'), ("../../outside.txt", "boom")])
    assert library.import_zip(path)["added"] == ["evil.txt"]
    entry = library.all()[0]
    assert entry["name"] == "evil.txt" and "extra" not in entry and entry["sha256"] != "lies"
    folder = library.root / "files" / entry["id"]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == ["meta.json", "original.txt", "pages/00001.txt"]
    assert not list(tmp_path.rglob("evil.py")) and not list(tmp_path.rglob("outside.txt"))


@pytest.mark.parametrize("raw,words", [
    ({**GOOD, "ext": ".exe"}, "can't use"), ({**GOOD, "ext": ".py"}, "can't use"), ({**GOOD, "pages": "many"}, "can't use"), ({**GOOD, "pages": 0}, "can't use"),
    ({**GOOD, "class_name": ""}, "no class"), ({**GOOD, "id": "../../etc"}, "missing"), ({**GOOD, "id": "aaaaaaaaaaaa"}, "missing"), ("nonsense", "isn't a library export"),
])
def test_a_bad_entry_is_reported_and_leaves_nothing_behind(library, tmp_path, raw, words):
    path = make_zip(tmp_path / "bad.zip", {"format": 1, "files": [raw]}, [("files/abcdefabcdef/original.txt", "hello")])
    report = library.import_zip(path)
    assert report["added"] == [] and words in report["failed"][0] and library.all() == []


def test_what_is_not_an_export_is_refused(library, tmp_path):
    (tmp_path / "x.zip").write_bytes(b"not a zip at all")
    with pytest.raises(LibraryError, match="isn't a library export"):
        library.import_zip(tmp_path / "x.zip")
    with pytest.raises(LibraryError, match="isn't a library export"):
        library.import_zip(make_zip(tmp_path / "y.zip", None, [("readme.txt", "hi")]))
    with pytest.raises(LibraryError, match="version"):
        library.import_zip(make_zip(tmp_path / "z.zip", {"format": 99, "files": []}))


def test_an_export_name_cannot_reach_anything_else(library):
    for bad in ("../meta.json", "abc.zip", "a" * 32 + ".exe", ""):
        assert library.export_path(bad) is None
