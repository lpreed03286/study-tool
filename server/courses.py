"""Courses (experimental): a syllabus built with Claude one section at a time, where a section
unlocks only once its prerequisites are mastered or tested out.

The course file holds the structure and the results. Lessons and checks are ordinary documents
and sets, linked by id, so deleting a course leaves them in place. Status and mastery are always
computed here from results, never taken from a caller.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from storage import read_json, write_json

PHASES = ("intake", "research", "planning", "syllabus", "active", "complete")
LOCKED, AVAILABLE, GENERATING, IN_PROGRESS, MASTERED, TESTED_OUT = (
    "locked", "available", "generating", "in_progress", "mastered", "tested_out")
DONE = {MASTERED, TESTED_OUT}
ASSESSMENT_KINDS = ("multiple_choice", "numeric", "short_answer", "math")
EXAMPLES = ("worked_first", "after_theory", "none")
ROLES = ("lesson", "check", "practice", "placement")
INTAKE_FIELDS = ("goal", "intention", "intention_details", "end_goal", "background", "time_per_week", "materials",
                 "library_class")
# Why the student is learning it. It shapes depth, pacing and what the checks focus on.
INTENTIONS = {"class": "a class or exam", "project": "a project", "work": "work or a job", "curiosity": "curiosity",
              "other": "something else"}
DEFAULT_PLAN = {
    "presentation": {"style": "", "examples": "worked_first", "figures": True, "notes": ""},
    "assessment": {"default_kinds": ["multiple_choice", "short_answer"], "per_section_override": True},
    "mastery": {"threshold": 0.8, "max_hints_ratio": 0.5, "require_all_prereqs": True},
    "pacing": "",
}
MAX_CHAPTERS = 30
MAX_SECTIONS = 200
MAX_TEXT = 2000
HISTORY_KEPT = 100
ATTEMPTS_KEPT = 20
OVERVIEW_OBJECTIVE_CHARS = 90
OVERVIEW_WEAK_SPOTS = 5


class CourseError(Exception):
    """A refusal with a sentence the student can act on."""


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:limit].strip("-") or "objective"


def _text(value, name: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise CourseError(f"Give the {name}.")
    if len(text) > MAX_TEXT:
        raise CourseError(f"The {name} is too long. Keep it under {MAX_TEXT} characters.")
    return text


def _ratio(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CourseError(f"The {name} has to be a number from 0 to 1.")
    if not 0 <= number <= 1:
        raise CourseError(f"The {name} has to be a number from 0 to 1.")
    return number


def mastery(rule: dict, items: list[dict]) -> dict:
    """Scores one finished check. An item counts if it's right without the answer being shown, so
    a nudge or method hint is fine, but if more than max_hints_ratio of the items needed one, the
    section isn't mastered yet. Skipped items count as missed."""
    total = len(items)
    counted = [i for i in items if i.get("correct") and not i.get("answer_shown") and not i.get("skipped")]
    hinted = sum(bool(i.get("hinted")) for i in counted)
    score = len(counted) / total if total else 0.0
    hints_ratio = hinted / total if total else 0.0
    # A float threshold like 0.8 must accept exactly 4 of 5.
    passed = bool(total) and score >= rule["threshold"] - 1e-9 and hints_ratio <= rule["max_hints_ratio"] + 1e-9
    missed = sorted({i.get("objective") or "" for i in items if i not in counted} - {""})
    return {"score": round(score, 4), "hints_ratio": round(hints_ratio, 4), "passed": passed,
            "correct": len(counted), "total": total, "missed_objectives": missed,
            "judged_by_claude": sum(bool(i.get("judged_by_claude")) for i in items)}


def find_cycle(prereqs: dict[str, list[str]]) -> list[str] | None:
    """A prerequisite loop as a list of ids, or None."""
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        path.append(node)
        for nxt in prereqs.get(node, []):
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            if not state.get(nxt):
                found = visit(nxt)
                if found:
                    return found
        state[node] = 2
        path.pop()
        return None

    for node in prereqs:
        if not state.get(node):
            found = visit(node)
            if found:
                return found
    return None


class Courses:
    def __init__(self, data: Path):
        self.root = Path(data) / "courses"
        self.lock = threading.RLock()

    # --- Paths and storage -----------------------------------------------------

    def _path(self, course_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]+", str(course_id)):
            raise CourseError("That isn't a course name.")
        return self.root / f"{course_id}.json"

    def overview_path(self, course_id: str) -> Path:
        return self._path(course_id).with_suffix(".overview.md")

    def get(self, course_id: str) -> dict:
        path = self._path(course_id)
        if not path.exists():
            raise CourseError(f"There's no course called {course_id}.")
        return read_json(path, None)

    def exists(self, course_id: str) -> bool:
        try:
            return self._path(course_id).exists()
        except CourseError:
            return False

    def all(self) -> list[dict]:
        if not self.root.exists():
            return []
        found = [read_json(path, None) for path in self.root.glob("*.json")]
        return sorted(found, key=lambda course: -course["created_at"])

    def _save(self, course: dict, change: str = "") -> dict:
        """Every write recomputes the statuses and the overview, so neither can go stale."""
        if change:
            course["history"] = (course["history"] + [{"at": time.time(), "change": change}])[-HISTORY_KEPT:]
        self._refresh(course)
        write_json(self._path(course["id"]), course)
        self.overview_path(course["id"]).write_text(self.overview_text(course))
        return course

    def _new_id(self, title: str) -> str:
        stem = slug(title, 60) if title else "course"
        candidate, count = stem, 1
        while self._path(candidate).exists():
            count += 1
            candidate = f"{stem}-{count}"
        return candidate

    # --- Phases ------------------------------------------------------------------

    def create(self, args: dict) -> dict:
        intake = {name: _text(args.get(name), name.replace("_", " ")) for name in INTAKE_FIELDS}
        if not intake["goal"]:
            raise CourseError("Give the goal: even one sentence on what you want to learn is enough.")
        if intake["intention"] and intake["intention"] not in INTENTIONS:
            raise CourseError(f"intention has to be one of: {', '.join(INTENTIONS)}.")
        title = _text(args.get("title"), "title") or intake["goal"][:60]
        with self.lock:
            course = {
                "id": self._new_id(title), "title": title, "experimental": True, "created_at": time.time(),
                "phase": "research", "intake": intake, "research": {"summary": "", "sources": []},
                "plan": None, "chapters": [], "retired_ids": [], "placement": None, "history": [],
            }
            self._save(course, "course started")
        return course

    def save_research(self, course_id: str, summary: str, cited_sources: list[dict]) -> dict:
        """cited_sources have already been checked by sources.py."""
        with self.lock:
            course = self.get(course_id)
            course["research"] = {"summary": _text(summary, "research summary", True), "sources": cited_sources}
            if course["phase"] in ("intake", "research"):
                course["phase"] = "planning"
            return self._save(course, "research saved")

    def save_plan(self, course_id: str, plan: dict, change: str = "") -> dict:
        clean = self._clean_plan(plan or {})
        with self.lock:
            course = self.get(course_id)
            first = course["plan"] is None
            course["plan"] = clean
            if course["phase"] in ("intake", "research", "planning"):
                course["phase"] = "syllabus"
            note = _text(change, "change") or ("plan agreed" if first else "plan updated")
            return self._save(course, note if note.startswith("plan") else f"plan updated: {note}")

    @staticmethod
    def _clean_plan(plan: dict) -> dict:
        presentation = {**DEFAULT_PLAN["presentation"], **(plan.get("presentation") or {})}
        if presentation["examples"] not in EXAMPLES:
            raise CourseError(f"presentation.examples has to be one of: {', '.join(EXAMPLES)}.")
        assessment = {**DEFAULT_PLAN["assessment"], **(plan.get("assessment") or {})}
        kinds = list(assessment["default_kinds"] or [])
        unknown = [k for k in kinds if k not in ASSESSMENT_KINDS]
        if not kinds or unknown:
            raise CourseError(f"assessment.default_kinds takes one or more of: {', '.join(ASSESSMENT_KINDS)}.")
        rule = {**DEFAULT_PLAN["mastery"], **(plan.get("mastery") or {})}
        threshold = _ratio(rule["threshold"], "mastery threshold")
        if threshold == 0:
            raise CourseError("The mastery threshold has to be above 0.")
        return {
            "presentation": {"style": _text(presentation["style"], "presentation style"),
                             "examples": presentation["examples"], "figures": bool(presentation["figures"]),
                             "notes": _text(presentation["notes"], "presentation notes")},
            "assessment": {"default_kinds": kinds, "per_section_override": bool(assessment["per_section_override"])},
            "mastery": {"threshold": threshold, "max_hints_ratio": _ratio(rule["max_hints_ratio"], "hint limit"),
                        "require_all_prereqs": bool(rule["require_all_prereqs"])},
            "pacing": _text(plan.get("pacing"), "pacing"),
        }

    # --- The syllabus ------------------------------------------------------------

    def save_syllabus(self, course_id: str, chapters: list[dict], partial: bool = False, change: str = "") -> dict:
        """Full replaces the whole syllabus. Partial replaces the chapters it names and keeps the rest.
        Either way, a section that keeps its id keeps its lesson, checks and results."""
        with self.lock:
            course = self.get(course_id)
            if course["plan"] is None:
                raise CourseError("Agree the plan with course_save_plan before saving a syllabus.")
            if partial:
                given = {c.get("id"): c for c in chapters}
                merged = [given.pop(c["id"], c) for c in course["chapters"]]
                chapters = merged + [c for c in chapters if c.get("id") in given]
            old = {s["id"]: s for s in self.sections(course)}
            new_chapters = self._clean_chapters(chapters, old, set(course["retired_ids"]))
            kept = {s["id"] for c in new_chapters for s in c["sections"]}
            course["retired_ids"] = sorted(set(course["retired_ids"]) | (set(old) - kept))
            course["chapters"] = new_chapters
            if course["phase"] == "syllabus":
                course["phase"] = "active"
            removed = sorted(set(old) - kept)
            note = _text(change, "change") or "syllabus saved"
            if removed:
                note += f" (removed {', '.join(removed)})"
            return self._save(course, note)

    def _clean_chapters(self, chapters: list[dict], old: dict, retired: set[str]) -> list[dict]:
        if not chapters:
            raise CourseError("A syllabus needs at least one chapter.")
        if len(chapters) > MAX_CHAPTERS:
            raise CourseError(f"A course takes at most {MAX_CHAPTERS} chapters.")
        seen: set[str] = set()
        out = []
        for number, chapter in enumerate(chapters, 1):
            chapter_id = str(chapter.get("id") or "")
            if not re.fullmatch(r"[a-z0-9-]+", chapter_id):
                raise CourseError(f"Chapter {number} needs an id of lowercase letters, digits and dashes, like c{number}.")
            if chapter_id in seen:
                raise CourseError(f"The id {chapter_id} is used twice.")
            seen.add(chapter_id)
            sections = chapter.get("sections") or []
            if not sections:
                raise CourseError(f"Chapter {chapter_id} has no sections.")
            out.append({"id": chapter_id, "title": _text(chapter.get("title"), f"title of chapter {chapter_id}", True),
                        "sections": [self._clean_section(s, chapter_id, seen, old, retired) for s in sections]})
        all_sections = {s["id"]: s for c in out for s in c["sections"]}
        if len(all_sections) > MAX_SECTIONS:
            raise CourseError(f"A course takes at most {MAX_SECTIONS} sections.")
        for section in all_sections.values():
            missing = [p for p in section["prereqs"] if p not in all_sections]
            if missing:
                raise CourseError(f"Section {section['id']} lists prerequisites that aren't in the syllabus: {', '.join(missing)}.")
        cycle = find_cycle({s["id"]: s["prereqs"] for s in all_sections.values()})
        if cycle:
            raise CourseError("These prerequisites go in a circle, so none of them could ever unlock: "
                              + " → ".join(cycle) + ".")
        return out

    @staticmethod
    def _clean_section(section: dict, chapter_id: str, seen: set[str], old: dict, retired: set[str]) -> dict:
        section_id = str(section.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]+", section_id):
            raise CourseError(f"A section in chapter {chapter_id} needs an id of lowercase letters, digits and dashes.")
        if section_id in seen:
            raise CourseError(f"The id {section_id} is used twice.")
        if section_id in retired and section_id not in old:
            raise CourseError(f"The id {section_id} belonged to a removed section, and ids are never reused. Pick a new one.")
        seen.add(section_id)
        objectives = [_text(o, "objective") for o in section.get("objectives") or []]
        objectives = [o for o in objectives if o]
        if not objectives:
            raise CourseError(f"Section {section_id} needs at least one objective.")
        kinds = section.get("assessment_kinds")
        if kinds is not None:
            kinds = list(kinds)
            unknown = [k for k in kinds if k not in ASSESSMENT_KINDS]
            if not kinds or unknown:
                raise CourseError(f"Section {section_id}: assessment_kinds takes one or more of: {', '.join(ASSESSMENT_KINDS)}.")
        prereqs = list(dict.fromkeys(str(p) for p in section.get("prereqs") or []))
        if section_id in prereqs:
            raise CourseError(f"Section {section_id} can't be its own prerequisite.")
        subsections = [{"title": _text(s.get("title"), "subsection title", True),
                        "objectives": [_text(o, "objective") for o in s.get("objectives") or []]}
                       for s in section.get("subsections") or []]
        previous = old.get(section_id) or {}
        # Results and links come only from what the server recorded, never from the caller.
        return {"id": section_id, "title": _text(section.get("title"), f"title of section {section_id}", True),
                "objectives": objectives, "prereqs": prereqs, "assessment_kinds": kinds, "subsections": subsections,
                "status": previous.get("status", LOCKED), "generating": previous.get("generating"),
                "lesson_doc": previous.get("lesson_doc"), "check_set": previous.get("check_set"),
                "practice_sets": previous.get("practice_sets", []), "attempts": previous.get("attempts", []),
                "mastery": previous.get("mastery"), "tested_out": previous.get("tested_out", False)}

    @staticmethod
    def sections(course: dict) -> list[dict]:
        return [s for chapter in course["chapters"] for s in chapter["sections"]]

    def section(self, course: dict, section_id: str) -> dict:
        found = next((s for s in self.sections(course) if s["id"] == section_id), None)
        if found is None:
            raise CourseError(f"There's no section {section_id} in this course.")
        return found

    # --- Status ------------------------------------------------------------------

    @staticmethod
    def _status(section: dict, statuses: dict[str, str], require_all: bool) -> str:
        if (section.get("mastery") or {}).get("passed"):
            return MASTERED
        if section.get("tested_out"):
            return TESTED_OUT
        done = [statuses.get(p) in DONE for p in section["prereqs"]]
        if done and not (all(done) if require_all else any(done)):
            return LOCKED
        if section.get("generating"):
            return GENERATING
        if section.get("lesson_doc") or section.get("check_set") or section.get("attempts"):
            return IN_PROGRESS
        return AVAILABLE

    def _refresh(self, course: dict) -> None:
        """Prereqs can be anywhere in the syllabus, so repeat until nothing changes."""
        sections = self.sections(course)
        require_all = ((course.get("plan") or DEFAULT_PLAN)["mastery"]).get("require_all_prereqs", True)
        statuses: dict[str, str] = {}
        for _ in range(len(sections) + 1):
            changed = False
            for section in sections:
                status = self._status(section, statuses, require_all)
                if statuses.get(section["id"]) != status:
                    statuses[section["id"]] = status
                    changed = True
            if not changed:
                break
        for section in sections:
            section["status"] = statuses[section["id"]]
        if course["phase"] in ("active", "complete") and sections:
            course["phase"] = "complete" if all(s["status"] in DONE for s in sections) else "active"

    # --- Linking lessons and checks ---------------------------------------------

    def check_link(self, course_id: str, section_id: str | None, role: str, regenerate: bool = False) -> None:
        """Called before anything is built, so a refused link builds nothing."""
        if role not in ROLES:
            raise CourseError(f"course_role has to be one of: {', '.join(ROLES)}.")
        course = self.get(course_id)
        if role == "placement":
            if not course.get("placement"):
                raise CourseError("Choose the sections with course_placement before building the placement check.")
            return
        if not section_id:
            raise CourseError("Give the section this belongs to.")
        section = self.section(course, section_id)
        if section["status"] == LOCKED:
            raise CourseError(f"Section {section_id} is locked until its prerequisites are mastered, so nothing can be made for it yet.")
        if role == "lesson" and section.get("lesson_doc") and not regenerate:
            raise CourseError(f"Section {section_id} already has a lesson. Pass regenerate only if the student asked for a new one.")

    def link(self, course_id: str, section_id: str | None, role: str, item_id: str) -> dict:
        with self.lock:
            course = self.get(course_id)
            if role == "placement":
                course["placement"]["set"] = item_id
                return self._save(course, f"placement check {item_id} made")
            section = self.section(course, section_id)
            if role == "lesson":
                section["lesson_doc"] = item_id
            elif role == "check":
                section["check_set"] = item_id
            else:
                section["practice_sets"] = [*section["practice_sets"], item_id]
            if role in ("lesson", "check"):
                section["generating"] = None
            return self._save(course, f"{role} {item_id} added to {section_id}")

    def set_generating(self, course_id: str, section_id: str, request_id: str | None) -> dict:
        with self.lock:
            course = self.get(course_id)
            section = self.section(course, section_id)
            if request_id and section["status"] == LOCKED:
                raise CourseError(f"Section {section_id} is locked until its prerequisites are mastered.")
            section["generating"] = request_id
            return self._save(course)

    def clear_request(self, request_id: str) -> None:
        """A dismissed or failed request stops showing its section as generating."""
        with self.lock:
            for course in self.all():
                touched = False
                for section in self.sections(course):
                    if section.get("generating") == request_id:
                        section["generating"] = None
                        touched = True
                if touched:
                    self._save(course)

    def owner_of_set(self, set_id: str) -> tuple[dict, dict | None, str] | None:
        """The course, section and role a set belongs to, from the course files themselves."""
        for course in self.all():
            if (course.get("placement") or {}).get("set") == set_id:
                return course, None, "placement"
            for section in self.sections(course):
                if section.get("check_set") == set_id:
                    return course, section, "check"
                if set_id in section.get("practice_sets", []):
                    return course, section, "practice"
        return None

    # --- Results -------------------------------------------------------------------

    def record_check(self, course_id: str, section_id: str, set_id: str, items: list[dict], when: float) -> dict:
        """One finished check. The same finish is recorded once, and mastery once earned is kept."""
        with self.lock:
            course = self.get(course_id)
            section = self.section(course, section_id)
            if any(a["set"] == set_id and a["at"] == when for a in section["attempts"]):
                return course
            result = mastery(course["plan"]["mastery"], items)
            section["attempts"] = (section["attempts"] + [{"set": set_id, "at": when, **result}])[-ATTEMPTS_KEPT:]
            if not (section.get("mastery") or {}).get("passed"):
                section["mastery"] = {**result, "set": set_id, "at": when}
            verdict = "mastered" if result["passed"] else "not yet mastered"
            return self._save(course, f"{section_id} check: {result['correct']} of {result['total']}, {verdict}")

    def choose_placement(self, course_id: str, section_ids: list[str]) -> dict:
        """Early sections only: every prerequisite of a chosen section is chosen too."""
        with self.lock:
            course = self.get(course_id)
            if not section_ids:
                raise CourseError("Choose at least one section for the placement check.")
            chosen = list(dict.fromkeys(section_ids))
            for section_id in chosen:
                section = self.section(course, section_id)
                outside = [p for p in section["prereqs"] if p not in chosen]
                if outside:
                    raise CourseError(f"Section {section_id} needs {', '.join(outside)} first, so add those to the "
                                      "placement check too, or leave this one out.")
                if section["status"] in DONE:
                    raise CourseError(f"Section {section_id} is already {section['status'].replace('_', ' ')}.")
            course["placement"] = {"sections": chosen, "set": None, "result": None}
            return self._save(course, f"placement check planned over {', '.join(chosen)}")

    def record_placement(self, course_id: str, items_by_section: dict[str, list[dict]], when: float) -> dict:
        """Placement is one exam: each section it covers is tested out if its items pass."""
        with self.lock:
            course = self.get(course_id)
            placement = course.get("placement") or {}
            if (placement.get("result") or {}).get("at") == when:
                return course
            results = {}
            for section_id in placement.get("sections", []):
                result = mastery(course["plan"]["mastery"], items_by_section.get(section_id, []))
                results[section_id] = result
                if result["passed"]:
                    self.section(course, section_id)["tested_out"] = True
            placement["result"] = {"at": when, "sections": results}
            passed = [s for s, r in results.items() if r["passed"]]
            return self._save(course, "placement: tested out of " + (", ".join(passed) if passed else "nothing"))

    def delete(self, course_id: str) -> list[dict]:
        """Removes the course itself. Its documents and sets are returned so the caller can keep them or not."""
        with self.lock:
            course = self.get(course_id)
            items = []
            for section in self.sections(course):
                if section.get("lesson_doc"):
                    items.append({"kind": "doc", "id": section["lesson_doc"]})
                for set_id in [section.get("check_set"), *section.get("practice_sets", [])]:
                    if set_id:
                        items.append({"kind": "set", "id": set_id})
            if (course.get("placement") or {}).get("set"):
                items.append({"kind": "set", "id": course["placement"]["set"]})
            self._path(course_id).unlink()
            self.overview_path(course_id).unlink(missing_ok=True)
            return items

    # --- What Claude and the page read ----------------------------------------------

    def summary(self, course: dict) -> dict:
        sections = self.sections(course)
        return {"id": course["id"], "title": course["title"], "phase": course["phase"], "experimental": True,
                "created_at": course["created_at"], "sections": len(sections),
                "done": sum(s["status"] in DONE for s in sections),
                "available": sum(s["status"] == AVAILABLE for s in sections)}

    def overview(self, course_id: str) -> dict:
        course = self.get(course_id)
        path = self.overview_path(course_id)
        return {"course": course_id, "overview": path.read_text() if path.exists() else self.overview_text(course)}

    @staticmethod
    def weak_spots(course: dict) -> list[str]:
        counts: dict[str, int] = {}
        for section in Courses.sections(course):
            for attempt in section.get("attempts", []):
                for objective in attempt.get("missed_objectives", []):
                    counts[f"{section['id']}: {objective}"] = counts.get(f"{section['id']}: {objective}", 0) + 1
        return [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:OVERVIEW_WEAK_SPOTS]

    def overview_text(self, course: dict) -> str:
        """Short on purpose: Claude reads this first for every course step."""
        intake = course["intake"]
        lines = [f"# {course['title']} (experimental course, id {course['id']})", f"Phase: {course['phase']}",
                 f"Goal: {intake['goal']}"]
        if intake.get("intention") or intake.get("intention_details"):
            why = INTENTIONS.get(intake.get("intention"), "")
            details = intake.get("intention_details", "")[:200]
            lines.append("Learning it for: " + ("; ".join(part for part in (why, details) if part)))
        if intake.get("end_goal"):
            lines.append(f"By the end, able to: {intake['end_goal'][:300]}")
        for name in ("background", "time_per_week", "library_class"):
            if intake.get(name):
                lines.append(f"{name.replace('_', ' ').capitalize()}: {intake[name][:200]}")
        plan = course.get("plan")
        if plan:
            p = plan["presentation"]
            lines += ["", "## Plan",
                      f"Style: {p['style'][:200] or 'not set'}. Examples: {p['examples']}. Figures: {'on' if p['figures'] else 'off'}.",
                      f"Assessment: {', '.join(plan['assessment']['default_kinds'])}. "
                      f"Mastery: {round(plan['mastery']['threshold'] * 100)}% with at most "
                      f"{round(plan['mastery']['max_hints_ratio'] * 100)}% of items hinted.",
                      *([f"Pacing: {plan['pacing'][:200]}"] if plan["pacing"] else [])]
        if course["chapters"]:
            lines += ["", "## Syllabus"]
            for chapter in course["chapters"]:
                lines.append(f"{chapter['id']} {chapter['title']}")
                for s in chapter["sections"]:
                    goal = "; ".join(s["objectives"])
                    if len(goal) > OVERVIEW_OBJECTIVE_CHARS:
                        goal = goal[:OVERVIEW_OBJECTIVE_CHARS - 3] + "..."
                    needs = f" needs {', '.join(s['prereqs'])}" if s["prereqs"] else ""
                    score = f" {round(s['mastery']['score'] * 100)}%" if s.get("mastery") else ""
                    lines.append(f"- {s['id']} [{s['status']}{score}] {s['title']}{needs}: {goal}")
        elif course["research"]["summary"]:
            lines += ["", "## Research", course["research"]["summary"][:600]]
        weak = self.weak_spots(course)
        if weak:
            lines += ["", "## Weak spots", *[f"- {w}" for w in weak]]
        return "\n".join(lines) + "\n"

    def section_view(self, course_id: str, section_id: str) -> dict:
        """One section and how the student did on its prerequisites, for writing its lesson and check."""
        course = self.get(course_id)
        section = self.section(course, section_id)
        prereqs = []
        for prereq_id in section["prereqs"]:
            prereq = self.section(course, prereq_id)
            result = prereq.get("mastery") or {}
            prereqs.append({"id": prereq_id, "title": prereq["title"], "status": prereq["status"],
                            "score": result.get("score"), "missed_objectives": result.get("missed_objectives", [])})
        plan = course.get("plan") or DEFAULT_PLAN
        kinds = section["assessment_kinds"] if section["assessment_kinds"] and plan["assessment"]["per_section_override"] \
            else plan["assessment"]["default_kinds"]
        return {"course": course_id, "section": {k: section[k] for k in (
                    "id", "title", "objectives", "prereqs", "subsections", "status", "lesson_doc", "check_set", "practice_sets")},
                "objective_ids": {o: slug(o) for o in section["objectives"]},
                "assessment_kinds": kinds, "last_attempt": (section["attempts"] or [None])[-1],
                "prereq_performance": prereqs}
