"""Systems, Euler-Cauchy at every order, and what the new step types accept."""
import pytest
import sympy as sp

import systems
from grade import CORRECT, INCORRECT, RESTATE, grade
from mathparse import parse_vector
from methods import NotApplicable, all_methods
from verify import VerificationFailed, build_and_verify, verify_plan

METHODS = all_methods()
IMPROPER = "y_1'=-4y_1+y_2,\\quad y_2'=y_1-4y_2"
SADDLE = "y_1'=y_1+2y_2,\\quad y_2'=2y_1+y_2"
SPIRAL = "y_1'=-y_1+2y_2,\\quad y_2'=-2y_1-y_2"
CENTER = "y_1'=y_2,\\quad y_2'=-9y_1"
DEGENERATE = "y_1'=3y_1+y_2,\\quad y_2'=-y_1+y_2"
PROPER = "y_1'=-2y_1,\\quad y_2'=-2y_2"
TANKS = "y_1'=-\\frac{1}{25}y_1+\\frac{1}{25}y_2,\\quad y_2'=\\frac{1}{25}y_1-\\frac{1}{25}y_2"


def system(statement, **extra):
    return METHODS["linear_system"].build({"statement": statement, **extra}, {})


def verdict(plan, step_id, latex, rename=None):
    step = next(s for s in plan["steps"] if s["id"] == step_id)
    return grade(step["check"], latex, rename)


@pytest.mark.parametrize("statement,kind,stability", [
    (IMPROPER, systems.IMPROPER, systems.ATTRACTIVE),
    ("y_1'=4y_1+y_2,\\quad y_2'=y_1+4y_2", systems.IMPROPER, systems.UNSTABLE),
    (SADDLE, systems.SADDLE, systems.UNSTABLE),
    (SPIRAL, systems.SPIRAL, systems.ATTRACTIVE),
    ("y_1'=y_1+2y_2,\\quad y_2'=-2y_1+y_2", systems.SPIRAL, systems.UNSTABLE),
    (CENTER, systems.CENTER, systems.STABLE),
    (DEGENERATE, systems.DEGENERATE, systems.UNSTABLE),
    (PROPER, systems.PROPER, systems.ATTRACTIVE),
    (TANKS, systems.LINE, systems.STABLE),
])
def test_every_critical_point_type_is_classified_the_same_two_ways(statement, kind, stability):
    A = systems.parse_system(statement)
    assert systems.classify_by_criteria(A) == (kind, stability)
    assert systems.classify_by_eigenvalues(A) == (kind, stability)
    plan = build_and_verify(METHODS["linear_system"], {"statement": statement}, {})
    assert plan["verified"]["matrix_exponential"] == "agrees"
    assert "agrees" in plan["verified"]["numeric"]
    assert plan["verified"]["classification"] == "criteria and eigenvalues agree"
    assert verdict(plan, "type", f"\\text{{{kind}}}").status == CORRECT
    wrong = next(t for t in systems.TYPES if t != kind)
    assert verdict(plan, "type", f"\\text{{{wrong}}}").status == INCORRECT


def test_parse_system_refuses_what_it_cannot_do():
    for bad in ["y_1'=y_1", "y_1'=y_1+t,\\quad y_2'=y_2", "y_1'=y_1+1,\\quad y_2'=y_2", "y_1'=y_1,\\quad y_1'=y_2"]:
        with pytest.raises(systems.NotASystem):
            systems.parse_system(bad)


@pytest.mark.parametrize("latex", ["1,1", "(1,1)", "[1,1]", "x^{(1)}=(1,1)", "2,2", "-3,-3", r"\frac12,\frac12",
                                   r"\begin{pmatrix}1\\1\end{pmatrix}", r"\begin{pmatrix}1\\ 1\end{pmatrix}",
                                   r"\left[1,1\right]^T"])
def test_eigenvector_accepts_any_nonzero_multiple_in_any_notation(latex):
    plan = system(IMPROPER)  # lambda = -5 is first, with direction (1, -1); -3 has (1, 1)
    assert verdict(plan, "eigvec2", latex).status == CORRECT


def test_eigenvector_refusals_are_specific():
    plan = system(IMPROPER)
    assert verdict(plan, "eigvec2", "1,-1").status == INCORRECT
    zero = verdict(plan, "eigvec2", "0,0")
    assert zero.status == INCORRECT and "zero vector" in zero.message
    assert verdict(plan, "eigvec2", "1,1,1").status == RESTATE
    assert verdict(plan, "eigvec2", "a,a").status == RESTATE


def test_complex_eigenvector_and_real_general_solution():
    plan = system(SPIRAL)
    assert verdict(plan, "roots", r"-1\pm 2i").status == CORRECT
    assert verdict(plan, "eigvec1", "1,i").status == CORRECT
    assert verdict(plan, "eigvec1", "i,-1").status == CORRECT  # i times (1, i)
    assert verdict(plan, "eigvec1", "1,-i").status == INCORRECT
    good = r"y_1=e^{-t}(c_1\cos 2t+c_2\sin 2t),\ y_2=e^{-t}(-c_1\sin 2t+c_2\cos 2t)"
    assert verdict(plan, "general", good).status == CORRECT
    swapped = r"y_1=e^{-t}(A\sin 2t+B\cos 2t),\ y_2=e^{-t}(A\cos 2t-B\sin 2t)"
    assert verdict(plan, "general", swapped).status == CORRECT
    assert verdict(plan, "general", r"y_1=e^{-t}\cos 2t,\ y_2=-e^{-t}\sin 2t").status == INCORRECT


def test_system_solution_tests_the_system_and_counts_constants():
    plan = system(IMPROPER)
    good = "y_1=c_1e^{-3t}+c_2e^{-5t},\\ y_2=c_1e^{-3t}-c_2e^{-5t}"
    assert verdict(plan, "general", good).status == CORRECT
    assert verdict(plan, "general", "y_1=Ae^{-5t}+Be^{-3t},\\ y_2=-Ae^{-5t}+Be^{-3t}").status == CORRECT
    fails = verdict(plan, "general", "y_1=c_1e^{-3t}+c_2e^{-5t},\\ y_2=c_1e^{-3t}+c_2e^{-5t}")
    assert fails.status == INCORRECT and "y_2'" in fails.message or "y_1'" in fails.message
    too_few = verdict(plan, "general", "y_1=c_1e^{-3t},\\ y_2=c_1e^{-3t}")
    assert too_few.status == INCORRECT and "independent" in too_few.message
    # Two symbols, one essential constant.
    disguised = verdict(plan, "general", "y_1=(c_1+c_2)e^{-3t},\\ y_2=(c_1+c_2)e^{-3t}")
    assert disguised.status == INCORRECT and "independent" in disguised.message
    assert verdict(plan, "general", "c_1e^{-3t}").status == RESTATE
    assert verdict(plan, "general", "y_1=c_1e^{-3t}").status == RESTATE


def test_double_eigenvalue_needs_the_t_form():
    plan = system(DEGENERATE)
    assert verdict(plan, "roots", "2,2").status == CORRECT
    assert verdict(plan, "roots", "2").status == RESTATE
    assert verdict(plan, "eigvec1", "1,-1").status == CORRECT
    u = next(s for s in plan["steps"] if s["id"] == "u")
    assert grade(u["check"], u["answer"]).status == CORRECT
    assert verdict(plan, "u", "2,-1").status == CORRECT  # another solution of the same system
    assert verdict(plan, "u", "1,1").status == INCORRECT
    no_t = verdict(plan, "general", "y_1=(c_1+c_2)e^{2t},\\ y_2=-(c_1+c_2)e^{2t}")
    assert no_t.status == INCORRECT


def test_wronskian_allows_scaled_eigenvectors_but_not_wrong_functions():
    plan = system(IMPROPER)
    key = next(s for s in plan["steps"] if s["id"] == "wronskian")["answer"]
    assert verdict(plan, "wronskian", key).status == CORRECT
    assert verdict(plan, "wronskian", "W=4e^{-8t}").status == CORRECT
    assert verdict(plan, "wronskian", "e^{-2t}").status == INCORRECT


def test_criteria_step_uses_delta_by_name():
    plan = system(IMPROPER)
    assert verdict(plan, "criteria", r"p=-8,q=15,\Delta=4").status == CORRECT
    assert verdict(plan, "criteria", r"p=8,q=15,\Delta=4").status == INCORRECT
    assert verdict(plan, "criteria", "p=-8,q=15").status == RESTATE


def test_matrix_step_accepts_rows_and_pmatrix():
    plan = system(IMPROPER)
    assert verdict(plan, "matrix", "-4,1,1,-4").status == CORRECT
    assert verdict(plan, "matrix", r"\begin{pmatrix}-4&1\\1&-4\end{pmatrix}").status == CORRECT
    wrong = verdict(plan, "matrix", "-4,1,-1,-4")
    assert wrong.status == INCORRECT and "row 2, column 1" in wrong.message
    assert verdict(plan, "matrix", "-4,1").status == RESTATE


def test_choices_refuse_anything_not_offered():
    plan = system(SADDLE)
    assert verdict(plan, "type", r"\text{saddle POINT}").status == CORRECT
    assert verdict(plan, "type", r"\text{a whirlpool}").status == RESTATE
    assert verdict(plan, "stability", r"\text{Unstable}").status == CORRECT


def test_plots_come_from_the_matrix_and_show_the_right_motion():
    plan = system(SADDLE, ic=["y_1(0)=1", "y_2(0)=0"])
    figures = next(s for s in plan["steps"] if s["id"] == "discuss")["figures"]
    assert [f["kind"] for f in figures] == ["phase", "lines"]
    portrait, lines = figures
    assert sorted(portrait["eigen"]) == [[1.0, -1.0], [1.0, 1.0]]
    assert portrait["ic"] == [1.0, 0.0]
    # Saddle, unstable: a solution from (1, 0) leaves the window.
    end = portrait["ic_path"][-1]
    assert abs(end[0]) > 2 and abs(end[1]) > 2
    # Time plot values match the exact IVP key where both exist.
    t, y1 = sp.Symbol("t"), sp.sympify(plan["ivp"][0])
    point = lines["series"][0]["points"][40]
    assert abs(float(y1.subs(t, sp.Float(point[0]))) - point[1]) < 1e-2


def test_center_plot_closes_and_spiral_plot_decays():
    center = system(CENTER, ic=["y_1(0)=1", "y_2(0)=0"])
    lines = next(s for s in center["steps"] if s["id"] == "discuss")["figures"][1]
    values = [p[1] for p in lines["series"][0]["points"]]
    assert max(values) == pytest.approx(1, abs=0.01) and min(values) == pytest.approx(-1, abs=0.01)
    spiral = system(SPIRAL, ic=["y_1(0)=2", "y_2(0)=1"])
    lines = next(s for s in spiral["steps"] if s["id"] == "discuss")["figures"][1]
    assert abs(lines["series"][0]["points"][-1][1]) < 0.1


def test_rubric_states_the_long_term_limit_for_tanks():
    plan = system(TANKS, ic=["y_1(0)=0", "y_2(0)=100"])
    rubric = next(s for s in plan["steps"] if s["id"] == "discuss")["answer"]
    assert "y_1 \\to 50" in rubric and "y_2 \\to 50" in rubric


def test_verifier_catches_a_wrong_system_key_and_a_wrong_classification():
    plan = build_and_verify(METHODS["linear_system"], {"statement": IMPROPER}, {})
    tampered = {**plan, "general": [sp.srepr(sp.sympify(plan["general"][0]) + sp.Symbol("t")), plan["general"][1]]}
    with pytest.raises(VerificationFailed):
        verify_plan(tampered)
    with pytest.raises(VerificationFailed, match="criteria say"):
        verify_plan({**plan, "classification": [systems.SADDLE, systems.UNSTABLE]})


def test_ode_to_system_connects_back_to_the_ode():
    plan = build_and_verify(METHODS["ode_to_system"], {"statement": "y''+y'-2y=0", "ic": ["y(0)=1", "y'(0)=0"]}, {})
    assert verdict(plan, "convert", "0,1,2,-1").status == CORRECT
    assert verdict(plan, "convert", "0,1,-2,1").status == INCORRECT
    assert verdict(plan, "back", "c_1e^{t}+c_2e^{-2t}").status == CORRECT
    assert verdict(plan, "back", "c_1e^{t}").status == INCORRECT


@pytest.mark.parametrize("statement,roots,solution", [
    ("x^3y'''-3x^2y''+6xy'-6y=0", "1,2,3", "c_1x+c_2x^2+c_3x^3"),
    ("x^3y'''+3x^2y''+xy'=0", "0,0,0", r"c_1+c_2\ln x+c_3(\ln x)^2"),
    ("x^2y''-2y=0", "2,-1", r"c_1x^2+\frac{c_2}{x}"),
    ("x^2y''+xy'+4y=0", r"\pm 2i", r"c_1\cos(2\ln x)+c_2\sin(2\ln x)"),
])
def test_euler_cauchy_all_root_cases(statement, roots, solution):
    plan = build_and_verify(METHODS["euler_cauchy"], {"statement": statement}, {})
    assert verdict(plan, "roots", roots).status == CORRECT
    assert verdict(plan, "general", solution).status == CORRECT
    assert verdict(plan, "general", "c_1x^7").status == INCORRECT


def test_euler_cauchy_third_order_has_a_wronskian_and_uses_m_not_m_squared():
    plan = METHODS["euler_cauchy"].build({"statement": "x^3y'''-3x^2y''+6xy'-6y=0"}, {})
    assert verdict(plan, "indicial", "m(m-1)(m-2)-3m(m-1)+6m-6=0").status == CORRECT
    assert verdict(plan, "indicial", "m^3-3m^2+6m-6=0").status == INCORRECT
    assert verdict(plan, "wronskian", "2x^3").status == CORRECT
    assert verdict(plan, "wronskian", "W=x^3").status == INCORRECT


def test_euler_cauchy_ivp_gets_a_plot_and_a_discussion():
    plan = build_and_verify(METHODS["euler_cauchy"], {"statement": "x^2y''-2xy'+2y=0", "ic": ["y(1)=1", "y'(1)=0"]}, {})
    discuss = next(s for s in plan["steps"] if s["id"] == "discuss")
    assert discuss["figures"][0]["marker"] == [1.0, 1.0]
    assert all(p[0] > 0 for p in discuss["figures"][0]["series"][0]["points"])
    assert "0^+" in discuss["answer"] and "\\infty" in discuss["answer"]


def test_euler_cauchy_refuses_non_euler_cauchy():
    with pytest.raises(NotApplicable, match="isn't Euler-Cauchy"):
        METHODS["euler_cauchy"].build({"statement": "y''+xy'+y=0"}, {})
    with pytest.raises(NotApplicable, match="variation_of_parameters_higher"):
        METHODS["euler_cauchy"].build({"statement": "x^2y''-2y=x"}, {})


def test_variation_higher_uses_determinants_and_checks_y_p_by_substitution():
    plan = build_and_verify(METHODS["variation_of_parameters_higher"], {"statement": "x^3y'''-3x^2y''+6xy'-6y=x^4"}, {})
    assert verdict(plan, "standard", "x").status == CORRECT
    assert verdict(plan, "wronskian", "2x^3").status == CORRECT
    minors = next(s for s in plan["steps"] if s["id"] == "minors")
    assert grade(minors["check"], minors["answer"]).status == CORRECT
    assert grade(minors["check"], "W_1=x^4,W_2=-2x^3,W_3=x^2").status == CORRECT
    assert verdict(plan, "y_p_final", r"\frac{x^4}{6}").status == CORRECT
    # Adding a homogeneous solution still gives a valid particular solution.
    assert verdict(plan, "y_p_final", r"\frac{x^4}{6}+7x").status == CORRECT
    assert verdict(plan, "y_p_final", "x^4").status == INCORRECT


def test_vectors_parse_every_way_a_student_writes_them():
    for latex in ["1,2", "(1, 2)", "[1,2]^T", r"\begin{pmatrix}1\\2\end{pmatrix}", r"\begin{bmatrix}1\\ 2\end{bmatrix}", "x=(1,2)"]:
        assert parse_vector(latex) == [1, 2], latex


def test_oscillating_euler_cauchy_gets_a_log_axis_and_others_do_not():
    complex_roots = METHODS["euler_cauchy"].build({"statement": "x^2y''+xy'+4y=0", "ic": ["y(1)=1", "y'(1)=2"]}, {})
    real_roots = METHODS["euler_cauchy"].build({"statement": "x^2y''-4xy'+6y=0", "ic": ["y(1)=1", "y'(1)=0"]}, {})
    plot = lambda plan: next(s for s in plan["steps"] if s["id"] == "discuss")["figures"][0]
    assert plot(complex_roots)["log_x"] is True
    assert plot(real_roots)["log_x"] is False
    values = [p[1] for p in plot(complex_roots)["series"][0]["points"]]
    assert sum(a * b < 0 for a, b in zip(values, values[1:])) >= 3, "the plot should show it oscillating"
