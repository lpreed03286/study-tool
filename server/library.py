"""The student's files for each class, kept so Claude never has to reread them.

Adding a file saves its text page by page, which uses no Claude usage. Claude then adds
a summary and topic list once, and these build one overview per class. Later work starts
from an overview, searches the saved text, and reads only the pages it needs.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from storage import read_json, write_json

OFFICE = {".pptx", ".ppt", ".odp", ".docx", ".doc", ".odt", ".rtf"}
SLIDES = {".pptx", ".ppt", ".odp"}
IMAGES = {".png", ".jpg", ".jpeg"}
TEXT = {".txt", ".md"}
ALLOWED = {".pdf", *OFFICE, *IMAGES, *TEXT}
MAX_FILE_BYTES = 200_000_000
MAX_IMPORT_BYTES = 8_000_000_000
MAX_IMPORT_PAGES = 200_000
EXPORT_FORMAT = 1
EXPORT_MANIFEST = "library-export.json"
MIN_PAGE_WORDS = 12  # A page with a picture and fewer words than this is probably a scan.
TEXT_PART_CHARS = 3000
MAX_READ_PAGES = 40
OUTLINE_CHARS = 80
SNIPPET_CHARS = 220
CONVERT_SECONDS = 180
DETAILS = ("class_name", "class_number", "subject", "doc_type")


class LibraryError(Exception):
    """Something to show the student, not a bug."""


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise LibraryError(f"This needs {command[0]}, which isn't installed.")
    except subprocess.TimeoutExpired:
        raise LibraryError(f"{command[0]} took too long on that file.")


def _to_pdf(original: Path, folder: Path) -> Path:
    """Office files become PDFs, so they have real page numbers and a page to link to."""
    with tempfile.TemporaryDirectory() as profile:
        # Its own profile, so this works while LibreOffice is open.
        _run(["soffice", "--headless", f"-env:UserInstallation=file://{profile}", "--convert-to", "pdf",
              "--outdir", str(folder), str(original)], CONVERT_SECONDS)
    made = folder / f"{original.stem}.pdf"
    if not made.exists():
        raise LibraryError(f"LibreOffice couldn't convert {original.name}.")
    view = folder / "view.pdf"
    made.replace(view)
    return view


def _pdf_pages(pdf: Path) -> list[str]:
    done = _run(["pdftotext", str(pdf), "-"], CONVERT_SECONDS)
    if done.returncode != 0:
        raise LibraryError("That PDF couldn't be read. It may be damaged or password-protected.")
    pages = done.stdout.decode(errors="replace").split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


def _pages_with_pictures(pdf: Path) -> set[int] | None:
    done = _run(["pdfimages", "-list", str(pdf)], 60)
    if done.returncode != 0:
        return None
    return {int(line.split()[0]) for line in done.stdout.decode(errors="replace").splitlines()[2:] if line.split()}


def _text_parts(text: str) -> list[str]:
    parts, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        if current and len(current) + len(paragraph) > TEXT_PART_CHARS:
            parts.append(current)
            current = ""
        current += paragraph + "\n\n"
    return [*parts, current] if current.strip() else parts or [""]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def class_label(details: dict) -> str:
    return " ".join(part for part in (details.get("class_number", ""), details.get("class_name", "")) if part)


class Library:
    def __init__(self, data: Path):
        self.root = Path(data) / "library"
        self.lock = threading.RLock()

    # --- Paths ---------------------------------------------------------------

    def _folder(self, file_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", str(file_id)):
            raise LibraryError("That isn't a library file.")
        return self.root / "files" / file_id

    def _page_path(self, file_id: str, page: int, description: bool = False) -> Path:
        return self._folder(file_id) / "pages" / f"{page:05d}{'.desc' if description else ''}.txt"

    def get(self, file_id: str) -> dict:
        path = self._folder(file_id) / "meta.json"
        if not path.exists():
            raise LibraryError("That file isn't in the library anymore.")
        return read_json(path, None)

    def _save(self, entry: dict) -> None:
        write_json(self._folder(entry["id"]) / "meta.json", entry)

    def all(self) -> list[dict]:
        folder = self.root / "files"
        if not folder.exists():
            return []
        found = [read_json(meta, None) for meta in folder.glob("*/meta.json")]
        return sorted(found, key=lambda entry: -entry["added_at"])

    def view_path(self, file_id: str) -> Path | None:
        """The file to show the student: the PDF if there's one, or else the original."""
        try:
            entry = self.get(file_id)
        except LibraryError:
            return None
        folder = self._folder(file_id)
        view = folder / "view.pdf"
        return view if view.exists() else folder / f"original{entry['ext']}"

    # --- Adding --------------------------------------------------------------

    @staticmethod
    def _clean_details(details: dict) -> dict:
        clean = {key: str(details.get(key) or "").strip() for key in DETAILS}
        if not clean["class_name"] and not clean["class_number"]:
            raise LibraryError("Give the class a name or a number.")
        return clean

    def add(self, source: Path, name: str, details: dict) -> dict:
        """source is moved in. A file already in that class is reported, not stored twice."""
        clean = self._clean_details(details)
        name = Path(str(name).replace("\\", "/")).name or "file"
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED:
            raise LibraryError(f"{name}: {ext or 'this type'} isn't supported. Use PDF, Word, PowerPoint, an image or a text file.")
        size = source.stat().st_size
        if size == 0:
            raise LibraryError(f"{name} is empty.")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        label = class_label(clean)
        with self.lock:
            for entry in self.all():
                if entry["sha256"] == digest and class_label(entry) == label:
                    return {**entry, "duplicate": True}
        file_id = uuid.uuid4().hex[:12]
        folder = self._folder(file_id)
        (folder / "pages").mkdir(parents=True)
        try:
            original = folder / f"original{ext}"
            shutil.move(str(source), original)
            pages, needs_reading = self._extract(original, ext, folder)
            for number, text in enumerate(pages, 1):
                self._page_path(file_id, number).write_text(text)
            entry = {
                "id": file_id, "name": name, "ext": ext, "sha256": digest, "size": size, "added_at": time.time(), **clean,
                "unit": "slide" if ext in SLIDES else "part" if ext in TEXT else "page", "pages": len(pages),
                "needs_reading": needs_reading, "summary": "", "topics": [], "indexed_at": None,
            }
            self._save(entry)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._write_overview(label)
        return entry

    @staticmethod
    def _extract(original: Path, ext: str, folder: Path) -> tuple[list[str], list[int]]:
        if ext in IMAGES:
            return [""], [1]
        if ext in TEXT:
            return _text_parts(original.read_text(errors="replace")), []
        pdf = _to_pdf(original, folder) if ext in OFFICE else original
        pages = _pdf_pages(pdf)
        pictures = _pages_with_pictures(pdf)
        # With no way to see pictures, a nearly empty page still might be a scan.
        return pages, [number for number, text in enumerate(pages, 1) if len(_words(text)) < MIN_PAGE_WORDS
                       and (pictures is None or number in pictures)]

    # --- Moving files between libraries ---------------------------------------

    def export(self, file_ids: list[str]) -> str:
        """A zip of these files and their saved text and index. Returns its one-time name."""
        entries = [self.get(file_id) for file_id in file_ids]
        if not entries:
            raise LibraryError("There's nothing to export. Check your filters.")
        folder = self.root / "exports"
        shutil.rmtree(folder, ignore_errors=True)  # Anything left from an unfinished download.
        folder.mkdir(parents=True)
        name = f"{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(folder / name, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr(EXPORT_MANIFEST, json.dumps({"format": EXPORT_FORMAT, "files": entries}, indent=1))
            for entry in entries:
                source = self._folder(entry["id"])
                for path in sorted(source.rglob("*")):
                    if path.is_file() and path.name != "meta.json":
                        archive.write(path, f"files/{entry['id']}/{path.relative_to(source).as_posix()}")
        return name

    def export_path(self, name: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{32}\.zip", name):
            return None
        path = self.root / "exports" / name
        return path if path.is_file() else None

    @staticmethod
    def _imported_entry(raw: dict) -> tuple[dict, str]:
        """Only known fields of the expected kinds, since the zip came from somewhere else."""
        if not isinstance(raw, dict):
            raise LibraryError("That isn't a library export.")
        ext = str(raw.get("ext", "")).lower()
        pages = raw.get("pages")
        if ext not in ALLOWED or not isinstance(pages, int) or not 1 <= pages <= MAX_IMPORT_PAGES:
            raise LibraryError("That export has a file this library can't use.")
        details = {key: str(raw.get(key) or "").strip()[:200] for key in DETAILS}
        if not details["class_name"] and not details["class_number"]:
            raise LibraryError("That export has a file with no class.")
        unit = raw.get("unit") if raw.get("unit") in ("page", "slide", "part") else "page"
        reading = [page for page in raw.get("needs_reading") or [] if isinstance(page, int) and 1 <= page <= pages]
        topics = [{"topic": str(topic.get("topic", ""))[:300], "pages": str(topic.get("pages", ""))[:60]}
                  for topic in raw.get("topics") or [] if isinstance(topic, dict)]
        indexed = raw.get("indexed_at") if isinstance(raw.get("indexed_at"), (int, float)) else None
        return {"name": Path(str(raw.get("name") or f"file{ext}").replace("\\", "/")).name[:200] or f"file{ext}", "ext": ext, **details,
                "unit": unit, "pages": pages, "needs_reading": reading, "summary": str(raw.get("summary") or "")[:5000],
                "topics": topics, "indexed_at": indexed}, str(raw.get("id", ""))

    def import_zip(self, source: Path) -> dict:
        """Nothing in the zip chooses where anything is written. Each name is matched, then built here."""
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile:
            raise LibraryError("That isn't a library export. Choose a .zip made with Export.")
        added, skipped, failed = [], [], []
        with archive:
            sizes = {info.filename: info.file_size for info in archive.infolist()}
            if sum(sizes.values()) > MAX_IMPORT_BYTES:
                raise LibraryError("That export is too large to unpack.")
            try:
                manifest = json.loads(archive.read(EXPORT_MANIFEST))
            except (KeyError, ValueError):
                raise LibraryError("That isn't a library export. Choose a .zip made with Export.")
            if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT or not isinstance(manifest.get("files"), list):
                raise LibraryError("That export is from a version this library can't read.")
            for raw in manifest["files"]:
                try:
                    entry = self._import_one(archive, sizes, raw)
                    (skipped if entry.get("duplicate") else added).append(entry["name"])
                except LibraryError as reason:
                    failed.append(str(reason))
        return {"added": added, "skipped": skipped, "failed": failed}

    def _import_one(self, archive: zipfile.ZipFile, sizes: dict, raw: dict) -> dict:
        entry, old_id = self._imported_entry(raw)
        prefix = f"files/{old_id}/"
        original = f"{prefix}original{entry['ext']}"
        if not re.fullmatch(r"[0-9a-f]{12}", old_id) or original not in sizes or sizes[original] > MAX_FILE_BYTES:
            raise LibraryError(f"{entry['name']}: this file is missing from the export.")
        digest = hashlib.sha256()
        with archive.open(original) as data:
            for chunk in iter(lambda: data.read(1_048_576), b""):
                digest.update(chunk)
        entry["sha256"] = digest.hexdigest()
        label = class_label(entry)
        with self.lock:
            for existing in self.all():
                if existing["sha256"] == entry["sha256"] and class_label(existing) == label:
                    return {**existing, "duplicate": True}
            # Keeping the id means source links still work when restoring your own export.
            file_id = old_id if not self._folder(old_id).exists() else uuid.uuid4().hex[:12]
            folder = self._folder(file_id)
            (folder / "pages").mkdir(parents=True)
            try:
                wanted = {original: f"original{entry['ext']}"}
                if entry["ext"] in OFFICE and f"{prefix}view.pdf" in sizes:
                    wanted[f"{prefix}view.pdf"] = "view.pdf"
                for name in sizes:
                    match = re.fullmatch(re.escape(prefix) + r"pages/(\d{5})(\.desc)?\.txt", name)
                    if match and 1 <= int(match.group(1)) <= entry["pages"]:
                        wanted[name] = f"pages/{match.group(1)}{match.group(2) or ''}.txt"
                for name, target in wanted.items():
                    with archive.open(name) as data, open(folder / target, "wb") as out:
                        shutil.copyfileobj(data, out)
                entry.update({"id": file_id, "added_at": time.time(), "size": sizes[original]})
                self._save(entry)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            self._write_overview(label)
        return entry

    def update(self, file_id: str, details: dict) -> dict:
        with self.lock:
            entry = self.get(file_id)
            before = class_label(entry)
            entry.update(self._clean_details(details))
            self._save(entry)
            for label in {before, class_label(entry)}:
                self._write_overview(label)
        return entry

    def delete(self, file_id: str) -> None:
        with self.lock:
            entry = self.get(file_id)
            shutil.rmtree(self._folder(file_id))
            self._write_overview(class_label(entry))

    # --- For Claude ----------------------------------------------------------

    def _files_in(self, class_name: str = "", file_id: str = "") -> list[dict]:
        if file_id:
            return [self.get(file_id)]
        wanted = _slug(class_name)
        return [entry for entry in self.all() if not wanted or wanted in (_slug(class_label(entry)), _slug(entry["class_name"]), _slug(entry["class_number"]))]

    def _page_text(self, file_id: str, page: int) -> str:
        text = self._page_path(file_id, page).read_text() if self._page_path(file_id, page).exists() else ""
        description = self._page_path(file_id, page, True)
        if description.exists():
            text += f"\n[Claude's description of this page's picture or handwriting]\n{description.read_text()}"
        return text

    def stored_text(self, file_id: str, page: int) -> str:
        """Only the file's own words, so a citation can't be checked against Claude's notes."""
        path = self._page_path(file_id, page)
        return path.read_text() if path.exists() else ""

    def outline(self, file_id: str) -> list[dict]:
        """The first line of each page. On slides that's the title, at almost no cost."""
        lines = []
        for page in range(1, self.get(file_id)["pages"] + 1):
            text = self.stored_text(file_id, page)
            first = next((line.strip() for line in text.splitlines() if line.strip()), "")
            lines.append({"page": page, "first_line": first[:OUTLINE_CHARS]})
        return lines

    def read(self, file_id: str, pages: list[int]) -> dict:
        entry = self.get(file_id)
        wanted = sorted({int(page) for page in pages if 1 <= int(page) <= entry["pages"]})
        if not wanted:
            raise LibraryError(f"{entry['name']} has pages 1 to {entry['pages']}.")
        if len(wanted) > MAX_READ_PAGES:
            raise LibraryError(f"That's {len(wanted)} pages, and the limit is {MAX_READ_PAGES} at a time. Search for what you need first.")
        unread = self._unread(entry)
        return {"file": file_id, "name": entry["name"], "unit": entry["unit"],
                "pages": [{"page": page, "text": self._page_text(file_id, page),
                           "picture_not_read_yet": page in unread} for page in wanted]}

    def _unread(self, entry: dict) -> list[int]:
        return [page for page in entry["needs_reading"] if not self._page_path(entry["id"], page, True).exists()]

    def search(self, query: str, class_name: str = "", file_id: str = "", limit: int = 30) -> dict:
        terms = list(dict.fromkeys(_words(query)))
        if not terms:
            raise LibraryError("Give some words to search for.")
        phrase = " ".join(_words(query))
        hits = []
        for entry in self._files_in(class_name, file_id):
            for page in range(1, entry["pages"] + 1):
                text = self._page_text(entry["id"], page)
                words = _words(text)
                if not all(term in words for term in terms):
                    continue
                score = sum(words.count(term) for term in terms) + (100 if phrase in " ".join(words) else 0)
                hits.append((score, entry, page, text))
        hits.sort(key=lambda hit: -hit[0])
        return {"total": len(hits), "hits": [
            {"file": entry["id"], "name": entry["name"], "class": class_label(entry), "unit": entry["unit"],
             "page": page, "snippet": self._snippet(text, terms)} for _, entry, page, text in hits[:limit]]}

    @staticmethod
    def _snippet(text: str, terms: list[str]) -> str:
        flat = " ".join(text.split())
        found = re.search(r"\b" + re.escape(terms[0]) + r"\b", flat, re.I)
        start = max(0, found.start() - SNIPPET_CHARS // 2) if found else 0
        return flat[start:start + SNIPPET_CHARS]

    def save_index(self, file_id: str, summary: str, topics: list[dict], descriptions: list[dict]) -> dict:
        if not str(summary or "").strip():
            raise LibraryError("Give the file a short summary.")
        with self.lock:
            entry = self.get(file_id)
            for item in descriptions or []:
                page = int(item["page"])
                if not 1 <= page <= entry["pages"]:
                    raise LibraryError(f"{entry['name']} has no page {page}.")
                self._page_path(file_id, page, True).write_text(str(item["text"]))
            entry.update({"summary": str(summary).strip(), "indexed_at": time.time(),
                          "topics": [{"topic": str(t["topic"]), "pages": str(t.get("pages", ""))} for t in topics or []]})
            self._save(entry)
            self._write_overview(class_label(entry))
        return {"file": file_id, "indexed": True, "pictures_not_read_yet": self._unread(entry), "overview": class_label(entry)}

    def pending(self, entries: list[dict] | None = None) -> list[dict]:
        """What still needs indexing, and how to see any pictures that need reading."""
        out = []
        for entry in entries if entries is not None else self.all():
            unread = self._unread(entry)
            if entry["indexed_at"] and not unread:
                continue
            view = self.view_path(entry["id"])
            out.append({"file": entry["id"], "name": entry["name"], "class": class_label(entry), "doc_type": entry["doc_type"],
                        "unit": entry["unit"], "pages": entry["pages"], "pictures_to_read": unread, "path_for_read_tool": str(view)})
        return out

    # --- The overview Claude reads first -------------------------------------

    def _overview_path(self, label: str) -> Path:
        return self.root / "classes" / f"{_slug(label) or 'unnamed'}.md"

    def _write_overview(self, label: str) -> None:
        """Rebuilt from each file's index, so it can't drift from what's stored."""
        entries = [entry for entry in self.all() if class_label(entry) == label]
        path = self._overview_path(label)
        if not entries:
            path.unlink(missing_ok=True)
            return
        lines = [f"# {label}", f"Subject: {entries[0]['subject'] or 'not given'}", ""]
        for entry in sorted(entries, key=lambda item: item["name"].lower()):
            kind = entry["doc_type"] or entry["ext"].lstrip(".")
            lines.append(f"## {entry['name']} [{entry['id']}] {kind}, {entry['pages']} {entry['unit']}s")
            if entry["indexed_at"]:
                lines.append(entry["summary"])
                lines.extend(f"- {topic['topic']}: {entry['unit']}s {topic['pages']}" if topic["pages"] else f"- {topic['topic']}" for topic in entry["topics"])
            else:
                lines.append("Not indexed yet. Search it, or use its outline.")
            unread = self._unread(entry)
            if unread:
                lines.append(f"Pictures not read yet on {entry['unit']}s: {', '.join(map(str, unread))}")
            lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines))

    def overview(self, class_name: str = "") -> dict:
        """One class's overview, or a short list of the classes when none is named."""
        if class_name:
            entries = self._files_in(class_name)
            labels = {class_label(entry) for entry in entries}
            if not labels:
                return {"classes": self._class_list(), "note": f"No class matches {class_name!r}."}
            return {"overviews": {label: self._overview_path(label).read_text() for label in sorted(labels)}}
        return {"classes": self._class_list()}

    def _class_list(self) -> list[dict]:
        classes: dict[str, dict] = {}
        for entry in self.all():
            label = class_label(entry)
            slot = classes.setdefault(label, {"class": label, "subject": entry["subject"], "files": 0, "pages": 0, "not_indexed": 0})
            slot["files"] += 1
            slot["pages"] += entry["pages"]
            slot["not_indexed"] += not entry["indexed_at"]
        return sorted(classes.values(), key=lambda slot: slot["class"].lower())
