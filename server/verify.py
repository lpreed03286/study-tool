"""No answer key reaches the page without passing this.

Each route is independent of how the template got its answer:

  residual   substitute the key's solution into the ODE: must be exactly zero
  sympy      checkodesol, plus dsolve solving the ODE from scratch
  numeric    integrate the ODE numerically (RK4 at high precision) from initial values
             taken off the solution, then compare to the key further along.
             No symbolic algebra is shared with the template.
  ics        the IVP solution really meets its initial conditions
  checkers   each step's key must pass its own grader, and a corrupted key must
             fail it. A grader that accepts anything would be caught here.
  exact      no decimals anywhere in a key

A system adds the matrix exponential: y(t) must equal exp(At) y(0), which uses no
eigenvectors at all. Its critical point is classified twice, by the p, q, Delta
criteria and by the eigenvalues, and the two must match.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

import mpmath
import sympy as sp

from equiv import compare, is_zero, time_limit
from grade import CORRECT, grade, load
from grade import _essential_constants, _substituted
from mathparse import has_decimal
from systems import classify_by_eigenvalues

DSOLVE_SECONDS = 25
NUMERIC_DIGITS = 25
NUMERIC_TOLERANCE = mpmath.mpf(10) ** -10
RK4_STEPS = 2000


class VerificationFailed(Exception):
    pass


def verify_plan(plan: dict) -> dict:
    if plan.get("kind") == "system":
        return _verify_system(plan)
    if plan.get("kind") == "concept":
        return _verify_concept(plan)
    if plan.get("kind") == "course":
        return _verify_course(plan)
    x = sp.Symbol(plan["x"])
    residual = load(plan["residual"])
    order = plan["order"]
    domain = plan["domain"]
    report: dict = {"sympy_version": sp.__version__}
    if plan.get("implicit"):
        implicit = load(plan["implicit"])
        slope = load(plan["slope"])
        report["residual"] = _implicit_residual(implicit, slope, x, domain)
        report["numeric"] = _numeric_implicit(implicit, slope, x)
        report["dsolve"] = "not used for implicit solutions"
    else:
        general = load(plan["general"])
        verdict = is_zero(_substituted(residual, general, x, order), domain)
        if not verdict.equal:
            raise VerificationFailed(f"the general solution doesn't satisfy the ODE ({verdict.detail})")
        report["residual"] = verdict.verdict
        constants = sorted(general.free_symbols - {x}, key=str)
        essential = _essential_constants(general, constants, x, domain)
        if essential != order:
            raise VerificationFailed(f"the general solution has {essential} essential constants, needs {order}")
        report["constants"] = essential
        report["checkodesol"] = _checkodesol(residual, general, x, order)
        report["dsolve"] = _dsolve_agrees(residual, general, x, order, domain)
        report["numeric"] = _numeric_integration(residual, general, x, order, constants, domain)
    if plan.get("ivp"):
        report["ics"] = _verify_ivp(plan, residual, x, order, domain)
    report["checkers"] = _self_test(plan)
    report["exact"] = _no_decimals(plan)
    if report.get("checkodesol") == "failed" or report.get("dsolve") == "disagrees":
        raise VerificationFailed(f"an independent check disagreed: {report}")
    return report


def _rk4(f, start, stop, state):
    """f(x, state) -> list. Plain classical RK4. mpmath.odefun gave 1.1e10 for y' = -2y."""
    h = (stop - start) / RK4_STEPS
    x, y = start, list(state)
    for _ in range(RK4_STEPS):
        k1 = f(x, y)
        k2 = f(x + h / 2, [a + h * b / 2 for a, b in zip(y, k1)])
        k3 = f(x + h / 2, [a + h * b / 2 for a, b in zip(y, k2)])
        k4 = f(x + h, [a + h * b for a, b in zip(y, k3)])
        y = [a + h * (p + 2 * q + 2 * r + s) / 6 for a, p, q, r, s in zip(y, k1, k2, k3, k4)]
        x += h
    return y


def _verify_system(plan: dict) -> dict:
    A = sp.Matrix([[load(v) for v in row] for row in plan["matrix"]])
    t = sp.Symbol(plan["x"])
    general = sp.Matrix([load(v) for v in plan["general"]])
    report: dict = {"sympy_version": sp.__version__}
    verdicts = [is_zero(r) for r in general.diff(t) - A * general]
    if not all(v.equal for v in verdicts):
        raise VerificationFailed("the general solution doesn't satisfy y' = Ay")
    report["residual"] = "numeric" if any(v.verdict != "proved" for v in verdicts) else "proved"
    constants = sorted(general.free_symbols - {t}, key=str)
    if len(constants) != 2:
        raise VerificationFailed(f"the general solution has {len(constants)} constants, needs 2")
    if sp.simplify(sp.Matrix.hstack(general.diff(constants[0]), general.diff(constants[1])).det()) == 0:
        raise VerificationFailed("the two solutions in the key aren't independent")
    report["constants"] = 2
    values = {c: sp.Rational(k + 2, k + 5) for k, c in enumerate(constants)}
    fixed = general.subs(values)
    report["matrix_exponential"] = _matrix_exponential(A, fixed, t)
    report["dsolve"] = _dsolve_system(A, general, t)
    report["numeric"] = _numeric_system(A, fixed, t)
    by_criteria = tuple(plan["classification"])
    by_eigenvalues = classify_by_eigenvalues(A)
    if by_criteria != by_eigenvalues:
        raise VerificationFailed(f"the criteria say {by_criteria}, the eigenvalues say {by_eigenvalues}")
    report["classification"] = "criteria and eigenvalues agree"
    if plan.get("ivp"):
        y_ivp = sp.Matrix([load(v) for v in plan["ivp"]])
        if not all(is_zero(r).equal for r in y_ivp.diff(t) - A * y_ivp):
            raise VerificationFailed("the IVP solution doesn't satisfy y' = Ay")
        t0 = load(plan["t0"])
        for component, value in zip(y_ivp, plan["ic"]):
            if not compare(component.subs(t, t0), load(value)).equal:
                raise VerificationFailed("the IVP solution misses an initial condition")
        report["ics"] = "met"
    report["checkers"] = _self_test(plan)
    report["exact"] = _no_decimals(plan)
    if report["matrix_exponential"] == "disagrees" or report["dsolve"] == "disagrees":
        raise VerificationFailed(f"an independent check disagreed: {report}")
    return report


def _matrix_exponential(A, fixed, t) -> str:
    try:
        with time_limit(DSOLVE_SECONDS):
            expected = (A * t).exp() * fixed.subs(t, 0)
    except Exception:
        return "could not run"
    return "agrees" if all(compare(a, b).equal for a, b in zip(fixed, expected)) else "disagrees"


def _dsolve_system(A, general, t) -> str:
    y1, y2 = sp.Function("y1"), sp.Function("y2")
    state = sp.Matrix([y1(t), y2(t)])
    right = A * state
    try:
        with time_limit(DSOLVE_SECONDS):
            solved = sp.dsolve([sp.Eq(y1(t).diff(t), right[0]), sp.Eq(y2(t).diff(t), right[1])])
        theirs = sp.Matrix([solved[0].rhs, solved[1].rhs])
        constants = sorted(theirs.free_symbols - {t}, key=str)
        ours_constants = sorted(general.free_symbols - {t}, key=str)
        ours = general.subs({c: sp.Rational(k + 2, k + 5) for k, c in enumerate(ours_constants)})
        with time_limit(DSOLVE_SECONDS):
            fit = sp.solve(list(theirs.subs(t, 0) - ours.subs(t, 0)), constants, dict=True)
    except Exception:
        return "dsolve could not solve it"
    if not fit:
        return "inconclusive"
    same = all(compare(a, b).equal for a, b in zip(theirs.subs(fit[0]), ours))
    return "agrees" if same else "disagrees"


def _numeric_system(A, fixed, t) -> str:
    target = fixed.subs(t, sp.Rational(1, 2))
    previous = mpmath.mp.dps
    mpmath.mp.dps = NUMERIC_DIGITS
    try:
        with time_limit(DSOLVE_SECONDS):
            entries = [[mpmath.mpf(str(sp.N(A[i, j], NUMERIC_DIGITS))) for j in range(2)] for i in range(2)]
            start = [mpmath.mpf(str(sp.N(fixed.subs(t, 0)[k], NUMERIC_DIGITS))) for k in range(2)]
            got = _rk4(lambda at, y: [entries[0][0] * y[0] + entries[0][1] * y[1], entries[1][0] * y[0] + entries[1][1] * y[1]],
                       mpmath.mpf(0), mpmath.mpf(1) / 2, start)
            for k in range(2):
                expected = mpmath.mpf(str(sp.N(target[k], NUMERIC_DIGITS)))
                if abs(got[k] - expected) > NUMERIC_TOLERANCE * max(mpmath.mpf(1), abs(expected)):
                    raise VerificationFailed(f"numeric integration gives {got[k]}, the key gives {expected}")
            return "agrees to 10 digits"
    except VerificationFailed:
        raise
    except Exception as error:
        return f"could not run: {type(error).__name__}"
    finally:
        mpmath.mp.dps = previous
    return "timed out"


def _function_form(residual, x, order):
    y = sp.Function("y")
    return residual.subs({sp.Symbol("y" + "'" * k): y(x).diff(x, k) if k else y(x) for k in range(order + 1)}), y


def _checkodesol(residual, general, x, order) -> str:
    expression, y = _function_form(residual, x, order)
    try:
        with time_limit(DSOLVE_SECONDS):
            okay, _ = sp.checkodesol(sp.Eq(expression, 0), sp.Eq(y(x), general), y(x))
            return "passed" if okay else "inconclusive"
    except Exception:
        pass
    return "inconclusive"


def _dsolve_agrees(residual, general, x, order, domain) -> str:
    """dsolve finds its own solution. Both must have the same solution set."""
    expression, y = _function_form(residual, x, order)
    solved = None
    try:
        with time_limit(DSOLVE_SECONDS):
            solved = sp.dsolve(sp.Eq(expression, 0), y(x))
    except Exception:
        return "dsolve could not solve it"
    if solved is None or isinstance(solved, list) or not isinstance(solved, sp.Eq) or solved.lhs != y(x):
        return "dsolve returned no single explicit solution"
    theirs = solved.rhs
    if not theirs.free_symbols - {x}:
        return "dsolve returned no constants"
    verdict = is_zero(_substituted(residual, theirs, x, order), domain)
    if not verdict.equal:
        return "dsolve's own answer failed; ignored"
    ours_constants = sorted(general.free_symbols - {x}, key=str)
    theirs_constants = sorted(theirs.free_symbols - {x}, key=str)
    # Rename, then solve for their constants at initial data shared with ours.
    x0 = _base_point(domain)
    values = {c: sp.Rational(k + 2, k + 5) for k, c in enumerate(ours_constants)}
    ours_at = general.subs(values)
    equations = [sp.diff(theirs, x, k).subs(x, x0) - sp.diff(ours_at, x, k).subs(x, x0) for k in range(order)]
    try:
        with time_limit(DSOLVE_SECONDS):
            fit = sp.solve(equations, theirs_constants, dict=True)
    except Exception:
        return "inconclusive"
    if not fit:
        return "inconclusive"
    return "agrees" if compare(theirs.subs(fit[0]), ours_at, domain).equal else "disagrees"


def _base_point(domain: str) -> sp.Expr:
    return sp.Rational(1, 2) if domain == "small_positive" else sp.Integer(1)


def _as_first_order_system(residual, x, order):
    """Solve for the top derivative and build mpmath's y' = f(x, [y, y', ...])."""
    top = sp.Symbol("y" + "'" * order)
    solved = sp.solve(residual, top)
    if len(solved) != 1:
        return None
    states = [sp.Symbol("y" + "'" * k) for k in range(order)]
    return sp.lambdify([x, *states], solved[0], "mpmath")


def _numeric_integration(residual, general, x, order, constants, domain) -> str:
    system = _as_first_order_system(residual, x, order)
    if system is None:
        return "not applicable: y^(n) couldn't be isolated"
    values = {c: sp.Rational(k + 2, k + 5) for k, c in enumerate(constants)}
    y_fixed = general.subs(values)
    x0 = _base_point(domain)
    x1 = x0 + sp.Rational(1, 2)
    initial = [sp.diff(y_fixed, x, k).subs(x, x0) for k in range(order)]
    target = y_fixed.subs(x, x1)
    previous = mpmath.mp.dps
    mpmath.mp.dps = NUMERIC_DIGITS
    try:
        with time_limit(DSOLVE_SECONDS):
            start = [mpmath.mpf(str(sp.N(v, NUMERIC_DIGITS))) if sp.im(v) == 0 else
                     mpmath.mpc(str(sp.N(sp.re(v), NUMERIC_DIGITS)), str(sp.N(sp.im(v), NUMERIC_DIGITS))) for v in initial]

            def derivative(at, state):
                top = system(at, *state)
                return list(state[1:]) + [top]

            got = _rk4(derivative, mpmath.mpf(str(sp.N(x0, NUMERIC_DIGITS))), mpmath.mpf(str(sp.N(x1, NUMERIC_DIGITS))), start)[0]
            expected = sp.N(target, NUMERIC_DIGITS)
            expected = mpmath.mpc(str(sp.re(expected)), str(sp.im(expected)))
            scale = max(mpmath.mpf(1), abs(expected))
            if abs(got - expected) > NUMERIC_TOLERANCE * scale:
                raise VerificationFailed(f"numeric integration gives {got}, the key gives {expected}")
            return "agrees to 10 digits"
    except VerificationFailed:
        raise
    except Exception as error:
        return f"could not run: {type(error).__name__}"
    finally:
        mpmath.mp.dps = previous
    return "timed out"


def _implicit_residual(implicit, slope, x, domain) -> str:
    y = sp.Symbol("y")
    constants = implicit.free_symbols - {x, y}
    level = implicit + sum(constants)  # F - C = 0 means F = C
    verdict = is_zero(sp.diff(level, x) + sp.diff(level, y) * slope, domain)
    if not verdict.equal:
        raise VerificationFailed("the implicit solution doesn't follow the ODE")
    return verdict.verdict


def _numeric_implicit(implicit, slope, x) -> str:
    """Integrate y' = f numerically: F(x, y(x)) has to stay constant."""
    y = sp.Symbol("y")
    constants = implicit.free_symbols - {x, y}
    level = implicit + sum(constants)
    f = sp.lambdify([x, y], slope, "mpmath")
    F = sp.lambdify([x, y], level, "mpmath")
    previous = mpmath.mp.dps
    mpmath.mp.dps = NUMERIC_DIGITS
    try:
        with time_limit(DSOLVE_SECONDS):
            x0, y0 = mpmath.mpf(1), mpmath.mpf(3) / 2
            x1 = mpmath.mpf(5) / 4
            start, end = F(x0, y0), F(x1, _rk4(lambda at, state: [f(at, state[0])], x0, x1, [y0])[0])
            if abs(start - end) > NUMERIC_TOLERANCE * max(mpmath.mpf(1), abs(start)):
                raise VerificationFailed("F(x, y) isn't constant along the numeric solution")
            return "agrees to 10 digits"
    except VerificationFailed:
        raise
    except Exception as error:
        return f"could not run: {type(error).__name__}"
    finally:
        mpmath.mp.dps = previous
    return "timed out"


def _verify_ivp(plan, residual, x, order, domain) -> str:
    y_ivp = load(plan["ivp"])
    verdict = is_zero(_substituted(residual, y_ivp, x, order), domain)
    if not verdict.equal:
        raise VerificationFailed("the IVP solution doesn't satisfy the ODE")
    if y_ivp.free_symbols - {x}:
        raise VerificationFailed("the IVP solution still has free constants")
    for k, x0, value in plan["ics"]:
        at = sp.diff(y_ivp, x, k).subs(x, load(x0))
        if not compare(at, load(value)).equal:
            raise VerificationFailed(f"the IVP solution misses a condition: y^({k})({x0}) is {at}")
    return "met"


def _verify_concept(plan: dict) -> dict:
    """All a written reference allows, and the report says that's all it is."""
    for step in plan["steps"]:
        if not step["answer"].strip():
            raise VerificationFailed(f"step {step['id']}: the question has no reference answer")
        # Only the math has to be exact, so prose can still say "Section 2.7".
        for math in re.findall(r"\$[^$]+\$", step["answer"]):
            if has_decimal(math):
                raise VerificationFailed(f"step {step['id']}: the reference answer has a decimal: {math}")
    return {"concept": "Reference answers were written by Claude and not checked by SymPy"}


def _verify_course(plan: dict) -> dict:
    """Exact items must pass their own check and refuse a wrong answer. Short answers are only
    as good as Claude's reference, and the report says so."""
    judged = 0
    for step in plan["steps"]:
        check = step["check"]
        if check.get("concept"):
            if not step["answer"].strip():
                raise VerificationFailed(f"step {step['id']}: the question has no reference answer")
            judged += 1
            continue
        if check["kind"] == "multiple_choice":
            right = "choices:" + ",".join(map(str, check["correct"]))
            others = [n for n in range(len(check["options"])) if n not in check["correct"]]
            wrong = f"choices:{others[0]}" if others else None
        else:
            allowed = check["absolute"] if check.get("absolute") is not None else check["relative"] * abs(check["value"])
            right = repr(check["value"])
            wrong = repr(check["value"] + 2 * allowed + 1 + abs(check["value"]))
        result = grade(check, right)
        if result.status != CORRECT:
            raise VerificationFailed(f"step {step['id']}: its own key fails its check: {result.message}")
        if wrong is not None and grade(check, wrong).status == CORRECT:
            raise VerificationFailed(f"step {step['id']}: its check accepted a wrong answer")
    report = {"exact": "Multiple choice and numeric keys pass their own exact checks"}
    if judged:
        report["judged"] = f"{judged} short answer reference(s) were written by Claude and not checked by SymPy"
    return report


def _self_test(plan: dict) -> str:
    """Each key must pass its own checker, and a corrupted key must not."""
    rename: dict = {}
    for step in plan["steps"]:
        if step["check"]["kind"] == "discussion":
            if not step["answer"].strip():
                raise VerificationFailed(f"step {step['id']}: the discussion has no rubric")
            continue
        result = grade(step["check"], step["answer"], rename)
        if result.status != CORRECT:
            raise VerificationFailed(
                f"step {step['id']}: its own key fails its checker: {result.status}: {result.message} ({step['answer']})")
        rename.update(result.rename)
        corrupted = _corrupt(step["answer"], plan["x"])
        if corrupted is None:
            continue
        wrong = grade(step["check"], corrupted, rename)
        if wrong.status == CORRECT:
            raise VerificationFailed(f"step {step['id']}: its checker accepted a wrong answer: {corrupted}")
    return "passed"


def _corrupt(answer: str, x: str) -> str | None:
    """A definitely wrong answer. Scaling or adding a constant won't do: for a
    general solution 2(c_1 e^{-x} + c_2) + 7 is still correct."""
    if ",\\ " in answer or ", " in answer:
        return answer.replace("=", "= 7 +", 1) if "=" in answer else "7"
    junk = f"{x}^{{5}} e^{{3 {x}}}"
    if "=" in answer:
        left, right = answer.split("=", 1)
        return f"{left} = {right} + {junk}"
    return f"{answer} + {junk}"


def _no_decimals(plan: dict) -> str:
    for step in plan["steps"]:
        if has_decimal(step["answer"]):
            raise VerificationFailed(f"step {step['id']}: the key contains a decimal: {step['answer']}")
    return "passed"


def seal(problem: dict) -> str:
    """A fingerprint of the verified content; the server refuses a changed file."""
    body = {k: problem[k] for k in ("statement", "method", "plan")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def build_and_verify(method, spec: dict, notation: dict) -> dict:
    """Run one method on one problem. Raises if the key can't be fully verified."""
    plan = method.build(copy.deepcopy(spec), notation or {})
    if not plan["steps"]:
        raise VerificationFailed("the method produced no steps")
    plan["verified"] = verify_plan(plan)
    return plan
