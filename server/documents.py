"""Study guides and equation sheets, checked in the worker before they're saved.

A plot is computed from its formula and an example with a method is built and verified
like a worksheet problem, so neither depends on a number typed by hand. Written text
can't be checked this way, and each example records which kind it is.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import sympy as sp

import figures
from mathparse import to_sympy
from verify import build_and_verify

MAX_CURVES = 2  # The page has two validated line styles. More belongs in a second plot.
PLOT_SAMPLES = 240
MIN_PLOT_POINTS = 40
MAX_SVG_CHARS = 100_000
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_TAGS = {"svg", "g", "path", "line", "rect", "circle", "ellipse", "polygon", "polyline", "text", "tspan",
            "defs", "marker", "title", "desc"}


class Refused(Exception):
    """Why a block can't go in the document, for Claude to fix."""


def _plain(text: str) -> str:
    """The page draws text and $math$, so Markdown would show up as literal stars."""
    if "**" in text:
        raise Refused(f"it uses ** for bold, which the page shows literally, in: {text[:60]}")
    return text


def _text(block: dict, field: str, required: bool = True) -> str:
    value = block.get(field)
    if value is None or not str(value).strip():
        if required:
            raise Refused(f"it has no {field}")
        return ""
    return _plain(str(value).strip())


def _number(value) -> float:
    try:
        return float(to_sympy(str(value)))
    except Exception:
        raise Refused(f"{value!r} isn't a number")


def _tidy(value: float) -> float:
    return float(f"{value:.6g}")


def curves_figure(block: dict) -> dict:
    curves = block.get("curves") or []
    if not 1 <= len(curves) <= MAX_CURVES:
        raise Refused(f"a plot takes 1 or {MAX_CURVES} curves, so use a second plot for more")
    x_name = str(block.get("x") or "x")
    x = sp.Symbol(x_name)
    start, stop = _number(block.get("from")), _number(block.get("to"))
    log_x = bool(block.get("log_x"))
    if not start < stop or (log_x and start <= 0):
        raise Refused(f"the range from {start} to {stop} doesn't work for this axis")
    functions = []
    for curve in curves:
        expression = to_sympy(_text(curve, "latex"))
        extra = expression.free_symbols - {x}
        if extra:
            raise Refused(f"{curve['latex']} has symbols other than {x_name}: {sorted(map(str, extra))}. Give them numbers.")
        functions.append(sp.lambdify(x, expression, "math"))
    series = [{"name": str(curve.get("name") or "y"), "points": []} for curve in curves]
    for k in range(PLOT_SAMPLES + 1):
        at = start * (stop / start) ** (k / PLOT_SAMPLES) if log_x else start + (stop - start) * k / PLOT_SAMPLES
        try:
            values = [float(function(at)) for function in functions]
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            continue
        # Every curve keeps the same x values, since the page reads them by index.
        if all(math.isfinite(value) for value in values):
            for line, value in zip(series, values):
                line["points"].append([_tidy(at), _tidy(value)])
    if len(series[0]["points"]) < MIN_PLOT_POINTS:
        raise Refused(f"the curves aren't defined across {start} to {stop}")
    return {"kind": "lines", "title": str(block.get("title") or ""), "x": x_name, "series": series,
            "caption": str(block.get("caption") or ""), "alt": str(block.get("alt") or ""), "log_x": log_x}


def _matrix(block: dict) -> sp.Matrix:
    rows = block.get("matrix") or []
    if len(rows) != 2 or any(len(row) != 2 for row in rows):
        raise Refused("this plot needs a 2 by 2 matrix")
    return sp.Matrix(2, 2, [to_sympy(str(value)) for row in rows for value in row])


def _floats(matrix: sp.Matrix) -> list[list[float]]:
    return [[float(matrix[i, j]) for j in range(2)] for i in range(2)]


def _start(block: dict, required: bool = False):
    start = block.get("ic")
    if not start:
        if required:
            raise Refused("this plot needs a starting point in ic")
        return None
    return [_number(value) for value in start]


def phase_figure(block: dict) -> dict:
    matrix = _matrix(block)
    directions = [(float(vector[0]), float(vector[1])) for value, _, vectors in matrix.eigenvects()
                  if value.is_real for vector in vectors]
    figure = figures.phase_portrait(_floats(matrix), directions, _start(block),
                                    str(block.get("caption") or ""), str(block.get("alt") or ""))
    return {**figure, "title": str(block.get("title") or figure["title"])}


def time_figure(block: dict) -> dict:
    figure = figures.time_plot(_floats(_matrix(block)), _start(block, required=True),
                               str(block.get("caption") or ""), str(block.get("alt") or ""))
    return {**figure, "title": str(block.get("title") or figure["title"])}


PLOTS = {"curves": curves_figure, "phase": phase_figure, "time": time_figure}


def clean_svg(svg: str) -> str:
    """Only drawing elements, so a diagram can't run anything or load anything."""
    if len(svg) > MAX_SVG_CHARS:
        raise Refused(f"the drawing is over {MAX_SVG_CHARS} characters")
    if "<!" in svg or "<?" in svg:
        raise Refused("the drawing can't have a DOCTYPE, entities or processing instructions")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        raise Refused(f"the drawing isn't valid XML: {error}")
    if root.tag != f"{{{SVG_NAMESPACE}}}svg" or not root.get("viewBox"):
        raise Refused(f'the drawing needs an <svg> root with xmlns="{SVG_NAMESPACE}" and a viewBox')
    for node in root.iter():
        name = str(node.tag).split("}")[-1]
        if name not in SVG_TAGS:
            raise Refused(f"<{name}> isn't allowed in a drawing")
        for attribute, value in node.attrib.items():
            plain = attribute.split("}")[-1].lower()
            if plain.startswith("on") or plain in ("href", "style", "src"):
                raise Refused(f"the {plain} attribute isn't allowed in a drawing")
            if "url(" in value.lower() and not value.strip().lower().startswith("url(#"):
                raise Refused("a drawing can only refer to its own markers")
    return svg


def _steps_from(plan: dict) -> list[dict]:
    # Not the prompts: those tell a student what to type into a worksheet's answer box.
    return [{"label": step["label"], "answer": step["answer"],
             "answer_format": step.get("answer_format", "math"), "note": step.get("note", ""),
             "figures": step.get("figures", [])} for step in plan["steps"]]


def _example(block: dict, methods: dict, book_methods: dict) -> dict:
    title = str(block.get("title") or "")
    method_id = block.get("method")
    if not method_id:
        steps = [{"label": _text(step, "label"), "text": _text(step, "text")} for step in block.get("steps") or []]
        if not steps:
            raise Refused("an example without a method needs written steps")
        return {"type": "example", "title": title, "problem": _text(block, "problem"), "steps": steps, "verified": None}
    method = methods.get(method_id)
    if method is None or method_id == "concept_questions":
        raise Refused(f"{method_id!r} can't verify an example")
    spec = {"statement": _text(block, "statement"), "ic": block.get("ic") or [],
            "section": block.get("section", ""), "topic": block.get("topic", "")}
    notation = (book_methods.get(method_id) or {}).get("notation") or {}
    plan = build_and_verify(method, spec, notation)
    return {"type": "example", "title": title, "statement": spec["statement"], "ic": spec["ic"],
            "steps": _steps_from(plan), "verified": plan["verified"]}


def build_block(block: dict, methods: dict, book_methods: dict) -> dict:
    built = _build_block(block, methods, book_methods)
    tab = str(block.get("tab") or "").strip()
    return {**built, "tab": tab} if tab else built


def _build_block(block: dict, methods: dict, book_methods: dict) -> dict:
    kind = block.get("type")
    if kind == "heading":
        return {"type": kind, "text": _text(block, "text")}
    if kind == "text":
        return {"type": kind, "text": _text(block, "text")}
    if kind == "list":
        items = [_plain(str(item).strip()) for item in block.get("items") or [] if str(item).strip()]
        if not items:
            raise Refused("it has no items")
        return {"type": kind, "items": items}
    if kind == "equation":
        return {"type": kind, "latex": _text(block, "latex"), "name": _text(block, "name", False),
                "note": _text(block, "note", False)}
    if kind == "table":
        headers = [_plain(str(cell)) for cell in block.get("headers") or []]
        rows = [[_plain(str(cell)) for cell in row] for row in block.get("rows") or []]
        if not headers or not rows or any(len(row) != len(headers) for row in rows):
            raise Refused("every table row needs one cell per header")
        return {"type": kind, "headers": headers, "rows": rows}
    if kind == "definition":
        return {"type": kind, "term": _text(block, "term"), "meaning": _text(block, "meaning")}
    if kind == "nomenclature":
        rows = block.get("rows") or []
        if not rows:
            raise Refused("it has no rows")
        return {"type": kind, "rows": [{"symbol": _text(row, "symbol"), "meaning": _text(row, "meaning"),
                                        "unit": _text(row, "unit", False)} for row in rows]}
    if kind == "note":
        return {"type": kind, "title": _text(block, "title", False), "text": _text(block, "text")}
    if kind == "plot":
        maker = PLOTS.get(block.get("plot_type"))
        if maker is None:
            raise Refused(f"plot_type has to be one of {sorted(PLOTS)}")
        return {"type": kind, "figure": maker(block)}
    if kind == "diagram":
        return {"type": kind, "svg": clean_svg(_text(block, "svg")), "alt": _text(block, "alt"),
                "caption": _text(block, "caption", False)}
    if kind == "example":
        return _example(block, methods, book_methods)
    raise Refused(f"there's no block type called {kind!r}")


def build_blocks(blocks: list[dict], methods: dict, book_methods: dict) -> dict:
    built, failed = [], []
    for index, block in enumerate(blocks):
        try:
            built.append(build_block(block, methods, book_methods))
        except Exception as reason:  # Any block's failure is reported, and nothing is saved.
            failed.append({"index": index, "type": block.get("type"), "reason": f"{type(reason).__name__}: {reason}"})
    return {"blocks": built, "failed": failed}
