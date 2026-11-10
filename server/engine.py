"""The worksheet's rules and everything saved on disk. No HTTP here, and no SymPy:
all math goes through `call_worker`, so the same rules can be tested directly.

Answer keys stay in this process. A view sends a key to the page only once a
step is pre-filled, a level-3 hint was asked for, or the problem is finished,
so nothing can be read ahead from the browser.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

import prompts
import sources
from courses import INTENTIONS, CourseError, Courses
from library import Library, LibraryError, class_label
from storage import read_json, write_json

HINT_NAMES = {1: "Nudge", 2: "Method", 3: "Answer"}
EXAMPLES_KEPT = 5
NEUTRAL_RATE_ATTEMPTS = 3  # how many tries' worth of doubt a step type starts with
DIFFICULTIES = ("textbook", "easier", "harder", "challenge")
MAX_REQUESTED_PROBLEMS = 30
DOC_KINDS = {"study_guide", "equation_sheet"}
FORM_KINDS = {"worksheet", *DOC_KINDS, "course"}
COURSE_ASK_KINDS = {"next_section", "retry_check", "edit_syllabus", "change_plan"}
EXAMPLE_SECONDS = 240
DOC_BASE_SECONDS = 60
CONCEPT_METHOD = "concept_questions"  # Teaches no section's technique, so it needs no repertoire check.
COURSE_METHOD = "course_items"  # Course check items aren't a textbook technique either.
COURSE_LOG_PREFIX = "course:"  # Course misses are kept apart, so they can't shift ODE study plans.


class UserError(Exception):
    """Something to show on the page, not a bug."""


def seal(problem: dict) -> str:
    """A fingerprint of the verified content; a hand-edited key won't match."""
    body = {k: problem[k] for k in ("statement", "method", "plan")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def book_key(name: str, edition: str, section: str) -> str:
    plain = re.sub(r"[^a-z0-9]+", " ", f"{name} {edition}".lower()).strip()
    return f"{plain} | {str(section).strip().lstrip('§').strip()}"


class Engine:
    def __init__(self, data: Path, call_worker: Callable[[str, str, dict, float], dict],
                 emit: Callable[[dict], None] = lambda event: None, allow_local_sources: bool = False):
        self.data = Path(data)
        self.allow_local_sources = allow_local_sources
        self.call_worker = call_worker
        self.emit = emit
        self.library = Library(self.data)
        self.courses = Courses(self.data)
        self.lock = threading.RLock()
        self.asks: dict[str, dict] = {}
        for folder in ("sets", "progress", "repertoire", "docs"):
            (self.data / folder).mkdir(parents=True, exist_ok=True)

    # --- Paths ---------------------------------------------------------------

    def _set_path(self, set_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", set_id):
            raise UserError("That isn't a set name.")
        return self.data / "sets" / f"{set_id}.json"

    def _doc_path(self, doc_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", doc_id):
            raise UserError("That isn't a document name.")
        return self.data / "docs" / f"{doc_id}.json"

    def _progress_path(self, set_id: str) -> Path:
        return self.data / "progress" / f"{set_id}.json"

    @property
    def _log_path(self) -> Path:
        return self.data / "error_log.json"

    @property
    def _coverage_path(self) -> Path:
        return self.data / "coverage.json"

    @property
    def _requests_path(self) -> Path:
        return self.data / "requests.json"

    @property
    def repertoire(self) -> str:
        return str(self.data / "repertoire")

    # --- Sets ----------------------------------------------------------------

    def load_set(self, set_id: str) -> dict:
        path = self._set_path(set_id)
        if not path.exists():
            raise UserError(f"There's no set called {set_id}.")
        worksheet = read_json(path, None)
        for problem in worksheet["problems"]:
            problem["sealed"] = problem.get("seal") == seal(problem) and bool(problem["plan"].get("verified"))
        return worksheet

    def list_sets(self) -> list[dict]:
        found = []
        for path in sorted((self.data / "sets").glob("*.json"), key=lambda p: -p.stat().st_mtime):
            worksheet = read_json(path, None)
            found.append({"id": worksheet["id"], "title": worksheet["title"],
                          "problems": len(worksheet["problems"])})
        return found

    def load_progress(self, set_id: str) -> dict:
        return read_json(self._progress_path(set_id), {"active": None, "fade": 0, "problems": {}, "exam": None})

    def save_progress(self, set_id: str, progress: dict) -> None:
        write_json(self._progress_path(set_id), progress)

    def build_problem(self, spec: dict, problem_id: str, notation: dict, origin: str) -> dict:
        reply = self.call_worker("build", "build", {
            "method": spec["method"], "spec": spec, "notation": notation, "repertoire": self.repertoire,
        }, 240)
        if not reply.get("ok"):
            raise UserError(reply.get("error", "verification failed"))
        problem = {
            "id": problem_id, "statement": spec["statement"], "ic": spec.get("ic") or [],
            "method": spec["method"], "topic": spec.get("topic", ""), "section": spec.get("section", ""),
            "context": spec.get("context", ""), "origin": origin, "plan": reply["plan"],
        }
        problem["seal"] = seal(problem)
        return problem

    def create_set(self, request: dict) -> dict:
        """Refuses anything uncovered or unverified. What is refused is reported, not hidden."""
        book = request.get("textbook") or {}
        problems = request.get("problems") or []
        if not problems:
            raise UserError("A set needs at least one problem.")
        link = self._course_link(request, "set")
        uncovered = self.uncovered(book, problems)
        if uncovered:
            raise UserError(
                "These haven't had the repertoire check yet, so no set can be made from them: "
                + "; ".join(uncovered)
                + ". Call check_coverage, add any missing method with add_method, then record_coverage.")
        difficulty = request.get("difficulty") or "textbook"
        if difficulty not in DIFFICULTIES:
            raise UserError(f"Difficulty must be one of: {', '.join(DIFFICULTIES)}.")
        book_methods, problems, refused = self._cite_set(request.get("book_methods") or {}, problems)
        if refused:
            return {"ok": False, "created": False, "failed": refused}
        built, failed = [], []
        for index, spec in enumerate(problems):
            notation = (book_methods.get(spec.get("method")) or {}).get("notation") or {}
            try:
                built.append(self.build_problem(spec, f"p{len(built) + 1}", notation, request.get("origin", "generated")))
            except UserError as refusal:
                failed.append({"index": index, "statement": spec.get("statement"), "reason": str(refusal)})
        if failed:
            # All or nothing, so the student never sees a partial set by accident.
            return {"ok": False, "created": False, "failed": failed, "verified_ok": len(built)}
        set_id = self._new_id(request.get("title") or "worksheet", self._set_path)
        worksheet = {
            "id": set_id, "title": request.get("title") or "Worksheet", "created_at": time.time(),
            "textbook": book, "book_methods": book_methods, "problems": built,
            "weak_spots_used": request.get("weak_spots_used") or [], "difficulty": difficulty,
        }
        if link:
            worksheet["course"] = link
        write_json(self._set_path(set_id), worksheet)
        if link:
            self.courses.link(link["id"], link["section"], link["role"], set_id)
        if request.get("request_id"):
            self.dismiss_request(request["request_id"])
        return {"ok": True, "created": True, "set": set_id,
                "problems": [{"id": p["id"], "statement": p["statement"], "method": p["method"],
                              "section": p["section"], "topic": p["topic"],
                              "verified": p["plan"]["verified"]} for p in built]}

    def _cite_set(self, book_methods: dict, problems: list[dict]) -> tuple[dict, list[dict], list[dict]]:
        """Checks the worksheet's written claims: its method notes and concept questions."""
        checker = sources.Checker(self.data, self.allow_local_sources)
        written = [*book_methods.values(), *(q for p in problems for q in p.get("questions") or [])]
        checker.prefetch([s for item in written for s in item.get("sources") or [] if isinstance(s, dict)])
        refused = []
        notes = {}
        for method, note in book_methods.items():
            try:
                notes[method] = sources.cited(note, checker, f"the notes on {method}")
            except sources.Refused as reason:
                refused.append({"index": method, "statement": "book_methods", "reason": str(reason)})
        cited_problems = []
        for index, problem in enumerate(problems):
            questions = []
            for number, question in enumerate(problem.get("questions") or [], 1):
                try:
                    questions.append(sources.cited(question, checker, f"question {number}"))
                except sources.Refused as reason:
                    refused.append({"index": index, "statement": problem.get("statement"), "reason": str(reason)})
            cited_problems.append({**problem, "questions": questions} if problem.get("questions") else problem)
        return notes, cited_problems, refused

    def _new_id(self, title: str, path_for: Callable[[str], Path]) -> str:
        stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "worksheet"
        candidate, count = stem, 1
        while path_for(candidate).exists():
            count += 1
            candidate = f"{stem}-{count}"
        return candidate

    def add_problem(self, set_id: str, spec: dict) -> dict:
        worksheet = self.load_set(set_id)
        notation = ((worksheet.get("book_methods") or {}).get(spec.get("method")) or {}).get("notation") or {}
        problem = self.build_problem(spec, f"p{len(worksheet['problems']) + 1}", notation, "typed")
        worksheet["problems"].append(problem)
        for item in worksheet["problems"]:
            item.pop("sealed", None)
        write_json(self._set_path(set_id), worksheet)
        return problem

    # --- The class library -----------------------------------------------------

    def library_view(self) -> dict:
        requests = [r for r in self._load_requests() if r["form"].get("kind") == "index"]
        indexing = {file for r in requests if r["state"] == "waiting" for file in r["form"]["files"]}
        stopped = {file: r for r in requests if r["state"] == "failed" for file in r["form"]["files"]}
        files = []
        for entry in self.library.all():
            unread = len(self.library._unread(entry))
            files.append({**{k: entry[k] for k in ("id", "name", "ext", "size", "added_at", "class_name", "class_number", "subject",
                                                   "doc_type", "unit", "pages", "topics")},
                          "class": class_label(entry), "indexed": bool(entry["indexed_at"]), "pictures_unread": unread,
                          "indexing": entry["id"] in indexing,
                          "index_stopped": stopped[entry["id"]]["reason"] if entry["id"] in stopped else ""})
        return {"files": files}

    def request_index(self, file_ids: list[str]) -> dict:
        """Asks Claude to index these once. Retrying replaces any stopped request for them."""
        ids = [self.library.get(file_id)["id"] for file_id in file_ids]
        if not ids:
            return self.library_view()
        with self.lock:
            kept = []
            for request in self._load_requests():
                if request["form"].get("kind") == "index":
                    request["form"]["files"] = [f for f in request["form"]["files"] if f not in ids]
                    if not request["form"]["files"]:
                        continue
                kept.append(request)
            request = {"id": uuid.uuid4().hex[:12], "created_at": time.time(), "state": "waiting",
                       "form": {"kind": "index", "files": ids}}
            write_json(self._requests_path, [*kept, request])
        self._send_request(request)
        return self.library_view()

    def save_library_index(self, args: dict) -> dict:
        saved = self.library.save_index(args.get("file", ""), args.get("summary", ""), args.get("topics") or [], args.get("descriptions") or [])
        if not saved["pictures_not_read_yet"]:
            self._finish_index(saved["file"])
        return saved

    def _finish_index(self, file_id: str) -> None:
        with self.lock:
            kept = []
            for request in self._load_requests():
                if request["form"].get("kind") == "index":
                    request["form"]["files"] = [f for f in request["form"]["files"] if f != file_id]
                    if not request["form"]["files"]:
                        continue
                kept.append(request)
            write_json(self._requests_path, kept)

    def delete_library_file(self, file_id: str) -> dict:
        self.library.delete(file_id)
        self._finish_index(file_id)
        return self.library_view()

    # --- Study guides and equation sheets -------------------------------------

    def create_doc(self, request: dict) -> dict:
        """Like create_set: all or nothing, and what's refused is reported, not hidden."""
        kind = request.get("kind")
        if kind not in DOC_KINDS:
            raise UserError(f"kind has to be one of: {', '.join(sorted(DOC_KINDS))}.")
        blocks = request.get("blocks") or []
        if not blocks:
            raise UserError("A document needs at least one block.")
        link = self._course_link(request, "doc")
        if kind == "study_guide":
            self._require_reference(blocks)
        book = request.get("textbook") or {}
        examples = [b for b in blocks if b.get("type") == "example" and b.get("method")]
        uncovered = self.uncovered(book, examples)
        if uncovered:
            raise UserError(
                "These haven't had the repertoire check yet, so their examples can't be verified: "
                + "; ".join(uncovered)
                + ". Call check_coverage, add any missing method with add_method, then record_coverage.")
        checked, refused = sources.check_blocks(blocks, sources.Checker(self.data, self.allow_local_sources))
        if refused:
            return {"ok": False, "created": False, "failed": refused}
        reply = self.call_worker("build", "doc_build", {
            "blocks": blocks, "book_methods": request.get("book_methods") or {}, "repertoire": self.repertoire,
        }, DOC_BASE_SECONDS + EXAMPLE_SECONDS * len(examples))
        if reply["failed"]:
            return {"ok": False, "created": False, "failed": reply["failed"]}
        doc_id = self._new_id(request.get("title") or kind, self._doc_path)
        built = [{**block, **checked[index]} for index, block in enumerate(reply["blocks"])]
        doc = {
            "id": doc_id, "kind": kind, "title": request.get("title") or "Untitled", "created_at": time.time(),
            "subject": str(request.get("subject") or ""), "textbook": book,
            "source": request.get("source") or "memory", "blocks": built,
        }
        if link:
            doc["course"] = link
        write_json(self._doc_path(doc_id), doc)
        if link:
            self.courses.link(link["id"], link["section"], link["role"], doc_id)
        if request.get("request_id"):
            self.dismiss_request(request["request_id"])
        made = [b for b in built if b["type"] == "example"]
        cited_list = [s for b in built for s in b["sources"]]
        return {"ok": True, "created": True, "doc": doc_id,
                "sources_confirmed": sum(s["status"] == sources.CONFIRMED for s in cited_list),
                "sources_not_auto_checked": [{"title": s["title"], "why": s["note"]} for s in cited_list if s["status"] != sources.CONFIRMED],
                "blocks_in_own_words": sum(b["own_words"] for b in built),
                "examples_verified": sum(bool(b["verified"]) for b in made),
                "examples_not_checked": sum(not b["verified"] for b in made),
                "plots": sum(b["type"] == "plot" for b in built), "diagrams": sum(b["type"] == "diagram" for b in built)}

    @staticmethod
    def _require_reference(blocks: list[dict]) -> None:
        """The quick reference panel is built from these, so a guide can't go without them."""
        kinds = {block.get("type") for block in blocks}
        if "definition" not in kinds:
            raise UserError("A study guide needs a definition block for each key concept, for its quick reference panel.")
        if "equation" in kinds and "nomenclature" not in kinds:
            raise UserError("A study guide with equations needs a nomenclature block listing their symbols, for its quick reference panel.")

    def source_file(self, name: str) -> Path | None:
        """A saved source file by its hash name, which can't reach anything else."""
        if not re.fullmatch(r"[0-9a-f]{64}\.(pdf|png|jpg|jpeg)", name):
            return None
        path = self.data / "sources" / name
        return path if path.is_file() else None

    def load_doc(self, doc_id: str) -> dict:
        path = self._doc_path(doc_id)
        if not path.exists():
            raise UserError(f"There's no document called {doc_id}.")
        return read_json(path, None)

    # --- Questions on a study guide ------------------------------------------

    def _questions_path(self, doc_id: str) -> Path:
        self._doc_path(doc_id)
        return self.data / "doc_questions" / f"{doc_id}.json"

    def doc_questions(self, doc_id: str) -> dict:
        with self.lock:
            questions = read_json(self._questions_path(doc_id), [])
            for entry in questions:
                # The server restarted while Claude was answering, so no answer will come.
                if entry["state"] == "pending" and entry.get("ask") not in self.asks:
                    entry.update(state="failed", error="Claude didn't answer before the program restarted. Ask again.")
            return {"questions": questions}

    def ask_doc(self, doc_id: str, block, question: str) -> dict:
        question = question.strip()
        if not question:
            raise UserError("Type your question first.")
        if len(question) > 4000:
            raise UserError("That question is too long. Keep it under 4,000 characters.")
        with self.lock:
            doc = self.load_doc(doc_id)
            if doc["kind"] != "study_guide":
                raise UserError("Questions work on study guides.")
            if block is not None and (isinstance(block, bool) or not isinstance(block, int) or not 0 <= block < len(doc["blocks"])):
                raise UserError("That part of the guide isn't there anymore. Reload the page.")
            questions = self.doc_questions(doc_id)["questions"]
            earlier = [entry for entry in questions if entry["block"] == block and entry["state"] == "done"][-3:]
            entry = {"id": uuid.uuid4().hex, "block": block, "question": question, "state": "pending",
                     "answer": "", "error": "", "asked_at": time.time()}
            entry["ask"] = self._start_ask(prompts.doc_question(doc, block, question, earlier),
                                           {"kind": "doc_question", "doc": doc_id, "question": entry["id"]})
            questions.append(entry)
            self._questions_path(doc_id).parent.mkdir(exist_ok=True)
            write_json(self._questions_path(doc_id), questions)
            return {"ask": entry["ask"], "questions": questions}

    def delete_doc_question(self, doc_id: str, question_id: str) -> dict:
        with self.lock:
            questions = [entry for entry in self.doc_questions(doc_id)["questions"] if entry["id"] != question_id]
            if self._questions_path(doc_id).exists():
                write_json(self._questions_path(doc_id), questions)
            return {"questions": questions}

    def _finish_doc_question(self, meta: dict, text: str | None, error: str) -> None:
        questions = read_json(self._questions_path(meta["doc"]), [])
        for entry in questions:
            if entry["id"] == meta["question"]:
                if text:
                    entry.update(state="done", answer=text)
                else:
                    entry.update(state="failed", error=f"Claude couldn't be reached ({error}). Ask again.")
        write_json(self._questions_path(meta["doc"]), questions)

    # --- The home page and requests for new sets -----------------------------

    def home_view(self) -> dict:
        sets = []
        for path in (self.data / "sets").glob("*.json"):
            worksheet = self.load_set(path.stem)
            progress = self.load_progress(path.stem)
            problems = worksheet["problems"]
            tabs = [self._tab_view(number, problem, progress) for number, problem in enumerate(problems, 1)]
            skipped = sum(bool(tab.get("skipped")) for tab in tabs)
            saved = self._progress_path(path.stem)
            sets.append({
                "id": worksheet["id"], "title": worksheet["title"], "textbook": worksheet.get("textbook") or {},
                "difficulty": worksheet.get("difficulty") or "textbook",
                "sections": list(dict.fromkeys(str(p["section"]) for p in problems if p.get("section"))),
                "problems": len(problems) - skipped, "skipped": skipped, "done": sum(tab["complete"] for tab in tabs),
                "created_at": worksheet.get("created_at") or path.stat().st_mtime,
                "last_opened": saved.stat().st_mtime if saved.exists() else None,
            })
        docs = []
        for path in (self.data / "docs").glob("*.json"):
            doc = read_json(path, None)
            docs.append({"id": doc["id"], "kind": doc["kind"], "title": doc["title"], "subject": doc.get("subject", ""),
                         "textbook": doc.get("textbook") or {}, "created_at": doc["created_at"]})
        return {"sets": sets, "docs": docs, "requests": self._load_requests()}

    def _load_requests(self) -> list[dict]:
        return read_json(self._requests_path, [])

    @staticmethod
    def _clean_course_form(form: dict) -> dict:
        clean = {key: str(form.get(key) or "").strip()
                 for key in ("goal", "intention", "intention_details", "end_goal", "background", "time_per_week",
                             "materials", "library_class")}
        if not clean["goal"]:
            raise UserError("Say what you want to learn. One sentence is enough.")
        if clean["intention"] and clean["intention"] not in INTENTIONS:
            raise UserError("Choose what you're learning it for from the list.")
        return {**clean, "kind": "course"}

    @staticmethod
    def _clean_form(form: dict) -> dict:
        if form.get("kind") == "course":
            return Engine._clean_course_form(form)
        clean = {key: str(form.get(key) or "").strip()
                 for key in ("subject", "book", "edition", "topics", "focus", "typed", "pages", "library_class")}
        kind = form.get("kind") or "worksheet"
        if kind not in FORM_KINDS:
            raise UserError("Choose a worksheet, a study guide or an equation sheet.")
        if kind != "worksheet" and not clean["topics"]:
            raise UserError("Give the sections or topics it should cover.")
        if kind == "worksheet" and not clean["typed"] and not (clean["book"] and clean["topics"]):
            raise UserError("Give the textbook and the sections or topics, or type your own problems.")
        difficulty = form.get("difficulty") or "textbook"
        if difficulty not in DIFFICULTIES:
            raise UserError(f"Difficulty must be one of: {', '.join(DIFFICULTIES)}.")
        try:
            count = 8 if form.get("count") in (None, "") else int(form["count"])
        except (TypeError, ValueError):
            raise UserError("The number of problems has to be a whole number.")
        if not 1 <= count <= MAX_REQUESTED_PROBLEMS:
            raise UserError(f"The number of problems has to be from 1 to {MAX_REQUESTED_PROBLEMS}.")
        pages = [str(Path(line.strip()).expanduser()) for line in clean["pages"].splitlines() if line.strip()]
        for page in pages:
            if not Path(page).is_file():
                raise UserError(f"There's no file at {page}.")
        return {**clean, "kind": kind, "difficulty": difficulty, "count": count, "pages": pages,
                "examples": bool(form.get("examples")), "figures": bool(form.get("figures"))}

    def _send_request(self, request: dict) -> None:
        self.emit({"event": "prompt", "id": request["id"], "text": prompts.new_set(request["form"], request["id"])})

    def request_set(self, form: dict) -> dict:
        request = {"id": uuid.uuid4().hex[:12], "created_at": time.time(),
                   "form": self._clean_form(form), "state": "waiting"}
        with self.lock:
            write_json(self._requests_path, [*self._load_requests(), request])
        self._send_request(request)
        return self.home_view()

    def _update_request(self, request_id: str, **changes) -> dict | None:
        with self.lock:
            requests = self._load_requests()
            found = next((r for r in requests if r["id"] == request_id), None)
            if found:
                found.update(changes)
                write_json(self._requests_path, requests)
            return found

    def retry_request(self, request_id: str) -> dict:
        request = self._update_request(request_id, state="waiting", reason="")
        if not request:
            raise UserError("That request is gone. Press New to make it again.")
        if request["form"].get("kind") == "next_section":
            self.courses.set_generating(request["form"]["course"], request["form"]["section"], request_id)
        self._send_request(request)
        return self.home_view()

    def request_failed(self, request_id: str, reason: str) -> None:
        self._update_request(request_id, state="failed", reason=reason)
        self._clear_course_request(request_id)

    def dismiss_request(self, request_id: str) -> dict:
        with self.lock:
            write_json(self._requests_path, [r for r in self._load_requests() if r["id"] != request_id])
        self._clear_course_request(request_id)
        return self.home_view()

    def _clear_course_request(self, request_id: str) -> None:
        # Course trouble must never stop the rest of the app.
        try:
            self.courses.clear_request(request_id)
        except Exception:
            pass

    def interrupt_requests(self) -> None:
        """A new server means the session that was working on these has ended."""
        with self.lock:
            requests = self._load_requests()
            for request in requests:
                if request["state"] == "waiting":
                    request.update(state="failed", reason="The Claude session closed before this finished.")
            if requests:
                write_json(self._requests_path, requests)

    # --- Courses (experimental) ----------------------------------------------

    def _course_link(self, request: dict, made: str) -> dict | None:
        """Checked before anything is built, so a refused link builds nothing."""
        course_id = request.get("course")
        if not course_id:
            return None
        role = request.get("course_role") or ("lesson" if made == "doc" else "check")
        if made == "doc" and (role != "lesson" or request.get("kind") != "study_guide"):
            raise UserError("A course document can only be a section's lesson, made as a study guide with course_role \"lesson\".")
        if made == "set" and role == "lesson":
            raise UserError("A lesson is a study guide, so make it with create_doc.")
        section = request.get("section") or None
        self.courses.check_link(course_id, section, role, bool(request.get("regenerate")))
        if made == "set":
            self._check_course_sections(course_id, section, role, request.get("problems") or [])
        return {"id": course_id, "section": section if role != "placement" else None, "role": role}

    def _check_course_sections(self, course_id: str, section: str | None, role: str, problems: list[dict]) -> None:
        if role == "placement":
            wanted = self.courses.get(course_id)["placement"]["sections"]
            stray = sorted({str(p.get("section")) for p in problems} - set(wanted))
            if stray:
                raise UserError(f"Each placement item's section has to be one the placement covers ({', '.join(wanted)}), "
                                f"not {', '.join(stray)}.")
            empty = [s for s in wanted if not any(p.get("section") == s for p in problems)]
            if empty:
                raise UserError(f"The placement check has nothing for {', '.join(empty)}.")
            return
        stray = sorted({str(p.get("section")) for p in problems if p.get("section") != section})
        if stray:
            raise UserError(f"Every problem in this set is for section {section}, so set each problem's section to "
                            f"{section}, not {', '.join(stray)}.")

    def _prompt_set(self, worksheet: dict) -> dict:
        """Adds what a tutor needs to know about the course, for a set that belongs to one."""
        link = worksheet.get("course")
        if not link:
            return worksheet
        try:
            course = self.courses.get(link["id"])
        except CourseError:
            return worksheet
        plan = course.get("plan") or {}
        presentation = plan.get("presentation") or {}
        return {**worksheet, "course_context": {
            "title": course["title"], "goal": course["intake"]["goal"], "background": course["intake"]["background"],
            "intention": INTENTIONS.get(course["intake"].get("intention"), ""),
            "intention_details": course["intake"].get("intention_details", ""), "end_goal": course["intake"].get("end_goal", ""),
            "style": presentation.get("style", ""), "notes": presentation.get("notes", "")}}

    def _course_label(self, link: dict) -> dict:
        """What a course set's page shows about the course, or just the ids if it's gone."""
        label = {"id": link["id"], "role": link["role"], "section": link.get("section"), "title": "", "section_title": ""}
        try:
            course = self.courses.get(link["id"])
            label["title"] = course["title"]
            if link.get("section"):
                label["section_title"] = self.courses.section(course, link["section"])["title"]
        except CourseError:
            pass
        return label

    def _course_progress(self, set_id: str) -> None:
        """Records a finished course check. Never lets a course problem break the worksheet."""
        try:
            worksheet = self.load_set(set_id)
            if not worksheet.get("course"):
                return
            owner = self.courses.owner_of_set(set_id)
            if owner is None or owner[2] == "practice":
                return
            course, section, role = owner
            progress = self.load_progress(set_id)
            exam = progress.get("exam")
            if exam and exam.get("submitted_at"):
                if exam["results"]["score"]["pending"]:
                    return
                items, when = self._exam_items(worksheet, progress, exam), exam["submitted_at"]
            elif role == "check" and not exam:
                items, when = self._practice_items(worksheet, progress), 0.0
            else:
                return  # Placement counts only as one exam, with no hints.
            if items is None:
                return
            if role == "placement":
                by_section: dict[str, list[dict]] = {}
                for item in items:
                    by_section.setdefault(item["section"], []).append(item)
                self.courses.record_placement(course["id"], by_section, when)
            else:
                self.courses.record_check(course["id"], section["id"], set_id, items, when)
        except Exception:
            traceback.print_exc(file=sys.stderr)

    @staticmethod
    def _item(problem: dict, step: dict, **result) -> dict:
        return {"section": problem.get("section", ""), "objective": step.get("objective") or step["type"],
                "judged_by_claude": bool(step["check"].get("concept")), **result}

    def _practice_items(self, worksheet: dict, progress: dict) -> list[dict] | None:
        """None until every step is resolved. A pre-filled step showed its answer, so it doesn't count."""
        items = []
        for problem in worksheet["problems"]:
            if self._is_skipped(progress, problem):
                items += [self._item(problem, step, correct=False, skipped=True) for step in problem["plan"]["steps"]]
                continue
            state = self._problem_state(progress, problem)
            statuses = self._status_list(problem, state, self._resolve_fade(progress, state))
            if "unresolved" in statuses:
                return None
            for step, status in zip(problem["plan"]["steps"], statuses):
                record = self._step_state(state, step)
                items.append(self._item(problem, step, correct=status == "correct", skipped=status == "skipped",
                                        hinted=1 <= record["hint_level"] <= 2,
                                        answer_shown=record["hint_level"] >= 3 or status == "prefilled",
                                        judged_by_claude=bool(step["check"].get("concept") or (record["own"] or {}).get("state") == "valid")))
        return items

    def _exam_items(self, worksheet: dict, progress: dict, exam: dict) -> list[dict]:
        results = exam["results"]["problems"]
        items = []
        for problem in worksheet["problems"]:
            rows = results.get(problem["id"])
            for step in problem["plan"]["steps"]:
                row = (rows or {}).get(step["id"])
                # A problem skipped before the exam still counts against the check.
                items.append(self._item(problem, step, correct=bool(row) and row["status"] == "correct", skipped=row is None))
        return items

    def course_request(self, body: dict) -> dict:
        """A button on the course page. It becomes a request to Claude like the New form's."""
        kind = body.get("kind")
        if kind not in COURSE_ASK_KINDS:
            raise UserError("That isn't something the course page can ask for.")
        course = self.courses.get(body.get("course", ""))
        section_id = str(body.get("section") or "")
        if kind in ("next_section", "retry_check"):
            section = self.courses.section(course, section_id)
            if kind == "next_section" and section["status"] != "available":
                raise UserError("Only an available section with nothing made yet can be generated.")
            if kind == "retry_check" and not (section["check_set"] and section["status"] == "in_progress"):
                raise UserError("A retry check is for a section whose check you've taken but not passed yet.")
        form = {"kind": kind, "course": course["id"], "section": section_id, "note": str(body.get("note") or "").strip()[:500],
                "title": course["title"]}
        request = {"id": uuid.uuid4().hex[:12], "created_at": time.time(), "form": form, "state": "waiting"}
        if kind == "next_section":
            self.courses.set_generating(course["id"], section_id, request["id"])
        with self.lock:
            write_json(self._requests_path, [*self._load_requests(), request])
        self._send_request(request)
        return self.course_view(course["id"])

    def course_delete(self, course_id: str, remove_items: bool) -> dict:
        """Its documents and sets stay as ordinary items unless the student chose otherwise."""
        items = self.courses.delete(course_id)
        if remove_items:
            for item in items:
                if item["kind"] == "doc":
                    self._doc_path(item["id"]).unlink(missing_ok=True)
                else:
                    self._set_path(item["id"]).unlink(missing_ok=True)
                    self._progress_path(item["id"]).unlink(missing_ok=True)
        return self.courses_view()

    def _course_requests(self, course_id: str | None = None) -> list[dict]:
        kinds = COURSE_ASK_KINDS | {"course"}
        return [r for r in self._load_requests() if r["form"].get("kind") in kinds
                and (course_id is None or r["form"].get("course") == course_id)]

    def courses_view(self) -> dict:
        return {"courses": [self.courses.summary(c) for c in self.courses.all()], "requests": self._course_requests()}

    def course_view(self, course_id: str) -> dict:
        """The course with the titles and progress of what's linked, so the page needs one call."""
        course = self.courses.get(course_id)
        linked: dict[str, dict] = {}
        for section in self.courses.sections(course):
            if section.get("lesson_doc") and self._doc_path(section["lesson_doc"]).exists():
                linked[section["lesson_doc"]] = {"title": read_json(self._doc_path(section["lesson_doc"]), {}).get("title", "")}
            for set_id in [section.get("check_set"), *section.get("practice_sets", [])]:
                if set_id and self._set_path(set_id).exists():
                    linked[set_id] = self._set_summary(set_id)
        placement = (course.get("placement") or {}).get("set")
        if placement and self._set_path(placement).exists():
            linked[placement] = self._set_summary(placement)
        return {"course": course, "linked": linked, "requests": self._course_requests(course_id)}

    def _set_summary(self, set_id: str) -> dict:
        worksheet = self.load_set(set_id)
        progress = self.load_progress(set_id)
        tabs = [self._tab_view(n, p, progress) for n, p in enumerate(worksheet["problems"], 1)]
        return {"title": worksheet["title"], "done": sum(t["done"] for t in tabs), "total": sum(t["total"] for t in tabs)}

    def course_create(self, args: dict) -> dict:
        course = self.courses.create(args)
        if args.get("request_id"):
            self.dismiss_request(args["request_id"])
        return {"ok": True, "course": course["id"], "title": course["title"], "phase": course["phase"],
                "next": "Research the topic, then save it with course_save_research. See the make-course skill."}

    def course_save_research(self, args: dict) -> dict:
        """Research is the course's footing, so every source must check out and there must be at least one."""
        raw = args.get("sources") or []
        if not isinstance(raw, list) or not raw:
            raise UserError("Research needs at least one source you opened in this session.")
        checker = sources.Checker(self.data, self.allow_local_sources)
        checker.prefetch([s for s in raw if isinstance(s, dict)])
        checked, failed = [], []
        for index, source in enumerate(raw):
            try:
                checked.append(checker.check(source))
            except sources.Refused as reason:
                failed.append({"index": index, "title": (source or {}).get("title", ""), "reason": str(reason)})
        if failed:
            return {"ok": False, "saved": False, "failed": failed}
        course = self.courses.save_research(args.get("course", ""), args.get("summary", ""), checked)
        return {"ok": True, "saved": True, "course": course["id"], "phase": course["phase"],
                "sources_confirmed": sum(s["status"] == sources.CONFIRMED for s in checked),
                "sources_not_auto_checked": [{"title": s["title"], "why": s["note"]} for s in checked
                                             if s["status"] != sources.CONFIRMED]}

    def course_save_plan(self, args: dict) -> dict:
        course = self.courses.save_plan(args.get("course", ""), args.get("plan") or {}, args.get("change", ""))
        if args.get("request_id"):
            self.dismiss_request(args["request_id"])
        return {"ok": True, "course": course["id"], "phase": course["phase"], "plan": course["plan"]}

    def course_save_syllabus(self, args: dict) -> dict:
        course = self.courses.save_syllabus(args.get("course", ""), args.get("chapters") or [],
                                            bool(args.get("partial")), args.get("change", ""))
        if args.get("request_id"):
            self.dismiss_request(args["request_id"])
        return {"ok": True, "course": course["id"], "phase": course["phase"],
                "sections": [{"id": s["id"], "status": s["status"]} for s in self.courses.sections(course)]}

    def course_overview(self, course_id: str) -> dict:
        if not course_id:
            return {"courses": [self.courses.summary(c) for c in self.courses.all()]}
        return self.courses.overview(course_id)

    def course_section(self, course_id: str, section_id: str) -> dict:
        return self.courses.section_view(course_id, section_id)

    def course_placement(self, course_id: str, section_ids: list[str]) -> dict:
        course = self.courses.choose_placement(course_id, section_ids)
        return {"ok": True, "course": course["id"], "sections": course["placement"]["sections"],
                "next": "Build the placement check with create_set, passing course and course_role \"placement\", "
                        "with each item tagged with its section. See the make-course skill."}

    # --- Repertoire coverage -------------------------------------------------

    def list_methods(self) -> dict:
        return self.call_worker("build", "list_methods", {"repertoire": self.repertoire}, 60)

    def add_method(self, file_name: str) -> dict:
        return self.call_worker("build", "add_method", {"file": file_name, "repertoire": self.repertoire}, 500)

    def check_coverage(self, book: dict, sections: list[dict]) -> dict:
        coverage = read_json(self._coverage_path, {})
        report = []
        for item in sections:
            entry = coverage.get(book_key(book.get("name", ""), book.get("edition", ""), item["section"]))
            report.append({"section": item["section"], "topic": item.get("topic"), "recorded": entry})
        return {"sections": report, **self.list_methods(), "repertoire_folder": self.repertoire,
                "method_examples": str(Path(__file__).with_name("methods.py"))}

    def record_coverage(self, book: dict, entries: list[dict]) -> dict:
        known = {m["id"] for m in self.list_methods()["methods"]}
        unknown = sorted({m for e in entries for m in e.get("methods", [])} - known)
        if unknown:
            raise UserError(f"Not in the repertoire yet: {', '.join(unknown)}. Add them with add_method first.")
        for entry in entries:
            if not entry.get("methods"):
                raise UserError(f"Section {entry.get('section')} has no methods. Add what it needs first.")
            if not entry.get("rationale"):
                raise UserError(f"Section {entry.get('section')} needs a rationale saying why these methods fit it.")
        coverage = read_json(self._coverage_path, {})
        for entry in entries:
            coverage[book_key(book.get("name", ""), book.get("edition", ""), entry["section"])] = {
                "book": book.get("name"), "edition": book.get("edition"), "section": entry["section"],
                "topic": entry.get("topic"), "methods": entry["methods"], "rationale": entry["rationale"],
                "recorded_at": time.time(),
            }
        write_json(self._coverage_path, coverage)
        return {"ok": True, "recorded": [e["section"] for e in entries]}

    def uncovered(self, book: dict, problems: list[dict]) -> list[str]:
        coverage = read_json(self._coverage_path, {})
        missing = []
        for problem in problems:
            if problem.get("method") in (CONCEPT_METHOD, COURSE_METHOD):
                continue
            section = problem.get("section")
            if not section:
                missing.append(f"{problem.get('statement')} has no section")
                continue
            entry = coverage.get(book_key(book.get("name", ""), book.get("edition", ""), section))
            if entry is None:
                missing.append(f"section {section}")
            elif problem.get("method") not in entry["methods"]:
                missing.append(f"method {problem.get('method')} for section {section}")
        return sorted(set(missing))

    # --- Error log -----------------------------------------------------------

    def _log_event(self, step_type: str, kind: str, context: dict | None = None) -> None:
        with self.lock:
            log = read_json(self._log_path, {"step_types": {}})
            entry = log["step_types"].setdefault(step_type, {
                "attempts": 0, "first_try_misses": 0, "wrong_submissions": 0,
                "hints_used": {"1": 0, "2": 0, "3": 0}, "last_missed": None, "examples": [],
            })
            if kind == "first_attempt":
                entry["attempts"] += 1
            elif kind == "first_miss":
                entry["attempts"] += 1
                entry["first_try_misses"] += 1
                entry["wrong_submissions"] += 1
                entry["last_missed"] = time.time()
                entry["examples"] = (entry["examples"] + [context])[-EXAMPLES_KEPT:]
            elif kind == "wrong":
                entry["wrong_submissions"] += 1
                entry["last_missed"] = time.time()
            elif kind.startswith("hint"):
                entry["hints_used"][kind[-1]] += 1
            write_json(self._log_path, log)

    @staticmethod
    def _log_type(worksheet: dict, step: dict) -> str:
        link = worksheet.get("course")
        if not link:
            return step["type"]
        return f"{COURSE_LOG_PREFIX}{link['id']}:{step.get('objective') or step['type']}"

    def _miss_rate(self, entry: dict | None) -> float:
        """Smoothed so a single miss doesn't dominate and an unseen type stays neutral."""
        if not entry:
            return 1 / NEUTRAL_RATE_ATTEMPTS
        return (entry["first_try_misses"] + 1) / (entry["attempts"] + NEUTRAL_RATE_ATTEMPTS)

    def study_plan(self, methods: list[str], count: int) -> dict:
        log = read_json(self._log_path, {"step_types": {}})
        catalogue = {m["id"]: m for m in self.list_methods()["methods"]}
        unknown = [m for m in methods if m not in catalogue]
        if unknown:
            raise UserError(f"Not in the repertoire yet: {', '.join(unknown)}.")
        rates = {step_type: self._miss_rate(entry) for step_type, entry in log["step_types"].items()}
        weights = {}
        for method in methods:
            types = catalogue[method]["step_types"]
            weights[method] = max(rates.get(t, 1 / NEUTRAL_RATE_ATTEMPTS) for t in types)
        total = sum(weights.values()) or 1
        ideal = {m: count * w / total for m, w in weights.items()}
        allocation = {m: math.floor(v) for m, v in ideal.items()}
        leftovers = sorted(methods, key=lambda m: ideal[m] - allocation[m], reverse=True)
        for method in leftovers[: count - sum(allocation.values())]:
            allocation[method] += 1
        # Every topic in the set stays represented when there is room for it.
        if count >= len(methods):
            for method in methods:
                if allocation[method] == 0:
                    donor = max(allocation, key=allocation.get)
                    allocation[donor] -= 1
                    allocation[method] = 1
        weak = sorted(
            ({"step_type": t, "miss_rate": round(rates[t], 3),
              "attempts": e["attempts"], "first_try_misses": e["first_try_misses"]}
             for t, e in log["step_types"].items() if e["first_try_misses"] and not t.startswith(COURSE_LOG_PREFIX)),
            key=lambda row: -row["miss_rate"])
        return {"allocation": allocation, "weak_spots": weak[:8]}

    def error_log(self) -> dict:
        """The worksheet error log. Course misses live in their course's results instead."""
        log = read_json(self._log_path, {"step_types": {}})
        return {**log, "step_types": {t: e for t, e in log["step_types"].items() if not t.startswith(COURSE_LOG_PREFIX)}}

    # --- Practice ------------------------------------------------------------

    @staticmethod
    def _problem_state(progress: dict, problem: dict) -> dict:
        state = progress["problems"].setdefault(problem["id"], {"fade": None, "rename": {}, "steps": {}})
        return state

    @staticmethod
    def _step_state(state: dict, step: dict) -> dict:
        return state["steps"].setdefault(step["id"], {
            "correct": False, "attempts": 0, "wrong": 0, "draft": "", "message": "", "status": "",
            "confidence": "", "readback": "", "diagnosis": "", "hint_level": 0, "explanations": [],
            "own": None, "last_wrong": "", "skipped": False,
        })

    @staticmethod
    def _prefill_count(steps: list[dict], fade: float) -> int:
        return math.floor(len(steps) * max(0, min(100, fade)) / 100)

    def _resolve_fade(self, progress: dict, state: dict) -> float:
        return state["fade"] if state["fade"] is not None else progress.get("fade", 0)

    def _status_list(self, problem: dict, state: dict, fade: float) -> list[str]:
        """correct, skipped, prefilled, or unresolved for each step, in order."""
        steps = problem["plan"]["steps"]
        prefilled = self._prefill_count(steps, fade)
        out = []
        for index, step in enumerate(steps):
            record = self._step_state(state, step)
            if record["correct"]:
                out.append("correct")
            elif record.get("skipped"):
                out.append("skipped")
            elif index < prefilled:
                out.append("prefilled")
            else:
                out.append("unresolved")
        return out

    def _find(self, worksheet: dict, problem_id: str, step_id: str | None = None):
        problem = next((p for p in worksheet["problems"] if p["id"] == problem_id), None)
        if problem is None:
            raise UserError("That problem isn't in this set.")
        if not problem["sealed"]:
            raise UserError("This problem's verified answer key has been changed on disk, so it won't be graded.")
        if step_id is None:
            return problem, None
        step = next((s for s in problem["plan"]["steps"] if s["id"] == step_id), None)
        if step is None:
            raise UserError("That step isn't in this problem.")
        return problem, step

    def _open_step(self, problem: dict, state: dict, fade: float) -> dict | None:
        statuses = self._status_list(problem, state, fade)
        for step, status in zip(problem["plan"]["steps"], statuses):
            if status == "unresolved":
                return step
        return None

    def submit(self, set_id: str, problem_id: str, step_id: str, latex: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, step = self._find(worksheet, problem_id, step_id)
            state = self._problem_state(progress, problem)
            fade = self._resolve_fade(progress, state)
            current = self._open_step(problem, state, fade)
            if current is None or current["id"] != step_id:
                raise UserError("That step isn't the one open right now.")
            record = self._step_state(state, step)
            rename = dict(state.get("rename") or {})
        graded = self.call_worker("grade", "grade", {
            "check": step["check"], "latex": latex, "rename": rename, "answer": step["answer"],
        }, 30)
        with self.lock:
            progress = self.load_progress(set_id)
            state = self._problem_state(progress, problem)
            record = self._step_state(state, step)
            record["draft"] = latex
            record["status"] = graded["status"]
            record["message"] = graded["message"]
            record["readback"] = graded.get("readback", "")
            if graded["status"] == "correct":
                if record["attempts"] == 0:
                    self._log_event(self._log_type(worksheet, step), "first_attempt")
                record["correct"] = True
                record["confidence"] = graded.get("confidence", "")
                record["attempts"] += 1
                state.setdefault("rename", {}).update(graded.get("rename") or {})
            elif graded["status"] == "incorrect":
                if record["attempts"] == 0:
                    self._log_event(self._log_type(worksheet, step), "first_miss", {
                        "set": set_id, "problem": problem_id, "step": step_id,
                        "answer": latex, "at": time.time(),
                    })
                else:
                    self._log_event(self._log_type(worksheet, step), "wrong")
                record["attempts"] += 1
                record["wrong"] += 1
                record["last_wrong"] = latex
                record["diagnosis"] = graded.get("diagnosis", "")
            self.save_progress(set_id, progress)
        self._course_progress(set_id)
        return self.view(set_id, problem_id)

    def draft(self, set_id: str, problem_id: str, step_id: str, latex: str) -> None:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            problem, step = self._find(worksheet, problem_id, step_id)
            exam = progress.get("exam")
            if exam and not exam.get("submitted_at"):
                exam["answers"].setdefault(problem_id, {}).setdefault(step_id, {})["latex"] = latex
            else:
                state = self._problem_state(progress, problem)
                record = self._step_state(state, step)
                if not record["correct"]:
                    record["draft"] = latex
            self.save_progress(set_id, progress)

    def hint(self, set_id: str, problem_id: str, step_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, step = self._find(worksheet, problem_id, step_id)
            state = self._problem_state(progress, problem)
            record = self._step_state(state, step)
            if record["hint_level"] < 3:
                record["hint_level"] += 1
                self._log_event(self._log_type(worksheet, step), f"hint{record['hint_level']}")
            self.save_progress(set_id, progress)
        return self.view(set_id, problem_id)

    def set_fade(self, set_id: str, problem_id: str, fade: float) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, _ = self._find(worksheet, problem_id)
            state = self._problem_state(progress, problem)
            state["fade"] = max(0, min(100, float(fade)))
            progress["fade"] = state["fade"]
            self.save_progress(set_id, progress)
        return self.view(set_id, problem_id)

    # --- Skipping. Nothing here touches the error log, since a skip isn't a miss. ---

    def skip_step(self, set_id: str, problem_id: str, step_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, step = self._find(worksheet, problem_id, step_id)
            state = self._problem_state(progress, problem)
            current = self._open_step(problem, state, self._resolve_fade(progress, state))
            if current is None or current["id"] != step_id:
                raise UserError("That step isn't the one open right now.")
            self._step_state(state, step)["skipped"] = True
            self.save_progress(set_id, progress)
        self._course_progress(set_id)
        return self.view(set_id, problem_id)

    def unskip_step(self, set_id: str, problem_id: str, step_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, step = self._find(worksheet, problem_id, step_id)
            self._step_state(self._problem_state(progress, problem), step)["skipped"] = False
            self.save_progress(set_id, progress)
        return self.view(set_id, problem_id)

    def _is_skipped(self, progress: dict, problem: dict) -> bool:
        return bool(progress["problems"].get(problem["id"], {}).get("skipped"))

    def skip_problem(self, set_id: str, problem_id: str) -> dict:
        """Moves on to the next problem still in play, since that's what a skip is for."""
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, _ = self._find(worksheet, problem_id)
            self._problem_state(progress, problem)["skipped"] = True
            problems = worksheet["problems"]
            after = problems[problems.index(problem) + 1:] + problems
            following = next((p for p in after if not self._is_skipped(progress, p)), problem)
            progress["active"] = following["id"]
            self.save_progress(set_id, progress)
        self._course_progress(set_id)
        return self.view(set_id, following["id"])

    def unskip_problem(self, set_id: str, problem_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, _ = self._find(worksheet, problem_id)
            self._problem_state(progress, problem)["skipped"] = False
            self.save_progress(set_id, progress)
        return self.view(set_id, problem_id)

    def set_active(self, set_id: str, problem_id: str) -> dict:
        with self.lock:
            progress = self.load_progress(set_id)
            progress["active"] = problem_id
            self.save_progress(set_id, progress)
        return self.view(set_id, problem_id)

    @staticmethod
    def _refuse_during_exam(progress: dict) -> None:
        exam = progress.get("exam")
        if exam and not exam.get("submitted_at"):
            raise UserError("That isn't available during an exam.")

    # --- Asking Claude -------------------------------------------------------

    def _start_ask(self, request: dict, meta: dict) -> str:
        ask_id = uuid.uuid4().hex
        self.asks[ask_id] = {"state": "pending", "meta": meta, "started": time.time()}
        self.emit({"event": "ask", "id": ask_id, **request})
        return ask_id

    def explain(self, set_id: str, problem_id: str, step_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, step = self._find(worksheet, problem_id, step_id)
            record = self._step_state(self._problem_state(progress, problem), step)
            answer = record["last_wrong"]
            if not answer:
                raise UserError("Explain why works after a wrong answer.")
            for earlier in record["explanations"]:
                if earlier["answer"] == answer:
                    return {"ask": None, "view": self.view(set_id, problem_id)}
            pending = self._explaining(set_id, problem_id, step_id, answer)
            if pending:
                return {"ask": pending, "view": self.view(set_id, problem_id)}
            request = prompts.explain(self._prompt_set(worksheet), problem, step, answer, record["readback"], record["diagnosis"])
            ask_id = self._start_ask(request, {
                "kind": "explain", "set": set_id, "problem": problem_id, "step": step_id, "answer": answer,
                "fallback": record["diagnosis"],
            })
        return {"ask": ask_id, "view": self.view(set_id, problem_id)}

    def _explaining(self, set_id: str, problem_id: str, step_id: str, answer: str) -> str | None:
        """The ask still working on this answer, so the page can show it and a second click can't repeat it."""
        for ask_id, ask in list(self.asks.items()):
            meta = ask["meta"]
            if (ask["state"] == "pending" and meta.get("kind") == "explain" and meta.get("set") == set_id
                    and meta.get("problem") == problem_id and meta.get("step") == step_id and meta.get("answer") == answer):
                return ask_id
        return None

    def walkthrough(self, set_id: str, problem_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, _ = self._find(worksheet, problem_id)
            state = self._problem_state(progress, problem)
            if state.get("walkthrough"):
                return {"ask": None, "view": self.view(set_id, problem_id)}
            request = prompts.walkthrough(self._prompt_set(worksheet), problem, problem["plan"]["steps"])
            ask_id = self._start_ask(request, {"kind": "walkthrough", "set": set_id, "problem": problem_id})
        return {"ask": ask_id, "view": self.view(set_id, problem_id)}

    def own_method(self, set_id: str, problem_id: str, step_id: str, work: str, result: str) -> dict:
        if not work.strip():
            raise UserError("Write out your method first.")
        cas = {"status": "", "message": ""}
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            self._refuse_during_exam(progress)
            problem, step = self._find(worksheet, problem_id, step_id)
            state = self._problem_state(progress, problem)
            rename = dict(state.get("rename") or {})
        if result.strip():
            cas = self.call_worker("grade", "grade", {
                "check": step["check"], "latex": result, "rename": rename, "answer": step["answer"],
            }, 30)
        with self.lock:
            progress = self.load_progress(set_id)
            state = self._problem_state(progress, problem)
            record = self._step_state(state, step)
            record["own"] = {"work": work, "result": result, "state": "pending", "reason": "",
                             "cas": cas["status"]}
            self.save_progress(set_id, progress)
            request = prompts.judge(self._prompt_set(worksheet), problem, step, work, result, _cas_note(cas))
            ask_id = self._start_ask(request, {
                "kind": "own_method", "set": set_id, "problem": problem_id, "step": step_id,
                "cas": cas["status"], "rename": cas.get("rename") or {},
                "confidence": _judged_by(step),
            })
        return {"ask": ask_id, "view": self.view(set_id, problem_id)}

    def ask_status(self, ask_id: str) -> dict:
        ask = self.asks.get(ask_id)
        if ask is None:
            return {"state": "gone"}
        if ask["state"] == "pending" and time.time() - ask["started"] > 150:
            self.ask_result(ask_id, None, "Claude didn't answer in time.")
        return {"state": self.asks[ask_id]["state"]}

    def ask_result(self, ask_id: str, text: str | None, error: str = "") -> None:
        with self.lock:
            ask = self.asks.get(ask_id)
            if ask is None or ask["state"] != "pending":
                return
            ask["state"] = "done"
            meta = ask["meta"]
            handler = {"explain": self._finish_explain, "walkthrough": self._finish_walkthrough,
                       "own_method": self._finish_own_method, "doc_question": self._finish_doc_question}[meta["kind"]]
            handler(meta, text, error)

    def _finish_explain(self, meta: dict, text: str | None, error: str) -> None:
        worksheet = self.load_set(meta["set"])
        progress = self.load_progress(meta["set"])
        problem, step = self._find(worksheet, meta["problem"], meta["step"])
        record = self._step_state(self._problem_state(progress, problem), step)
        body = text or (f"Claude couldn't be reached ({error}). What SymPy found: {meta['fallback']}"
                        if meta["fallback"] else f"Claude couldn't be reached ({error}).")
        record["explanations"].append({"answer": meta["answer"], "text": body, "from": "claude" if text else "sympy"})
        self.save_progress(meta["set"], progress)

    def _finish_walkthrough(self, meta: dict, text: str | None, error: str) -> None:
        worksheet = self.load_set(meta["set"])
        progress = self.load_progress(meta["set"])
        problem, _ = self._find(worksheet, meta["problem"])
        state = self._problem_state(progress, problem)
        if text:
            state["walkthrough"] = text
        else:
            state["walkthrough_error"] = f"Claude couldn't be reached ({error})."
        self.save_progress(meta["set"], progress)

    def _finish_own_method(self, meta: dict, text: str | None, error: str) -> None:
        worksheet = self.load_set(meta["set"])
        progress = self.load_progress(meta["set"])
        exam_slot = meta.get("exam")
        problem, step = self._find(worksheet, meta["problem"], meta["step"])
        verdict = parse_own_verdict(text) if text else {"valid": None, "reason": f"Claude couldn't be reached ({error})."}
        valid = verdict["valid"] is True and meta["cas"] != "incorrect"
        reason = verdict["reason"]
        if verdict["valid"] is True and meta["cas"] == "incorrect":
            reason = "Your reasoning read as valid, but SymPy shows your stated result isn't correct. " + reason
        if exam_slot:
            exam = progress["exam"]
            result = exam["results"]["problems"][meta["problem"]][meta["step"]]
            result.update({"status": "correct" if valid else "incorrect", "message": reason, "pending": False})
            if not valid:
                self._log_event(self._log_type(worksheet, step), "first_miss", {"set": meta["set"], "problem": meta["problem"],
                                                             "step": meta["step"], "answer": "own method", "at": time.time()})
            self._tally_exam(worksheet, exam)
        else:
            state = self._problem_state(progress, problem)
            record = self._step_state(state, step)
            record["own"].update({"state": "valid" if valid else "invalid", "reason": reason})
            if valid:
                if record["attempts"] == 0:
                    self._log_event(self._log_type(worksheet, step), "first_attempt")
                record["correct"] = True
                record["confidence"] = meta.get("confidence") or ("own method: Claude's judgement" + (", result confirmed by SymPy" if meta["cas"] == "correct" else ""))
                record["message"] = "Accepted."
                record["status"] = "correct"
                record["attempts"] += 1
                record["draft"] = record["own"]["result"]
                state.setdefault("rename", {}).update(meta.get("rename") or {})
            elif verdict["valid"] is not None:
                if record["attempts"] == 0:
                    self._log_event(self._log_type(worksheet, step), "first_miss", {"set": meta["set"], "problem": meta["problem"],
                                                                 "step": meta["step"], "answer": "own method", "at": time.time()})
                else:
                    self._log_event(self._log_type(worksheet, step), "wrong")
                record["attempts"] += 1
        self.save_progress(meta["set"], progress)
        self._course_progress(meta["set"])

    # --- Exam ----------------------------------------------------------------

    def exam_start(self, set_id: str, minutes: float | None) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            if not all(p["sealed"] for p in worksheet["problems"]):
                raise UserError("A problem in this set failed its verification check.")
            progress = self.load_progress(set_id)
            remaining = [p for p in worksheet["problems"] if not self._is_skipped(progress, p)]
            if not remaining:
                raise UserError("Every problem in this set is skipped, so there's nothing to put in an exam.")
            progress["exam"] = {
                "started_at": time.time(), "limit_seconds": minutes * 60 if minutes else None,
                "answers": {}, "submitted_at": None, "results": None,
            }
            self.save_progress(set_id, progress)
        return self.view(set_id, remaining[0]["id"])

    def exam_own(self, set_id: str, problem_id: str, step_id: str, work: str, result: str) -> dict:
        with self.lock:
            progress = self.load_progress(set_id)
            exam = progress.get("exam")
            if not exam or exam.get("submitted_at"):
                raise UserError("There's no exam running.")
            exam["answers"].setdefault(problem_id, {}).setdefault(step_id, {})["own"] = {"work": work, "result": result}
            self.save_progress(set_id, progress)
        return self.view(set_id, problem_id)

    def exam_submit(self, set_id: str) -> dict:
        with self.lock:
            worksheet = self.load_set(set_id)
            progress = self.load_progress(set_id)
            exam = progress.get("exam")
            if not exam:
                raise UserError("There's no exam running.")
            if exam.get("submitted_at"):
                return self.view(set_id)
            answers = json.loads(json.dumps(exam["answers"]))
            remaining = [p for p in worksheet["problems"] if not self._is_skipped(progress, p)]
        results: dict = {}
        asks: list[tuple] = []
        for problem in remaining:
            rename: dict = {}
            results[problem["id"]] = {}
            for step in problem["plan"]["steps"]:
                entry = answers.get(problem["id"], {}).get(step["id"], {})
                own = entry.get("own")
                latex = (own or {}).get("result") or entry.get("latex") or ""
                if latex.strip():
                    graded = self.call_worker("grade", "grade", {
                        "check": step["check"], "latex": latex, "rename": rename, "answer": step["answer"],
                    }, 30)
                else:
                    graded = {"status": "blank", "message": "Left blank.", "rename": {}}
                rename.update(graded.get("rename") or {})
                # A choice is sent as option numbers, so show the options it means.
                shown = graded.get("readback") if step["check"]["kind"] in ("multiple_choice", "numeric") else ""
                row = {"status": graded["status"], "message": graded["message"], "answer": shown or latex,
                       "key": step["answer"], "label": step["label"], "type": step["type"], "pending": False,
                       "key_format": step.get("answer_format", "math"), **_citation(step)}
                if own and own.get("work", "").strip():
                    row.update({"pending": True, "own": True, "work": own["work"]})
                    asks.append((problem, step, own, graded))
                elif graded["status"] == "correct":
                    self._log_event(self._log_type(worksheet, step), "first_attempt")
                elif graded["status"] in ("incorrect", "blank"):
                    self._log_event(self._log_type(worksheet, step), "first_miss", {
                        "set": set_id, "problem": problem["id"], "step": step["id"],
                        "answer": latex, "at": time.time(), "exam": True,
                    })
                results[problem["id"]][step["id"]] = row
        with self.lock:
            progress = self.load_progress(set_id)
            exam = progress["exam"]
            exam["submitted_at"] = time.time()
            exam["results"] = {"problems": results}
            self._tally_exam(worksheet, exam)
            self.save_progress(set_id, progress)
            for problem, step, own, graded in asks:
                request = prompts.judge(self._prompt_set(worksheet), problem, step, own["work"], own.get("result", ""), _cas_note(graded))
                self._start_ask(request, {"kind": "own_method", "exam": True, "set": set_id, "problem": problem["id"],
                                          "step": step["id"], "cas": graded["status"]})
        self._course_progress(set_id)
        return self.view(set_id)

    def _tally_exam(self, worksheet: dict, exam: dict) -> None:
        results = exam["results"]["problems"]
        rows = [row for problem in results.values() for row in problem.values()]
        by_problem = {}
        for problem in worksheet["problems"]:
            own = list(results.get(problem["id"], {}).values())
            by_problem[problem["id"]] = {"correct": sum(r["status"] == "correct" for r in own), "total": len(own)}
        by_type: dict = {}
        for row in rows:
            slot = by_type.setdefault(row["type"], {"correct": 0, "total": 0})
            slot["total"] += 1
            slot["correct"] += row["status"] == "correct"
        exam["results"]["score"] = {
            "correct": sum(r["status"] == "correct" for r in rows), "total": len(rows),
            "pending": sum(bool(r["pending"]) for r in rows),
            "by_problem": by_problem, "by_type": by_type,
            "seconds": exam["submitted_at"] - exam["started_at"],
        }

    def exam_close(self, set_id: str) -> dict:
        with self.lock:
            progress = self.load_progress(set_id)
            progress["exam"] = None
            self.save_progress(set_id, progress)
        return self.view(set_id)

    # --- What the page sees --------------------------------------------------

    def view(self, set_id: str, problem_id: str | None = None) -> dict:
        worksheet = self.load_set(set_id)
        progress = self.load_progress(set_id)
        exam = progress.get("exam")
        if exam and not exam.get("submitted_at") and exam.get("limit_seconds"):
            if time.time() - exam["started_at"] >= exam["limit_seconds"]:
                self.exam_submit(set_id)
                progress = self.load_progress(set_id)
                exam = progress.get("exam")
        problems = worksheet["problems"]
        active_id = problem_id or progress.get("active") or (problems[0]["id"] if problems else None)
        if active_id not in {p["id"] for p in problems} and problems:
            active_id = problems[0]["id"]
        if problem_id and progress.get("active") != problem_id:
            with self.lock:
                fresh = self.load_progress(set_id)
                fresh["active"] = problem_id
                self.save_progress(set_id, fresh)
        in_exam = bool(exam and not exam.get("submitted_at"))
        tabs = []
        for number, problem in enumerate(problems, 1):
            if not (exam and self._is_skipped(progress, problem)):
                tabs.append(self._tab_view(number, problem, progress))
        if exam and tabs and active_id not in {tab["id"] for tab in tabs}:
            active_id = tabs[0]["id"]
        active = next((p for p in problems if p["id"] == active_id), None)
        book = worksheet.get("textbook") or {}
        result = {
            "set": {"id": worksheet["id"], "title": worksheet["title"], "textbook": book,
                    "difficulty": worksheet.get("difficulty") or "textbook",
                    "weak_spots_used": worksheet.get("weak_spots_used") or []},
            "sets": self.list_sets(),
            "tabs": tabs,
            "active": active_id,
            "exam": self._exam_view(worksheet, exam),
            "fade": progress.get("fade", 0),
        }
        if worksheet.get("course"):
            result["set"]["course"] = self._course_label(worksheet["course"])
        if active is not None:
            result["problem"] = self._exam_problem_view(worksheet, active, exam) if in_exam else \
                self._problem_view(worksheet, active, progress)
        return result

    def _tab_view(self, number: int, problem: dict, progress: dict) -> dict:
        tab = {"id": problem["id"], "number": number, "method": problem["method"]}
        if not problem["sealed"]:
            return {**tab, "done": 0, "total": 0, "complete": False, "unverified": True}
        if self._is_skipped(progress, problem):
            return {**tab, "done": 0, "total": 0, "complete": False, "skipped": True}
        steps = problem["plan"]["steps"]
        state = self._problem_state(progress, problem)
        statuses = self._status_list(problem, state, self._resolve_fade(progress, state))
        done = sum(s in ("correct", "prefilled") for s in statuses)
        return {**tab, "done": done, "total": len(steps) - statuses.count("skipped"),
                "complete": "unresolved" not in statuses}

    def _book_notes(self, worksheet: dict, problem: dict) -> dict:
        notes = (worksheet.get("book_methods") or {}).get(problem["method"]) or {}
        book = worksheet.get("textbook") or {}
        return {"summary": notes.get("summary", ""), "source": notes.get("source", "memory"),
                "sources": notes.get("sources") or [], "own_words": bool(notes.get("own_words")),
                "book": book.get("name", ""), "edition": book.get("edition", "")}

    def _problem_view(self, worksheet: dict, problem: dict, progress: dict) -> dict:
        base = {"id": problem["id"], "statement": problem["statement"], "ic": problem["ic"],
                "context": problem.get("context", ""), "topic": problem["topic"], "section": problem["section"], "method": problem["method"],
                "origin": problem["origin"]}
        if not problem["sealed"]:
            return {**base, "unverified": True, "steps": [], "total": 0}
        if self._is_skipped(progress, problem):
            return {**base, "skipped": True, "steps": [], "total": 0}
        state = self._problem_state(progress, problem)
        fade = self._resolve_fade(progress, state)
        steps = problem["plan"]["steps"]
        statuses = self._status_list(problem, state, fade)
        out = []
        shown_open = False
        for step, status in zip(steps, statuses):
            if status == "unresolved" and shown_open:
                break
            out.append(self._step_view(worksheet, problem, step, status, self._step_state(state, step)))
            if status == "unresolved":
                shown_open = True
        complete = all(s != "unresolved" for s in statuses)
        view = {**base, "steps": out, "total": len(steps), "fade": fade,
                "prefill_count": self._prefill_count(steps, fade), "complete": complete,
                "book": self._book_notes(worksheet, problem), "verified": problem["plan"]["verified"],
                "walkthrough": state.get("walkthrough", ""), "walkthrough_error": state.get("walkthrough_error", ""),
                "hints_used": {s["id"]: self._step_state(state, s)["hint_level"] for s in steps
                               if self._step_state(state, s)["hint_level"]}}
        if complete:
            view["solution"] = [{"label": s["label"], "answer": s["answer"], "note": s.get("note", ""),
                                 "answer_format": s.get("answer_format", "math"), "figures": s.get("figures", []),
                                 "kind": s["check"]["kind"], **_citation(s),
                                 "status": st, "hint_level": self._step_state(state, s)["hint_level"],
                                 "own": (self._step_state(state, s)["own"] or {}).get("work", "")
                                 if self._step_state(state, s)["correct"] else ""}
                                for s, st in zip(steps, statuses)]
        return view

    def _step_view(self, worksheet: dict, problem: dict, step: dict, status: str, record: dict) -> dict:
        view = {"id": step["id"], "label": step["label"], "prompt": step["prompt"], "type": step["type"],
                "status": status, **_step_shape(step)}
        if status in ("prefilled", "skipped"):
            # A skipped step shows its answer, because the steps after it build on it.
            return {**view, "answer": step["answer"], "answer_format": step.get("answer_format", "math"), **_citation(step)}
        view.update({"draft": record["draft"], "feedback": record["status"], "message": record["message"],
                     "readback": record["readback"], "attempts": record["attempts"],
                     "confidence": record["confidence"], "hint_level": record["hint_level"]})
        if record["hint_level"]:
            view["hints"] = self._hints(worksheet, problem, step, record["hint_level"])
        if status == "correct":
            view["own"] = record["own"]
            return {**view, **_citation(step)}
        view["explanations"] = [e for e in record["explanations"] if e["answer"] == record["last_wrong"]]
        view["can_explain"] = record["status"] == "incorrect" and bool(record["last_wrong"])
        view["explaining"] = self._explaining(worksheet["id"], problem["id"], step["id"], record["last_wrong"])
        view["own"] = record["own"]
        return view

    def _hints(self, worksheet: dict, problem: dict, step: dict, level: int) -> list[dict]:
        notes = (worksheet.get("book_methods") or {}).get(problem["method"]) or {}
        book_note = (notes.get("step_notes") or {}).get(step["type"])
        out = []
        for current in range(1, level + 1):
            if current == 3:
                field = "text" if step.get("answer_format") == "text" else "math"
                out.append({"level": 3, "name": HINT_NAMES[3], field: step["answer"]})
                continue
            text = step["hints"][current - 1]
            if current == 2 and book_note:
                text = f"{text} {book_note}"
            out.append({"level": current, "name": HINT_NAMES[current], "text": text})
        return out

    def _exam_problem_view(self, worksheet: dict, problem: dict, exam: dict) -> dict:
        answers = exam["answers"].get(problem["id"], {})
        steps = []
        for step in problem["plan"]["steps"]:
            entry = answers.get(step["id"], {})
            steps.append({"id": step["id"], "label": step["label"], "prompt": step["prompt"], "status": "exam",
                          "draft": entry.get("latex", ""), "own": entry.get("own"), **_step_shape(step)})
        return {"id": problem["id"], "statement": problem["statement"], "ic": problem["ic"],
                "context": problem.get("context", ""),
                "topic": problem["topic"], "section": problem["section"], "method": problem["method"],
                "steps": steps, "total": len(steps), "exam": True}

    def _exam_view(self, worksheet: dict, exam: dict | None) -> dict | None:
        if not exam:
            return None
        view = {"submitted": bool(exam.get("submitted_at")), "started_at": exam["started_at"],
                "limit_seconds": exam["limit_seconds"], "now": time.time()}
        if view["submitted"]:
            problems = []
            for number, problem in enumerate(worksheet["problems"], 1):
                if problem["id"] not in exam["results"]["problems"]:
                    continue
                rows = exam["results"]["problems"].get(problem["id"], {})
                problems.append({"id": problem["id"], "number": number, "statement": problem["statement"],
                                 "rows": list(rows.values())})
            view["score"] = exam["results"]["score"]
            view["problems"] = problems
        return view


def _citation(step: dict) -> dict:
    """Sent only once a step is resolved, since a source could give the answer away."""
    return {"sources": step.get("sources") or [], "own_words": bool(step.get("own_words"))}


def _judged_by(step: dict) -> str:
    if step["check"].get("concept"):
        return "judged by Claude; SymPy can't check a written answer"
    return "judged by Claude against SymPy's facts" if step["check"]["kind"] == "discussion" else ""


def _step_shape(step: dict) -> dict:
    """What the page needs to draw a step's input. Figures are hidden until the step opens."""
    kind = step["check"]["kind"]
    shape = {"input": {"choice": "choice", "discussion": "discussion", "multiple_choice": "multiple_choice",
                       "numeric": "numeric"}.get(kind, "math"),
             "figures": step.get("figures", [])}
    if kind in ("choice", "multiple_choice"):
        shape["options"] = step["options"]
    if kind == "multiple_choice":
        shape["multi"] = bool(step.get("multi"))
    if kind == "numeric":
        shape["unit"] = step.get("unit", "")
    if step["check"].get("concept"):
        shape["placeholder"] = "Write your answer. Use $...$ for math."
    return shape


def _cas_note(cas: dict) -> str:
    status = cas.get("status")
    if not status:
        return "the student gave no separate result for it to check"
    if status == "correct":
        return "their result is correct for this step"
    if status == "blank":
        return "the student gave no separate result for it to check"
    return f"{status}: {cas.get('message', '')}"


def parse_own_verdict(text: str) -> dict:
    """Claude's reply should be JSON. Anything else is treated as no verdict."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            reply = json.loads(match.group())
            if isinstance(reply.get("valid"), bool):
                return {"valid": reply["valid"], "reason": str(reply.get("reason", ""))}
        except json.JSONDecodeError:
            pass
    return {"valid": None, "reason": "Claude's reply couldn't be read as a verdict. Try again."}
