"""Courses (experimental): the syllabus rules, unlocking, mastery and the overview, with no workers."""
import pytest

from courses import CourseError, Courses, find_cycle, mastery

RULE = {"threshold": 0.8, "max_hints_ratio": 0.5, "require_all_prereqs": True}
PLAN = {"presentation": {"style": "Concise, worked examples first", "examples": "worked_first", "figures": True},
        "assessment": {"default_kinds": ["multiple_choice", "numeric"]},
        "mastery": {"threshold": 0.8, "max_hints_ratio": 0.5}, "pacing": "One section a week"}


def section(section_id, *prereqs, objectives=("Say what it is",)):
    return {"id": section_id, "title": section_id.upper(), "objectives": list(objectives), "prereqs": list(prereqs)}


SYLLABUS = [
    {"id": "c1", "title": "Basics", "sections": [section("c1-s1"), section("c1-s2", "c1-s1")]},
    {"id": "c2", "title": "Next", "sections": [section("c2-s1", "c1-s1", "c1-s2")]},
]


@pytest.fixture
def courses(tmp_path):
    return Courses(tmp_path)


def started(courses, syllabus=SYLLABUS, plan=PLAN):
    course = courses.create({"goal": "Learn orbital mechanics."})
    courses.save_research(course["id"], "Kepler, then two-body motion.", [])
    courses.save_plan(course["id"], plan)
    courses.save_syllabus(course["id"], syllabus)
    return course["id"]


def statuses(courses, course_id):
    return {s["id"]: s["status"] for s in courses.sections(courses.get(course_id))}


def items(right, total, hinted=0, objective="say-what-it-is"):
    return [{"correct": n < right, "hinted": n < hinted, "objective": objective} for n in range(total)]


# --- Phases -------------------------------------------------------------------

def test_one_sentence_is_enough_to_start_and_the_phases_move_forward(courses):
    course = courses.create({"goal": "Music theory, from zero."})
    assert course["phase"] == "research" and course["experimental"] and course["id"] == "music-theory-from-zero"
    assert courses.save_research(course["id"], "Notes, scales, chords.", [])["phase"] == "planning"
    assert courses.save_plan(course["id"], PLAN)["phase"] == "syllabus"
    assert courses.save_syllabus(course["id"], SYLLABUS)["phase"] == "active"
    with pytest.raises(CourseError, match="goal"):
        courses.create({"goal": "  "})


def test_a_syllabus_waits_for_the_plan(courses):
    course = courses.create({"goal": "Rust"})
    with pytest.raises(CourseError, match="plan"):
        courses.save_syllabus(course["id"], SYLLABUS)


def test_changing_the_plan_is_recorded_in_history(courses):
    course_id = started(courses)
    changed = courses.save_plan(course_id, {**PLAN, "pacing": "Two a week"}, change="faster pacing")
    assert changed["plan"]["pacing"] == "Two a week"
    assert changed["history"][-1]["change"] == "plan updated: faster pacing"
    with pytest.raises(CourseError, match="threshold"):
        courses.save_plan(course_id, {**PLAN, "mastery": {"threshold": 1.5}})
    with pytest.raises(CourseError, match="default_kinds"):
        courses.save_plan(course_id, {**PLAN, "assessment": {"default_kinds": ["essay"]}})


# --- Syllabus validation ----------------------------------------------------------

def test_duplicate_ids_missing_prereqs_and_cycles_are_refused(courses):
    course_id = started(courses)
    with pytest.raises(CourseError, match="used twice"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [section("x"), section("x")]}])
    with pytest.raises(CourseError, match="used twice"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [section("c1")]}])
    with pytest.raises(CourseError, match="aren't in the syllabus: nowhere"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [section("a", "nowhere")]}])
    with pytest.raises(CourseError, match="circle"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [
            section("a", "c"), section("b", "a"), section("c", "b")]}])
    with pytest.raises(CourseError, match="its own prerequisite"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [section("a", "a")]}])
    with pytest.raises(CourseError, match="objective"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [section("a", objectives=())]}])
    with pytest.raises(CourseError, match="lowercase"):
        courses.save_syllabus(course_id, [{"id": "c1", "title": "A", "sections": [section("Bad Id")]}])
    # Nothing refused was saved.
    assert list(statuses(courses, course_id)) == ["c1-s1", "c1-s2", "c2-s1"]


def test_find_cycle_names_the_loop():
    assert find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]
    assert find_cycle({"a": [], "b": ["a"], "c": ["a", "b"]}) is None


def test_an_edit_keeps_progress_by_id_and_never_reuses_a_removed_id(courses, tmp_path):
    course_id = started(courses)
    courses.link(course_id, "c1-s1", "lesson", "lesson-doc")
    courses.record_check(course_id, "c1-s1", "check-1", items(5, 5), 1.0)
    renamed = [{"id": "c1", "title": "Basics, renamed", "sections": [
        {**section("c1-s2", "c1-s1"), "title": "Moved first"}, {**section("c1-s1"), "title": "Renamed"}]}]
    edited = courses.save_syllabus(course_id, renamed, change="reordered")
    first = courses.section(edited, "c1-s1")
    assert first["title"] == "Renamed" and first["lesson_doc"] == "lesson-doc" and first["status"] == "mastered"
    assert edited["retired_ids"] == ["c2-s1"] and "removed c2-s1" in edited["history"][-1]["change"]
    with pytest.raises(CourseError, match="never reused"):
        courses.save_syllabus(course_id, [*renamed, {"id": "c2", "title": "Back", "sections": [section("c2-s1")]}])


def test_a_caller_cant_set_status_or_mastery(courses):
    course_id = started(courses)
    forged = [{"id": "c1", "title": "A", "sections": [
        {**section("c1-s1"), "status": "mastered", "mastery": {"passed": True}},
        {**section("c1-s2", "c1-s1"), "status": "available", "tested_out": True, "lesson_doc": "x"}]}]
    saved = courses.save_syllabus(course_id, forged)
    assert statuses(courses, course_id) == {"c1-s1": "available", "c1-s2": "locked"}
    assert courses.section(saved, "c1-s2")["lesson_doc"] is None


def test_a_partial_syllabus_replaces_only_the_chapters_it_names(courses):
    course_id = started(courses)
    saved = courses.save_syllabus(course_id, [{"id": "c2", "title": "Next, expanded", "sections": [
        section("c2-s1", "c1-s1", "c1-s2"), section("c2-s2", "c2-s1")]}], partial=True)
    assert [c["title"] for c in saved["chapters"]] == ["Basics", "Next, expanded"]
    assert list(statuses(courses, course_id)) == ["c1-s1", "c1-s2", "c2-s1", "c2-s2"]


# --- Unlocking ------------------------------------------------------------------

def test_a_section_unlocks_only_when_every_prereq_is_mastered_or_tested_out(courses):
    course_id = started(courses)
    assert statuses(courses, course_id) == {"c1-s1": "available", "c1-s2": "locked", "c2-s1": "locked"}
    courses.record_check(course_id, "c1-s1", "check-1", items(5, 5), 1.0)
    assert statuses(courses, course_id) == {"c1-s1": "mastered", "c1-s2": "available", "c2-s1": "locked"}
    courses.record_check(course_id, "c1-s2", "check-2", items(3, 5), 2.0)
    assert statuses(courses, course_id)["c2-s1"] == "locked"
    courses.record_check(course_id, "c1-s2", "check-3", items(4, 5), 3.0)
    assert statuses(courses, course_id) == {"c1-s1": "mastered", "c1-s2": "mastered", "c2-s1": "available"}


def test_prereqs_may_point_later_in_the_syllabus(courses):
    course_id = started(courses, [{"id": "c1", "title": "A", "sections": [section("late", "early"), section("early")]}])
    courses.record_check(course_id, "early", "s", items(1, 1), 1.0)
    assert statuses(courses, course_id) == {"late": "available", "early": "mastered"}


def test_any_prereq_unlocks_when_the_plan_says_so(courses):
    course_id = started(courses, plan={**PLAN, "mastery": {**PLAN["mastery"], "require_all_prereqs": False}})
    courses.record_check(course_id, "c1-s1", "s", items(1, 1), 1.0)
    assert statuses(courses, course_id)["c2-s1"] == "available"


def test_generating_and_in_progress_follow_the_links(courses):
    course_id = started(courses)
    courses.set_generating(course_id, "c1-s1", "req1")
    assert statuses(courses, course_id)["c1-s1"] == "generating"
    courses.clear_request("req1")
    assert statuses(courses, course_id)["c1-s1"] == "available"
    courses.link(course_id, "c1-s1", "lesson", "doc-1")
    assert statuses(courses, course_id)["c1-s1"] == "in_progress"
    with pytest.raises(CourseError, match="locked"):
        courses.set_generating(course_id, "c2-s1", "req2")


def test_nothing_links_to_a_locked_section_and_a_lesson_isnt_replaced_by_accident(courses):
    course_id = started(courses)
    with pytest.raises(CourseError, match="locked"):
        courses.check_link(course_id, "c1-s2", "lesson")
    with pytest.raises(CourseError, match="course_role"):
        courses.check_link(course_id, "c1-s1", "homework")
    courses.link(course_id, "c1-s1", "lesson", "doc-1")
    with pytest.raises(CourseError, match="already has a lesson"):
        courses.check_link(course_id, "c1-s1", "lesson")
    courses.check_link(course_id, "c1-s1", "lesson", regenerate=True)
    courses.check_link(course_id, "c1-s1", "check")


# --- Mastery -------------------------------------------------------------------

def test_mastery_at_the_threshold_boundary():
    assert mastery(RULE, items(4, 5))["passed"] is True
    assert mastery(RULE, items(3, 5))["passed"] is False
    assert mastery(RULE, items(8, 10))["score"] == 0.8
    assert mastery(RULE, [])["passed"] is False


def test_hints_count_up_to_the_limit_and_a_shown_answer_never_counts():
    assert mastery(RULE, items(4, 4, hinted=2))["passed"] is True  # half hinted is allowed
    assert mastery(RULE, items(4, 4, hinted=3))["passed"] is False  # 3 of 4 is over half
    shown = [{"correct": True, "answer_shown": True, "objective": "a"}] + items(4, 4)
    result = mastery(RULE, shown)
    assert result["correct"] == 4 and result["total"] == 5 and result["missed_objectives"] == ["a"]
    assert mastery(RULE, [{"correct": True, "skipped": True}])["passed"] is False


def test_mastery_once_earned_is_kept_and_the_same_finish_counts_once(courses):
    course_id = started(courses)
    courses.record_check(course_id, "c1-s1", "check-1", items(5, 5), 1.0)
    courses.record_check(course_id, "c1-s1", "check-1", items(5, 5), 1.0)
    courses.record_check(course_id, "c1-s1", "retry", items(0, 5), 2.0)
    course = courses.get(course_id)
    assert len(courses.section(course, "c1-s1")["attempts"]) == 2
    assert courses.section(course, "c1-s1")["status"] == "mastered"


# --- Placement ------------------------------------------------------------------

def test_placement_covers_early_sections_and_tests_out_the_ones_passed(courses):
    course_id = started(courses)
    with pytest.raises(CourseError, match="course_placement"):
        courses.check_link(course_id, None, "placement")
    with pytest.raises(CourseError, match="needs c1-s1 first"):
        courses.choose_placement(course_id, ["c1-s2"])
    courses.choose_placement(course_id, ["c1-s1", "c1-s2"])
    courses.link(course_id, None, "placement", "placement-set")
    courses.record_placement(course_id, {"c1-s1": items(2, 2), "c1-s2": items(0, 2)}, 5.0)
    assert statuses(courses, course_id) == {"c1-s1": "tested_out", "c1-s2": "available", "c2-s1": "locked"}
    assert courses.owner_of_set("placement-set")[2] == "placement"


# --- Overview and the rest --------------------------------------------------------

def test_the_overview_stays_short_for_a_ten_chapter_course(courses):
    long_objective = "Explain and apply the main idea of this section in a realistic engineering problem " * 2
    syllabus = [{"id": f"c{c}", "title": f"Chapter {c} with a fairly long descriptive title", "sections": [
        section(f"c{c}-s{s}", *([f"c{c}-s{s - 1}"] if s > 1 else []), objectives=[long_objective, long_objective])
        for s in range(1, 7)]} for c in range(1, 11)]
    course_id = started(courses, syllabus)
    text = courses.overview(course_id)["overview"]
    assert "c10-s6 [locked]" in text and len(text) < 12_000
    assert courses.overview_path(course_id).read_text() == text


def test_section_view_reports_prereq_performance_and_the_assessment_kinds(courses):
    course_id = started(courses)
    courses.record_check(course_id, "c1-s1", "check", items(4, 5, objective="kepler"), 1.0)
    view = courses.section_view(course_id, "c1-s2")
    assert view["prereq_performance"] == [{"id": "c1-s1", "title": "C1-S1", "status": "mastered", "score": 0.8,
                                          "missed_objectives": ["kepler"]}]
    assert view["assessment_kinds"] == ["multiple_choice", "numeric"]
    assert "c1-s1: kepler" in courses.overview(course_id)["overview"]


def test_deleting_a_course_returns_its_items_and_leaves_them(courses):
    course_id = started(courses)
    courses.link(course_id, "c1-s1", "lesson", "doc-1")
    courses.link(course_id, "c1-s1", "check", "set-1")
    assert courses.delete(course_id) == [{"kind": "doc", "id": "doc-1"}, {"kind": "set", "id": "set-1"}]
    assert courses.all() == [] and not courses.exists(course_id)
    with pytest.raises(CourseError, match="isn't a course"):
        courses.get("../escape")


def test_the_course_is_complete_when_every_section_is_done(courses):
    course_id = started(courses, [{"id": "c1", "title": "A", "sections": [section("only")]}])
    courses.record_check(course_id, "only", "s", items(1, 1), 1.0)
    assert courses.get(course_id)["phase"] == "complete"


# --- Through the engine: tools and links to documents and sets ---------------------

import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from engine import Engine  # noqa: E402
from server import App, Worker  # noqa: E402

PAGE = ("<html><body><p>Kepler's laws describe how planets move around the Sun in ellipses.</p>"
        + "<p>Orbital mechanics applies these laws to satellites, probes and spacecraft of every size.</p>" * 20
        + "</body></html>")
QUOTE = "Kepler's laws describe how planets move around the Sun"
GUIDE = [{"type": "heading", "text": "Kepler"},
         {"type": "definition", "term": "Orbit", "meaning": "The path a body takes around another.", "own_words": True}]
CONCEPT = {"statement": "\\text{Concept check}", "method": "concept_questions", "section": "c1-s1", "topic": "Kepler",
           "questions": [{"question": "What shape is an orbit?", "answer": "An ellipse.", "nudge": "Think conics.",
                          "method_hint": "Kepler's first law.", "own_words": True}]}


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = PAGE.encode()
            self.send_response(200 if self.path == "/kepler" else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    running = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}"
    running.shutdown()


@pytest.fixture(scope="module")
def workers():
    pool = {"grade": Worker("grading"), "build": Worker("verification")}
    yield pool
    for worker in pool.values():
        worker.stop()


@pytest.fixture
def engine(tmp_path, workers):
    return Engine(tmp_path, lambda which, op, args, timeout: workers[which].call(op, args, timeout),
                  lambda event: None, allow_local_sources=True)


def through_tools(engine, site):
    course = engine.course_create({"goal": "Orbital mechanics for a satellite project", "title": "Orbital Mechanics"})
    assert course["phase"] == "research" and course["course"] == "orbital-mechanics"
    saved = engine.course_save_research({"course": course["course"], "summary": "Start with Kepler.",
                                         "sources": [{"title": "Kepler", "url": f"{site}/kepler", "quote": QUOTE}]})
    assert saved["saved"] and saved["sources_confirmed"] == 1 and saved["phase"] == "planning"
    assert engine.course_save_plan({"course": course["course"], "plan": PLAN})["phase"] == "syllabus"
    made = engine.course_save_syllabus({"course": course["course"], "chapters": SYLLABUS})
    assert made["sections"][0] == {"id": "c1-s1", "status": "available"}
    return course["course"]


def test_the_tools_take_a_course_from_intake_to_active(engine, site):
    course_id = through_tools(engine, site)
    assert engine.course_overview("")["courses"][0]["sections"] == 3
    assert "c1-s1 [available]" in engine.course_overview(course_id)["overview"]
    assert engine.course_section(course_id, "c1-s2")["section"]["status"] == "locked"


def test_research_needs_real_sources(engine, site):
    course = engine.course_create({"goal": "Rust"})
    with pytest.raises(Exception, match="at least one source"):
        engine.course_save_research({"course": course["course"], "summary": "x", "sources": []})
    refused = engine.course_save_research({"course": course["course"], "summary": "x",
                                           "sources": [{"title": "Made up", "url": f"{site}/kepler", "quote": "words that are not there at all"}]})
    assert refused["saved"] is False and refused["failed"][0]["index"] == 0
    assert engine.courses.get(course["course"])["phase"] == "research"


def test_a_lesson_links_to_an_unlocked_section_only(engine, site):
    course_id = through_tools(engine, site)
    lesson = {"kind": "study_guide", "title": "Kepler's laws", "blocks": GUIDE, "course": course_id, "course_role": "lesson"}
    with pytest.raises(CourseError, match="locked"):
        engine.create_doc({**lesson, "section": "c1-s2"})
    assert list((engine.data / "docs").glob("*.json")) == []
    with pytest.raises(Exception, match="study guide"):
        engine.create_doc({**lesson, "kind": "equation_sheet", "section": "c1-s1"})
    made = engine.create_doc({**lesson, "section": "c1-s1"})
    assert made["created"]
    section = engine.courses.section(engine.courses.get(course_id), "c1-s1")
    assert section["lesson_doc"] == made["doc"] and section["status"] == "in_progress"
    assert engine.load_doc(made["doc"])["course"] == {"id": course_id, "section": "c1-s1", "role": "lesson"}
    with pytest.raises(CourseError, match="already has a lesson"):
        engine.create_doc({**lesson, "section": "c1-s1"})
    again = engine.create_doc({**lesson, "section": "c1-s1", "regenerate": True})
    assert engine.courses.section(engine.courses.get(course_id), "c1-s1")["lesson_doc"] == again["doc"]


def test_a_check_set_links_to_its_section_and_placement_waits_for_its_sections(engine, site):
    course_id = through_tools(engine, site)
    made = engine.create_set({"title": "Kepler check", "textbook": {}, "problems": [CONCEPT],
                              "course": course_id, "section": "c1-s1", "course_role": "check"})
    assert made["created"]
    assert engine.courses.section(engine.courses.get(course_id), "c1-s1")["check_set"] == made["set"]
    assert engine.load_set(made["set"])["course"]["role"] == "check"
    with pytest.raises(Exception, match="create_doc"):
        engine.create_set({"title": "x", "textbook": {}, "problems": [CONCEPT], "course": course_id,
                           "section": "c1-s1", "course_role": "lesson"})
    with pytest.raises(CourseError, match="course_placement"):
        engine.create_set({"title": "Placement", "textbook": {}, "problems": [CONCEPT], "course": course_id,
                           "course_role": "placement"})
    engine.course_placement(course_id, ["c1-s1"])
    placed = engine.create_set({"title": "Placement", "textbook": {}, "problems": [CONCEPT], "course": course_id,
                                "course_role": "placement"})
    assert engine.courses.get(course_id)["placement"]["set"] == placed["set"]


def test_documents_and_sets_without_a_course_are_unchanged(engine):
    made = engine.create_doc({"kind": "study_guide", "title": "Plain", "blocks": GUIDE})
    assert sorted(made) == ["blocks_in_own_words", "created", "diagrams", "doc", "examples_not_checked",
                            "examples_verified", "ok", "plots", "sources_confirmed", "sources_not_auto_checked"]
    assert "course" not in engine.load_doc(made["doc"])
    plain = engine.create_set({"title": "Plain set", "textbook": {}, "problems": [CONCEPT]})
    assert sorted(plain) == ["created", "ok", "problems", "set"] and "course" not in engine.load_set(plain["set"])
    assert engine.courses.all() == []


def test_a_dismissed_request_stops_a_section_showing_as_generating(engine, site):
    course_id = through_tools(engine, site)
    engine.courses.set_generating(course_id, "c1-s1", "abc123")
    engine.dismiss_request("abc123")
    assert engine.courses.section(engine.courses.get(course_id), "c1-s1")["status"] == "available"


def test_the_server_routes_course_tools(tmp_path):
    app = App(tmp_path, emit_stdout=False)
    try:
        made = app.tool("course_create", {"goal": "Control systems"})
        assert app.tool("course_overview", {"course": made["course"]})["overview"].startswith("# Control systems")
        with pytest.raises(CourseError, match="isn't a course"):
            app.tool("course_section", {"course": "Not An Id", "section": "x"})
    finally:
        for worker in app.workers.values():
            worker.stop()
