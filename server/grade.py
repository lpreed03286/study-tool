"""Grading one step.

Each step type is checked by what makes the answer correct mathematically, not
by comparison to one stored form wherever a stronger test exists:

  general_solution    substitute into the ODE, then count essential constants
  particular_of       substitute into the ODE; no free constants allowed
  integrating_factor  mu'/mu must equal p(x), so any constant multiple passes
  antiderivative      differentiate the answer back to the integrand
  trial_form          same function span as the key, regardless of coefficient names
  linear_system       same row space as the key, in exact rational arithmetic
  implicit_solution   the level sets of F must follow the ODE's direction field
  eigenvector         Av must equal lambda v exactly, so any nonzero multiple passes
  generalized_vector  (A - lambda I)u must equal x exactly, so any solution passes
  system_solution     substitute into both equations, then count essential constants

String comparison happens only when an answer cannot be parsed, and the result
then says so. A timeout or parse failure is never reported as "wrong".
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import sympy as sp

from equiv import DIFFERENT, NUMERIC, PROVED, compare, evaluate, is_zero, numeric_rank, sample_points, time_limit
from mathparse import ParseFailure, Parsed, has_decimal, normalise_string, parse_answer, parse_list, parse_vector

CORRECT, INCORRECT, RESTATE, UNPARSED = "correct", "incorrect", "restate", "unparsed"


def dump(expr) -> str:
    return sp.srepr(sp.sympify(expr))


def load(text: str) -> sp.Expr:
    return sp.sympify(text)


@dataclass
class Result:
    status: str
    message: str = ""
    confidence: str = ""
    diagnosis: str = ""
    readback: str = ""
    rename: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class _Stop(Exception):
    def __init__(self, status: str, message: str, diagnosis: str = ""):
        self.status, self.message, self.diagnosis = status, message, diagnosis


def _wrong(message: str, diagnosis: str = "") -> _Stop:
    return _Stop(INCORRECT, message, diagnosis or message)


def _restate(message: str) -> _Stop:
    return _Stop(RESTATE, message, message)


def _correct(compared, readback: str = "", rename: dict | None = None, message: str = "") -> Result:
    if compared.verdict == NUMERIC:
        confidence = "numeric"
        note = "Correct. SymPy could not finish a symbolic proof in time; this passed at 12 exact points to 45 digits."
    else:
        confidence, note = "proved", "Correct."
    return Result(CORRECT, message or note, confidence, readback=readback, rename=rename or {})


def smooth(expr: sp.Expr) -> sp.Expr:
    """|u| as sqrt(u^2): SymPy can't differentiate Abs of a symbol it doesn't know is real."""
    return expr.replace(sp.Abs, lambda u: sp.sqrt(u**2))


def _apply_rename(expr: sp.Expr, rename: dict | None) -> sp.Expr:
    if not rename:
        return expr
    return expr.xreplace({sp.Symbol(a): sp.Symbol(b) for a, b in rename.items()})


def _value(parsed: Parsed, aliases: list[str]) -> sp.Expr:
    """The answer's value; 'name = value' is fine when name is one of aliases."""
    if not parsed.is_equation:
        return parsed.parts[0]
    left = parsed.parts[0]
    if not isinstance(left, sp.Symbol) or (aliases and str(left) not in aliases):
        wanted = f" (expected {aliases[0]})" if aliases else ""
        raise _wrong(f"The left side of your equation doesn't match this step{wanted}.")
    return parsed.parts[-1]


def describe_difference(student: sp.Expr, key: sp.Expr, domain: str = "real") -> str:
    """Facts about how an answer is off, for the 'Explain why' prompt."""
    facts = []
    try:
        with time_limit(2):
            difference = sp.simplify(student - key)
            facts.append(f"student minus correct = {difference}")
            if key != 0:
                ratio = sp.simplify(student / key)
                if not ratio.free_symbols:
                    facts.append(f"student / correct = {ratio} (off by a constant factor)")
            if not difference.free_symbols:
                facts.append("the two differ only by a constant")
    except Exception:  # A failed diagnosis must never turn into a failed grade.
        pass
    missing = key.free_symbols - student.free_symbols
    extra = student.free_symbols - key.free_symbols
    if missing:
        facts.append("variables in the correct answer but not the student's: " + ", ".join(map(str, missing)))
    if extra:
        facts.append("variables in the student's answer but not the correct one: " + ", ".join(map(str, extra)))
    return "; ".join(facts)


# --- Checks ------------------------------------------------------------------

def check_expr(spec, latex, rename):
    student = _apply_rename(_value(parse_answer(latex), spec.get("aliases", [])), rename)
    key = load(spec["key"])
    domain = spec.get("domain", "real")
    if spec.get("up_to_factor"):
        verdict = _proportional(student, key, domain)
        if verdict is not None:
            return _correct(verdict, sp.latex(student))
        raise _wrong("That isn't a constant multiple of the correct expression.",
                     describe_difference(student, key, domain))
    result = compare(student, key, domain)
    if result.equal:
        return _correct(result, sp.latex(student))
    raise _wrong("Not equivalent to the correct expression.", describe_difference(student, key, domain))


def _proportional(student_side: sp.Expr, key_side: sp.Expr, domain: str):
    """Whether two 'expression = 0' forms differ by a nonzero constant factor."""
    for point in sample_points(student_side.free_symbols | key_side.free_symbols, 6, domain):
        top, bottom = evaluate(student_side, point), evaluate(key_side, point)
        if top is None or bottom is None or abs(bottom) < sp.Rational(1, 10) ** 30:
            continue
        factor = sp.nsimplify(top / bottom, rational=True, tolerance=sp.Rational(1, 10) ** 35)
        if factor == 0:
            return None
        verdict = compare(student_side, factor * key_side, domain)
        return verdict if verdict.equal else None
    return None


def check_equation(spec, latex, rename):
    parsed = parse_answer(latex)
    key_lhs, key_rhs = load(spec["key_lhs"]), load(spec["key_rhs"])
    domain = spec.get("domain", "real")
    if parsed.is_equation:
        left, right = parsed.parts[0], parsed.parts[-1]
    else:
        left, right = parsed.parts[0], key_rhs
    left, right = _apply_rename(left, rename), _apply_rename(right, rename)
    student = left - right
    key = key_lhs - key_rhs
    variable = spec.get("any_variable")
    if variable and len(student.free_symbols) == 1:
        student = student.xreplace({next(iter(student.free_symbols)): sp.Symbol(variable)})
    monic = spec.get("monic")
    if monic:
        return _check_standard_form(left, right, student, key, sp.Symbol(monic), domain)
    verdict = _proportional(student, key, domain)
    if verdict is None:
        raise _wrong("That isn't an equivalent equation.", describe_difference(student, key, domain))
    return _correct(verdict, f"{sp.latex(left)} = {sp.latex(right)}")


def _check_standard_form(left, right, student, key, symbol, domain):
    """Dividing by the coefficient of y' shows whether it's the same ODE at all."""
    leading = sp.diff(student, symbol)
    if leading == 0:
        raise _wrong(f"That equation has lost ${symbol}$.", f"student equation has no {symbol}")
    verdict = compare(student / leading, key, domain)
    if not verdict.equal:
        raise _wrong("That isn't an equivalent equation.", describe_difference(student / leading, key, domain))
    if not compare(sp.diff(left, symbol), sp.Integer(1), domain).equal:
        raise _restate(f"That's the same ODE, but standard form needs the coefficient of ${symbol}$ to be 1, "
                       "so divide through by it.")
    if {s for s in right.free_symbols if str(s).startswith("y")}:
        raise _restate("That's the same ODE, but standard form has every $y$ term on the left.")
    return _correct(verdict, f"{sp.latex(left)} = {sp.latex(right)}")


def check_assignments(spec, latex, rename):
    key = {name: load(value) for name, value in spec["key"].items()}
    domain = spec.get("domain", "real")
    student: dict[str, sp.Expr] = {}
    for item in parse_list(latex):
        if len(item.parts) < 2 or not isinstance(item.parts[0], sp.Symbol):
            raise _restate("Write each one as name = value, for example " + _assignment_example(key) + ".")
        name = str(_apply_rename(item.parts[0], rename))
        student[name] = _apply_rename(item.parts[-1], rename)
    missing = [k for k in key if k not in student]
    extra = [k for k in student if k not in key]
    if extra:
        raise _wrong(f"This step doesn't use {', '.join(extra)}; it asks for {', '.join(key)}.")
    if missing:
        raise _restate(f"Please also give {', '.join(missing)}. Write 0 if a coefficient is zero.")
    verdicts, wrong = [], []
    for name, value in key.items():
        verdict = compare(student[name], value, domain)
        verdicts.append(verdict)
        if not verdict.equal:
            wrong.append(name)
    if wrong:
        facts = "; ".join(f"{n}: student {student[n]} vs correct {key[n]}" for n in wrong)
        raise _wrong(f"Not right: {', '.join(wrong)}.", facts)
    weakest = next((v for v in verdicts if v.verdict == NUMERIC), verdicts[0])
    return _correct(weakest, ", ".join(f"{n} = {sp.latex(student[n])}" for n in key))


def _assignment_example(key: dict) -> str:
    return ", ".join(f"{name} = ..." for name in key)


def check_roots(spec, latex, rename):
    key = [load(v) for v in spec["key"]]
    student = []
    for item in parse_list(latex, allow_i=True):
        value = item.parts[-1]
        if item.is_equation and not isinstance(item.parts[0], sp.Symbol):
            raise _wrong("Write the roots as values, like r = -1, r = -2.")
        student.append(value)
    remaining = list(key)
    unmatched = []
    for value in student:
        for candidate in remaining:
            if compare(value, candidate).equal:
                remaining.remove(candidate)
                break
        else:
            unmatched.append(value)
    if unmatched:
        facts = f"student roots {student}; correct roots {key}"
        raise _wrong("At least one of those isn't a root of the characteristic equation.", facts)
    if remaining:
        if set(key) == set(student) or all(any(compare(k, s).equal for s in student) for k in remaining):
            raise _restate("Those values are right. A repeated root counts twice, so list it twice (for example -1, -1).")
        raise _wrong(f"You're missing a root: there should be {len(key)} counting multiplicity.",
                     f"student roots {student}; correct roots {key}")
    return Result(CORRECT, "Correct.", "proved", readback=", ".join(sp.latex(v) for v in student))


def _substituted(residual: sp.Expr, y_expr: sp.Expr, x: sp.Symbol, order: int, name: str = "y") -> sp.Expr:
    y_expr = smooth(y_expr)
    swaps = {sp.Symbol(name): y_expr}
    derivative = y_expr
    for k in range(1, order + 1):
        derivative = sp.diff(derivative, x)
        swaps[sp.Symbol(name + "'" * k)] = derivative
    return residual.subs(swaps)


def _essential_constants(y_expr: sp.Expr, constants: list[sp.Symbol], x: sp.Symbol, domain: str) -> int:
    """How many constants really matter: c_1 e^x + c_2 e^x has only one."""
    if not constants:
        return 0
    gradient = [sp.diff(y_expr, c) for c in constants]
    rows = []
    for point in sample_points(y_expr.free_symbols | {x}, len(constants) + 4, domain):
        row = [evaluate(g, point) for g in gradient]
        if all(v is not None for v in row):
            rows.append(row)
    return numeric_rank(rows)


def check_general_solution(spec, latex, rename):
    residual = load(spec["ode"])
    x = sp.Symbol(spec.get("x", "x"))
    order = spec["order"]
    domain = spec.get("domain", "real")
    student = _value(parse_answer(latex), spec.get("aliases", ["y"]))
    if sp.Symbol("y") in student.free_symbols:
        raise _restate("Solve for y explicitly: the right side shouldn't contain y.")
    verdict = is_zero(_substituted(residual, student, x, order), domain)
    constants = sorted(student.free_symbols - {x}, key=str)
    if not verdict.equal:
        leftover = _substituted(residual, student, x, order)
        diagnosis = describe_difference(leftover, sp.Integer(0), domain)
        raise _wrong("That doesn't satisfy the ODE: substituting it in leaves something nonzero.",
                     f"after substitution the ODE residual is not zero; {diagnosis}")
    essential = _essential_constants(student, constants, x, domain)
    if essential < order:
        raise _wrong(f"It satisfies the ODE, but a general solution of an order-{order} ODE needs {order} independent "
                     f"arbitrary constant{'s' if order > 1 else ''}. Yours has {essential}.",
                     f"student has {essential} essential constants, needs {order}")
    if essential > order:
        raise _wrong("That has too many independent constants for this ODE.",
                     f"student has {essential} essential constants, needs {order}")
    return _correct(verdict, f"y = {sp.latex(student)}")


def check_particular_of(spec, latex, rename):
    residual = load(spec["ode"])
    x = sp.Symbol(spec.get("x", "x"))
    student = _apply_rename(_value(parse_answer(latex), spec.get("aliases", ["y_p"])), rename)
    extra = student.free_symbols - {x}
    if extra:
        raise _restate(f"Put in the coefficient values: {', '.join(map(str, extra))} should be gone by this step.")
    domain = spec.get("domain", "real")
    leftover = _substituted(residual, student, x, spec["order"])
    verdict = is_zero(leftover, domain)
    if not verdict.equal:
        raise _wrong("Substituting that into the ODE doesn't give the right side.",
                     f"ODE residual after substitution: {describe_difference(leftover, sp.Integer(0), domain)}")
    return _correct(verdict, sp.latex(student))


def _linear_basis(expr: sp.Expr, unknowns: list[sp.Symbol]) -> dict[sp.Symbol, sp.Expr]:
    basis = {}
    remainder = expr
    for unknown in unknowns:
        part = sp.diff(expr, unknown)
        if part.free_symbols & set(unknowns):
            raise _wrong("A trial form should be linear in its unknown coefficients.")
        basis[unknown] = part
        remainder = remainder - unknown * part
    if not is_zero(remainder).equal:
        raise _wrong("Every term in a trial form needs its own unknown coefficient.",
                     f"the part with no coefficient is {sp.simplify(remainder)}")
    return basis


def _span_rank(functions: list[sp.Expr], x: sp.Symbol, domain: str) -> int:
    rows = []
    for point in sample_points({x}, len(functions) + 5, domain):
        row = [evaluate(f, point) for f in functions]
        if all(v is not None for v in row):
            rows.append(row)
    return numeric_rank(rows)


def check_trial_form(spec, latex, rename):
    x = sp.Symbol(spec.get("x", "x"))
    domain = spec.get("domain", "real")
    key = load(spec["key"])
    key_names = [sp.Symbol(n) for n in spec["coefficients"]]
    student = _value(parse_answer(latex), spec.get("aliases", ["y_p"]))
    unknowns = sorted(student.free_symbols - {x}, key=str)
    if not unknowns:
        raise _wrong("A trial form needs unknown coefficients such as A and B.")
    student_basis = _linear_basis(student, unknowns)
    key_basis = _linear_basis(key, key_names)
    homogeneous = load(spec["homogeneous_ode"])
    resonant = [f for f in student_basis.values()
                if is_zero(_substituted(homogeneous, f, x, spec["order"]), domain).equal]
    student_functions = list(student_basis.values())
    key_functions = list(key_basis.values())
    rank_student = _span_rank(student_functions, x, domain)
    rank_key = _span_rank(key_functions, x, domain)
    rank_both = _span_rank(student_functions + key_functions, x, domain)
    if resonant:
        names = ", ".join(sp.latex(f) for f in resonant)
        raise _wrong(f"${names}$ already solves the homogeneous equation, so it can't produce the right side. "
                     "Multiply by x (the modification rule).",
                     f"student trial contains homogeneous solutions {names}; correct trial {key}")
    if rank_student != rank_key or rank_both != rank_key:
        if rank_both == rank_student and rank_student > rank_key:
            problem = "It contains terms this right side doesn't need."
        elif rank_both == rank_key:
            problem = "It's missing some terms the right side requires."
        else:
            problem = "It isn't the right family of functions."
        raise _wrong(f"Not the right trial form. {problem}", f"student trial {student}; correct trial {key}")
    mapping = {}
    available = list(key_names)
    for unknown in unknowns:
        for name in available:
            if compare(student_basis[unknown], key_basis[name], domain).equal:
                mapping[str(unknown)] = str(name)
                available.remove(name)
                break
    if len(mapping) != len(unknowns) or available:
        raise _restate("That form works mathematically, but please write it with one plain coefficient on each "
                       "function so the next steps line up, like " + spec["answer_example"] + ".")
    message = "Correct."
    moved = {a: b for a, b in mapping.items() if a != b}
    if moved:
        message += " From here on the worksheet accepts your coefficient names."
    return Result(CORRECT, message, "proved", readback=sp.latex(student), rename=mapping)


def _as_matrix(equations: list[sp.Expr], unknowns: list[sp.Symbol]) -> sp.Matrix:
    matrix, vector = sp.linear_eq_to_matrix(equations, unknowns)
    return matrix.row_join(vector)


def check_linear_system(spec, latex, rename):
    key_equations = [load(e) for e in spec["key"]]
    unknowns = [sp.Symbol(n) for n in spec["unknowns"]]
    student = []
    for item in parse_list(latex):
        if not item.is_equation:
            raise _restate("Write each match as an equation, like 12A = 3.")
        equation = _apply_rename(item.parts[0] - item.parts[-1], rename)
        extra = equation.free_symbols - set(unknowns)
        if extra:
            raise _wrong(f"These equations should involve only the coefficients, not {', '.join(map(str, extra))}: "
                         "matching removes the functions of x.",
                         f"student equation {equation} contains {extra}")
        student.append(sp.expand(equation))
    try:
        student_matrix = _as_matrix(student, unknowns)
    except sp.matrices.exceptions.NonlinearError:
        raise _wrong("The coefficient equations should be linear in the unknowns.")
    key_matrix = _as_matrix(key_equations, unknowns)
    rank_student, rank_key = student_matrix.rank(), key_matrix.rank()
    rank_both = student_matrix.col_join(key_matrix).rank()
    if rank_both != rank_key:
        raise _wrong("At least one of those equations doesn't follow from matching coefficients.",
                     f"student system {student}; correct system {key_equations}")
    if rank_student != rank_key:
        raise _wrong(f"Those equations are consistent, but you need {rank_key} independent equations "
                     f"and these give {rank_student}.",
                     f"student system {student}; correct system {key_equations}")
    return Result(CORRECT, "Correct.", "proved", readback=", ".join(f"{sp.latex(e)} = 0" for e in student))


def check_integrating_factor(spec, latex, rename):
    x = sp.Symbol(spec.get("x", "x"))
    p = load(spec["p"])
    domain = spec.get("domain", "real")
    mu = _value(parse_answer(latex), spec.get("aliases", ["mu", "F"]))
    extra = mu.free_symbols - {x}
    if extra:
        raise _restate(f"Leave out arbitrary constants here ({', '.join(map(str, extra))}): "
                       "any one integrating factor is enough.")
    verdict = is_zero(sp.diff(smooth(mu), x) - p * smooth(mu), domain)
    if verdict.equal and not is_zero(mu, domain).equal:
        return _correct(verdict, sp.latex(mu))
    integral = sp.integrate(p, x)
    fact = f"student mu = {mu}; mu'/mu should equal p(x) = {p}; integral of p = {integral}"
    if compare(mu, integral, domain).equal:
        raise _wrong("That's the integral of p(x), which is only the exponent. The integrating factor is e to that power.", fact)
    raise _wrong("That isn't an integrating factor: its derivative over itself must equal p(x).", fact)


def check_antiderivative(spec, latex, rename):
    variable = sp.Symbol(spec.get("var", "x"))
    integrand = load(spec["integrand"])
    domain = spec.get("domain", "real")
    allowed = {sp.Symbol(s) for s in spec.get("parameters", [])}
    student = smooth(_value(parse_answer(latex), spec.get("aliases", [])))
    constants = sorted(student.free_symbols - {variable} - allowed, key=str)
    stray = [c for c in constants if str(c) in ("x", "y", "t")]
    if stray:
        raise _wrong(f"This integral is with respect to ${variable}$, so ${stray[0]}$ shouldn't appear in it.",
                     f"student's antiderivative contains the variable {stray[0]}")
    if spec.get("require_constant") and not constants:
        if is_zero(sp.diff(student, variable) - integrand, domain).equal:
            raise _wrong("The antiderivative is right, but this is where the arbitrary constant comes in. Add +C.",
                         "correct antiderivative but missing the constant of integration")
    verdict = is_zero(sp.diff(student, variable) - integrand, domain)
    if not verdict.equal:
        derivative = sp.simplify(sp.diff(student, variable))
        raise _wrong("Differentiating your answer doesn't give back the integrand.",
                     f"derivative of student's answer = {derivative}; integrand = {integrand}")
    return _correct(verdict, sp.latex(student))


def check_implicit_solution(spec, latex, rename):
    x, y = sp.Symbol(spec.get("x", "x")), sp.Symbol("y")
    slope = load(spec["slope"])
    domain = spec.get("domain", "positive")
    parsed = parse_answer(latex)
    if not parsed.is_equation:
        raise _restate("Write the solution as an equation, like F(x, y) = C.")
    left, right = parsed.parts[0], parsed.parts[-1]
    constants = sorted((left.free_symbols | right.free_symbols) - {x, y}, key=str)
    if len(constants) != 1:
        raise _wrong("A first-order general solution has exactly one arbitrary constant.",
                     f"student's answer has constants {constants}")
    constant = constants[0]
    relation = smooth(left - right)
    if y not in relation.free_symbols:
        raise _wrong("The solution has to involve y.")
    try:
        with time_limit(5):
            solved = sp.solve(relation, constant)
    except Exception:
        solved = []
    if not solved:
        raise _Stop(UNPARSED, "I couldn't isolate the constant in that form. Try writing it as F(x, y) = C.")
    level = solved[0]
    verdict = is_zero(sp.diff(level, x) + sp.diff(level, y) * slope, domain)
    if not verdict.equal:
        raise _wrong("Differentiating that relation implicitly doesn't give back the ODE.",
                     f"F = {level}; dF/dx + dF/dy*f = {sp.simplify(sp.diff(level, x) + sp.diff(level, y) * slope)} should be 0")
    return _correct(verdict, f"{sp.latex(left)} = {sp.latex(right)}")


def _load_matrix(rows: list[list[str]]) -> sp.Matrix:
    return sp.Matrix([[load(entry) for entry in row] for row in rows])


def _student_vector(latex: str, allow_complex: bool) -> sp.Matrix:
    entries = parse_vector(latex, allow_i=allow_complex)
    if len(entries) != 2:
        raise _restate(f"Enter exactly two entries, separated by a comma. I read {len(entries)}.")
    free = {s for e in entries for s in e.free_symbols}
    if free:
        raise _restate(f"Pick actual numbers instead of {', '.join(map(str, sorted(free, key=str)))}: "
                       "any one specific vector is enough.")
    return sp.Matrix(entries)


def check_eigenvector(spec, latex, rename):
    A, lam = _load_matrix(spec["matrix"]), load(spec["lam"])
    vector = _student_vector(latex, allow_complex=True)
    if all(compare(entry, sp.Integer(0)).equal for entry in vector):
        raise _wrong("The zero vector never counts as an eigenvector.", "student gave the zero vector")
    left, right = A * vector, lam * vector
    verdicts = [compare(a, b) for a, b in zip(left, right)]
    if all(v.equal for v in verdicts):
        return _correct(next((v for v in verdicts if v.verdict == NUMERIC), verdicts[0]), sp.latex(list(vector)))
    raise _wrong("That isn't an eigenvector for this eigenvalue: $Ax$ doesn't equal $\\lambda x$.",
                 f"student x = {list(vector)}; Ax = {list(left)}; lambda x = {list(right)}")


def check_generalized_vector(spec, latex, rename):
    A, lam = _load_matrix(spec["matrix"]), load(spec["lam"])
    target = sp.Matrix([load(v) for v in spec["x"]])
    vector = _student_vector(latex, allow_complex=False)
    produced = (A - lam * sp.eye(2)) * vector
    verdicts = [compare(a, b) for a, b in zip(produced, target)]
    if all(v.equal for v in verdicts):
        return _correct(verdicts[0], sp.latex(list(vector)))
    raise _wrong("That doesn't solve $(A - \\lambda I)u = x$.",
                 f"student u = {list(vector)}; (A - lambda I)u = {list(produced)}; x = {list(target)}")


def check_matrix(spec, latex, rename):
    key = [load(v) for v in spec["key"]]
    size = spec["size"]
    entries = parse_vector(latex)
    if len(entries) != size * size:
        raise _restate(f"Enter all {size * size} entries row by row, separated by commas. I read {len(entries)}.")
    wrong = [f"row {k // size + 1}, column {k % size + 1}" for k, (a, b) in enumerate(zip(entries, key))
             if not compare(a, b).equal]
    if wrong:
        raise _wrong(f"Check {'; '.join(wrong)}.", f"student {entries}; correct {key}")
    return Result(CORRECT, "Correct.", "proved", readback=", ".join(sp.latex(e) for e in entries))


def check_system_solution(spec, latex, rename):
    A = _load_matrix(spec["matrix"])
    t = sp.Symbol(spec.get("t", "t"))
    found: dict[str, sp.Expr] = {}
    for item in parse_list(latex):
        if len(item.parts) < 2 or not isinstance(item.parts[0], sp.Symbol):
            raise _restate("Write both components as $y_1 = \\dots,\\ y_2 = \\dots$")
        found[str(item.parts[0])] = smooth(item.parts[-1])
    if set(found) != {"y_1", "y_2"}:
        raise _restate("Give both components: $y_1 = \\dots,\\ y_2 = \\dots$")
    y = sp.Matrix([found["y_1"], found["y_2"]])
    if sp.Symbol("i") in y.free_symbols:
        raise _restate("Write the real form, using $\\cos$ and $\\sin$ instead of $i$.")
    residuals = list(y.diff(t) - A * y)
    verdicts = [is_zero(r) for r in residuals]
    if not all(v.equal for v in verdicts):
        bad = next(k + 1 for k, v in enumerate(verdicts) if not v.equal)
        raise _wrong(f"That doesn't satisfy the system: the equation for $y_{bad}'$ fails.",
                     f"residual of y' - Ay = {[sp.simplify(r) for r in residuals]}")
    constants = sorted(y.free_symbols - {t}, key=str)
    rows = []
    for point in sample_points(y.free_symbols | {t}, len(constants) + 4, "real"):
        row = [[evaluate(sp.diff(component, c), point) for c in constants] for component in y]
        if all(v is not None for values in row for v in values):
            rows.extend(row)
    essential = numeric_rank(rows) if constants else 0
    if essential != 2:
        raise _wrong(f"It satisfies the system, but the general solution needs 2 independent constants. Yours has {essential}.",
                     f"student has {essential} essential constants, needs 2")
    return _correct(next((v for v in verdicts if v.verdict == NUMERIC), verdicts[0]),
                    f"y_1 = {sp.latex(y[0])},\\ y_2 = {sp.latex(y[1])}")


def _plain_choice(latex: str) -> str:
    match = re.fullmatch(r"\s*\\text\{(.*)\}\s*", latex)
    return (match.group(1) if match else latex).strip().lower()


def check_choice(spec, latex, rename):
    picked = _plain_choice(latex)
    options = [o.lower() for o in spec["options"]]
    if picked not in options:
        raise _restate("Pick one of the options.")
    if picked == spec["key"].lower():
        return Result(CORRECT, "Correct.", "proved", readback=latex)
    raise _wrong(f"Not {picked}.", f"student chose {picked}; {spec.get('facts', '')}")


def check_discussion(spec, latex, rename):
    raise _restate("A discussion is judged from your written answer.")


CHECKS = {
    "eigenvector": check_eigenvector,
    "generalized_vector": check_generalized_vector,
    "matrix": check_matrix,
    "system_solution": check_system_solution,
    "choice": check_choice,
    "discussion": check_discussion,
    "expr": check_expr,
    "equation": check_equation,
    "assignments": check_assignments,
    "roots": check_roots,
    "general_solution": check_general_solution,
    "particular_of": check_particular_of,
    "trial_form": check_trial_form,
    "linear_system": check_linear_system,
    "integrating_factor": check_integrating_factor,
    "antiderivative": check_antiderivative,
    "implicit_solution": check_implicit_solution,
}


def grade(spec: dict, latex: str, rename: dict | None = None, answer: str = "") -> Result:
    if not latex or not latex.strip():
        return Result(UNPARSED, "Enter an answer first.")
    if has_decimal(latex):
        return Result(RESTATE, "Keep it exact: use a fraction like \\frac{1}{2}, not a decimal.",
                      diagnosis="student used a decimal")
    kind = spec["kind"]
    try:
        return CHECKS[kind](spec, latex, rename)
    except _Stop as stop:
        return Result(stop.status, stop.message, diagnosis=stop.diagnosis, readback=_try_readback(latex))
    except ParseFailure as failure:
        if answer and normalise_string(latex) == normalise_string(answer):
            return Result(CORRECT, "Correct. It couldn't be parsed as math, so it was matched as text against the key.",
                          "string")
        return Result(UNPARSED, f"I couldn't read that as math: {failure}. Text comparison didn't match either, "
                      "so nothing is marked wrong yet. Please check the preview.", diagnosis=str(failure))


def _try_readback(latex: str) -> str:
    try:
        return parse_answer(latex).readback
    except ParseFailure:
        return ""
