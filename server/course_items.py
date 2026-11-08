"""Course check items (experimental): multiple choice and numeric, graded exactly by the server,
and short answers, judged by Claude like concept questions. A section's math practice uses the
ordinary repertoire methods instead, so this needs no repertoire check."""
from __future__ import annotations

import math
import re

from methods import NotApplicable, make_step

KINDS = ("multiple_choice", "numeric", "short_answer")
MAX_ITEMS = 20
MAX_OPTIONS = 8
ITEM_FIELDS = ("prompt", "objective", "nudge", "method_hint")


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:limit].strip("-") or "objective"


def _number(value, what: str) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise NotApplicable(f"{what} has to be a number, like 9.81 or 1.5e-3")
    if not math.isfinite(number):
        raise NotApplicable(f"{what} has to be a finite number")
    return number


def _choice_step(step_id: str, item: dict) -> dict:
    options = [str(o).strip() for o in item.get("options") or []]
    if not 2 <= len(options) <= MAX_OPTIONS or not all(options):
        raise NotApplicable(f"multiple choice takes 2 to {MAX_OPTIONS} non-empty options")
    if len(set(options)) != len(options):
        raise NotApplicable("two options are the same")
    correct = item.get("correct")
    correct = sorted({int(c) for c in (correct if isinstance(correct, list) else [correct])}) if correct is not None else []
    if not correct or any(not 0 <= c < len(options) for c in correct):
        raise NotApplicable(f"`correct` has to be the index or indices of the right options, from 0 to {len(options) - 1}")
    key = "; ".join(options[c] for c in correct)
    step = make_step(step_id, "course_choice", item["prompt"], item["prompt"], key,
                     {"kind": "multiple_choice", "options": options, "correct": correct},
                     item["nudge"], item["method_hint"], item.get("note", ""))
    step["answer_format"] = "text"
    step["options"] = options
    step["multi"] = len(correct) > 1
    return step


def _numeric_step(step_id: str, item: dict) -> dict:
    value = _number(item.get("answer"), "`answer`")
    tolerance = item.get("tolerance") or {}
    absolute, relative = tolerance.get("absolute"), tolerance.get("relative")
    if (absolute is None) == (relative is None):
        raise NotApplicable('give `tolerance` as {"absolute": 0.01} or {"relative": 0.02}, one of the two')
    amount = _number(absolute if absolute is not None else relative, "the tolerance")
    if amount < 0 or (relative is not None and amount >= 1):
        raise NotApplicable("the tolerance has to be at least 0, and a relative one under 1")
    unit = str(item.get("unit") or "").strip()
    prompt = item["prompt"]
    if unit and unit.lower() not in prompt.lower():
        # The student has to know what to enter, so the unit is always stated.
        prompt = f"{prompt} Give the answer in {unit}."
    key = str(item["answer"]).strip() + (f" {unit}" if unit else "")
    check = {"kind": "numeric", "value": value, "unit": unit,
             "absolute": amount if absolute is not None else None, "relative": amount if relative is not None else None}
    step = make_step(step_id, "course_numeric", item["prompt"], prompt, key, check,
                     item["nudge"], item["method_hint"], item.get("note", ""))
    step["answer_format"] = "text"
    step["unit"] = unit
    return step


def _short_step(step_id: str, item: dict) -> dict:
    answer = str(item.get("answer") or "").strip()
    if not answer:
        raise NotApplicable("a short answer needs a reference `answer`")
    step = make_step(step_id, "course_short_answer", item["prompt"], "Answer in a sentence or two.", answer,
                     {"kind": "discussion", "concept": True}, item["nudge"], item["method_hint"])
    step["answer_format"] = "text"
    step["figures"] = []
    return step


BUILDERS = {"multiple_choice": _choice_step, "numeric": _numeric_step, "short_answer": _short_step}


def build_course_items(spec: dict, notation: dict) -> dict:
    items = spec.get("items") or []
    if not 1 <= len(items) <= MAX_ITEMS:
        raise NotApplicable(f"it takes from 1 to {MAX_ITEMS} entries in `items`")
    steps = []
    for number, item in enumerate(items, 1):
        kind = item.get("kind")
        if kind not in BUILDERS:
            raise NotApplicable(f"item {number}: kind has to be one of {', '.join(KINDS)}")
        missing = [name for name in ITEM_FIELDS if not str(item.get(name) or "").strip()]
        if missing:
            raise NotApplicable(f"item {number} has no {', '.join(missing)}")
        try:
            step = BUILDERS[kind](f"q{number}", item)
        except NotApplicable as reason:
            raise NotApplicable(f"item {number}: {reason}") from None
        step["objective"] = slug(item["objective"])
        step["objective_text"] = str(item["objective"]).strip()
        step["sources"] = item.get("sources") or []
        step["own_words"] = bool(item.get("own_words"))
        steps.append(step)
    return {"kind": "course", "method": "course_items", "steps": steps}


SAMPLES = [
    {"statement": "\\text{Check}", "items": [
        {"kind": "multiple_choice", "prompt": "Which law says orbits are ellipses?", "objective": "Kepler's laws",
         "options": ["First", "Second", "Third"], "correct": 0, "nudge": "Count them.", "method_hint": "The first one.",
         "own_words": True},
        {"kind": "numeric", "prompt": "Gravity at Earth's surface.", "objective": "Gravity", "answer": "9.81",
         "unit": "m/s^2", "tolerance": {"absolute": 0.05}, "nudge": "About ten.", "method_hint": "g.", "own_words": True}]},
    {"statement": "\\text{Check}", "items": [
        {"kind": "short_answer", "prompt": "Why does a satellite stay up?", "objective": "Orbits",
         "answer": "It falls around the Earth: its sideways speed carries it past the horizon as it falls.",
         "nudge": "It is falling.", "method_hint": "Sideways speed.", "own_words": True}]},
]
