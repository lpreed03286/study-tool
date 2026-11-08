"""Every built-in method on every sample, through the full verifier."""
import pytest
import sympy as sp

from grade import CORRECT, INCORRECT, RESTATE, grade
from methods import NotApplicable, all_methods
from verify import build_and_verify

METHODS = all_methods()
SAMPLES = [(m, s) for m in METHODS.values() for s in m.samples if m.id not in ("concept_questions", "course_items")]


@pytest.mark.parametrize("method,spec", SAMPLES, ids=[f"{m.id}:{s['statement']}" for m, s in SAMPLES])
def test_sample_passes_full_verification(method, spec):
    plan = build_and_verify(method, spec, {})
    verified = plan["verified"]
    assert verified["checkers"] == "passed"
    assert verified["exact"] == "passed"
    assert verified["residual"] in ("proved", "numeric")
    assert "agrees" in verified["numeric"], verified


def plan_for(method_id, statement, **extra):
    return METHODS[method_id].build({"statement": statement, **extra}, {})


def step(plan, step_id):
    return next(s for s in plan["steps"] if s["id"] == step_id)


def verdict(plan, step_id, latex, rename=None):
    return grade(step(plan, step_id)["check"], latex, rename)


def test_undetermined_coefficients_has_the_required_steps_in_order():
    plan = plan_for("undetermined_coefficients", "y''+3y'+2y=3e^{2x}")
    ids = [s["id"] for s in plan["steps"]]
    wanted = ["y_p", "y_p_d1", "y_p_d2", "substitute", "match", "solve"]
    assert [i for i in ids if i in wanted] == wanted


def test_uc_keeps_fractions_exact():
    plan = plan_for("undetermined_coefficients", "y''+3y'+2y=3e^{2x}")
    assert verdict(plan, "solve", r"A=\frac14").status == CORRECT
    assert verdict(plan, "solve", r"A=0.25").status == RESTATE


def test_uc_trial_form_accepts_other_coefficient_names_and_tracks_them():
    plan = plan_for("undetermined_coefficients", "y''+4y=x")
    first = verdict(plan, "y_p", "y_p=Cx+D")
    assert first.status == CORRECT
    assert first.rename == {"C": "B", "D": "A"} or first.rename == {"C": "A", "D": "B"}
    later = verdict(plan, "y_p_d1", "C", first.rename)
    assert later.status == CORRECT, later


@pytest.mark.parametrize("statement,wrong,why", [
    ("y''-y=e^{x}", "Ae^{x}", "already solves"),
    ("y''+4y=\\cos 2x", "A\\cos 2x+B\\sin 2x", "already solves"),
    ("y''+4y=\\cos x", "A\\cos x", "missing"),
    ("y''+4y=x^2", "Ax^2", "missing"),
    ("y''+3y'+2y=3e^{2x}", "Ae^{2x}+Bxe^{2x}", "doesn't need"),
])
def test_uc_trial_form_rejections_are_specific(statement, wrong, why):
    plan = plan_for("undetermined_coefficients", statement)
    result = verdict(plan, "y_p", wrong)
    assert result.status == INCORRECT
    assert why in result.message


def test_uc_modification_rule_is_in_the_key():
    plan = plan_for("undetermined_coefficients", "y''-y=e^{x}")
    assert verdict(plan, "y_p", "Axe^{x}").status == CORRECT
    plan = plan_for("undetermined_coefficients", "y''-2y'+y=e^{x}")
    assert verdict(plan, "y_p", "Ax^2e^{x}").status == CORRECT


def test_uc_refuses_right_sides_outside_its_class():
    for statement in ["y''+y=\\sec x", "y''+y=\\frac{1}{x}", "y''+y=\\ln x"]:
        with pytest.raises(NotApplicable, match="variation_of_parameters|polynomial"):
            plan_for("undetermined_coefficients", statement)


def test_coefficient_matching_accepts_scaled_equivalent_systems():
    plan = plan_for("undetermined_coefficients", "y''+4y=\\cos x")
    key = step(plan, "match")["answer"]
    assert verdict(plan, "match", key).status == CORRECT
    assert verdict(plan, "match", "3A=1,3B=0").status == CORRECT
    assert verdict(plan, "match", "6A=2,B=0").status == CORRECT
    assert verdict(plan, "match", "3A+3B=1,3B=0").status == CORRECT  # same row space
    assert verdict(plan, "match", "3A=2,3B=0").status == INCORRECT
    too_few = verdict(plan, "match", "3A=1")
    assert too_few.status == INCORRECT and "independent" in too_few.message


def test_general_solution_accepts_any_constant_names_and_equivalent_forms():
    plan = plan_for("constant_coefficient_homogeneous", "y''+3y'+2y=0")
    assert verdict(plan, "y_h", "c_1e^{-x}+c_2e^{-2x}").status == CORRECT
    assert verdict(plan, "y_h", "Ae^{-2x}+Be^{-x}").status == CORRECT
    assert verdict(plan, "y_h", "y=C_1e^{-x}+C_2e^{-2x}").status == CORRECT


def test_general_solution_catches_too_few_real_constants():
    plan = plan_for("constant_coefficient_homogeneous", "y''+3y'+2y=0")
    only_one = verdict(plan, "y_h", "c_1e^{-x}")
    assert only_one.status == INCORRECT and "independent" in only_one.message
    # Two symbols but one essential constant: counting symbols would pass this.
    disguised = verdict(plan, "y_h", "c_1e^{-x}+c_2e^{-x}")
    assert disguised.status == INCORRECT and "independent" in disguised.message


def test_general_solution_catches_nonsolutions():
    plan = plan_for("constant_coefficient_homogeneous", "y''+3y'+2y=0")
    assert verdict(plan, "y_h", "c_1e^{x}+c_2e^{2x}").status == INCORRECT


def test_repeated_and_complex_root_forms():
    plan = plan_for("constant_coefficient_homogeneous", "y''+2y'+y=0")
    assert verdict(plan, "roots", "-1,-1").status == CORRECT
    assert verdict(plan, "roots", "-1").status == RESTATE
    assert verdict(plan, "y_h", "(c_1+c_2x)e^{-x}").status == CORRECT
    assert verdict(plan, "y_h", "c_1e^{-x}+c_2e^{-x}").status == INCORRECT
    plan = plan_for("constant_coefficient_homogeneous", "y''+2y'+5y=0")
    assert verdict(plan, "roots", r"-1\pm 2i").status == CORRECT
    assert verdict(plan, "roots", "-1+2i,-1-2i").status == CORRECT
    assert verdict(plan, "roots", "1+2i,1-2i").status == INCORRECT
    assert verdict(plan, "y_h", r"e^{-x}(A\cos 2x+B\sin 2x)").status == CORRECT


def test_characteristic_equation_accepts_scaling_and_other_variables():
    plan = plan_for("constant_coefficient_homogeneous", "y''+3y'+2y=0")
    for good in ["r^2+3r+2=0", "2r^2+6r+4=0", r"\lambda^2+3\lambda+2=0", "(r+1)(r+2)=0", "r^2+3r+2"]:
        assert verdict(plan, "char_eq", good).status == CORRECT, good
    assert verdict(plan, "char_eq", "r^2+3r-2=0").status == INCORRECT


FIRST_ORDER = "y'+2y=e^{x}"


def test_first_order_integrating_factor_is_checked_by_definition():
    plan = plan_for("first_order_linear", FIRST_ORDER)
    for good in ["e^{2x}", r"\mu=e^{2x}", r"\mu(x)=e^{2x}", "3e^{2x}", r"\frac{1}{2}e^{2x}"]:
        assert verdict(plan, "mu", good).status == CORRECT, good
    forgot = verdict(plan, "mu", "2x")
    assert forgot.status == INCORRECT and "exponent" in forgot.message
    assert verdict(plan, "mu", "e^{-2x}").status == INCORRECT


def test_first_order_constant_of_integration_is_required_where_it_belongs():
    plan = plan_for("first_order_linear", FIRST_ORDER)
    missing = verdict(plan, "integrate", r"\frac{1}{3}e^{3x}")
    assert missing.status == INCORRECT and "+C" in missing.message
    assert verdict(plan, "integrate", r"\frac{1}{3}e^{3x}+C").status == CORRECT
    assert verdict(plan, "integrate", r"\frac{1}{3}e^{3x}+K").status == CORRECT
    assert verdict(plan, "integrate", r"\frac{1}{2}e^{3x}+C").status == INCORRECT


def test_first_order_standard_form_is_enforced_without_marking_it_wrong():
    plan = plan_for("first_order_linear", "xy'+y=x^2")
    assert verdict(plan, "standard", r"y'+\frac{y}{x}=x").status == CORRECT
    assert verdict(plan, "standard", "xy'+y=x^2").status == RESTATE
    assert verdict(plan, "standard", r"y'=x-\frac{y}{x}").status == RESTATE
    assert verdict(plan, "standard", "y'+y=x").status == INCORRECT


def test_first_order_with_log_uses_a_positive_domain():
    plan = plan_for("first_order_linear", "xy'+y=x^2")
    assert plan["domain"] == "positive"
    assert verdict(plan, "mu", r"e^{\ln|x|}").status == CORRECT
    assert verdict(plan, "mu", "x").status == CORRECT


def test_equivalent_forms_from_the_request_are_accepted():
    plan = plan_for("first_order_linear", "2y'+y=x")
    # y/2 and \frac{1}{2}y are the same answer.
    assert verdict(plan, "standard", r"y'+y/2=\frac{x}{2}").status == CORRECT
    assert verdict(plan, "standard", r"y'+\frac{1}{2}y=\frac{1}{2}x").status == CORRECT


def test_implicit_solutions_are_checked_against_the_direction_field():
    plan = plan_for("separable", "y'=\\frac{x^2}{y}")
    for good in [r"\frac{y^2}{2}=\frac{x^3}{3}+C", r"3y^2-2x^3=C", r"y^2=\frac{2x^3}{3}+K"]:
        assert verdict(plan, "general", good).status == CORRECT, good
    assert verdict(plan, "general", r"y^2=x^3+C").status == INCORRECT


def test_exact_equation_refuses_non_exact():
    with pytest.raises(NotApplicable, match="isn't exact"):
        plan_for("exact", "(y)dx+(2x)dy=0")


def test_unparseable_answers_are_never_marked_wrong():
    plan = plan_for("first_order_linear", FIRST_ORDER)
    result = verdict(plan, "mu", r"\frac{1}{")
    assert result.status == "unparsed"
    assert "couldn't read" in result.message


def test_verifier_refuses_a_corrupted_key():
    from verify import VerificationFailed, verify_plan
    plan = plan_for("first_order_linear", FIRST_ORDER)
    plan["general"] = sp.srepr(sp.Symbol("C") * sp.exp(-2 * sp.Symbol("x")) + sp.exp(sp.Symbol("x")))
    with pytest.raises(VerificationFailed):
        verify_plan(plan)


CONCEPT_QUESTION = {"question": "Why add $y_h$?", "answer": "For the constants.", "nudge": "Count them.", "method_hint": "Compare the order."}


def concept(*questions):
    return {"statement": "\\text{Concept check}", "questions": list(questions)}


@pytest.mark.parametrize("spec", METHODS["concept_questions"].samples)
def test_concept_samples_pass_and_the_report_admits_sympy_did_not_check_them(spec):
    verified = build_and_verify(METHODS["concept_questions"], spec, {})["verified"]
    assert "not checked by SymPy" in verified["concept"]
    assert "residual" not in verified


@pytest.mark.parametrize("spec,words", [
    (concept(), "from 1 to 6"),
    (concept(*[CONCEPT_QUESTION] * 7), "from 1 to 6"),
    (concept({**CONCEPT_QUESTION, "answer": " "}), "no answer"),
    (concept({**CONCEPT_QUESTION, "nudge": ""}), "no nudge"),
])
def test_concept_questions_refuse_anything_incomplete(spec, words):
    with pytest.raises(NotApplicable, match=words):
        METHODS["concept_questions"].build(spec, {})


def test_concept_reference_math_must_be_exact_but_prose_may_cite_a_section():
    build_and_verify(METHODS["concept_questions"], concept({**CONCEPT_QUESTION, "answer": "See Section 2.7 for $\\frac{1}{2}$."}), {})
    from verify import VerificationFailed
    with pytest.raises(VerificationFailed, match="decimal"):
        build_and_verify(METHODS["concept_questions"], concept({**CONCEPT_QUESTION, "answer": "It is $0.5x$."}), {})


@pytest.mark.parametrize("answer", [r"y\left(x\right)=c_1e^{-x}+c_2e^{-2x}", r"y\left(x\right)=C_1e^{-2x}+C_2e^{-x}"])
def test_general_solution_written_as_the_answer_box_writes_y_of_x(answer):
    assert verdict(plan_for("constant_coefficient_homogeneous", "y''+3y'+2y=0"), "y_h", answer).status == CORRECT


def test_y_of_x_on_the_left_still_gets_the_answer_checked():
    wrong = verdict(plan_for("constant_coefficient_homogeneous", "y''+3y'+2y=0"), "y_h", r"y\left(x\right)=c_1e^{x}+c_2e^{2x}")
    assert wrong.status == INCORRECT and "left side" not in wrong.message
