"""What a study guide or equation sheet may contain, and what's computed for it."""
import math

import pytest

import documents
from documents import Refused, build_block, build_blocks, clean_svg
from methods import all_methods

METHODS = all_methods()
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><line x1="0" y1="0" x2="9" y2="9" stroke="black"/></svg>'


def block(**fields):
    return build_block(fields, METHODS, {})


def curve(**fields):
    return block(type="plot", plot_type="curves", **fields)["figure"]


def test_a_plot_is_computed_from_its_formula():
    figure = curve(curves=[{"latex": r"e^{-x}\cos 3x", "name": "y"}], **{"from": 0, "to": 6}, title="Decay")
    points = figure["series"][0]["points"]
    assert figure["kind"] == "lines" and figure["title"] == "Decay" and len(points) == 241
    for at, value in points[::40]:
        assert value == pytest.approx(math.exp(-at) * math.cos(3 * at), rel=1e-3, abs=1e-3)


def test_a_range_can_be_written_in_latex():
    assert curve(curves=[{"latex": r"\sin x"}], **{"from": 0, "to": r"2\pi"})["series"][0]["points"][-1][0] == pytest.approx(2 * math.pi, rel=1e-4)


def test_two_curves_share_x_values_even_where_one_is_undefined():
    figure = curve(curves=[{"latex": r"\ln x", "name": "a"}, {"latex": "x", "name": "b"}], **{"from": -1, "to": 4})
    one, two = figure["series"]
    assert [p[0] for p in one["points"]] == [p[0] for p in two["points"]]
    assert all(p[0] > 0 for p in one["points"])


@pytest.mark.parametrize("fields,words", [
    ({"curves": [], "from": 0, "to": 1}, "curves"),
    ({"curves": [{"latex": "x"}] * 3, "from": 0, "to": 1}, "second plot"),
    ({"curves": [{"latex": "ax"}], "from": 0, "to": 1}, "other than x"),
    ({"curves": [{"latex": "x"}], "from": 2, "to": 1}, "range"),
    ({"curves": [{"latex": "x"}], "from": 0, "to": 1, "log_x": True}, "range"),
    ({"curves": [{"latex": r"\ln x"}], "from": -5, "to": -1}, "aren't defined"),
])
def test_a_plot_it_cannot_compute_is_refused(fields, words):
    with pytest.raises(Refused, match=words):
        curve(**fields)


def test_phase_and_time_plots_come_from_the_matrix():
    phase = block(type="plot", plot_type="phase", matrix=[[0, 1], [-2, -3]], ic=[1, 0])["figure"]
    assert phase["kind"] == "phase" and phase["paths"] and len(phase["eigen"]) == 2
    spiral = block(type="plot", plot_type="phase", matrix=[["-1", "1"], ["-1", "-1"]])["figure"]
    assert spiral["eigen"] == [], "complex eigenvalues have no real lines to draw"
    assert block(type="plot", plot_type="time", matrix=[[0, 1], [-2, -3]], ic=[1, 0])["figure"]["kind"] == "lines"
    with pytest.raises(Refused, match="starting point"):
        block(type="plot", plot_type="time", matrix=[[0, 1], [-2, -3]])
    with pytest.raises(Refused, match="2 by 2"):
        block(type="plot", plot_type="phase", matrix=[[1, 2, 3]])


def test_a_drawing_is_kept_as_written():
    assert clean_svg(SVG) == SVG


@pytest.mark.parametrize("svg,words", [
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script></svg>', "script"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><foreignObject/></svg>', "foreignObject"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><image href="http://x/y.png"/></svg>', "image"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect onclick="x()"/></svg>', "onclick"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect style="fill:red"/></svg>', "style"),
    ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect fill="url(http://x/#a)"/></svg>', "own markers"),
    ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink" viewBox="0 0 1 1"><path x:href="#a"/></svg>', "href"),
    ('<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>', "DOCTYPE"),
    ('<svg viewBox="0 0 1 1"/>', "xmlns"),
    ('<svg xmlns="http://www.w3.org/2000/svg"/>', "viewBox"),
    ("<svg", "valid XML"),
    ("<svg>" + " " * documents.MAX_SVG_CHARS, "characters"),
])
def test_a_drawing_can_only_draw(svg, words):
    with pytest.raises(Refused, match=words):
        clean_svg(svg)


def test_an_example_with_a_method_is_computed_and_verified():
    made = block(type="example", title="Modification Rule", method="undetermined_coefficients", statement="y''-y=e^{x}")
    assert made["verified"]["checkers"] == "passed" and made["verified"]["residual"] in ("proved", "numeric")
    assert any("x" in step["answer"] and "e^{x}" in step["answer"] for step in made["steps"])
    assert set(made["steps"][0]) == {"label", "answer", "answer_format", "note", "figures"}, "no grading data or typing instructions"


def test_a_written_example_records_that_nothing_checked_it():
    made = block(type="example", title="T", problem="A beam...", steps=[{"label": "Sum forces", "text": "$F=0$"}])
    assert made["verified"] is None and made["steps"] == [{"label": "Sum forces", "text": "$F=0$"}]


def test_an_example_is_refused_rather_than_shown_unverified():
    report = build_blocks([{"type": "text", "text": "ok"},
                           {"type": "example", "method": "undetermined_coefficients", "statement": r"y''+y=\sec x"},
                           {"type": "example", "method": "concept_questions", "statement": "x"},
                           {"type": "example", "method": "no_such_method", "statement": "y'=y"}], METHODS, {})
    assert [f["index"] for f in report["failed"]] == [1, 2, 3]


@pytest.mark.parametrize("fields,words", [
    ({"type": "nonsense"}, "no block type"),
    ({"type": "heading"}, "no text"),
    ({"type": "equation"}, "no latex"),
    ({"type": "list", "items": [" "]}, "no items"),
    ({"type": "table", "headers": ["a", "b"], "rows": [["1"]]}, "one cell per header"),
    ({"type": "plot", "plot_type": "pie"}, "plot_type"),
    ({"type": "diagram", "svg": SVG}, "no alt"),
    ({"type": "example", "problem": "p"}, "written steps"),
])
def test_an_incomplete_block_is_refused(fields, words):
    with pytest.raises(Refused, match=words):
        block(**fields)


@pytest.mark.parametrize("fields", [
    {"type": "text", "text": "**Bold** lead-in"},
    {"type": "list", "items": ["**a**"]},
    {"type": "note", "text": "**a**"},
    {"type": "table", "headers": ["**a**"], "rows": [["b"]]},
])
def test_markdown_is_refused_since_the_page_would_show_the_stars(fields):
    with pytest.raises(Refused, match="bold"):
        block(**fields)


def test_only_known_fields_are_kept():
    assert block(type="text", text="hi", extra="<script>") == {"type": "text", "text": "hi"}
