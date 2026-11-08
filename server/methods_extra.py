"""Euler-Cauchy of any order, variation of parameters by Wronskian determinants, and
linear systems with the phase plane. Added for Kreyszig Chapters 3 and 4."""
from __future__ import annotations

import sympy as sp

import figures
import systems
from course_items import SAMPLES as COURSE_SAMPLES, build_course_items
from grade import dump
from methods import (Method, NotApplicable, ODE, combine, constant_names, dy, expr_check, general_step,
                     homogeneous_basis, ivp_steps, make_step, plan, tex)
from systems import ATTRACTIVE, CENTER, DEGENERATE, IMPROPER, LINE, PROPER, SADDLE, SPIRAL, STABILITIES, STABLE, TYPES, UNSTABLE


def _floats(A: sp.Matrix) -> list[list[float]]:
    return [[float(A[i, j]) for j in range(2)] for i in range(2)]


def text_step(step_id, step_type, label, prompt, rubric, figure_list, nudge, method) -> dict:
    """A written discussion. Claude judges it against a rubric of SymPy-derived facts."""
    step = make_step(step_id, step_type, label, prompt, rubric, {"kind": "discussion"}, nudge, method)
    step["answer_format"] = "text"
    step["figures"] = figure_list
    return step


def choice_step(step_id, step_type, label, prompt, key, options, nudge, method, note="", facts="") -> dict:
    wrapped = [f"\\text{{{o}}}" for o in options]
    return make_step(step_id, step_type, label, prompt, f"\\text{{{key}}}",
                     {"kind": "choice", "key": key, "options": options, "facts": facts}, nudge, method, note) | {
                         "options": wrapped}


# --- Concept questions -------------------------------------------------------

CONCEPT_FIELDS = ("question", "answer", "nudge", "method_hint")
MAX_CONCEPT_QUESTIONS = 6


def build_concept_questions(spec: dict, notation: dict) -> dict:
    """Short written questions. SymPy has nothing to check here, so the plan says so
    and Claude judges each answer, deciding by the mathematics over the reference."""
    questions = spec.get("questions") or []
    if not 1 <= len(questions) <= MAX_CONCEPT_QUESTIONS:
        raise NotApplicable(f"it takes from 1 to {MAX_CONCEPT_QUESTIONS} entries in `questions`")
    steps = []
    for number, question in enumerate(questions, 1):
        missing = [name for name in CONCEPT_FIELDS if not str(question.get(name) or "").strip()]
        if missing:
            raise NotApplicable(f"question {number} has no {', '.join(missing)}")
        step = text_step(f"q{number}", "concept_question", question["question"], "Answer in a sentence or two.",
                         question["answer"], [], question["nudge"], question["method_hint"])
        step["check"]["concept"] = True
        step["sources"] = question.get("sources") or []
        step["own_words"] = bool(question.get("own_words"))
        steps.append(step)
    return {"kind": "concept", "method": "concept_questions", "steps": steps}


# --- Euler-Cauchy ------------------------------------------------------------

def euler_cauchy_parts(ode: ODE) -> tuple[list[sp.Expr], sp.Expr]:
    """a_k from a_k x^k y^(k), and the right side."""
    coefficients, g = ode.linear_form()
    a = [sp.simplify(c / ode.x**k) for k, c in enumerate(coefficients)]
    if any(v.free_symbols for v in a) or a[-1] == 0:
        raise NotApplicable("the coefficients aren't of the form a_k x^k, so this isn't Euler-Cauchy")
    return a, g


def auxiliary(a: list[sp.Expr], m: sp.Symbol) -> sp.Expr:
    return sp.expand(sum(c * sp.expand_func(sp.ff(m, k)) for k, c in enumerate(a)))


def euler_cauchy_basis(roots: dict, x: sp.Symbol) -> list[sp.Expr]:
    basis, used = [], set()
    log = sp.log(x)
    for root, multiplicity in sorted(roots.items(), key=lambda kv: sp.default_sort_key(kv[0])):
        if root in used:
            continue
        alpha, beta = sp.re(root), sp.im(root)
        for k in range(multiplicity):
            power = log**k
            if beta == 0:
                basis.append(x**alpha * power)
            else:
                basis.append(x**alpha * power * sp.cos(abs(beta) * log))
                basis.append(x**alpha * power * sp.sin(abs(beta) * log))
        used.update({root, sp.conjugate(root)})
    return basis


def aux_steps(ode: ODE, a: list[sp.Expr], notation: dict):
    """Auxiliary equation, its roots, and y_h: the start of every Euler-Cauchy problem."""
    x = ode.x
    word = notation.get("aux_name", "auxiliary equation")
    m = sp.Symbol("m")
    poly = auxiliary(a, m)
    roots = sp.roots(sp.Poly(poly, m))
    if sum(roots.values()) != ode.order:
        raise NotApplicable("the auxiliary equation has no closed-form roots")
    root_list = [r for r, n in sorted(roots.items(), key=lambda kv: sp.default_sort_key(kv[0])) for _ in range(n)]
    basis = euler_cauchy_basis(roots, x)
    derivative_hint = ", ".join(f"y{chr(39) * k} = {tex(sp.expand_func(sp.ff(m, k)))}\\,{x}^{{m-{k}}}" for k in range(1, min(ode.order, 3) + 1))
    steps = [
        make_step(
            "indicial", "indicial_equation", word.capitalize(),
            f"Substitute $y = {x}^m$ and cancel ${x}^m$. Write the {word} in $m$.",
            f"{tex(poly)} = 0",
            {"kind": "equation", "key_lhs": dump(poly), "key_rhs": dump(0), "any_variable": "m"},
            f"What does each $y^{{(k)}}$ become when $y = {x}^m$?",
            f"${derivative_hint}$. Every power of ${x}$ then cancels, and $m(m-1)$ doesn't reduce to $m^2$.",
        ),
        make_step(
            "roots", "characteristic_roots", "Roots",
            "Find the roots. List a repeated root as many times as it repeats.",
            ", ".join(tex(v) for v in root_list),
            {"kind": "roots", "key": [dump(v) for v in root_list]},
            "Expand the falling factors first, then factor. Try small whole numbers for a cubic.",
            "Use the rational root test or the quadratic formula as needed.",
        ),
    ]
    return steps, roots, basis


def basis_step_general(ode: ODE, basis: list[sp.Expr], notation: dict):
    names = constant_names(len(basis), notation)
    return combine(basis, names), names


def basis_hint(x: sp.Symbol) -> tuple[str, str]:
    return ("The form depends on whether the roots are real and distinct, repeated, or complex.",
            f"Distinct real $m$: ${x}^m$. Repeated: multiply by $\\ln {x}$ each time. "
            f"Complex $\\alpha \\pm \\beta i$: ${x}^{{\\alpha}}\\cos(\\beta\\ln {x})$ and ${x}^{{\\alpha}}\\sin(\\beta\\ln {x})$.")


def euler_cauchy_rubric(ode: ODE, roots: dict, y_ivp: sp.Expr) -> str:
    x = ode.x
    facts = []
    if any(sp.im(r) != 0 for r in roots):
        root = next(r for r in roots if sp.im(r) != 0)
        alpha, beta = sp.re(root), abs(sp.im(root))
        facts.append(f"The roots are complex, so the solution oscillates in $\\ln {x}$, not in ${x}$. "
                     f"The oscillations stretch out as ${x}$ grows: one full cycle multiplies ${x}$ by "
                     f"$e^{{2\\pi/{tex(beta)}}}$.")
        if alpha > 0:
            facts.append(f"The amplitude grows like ${x}^{{{tex(alpha)}}}$.")
        elif alpha < 0:
            facts.append(f"The amplitude decays like ${x}^{{{tex(alpha)}}}$ as ${x} \\to \\infty$ and blows up as ${x} \\to 0^+$.")
        else:
            facts.append("The amplitude stays constant because the real part is 0.")
    else:
        for side, label in ((sp.oo, f"{x} \\to \\infty"), (0, f"{x} \\to 0^+")):
            try:
                value = sp.limit(y_ivp, x, side, "+" if side == 0 else "-")
            except (NotImplementedError, ValueError):
                continue
            facts.append(f"As ${label}$, $y \\to {tex(value)}$.")
        facts.append("The behavior follows the powers of $x$: the largest root dominates for large $x$ "
                     "and the smallest near $x = 0$.")
    facts.append(f"${x} = 0$ is a singular point where the leading coefficient vanishes, so the solution is taken on ${x} > 0$.")
    return " ".join(facts)


def build_euler_cauchy(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    x = ode.x
    ode.domain = "positive"
    if ode.order < 2:
        raise NotApplicable("this method is for second order and up")
    a, g = euler_cauchy_parts(ode)
    if g != 0:
        raise NotApplicable("the ODE isn't homogeneous; use variation_of_parameters_higher")
    steps, roots, basis = aux_steps(ode, a, notation)
    y_general, _ = basis_step_general(ode, basis, notation)
    nudge, method = basis_hint(x)
    if ode.order >= 3:
        wronskian = sp.simplify(sp.wronskian(basis, x))
        pair = ",\\ ".join(f"y_{k + 1} = {tex(f)}" for k, f in enumerate(basis))
        steps.append(make_step(
            "wronskian", "wronskian", "Basis and Wronskian",
            f"Check that ${pair}$ are a basis: compute the Wronskian $W$. It is nonzero on ${x} > 0$ when they are.",
            f"W = {tex(wronskian)}",
            expr_check(wronskian, aliases=["W"], domain="positive"),
            "Build the determinant with the functions in the first row and their derivatives below.",
            "Row $k$ holds the $(k-1)$th derivatives. A basis needs $W \\neq 0$ on the interval.",
        ))
    steps.append(general_step(ode, y_general, f"one arbitrary constant on each basis function"))
    steps[-1]["hints"] = [nudge, method]
    extra, y_ivp = ivp_steps(ode, y_general)
    steps += extra
    if y_ivp is not None:
        function = sp.lambdify(x, y_ivp, "cmath")
        ic_x, ic_y = float(ode.ics[0][1]), float(ode.ics[0][2]) if ode.ics[0][0] == 0 else None
        oscillates = any(sp.im(r) != 0 for r in roots)
        span = 50 if oscillates else 8
        caption = (f"The {x}-axis is logarithmic. Evenly spaced cycles here are cycles that stretch out in {x}."
                   if oscillates else f"Shown for {ic_x / span:g} < {x} < {ic_x * span:g}. The solution is only taken on {x} > 0.")
        figure = figures.curve_plot(function, ic_x / span, ic_x * span, "y", str(x),
                                    marker=(ic_x, ic_y) if ic_y is not None else None,
                                    title="Solution of the initial value problem", caption=caption, log_x=oscillates,
                                    alt=f"The solution y({x}) of the initial value problem on x > 0.")
        steps.append(text_step(
            "discuss", "solution_discussion", "Discuss the plot",
            f"Using the plot and your solution, describe how $y$ behaves as ${x} \\to 0^+$ and as ${x} \\to \\infty$, "
            f"and connect that to the roots. Does it oscillate? Why is the solution only taken on ${x} > 0$?",
            euler_cauchy_rubric(ode, roots, y_ivp), [figure],
            "Look at each end of the plot, then at your solution's powers of $x$.",
            "Each real root contributes a power of $x$. Complex roots give $\\cos$ and $\\sin$ of $\\ln x$.",
        ))
    return plan("euler_cauchy", ode, steps, y_general, y_ivp)


# --- Variation of parameters with Wronskian determinants ---------------------

def build_variation_higher(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    x = ode.x
    coefficients, g = ode.linear_form()
    if g == 0:
        raise NotApplicable("the ODE is homogeneous")
    if ode.order not in (2, 3):
        raise NotApplicable("this template handles second and third order")
    constant = not any(c.free_symbols for c in coefficients)
    if constant:
        from methods import char_steps
        ode.domain = "small_positive"
        r_name = notation.get("char", "r").replace("\\", "")
        steps, roots, _ = char_steps(ode, coefficients, r_name, notation)
        basis = homogeneous_basis(roots, x)
        steps = steps[:2]
    else:
        ode.domain = "positive"
        a, _ = euler_cauchy_parts(ode)
        steps, roots, basis = aux_steps(ode, a, notation)
    leading = coefficients[-1]
    r = sp.simplify(g / leading)
    names = constant_names(len(basis), notation)
    y_h = combine(basis, names)
    h_residual = ode.homogeneous_residual()
    nudge, method = basis_hint(x) if not constant else (
        "The form depends on the roots.", f"Distinct: $e^{{\\lambda {x}}}$. Repeated: multiply by ${x}$. Complex: $e^{{\\alpha {x}}}\\cos\\beta {x}$ and $e^{{\\alpha {x}}}\\sin\\beta {x}$.")
    steps.append(make_step(
        "y_h", "homogeneous_solution", "Homogeneous solution $y_h$",
        "Write $y_h$, the general solution of the homogeneous ODE.",
        f"y_h = {tex(y_h)}",
        {"kind": "general_solution", "ode": dump(h_residual), "order": ode.order, "x": str(x),
         "aliases": ["y_h", "y"], "domain": ode.domain},
        nudge, method,
    ))
    n = len(basis)
    wronskian = sp.simplify(sp.wronskian(basis, x))
    rows = [[sp.diff(f, x, k) for f in basis] for k in range(n)]
    minors = []
    for j in range(n):
        matrix = sp.Matrix(rows)
        matrix[:, j] = sp.Matrix([0] * (n - 1) + [1])
        minors.append(sp.simplify(matrix.det()))
    y_p = sum(f * sp.integrate(sp.simplify(minor / wronskian * r), x) for f, minor in zip(basis, minors))
    if y_p.has(sp.Integral):
        raise NotApplicable("an integral for y_p has no closed form")
    y_p = sp.simplify(y_p)
    pair = ",\\ ".join(f"y_{k + 1} = {tex(f)}" for k, f in enumerate(basis))
    minor_names = [f"W_{j + 1}" for j in range(n)]
    steps += [
        make_step(
            "standard", "standard_form", "Standard form: $r$",
            f"Divide by the leading coefficient to get $y^{{({n})}} + \\dots = r({x})$. What is $r({x})$?",
            f"r = {tex(r)}",
            expr_check(r, aliases=["r"], domain=ode.domain),
            "The leading coefficient has to be 1 before the formula applies.",
            "Divide the right side by whatever multiplies the highest derivative.",
        ),
        make_step(
            "wronskian", "wronskian", "Wronskian $W$",
            f"With ${pair}$, compute the Wronskian $W$.",
            f"W = {tex(wronskian)}",
            expr_check(wronskian, aliases=["W"], domain=ode.domain),
            "Put the functions in the first row and their derivatives below.",
            "Row $k$ holds the $(k-1)$th derivatives of the basis.",
        ),
        make_step(
            "minors", "wronskian_minors", "Determinants $W_j$",
            f"Find ${', '.join(minor_names)}$. Each $W_j$ is $W$ with column $j$ replaced by $(0, \\dots, 0, 1)^T$.",
            ",\\ ".join(f"{name} = {tex(minor)}" for name, minor in zip(minor_names, minors)),
            {"kind": "assignments", "key": {name: dump(minor) for name, minor in zip(minor_names, minors)},
             "domain": ode.domain},
            "Replace one column at a time and expand along it.",
            "Expanding along the replaced column leaves a single smaller determinant with a sign.",
        ),
        make_step(
            "y_p_final", "particular_solution", "Particular solution",
            f"Find $y_p = \\sum_j y_j \\int \\frac{{W_j}}{{W}}\\,r\\,d{x}$.",
            f"y_p = {tex(y_p)}",
            {"kind": "particular_of", "ode": dump(ode.residual), "order": ode.order, "x": str(x),
             "aliases": ["y_p", "y"], "domain": ode.domain},
            "Simplify each $W_j r / W$ before you integrate it.",
            "Constants of integration here only re-create $y_h$, so they can be dropped.",
        ),
    ]
    y_general = y_h + y_p
    steps.append(general_step(ode, y_general, "$y = y_h + y_p$"))
    extra, y_ivp = ivp_steps(ode, y_general)
    return plan("variation_of_parameters_higher", ode, steps + extra, y_general, y_ivp)


# --- Linear systems ----------------------------------------------------------

def vector_tex(vector) -> str:
    return "\\begin{pmatrix}" + " \\\\ ".join(tex(v) for v in vector) + "\\end{pmatrix}"


def matrix_tex(A: sp.Matrix) -> str:
    return "\\begin{pmatrix}" + " \\\\ ".join(f"{tex(A[i, 0])} & {tex(A[i, 1])}" for i in range(2)) + "\\end{pmatrix}"


def matrix_dump(A: sp.Matrix) -> list[list[str]]:
    return [[dump(A[i, j]) for j in range(2)] for i in range(2)]


def _rotation_word(A: sp.Matrix) -> str:
    return "counterclockwise" if A[1, 0] > 0 else "clockwise"


def system_rubric(A: sp.Matrix, kind: str, stability: str, eigen, y_ivp, t: sp.Symbol) -> str:
    p, q, delta = systems.criteria(A)
    facts = [f"The origin is a {kind.lower()} ($p = {tex(p)}$, $q = {tex(q)}$, $\\Delta = {tex(delta)}$), "
             f"and it is {stability.lower()}."]
    values = [e[0] for e in eigen]
    if kind == IMPROPER:
        slow, fast = sorted(eigen, key=lambda e: abs(e[0]))
        facts.append(f"Trajectories leave or enter along the eigenvector lines. Near the origin they run tangent to "
                     f"${vector_tex(slow[1])}$, the direction of $\\lambda = {tex(slow[0])}$, the eigenvalue closer to 0; "
                     f"far from it they run nearly parallel to ${vector_tex(fast[1])}$.")
    elif kind == PROPER:
        facts.append("Every eigenvector direction works, so every trajectory is a straight line through the origin.")
    elif kind == DEGENERATE:
        facts.append(f"There is only one eigenvector direction, ${vector_tex(eigen[0][1])}$, so only one straight-line "
                     "direction, and all other trajectories become tangent to it at the origin.")
    elif kind == SADDLE:
        stable = next(e for e in eigen if e[0] < 0)
        unstable = next(e for e in eigen if e[0] > 0)
        facts.append(f"Two straight lines pass through the origin: along ${vector_tex(stable[1])}$ ($\\lambda = {tex(stable[0])}$) "
                     f"solutions come in, and along ${vector_tex(unstable[1])}$ ($\\lambda = {tex(unstable[0])}$) they go out. "
                     "Every other trajectory comes close and then leaves along the unstable line, so almost every solution grows without bound.")
    elif kind == CENTER:
        beta = abs(sp.im(values[0]))
        facts.append(f"Trajectories are closed curves, ellipses here, traversed {_rotation_word(A)}. Each solution is periodic "
                     f"with period $2\\pi/{tex(beta)}$, and it neither decays nor grows.")
    elif kind == SPIRAL:
        alpha, beta = sp.re(values[0]), abs(sp.im(values[0]))
        direction = "in toward" if alpha < 0 else "out from"
        facts.append(f"Trajectories spiral {direction} the origin, turning {_rotation_word(A)}. The size changes like "
                     f"$e^{{{tex(alpha)} t}}$ while the angular rate comes from $\\beta = {tex(beta)}$.")
    elif kind == LINE:
        nonzero = next(e for e in eigen if e[0] != 0)
        zero = next(e for e in eigen if e[0] == 0)
        facts.append(f"$\\det A = 0$, so the critical points aren't isolated: the whole line along ${vector_tex(zero[1])}$ "
                     f"is made of them. Other trajectories are straight lines along ${vector_tex(nonzero[1])}$ "
                     f"that {'approach' if nonzero[0] < 0 else 'leave'} that line.")
    if y_ivp is not None:
        limits = []
        for name, component in zip(("y_1", "y_2"), y_ivp):
            try:
                limits.append((name, sp.limit(component, t, sp.oo)))
            except (NotImplementedError, ValueError):
                limits = []
                break
        if limits and all(value.is_number and value.is_finite for _, value in limits):
            facts.append("For this initial condition, as $t \\to \\infty$: "
                         + ", ".join(f"${name} \\to {tex(value)}$" for name, value in limits) + ".")
    return " ".join(facts)


def eigen_steps(A: sp.Matrix, eigenvalues: dict, t: sp.Symbol):
    """Eigenvector steps, then the basis of real solutions. Returns (steps, basis, eigen, directions)."""
    steps = []
    eigen = []
    directions = []
    ordered = sorted(eigenvalues, key=sp.default_sort_key)
    identity = sp.eye(2)
    matrix = matrix_dump(A)
    if len(ordered) == 2 and sp.im(ordered[0]) == 0:
        basis = []
        for index, value in enumerate(ordered, 1):
            vector = systems.integer_vector((A - value * identity).nullspace()[0])
            eigen.append((value, vector))
            directions.append((float(vector[0]), float(vector[1])))
            basis.append(vector * sp.exp(value * t))
            steps.append(make_step(
                f"eigvec{index}", "eigenvector", f"Eigenvector for $\\lambda_{index}$",
                f"For $\\lambda_{index} = {tex(value)}$, find an eigenvector $x^{{({index})}}$. Enter its two entries, like $1, 2$.",
                f"{tex(vector[0])}, {tex(vector[1])}",
                {"kind": "eigenvector", "matrix": matrix, "lam": dump(value)},
                "Solve $(A - \\lambda I)x = 0$. The two equations should be multiples of each other.",
                "Pick any nonzero solution of one equation. Any nonzero multiple is also an eigenvector.",
            ))
        return steps, basis, eigen, directions
    if len(ordered) == 2:
        value = next(v for v in ordered if sp.im(v) > 0)
        alpha, beta = sp.re(value), sp.im(value)
        vector = systems.integer_vector((A - value * identity).nullspace()[0])
        a_part = vector.applyfunc(sp.re)
        b_part = vector.applyfunc(sp.im)
        eigen = [(value, vector), (sp.conjugate(value), vector.applyfunc(sp.conjugate))]
        steps.append(make_step(
            "eigvec1", "eigenvector", "Complex eigenvector",
            f"For $\\lambda = {tex(value)}$, find an eigenvector. Enter its two entries, using $i$, like $1, i$.",
            f"{tex(vector[0])}, {tex(vector[1])}",
            {"kind": "eigenvector", "matrix": matrix, "lam": dump(value)},
            "Solve $(A - \\lambda I)x = 0$ with complex arithmetic. One equation is enough.",
            "The conjugate eigenvalue's eigenvector is the conjugate of this one, so one is enough.",
        ))
        wave = sp.exp(alpha * t)
        basis = [wave * (a_part * sp.cos(beta * t) - b_part * sp.sin(beta * t)),
                 wave * (a_part * sp.sin(beta * t) + b_part * sp.cos(beta * t))]
        return steps, basis, eigen, directions
    value = ordered[0]
    nullspace = (A - value * identity).nullspace()
    if len(nullspace) == 2:
        one, two = sp.Matrix([1, 0]), sp.Matrix([0, 1])
        eigen = [(value, one), (value, two)]
        directions = [(1.0, 0.0), (0.0, 1.0)]
        return steps, [one * sp.exp(value * t), two * sp.exp(value * t)], eigen, directions
    vector = systems.integer_vector(nullspace[0])
    eigen = [(value, vector)]
    directions = [(float(vector[0]), float(vector[1]))]
    solution, free = (A - value * identity).gauss_jordan_solve(vector)
    u = solution.subs({symbol: 0 for symbol in free})
    steps += [
        make_step(
            "eigvec1", "eigenvector", "Eigenvector",
            f"For the double eigenvalue $\\lambda = {tex(value)}$, find an eigenvector $x$. Enter its two entries.",
            f"{tex(vector[0])}, {tex(vector[1])}",
            {"kind": "eigenvector", "matrix": matrix, "lam": dump(value)},
            "Solve $(A - \\lambda I)x = 0$.",
            "There is only one independent direction here, so a second solution needs another form.",
        ),
        make_step(
            "u", "generalized_vector", "Second vector $u$",
            f"The second solution is $y^{{(2)}} = x t e^{{\\lambda t}} + u e^{{\\lambda t}}$. With $x = {vector_tex(vector)}$, "
            f"solve $(A - \\lambda I)u = x$. Enter the two entries of any solution $u$.",
            f"{tex(u[0])}, {tex(u[1])}",
            {"kind": "generalized_vector", "matrix": matrix, "lam": dump(value), "x": [dump(v) for v in vector]},
            "This system is singular but consistent, so it has many solutions.",
            "Substituting $y^{(2)}$ into $y' = Ay$ gives $(A - \\lambda I)u = x$. Set one entry of $u$ to 0 if that helps.",
        ),
    ]
    basis = [vector * sp.exp(value * t), (vector * t + u) * sp.exp(value * t)]
    return steps, basis, eigen, directions


def build_system_core(A: sp.Matrix, ic, t0, first_steps: list[dict], method_id: str, statement_ode=None, notation=None) -> dict:
    notation = notation or {}
    t = sp.Symbol("t")
    lam = sp.Symbol("lambda")
    polynomial = sp.expand((A - lam * sp.eye(2)).det())
    eigenvalues = sp.roots(sp.Poly(polynomial, lam))
    if sum(eigenvalues.values()) != 2:
        raise NotApplicable("the characteristic equation has no closed-form roots")
    p, q, delta = systems.criteria(A)
    try:
        kind, stability = systems.classify_by_criteria(A)
    except systems.NotASystem as reason:
        raise NotApplicable(str(reason)) from reason
    listed = [v for v, n in sorted(eigenvalues.items(), key=lambda kv: sp.default_sort_key(kv[0])) for _ in range(n)]
    steps = list(first_steps)
    steps += [
        make_step(
            "char_eq", "characteristic_equation", "Characteristic equation",
            "Write the characteristic equation $\\det(A - \\lambda I) = 0$ in $\\lambda$.",
            f"{tex(polynomial)} = 0",
            {"kind": "equation", "key_lhs": dump(polynomial), "key_rhs": dump(0), "any_variable": "lambda"},
            "Subtract $\\lambda$ from the diagonal, then take the determinant.",
            "For a $2 \\times 2$ matrix this is $\\lambda^2 - (a_{11} + a_{22})\\lambda + \\det A = 0$.",
        ),
        make_step(
            "roots", "eigenvalues", "Eigenvalues",
            "Find the eigenvalues. List a double eigenvalue twice.",
            ", ".join(tex(v) for v in listed),
            {"kind": "roots", "key": [dump(v) for v in listed]},
            "Factor or use the quadratic formula. Complex eigenvalues come as a conjugate pair.",
            "$\\lambda = \\frac{p \\pm \\sqrt{p^2 - 4q}}{2}$ with $p$ the trace and $q$ the determinant.",
        ),
    ]
    vector_steps, basis, eigen, directions = eigen_steps(A, eigenvalues, t)
    steps += vector_steps
    constants = constant_names(2, {"constant": notation.get("constant", "c")})
    c1, c2 = sp.Symbol(constants[0]), sp.Symbol(constants[1])
    general = (c1 * basis[0] + c2 * basis[1]).applyfunc(sp.expand)
    answer = f"y_1 = {tex(general[0])},\\ y_2 = {tex(general[1])}"
    steps.append(make_step(
        "general", "system_general_solution", "General solution",
        "Write the general solution as two real functions: $y_1 = \\dots,\\ y_2 = \\dots$ with constants $c_1, c_2$.",
        answer,
        {"kind": "system_solution", "matrix": matrix_dump(A), "t": "t"},
        "Combine your solutions as $c_1 y^{(1)} + c_2 y^{(2)}$, then read off each component.",
        {True: "For $\\lambda = \\alpha + \\beta i$ with eigenvector $a + ib$, the real solutions are "
               "$e^{\\alpha t}(a\\cos\\beta t - b\\sin\\beta t)$ and $e^{\\alpha t}(a\\sin\\beta t + b\\cos\\beta t)$.",
         False: "Each solution is an eigenvector times $e^{\\lambda t}$, with the extra $t$ form for a double eigenvalue."}[
            bool(sp.im(listed[0]) != 0)],
    ))
    wronskian = sp.simplify(sp.Matrix.hstack(basis[0], basis[1]).det())
    shown = ",\\quad ".join(f"y^{{({k + 1})}} = {vector_tex(b.applyfunc(sp.simplify))}" for k, b in enumerate(basis))
    steps.append(make_step(
        "wronskian", "system_wronskian", "Wronskian: is it a basis?",
        f"Take ${shown}$. Compute the Wronskian $W$, the determinant with these as columns.",
        f"W = {tex(wronskian)}",
        expr_check(wronskian, aliases=["W"], up_to_factor=True),
        "Put the two solution vectors side by side as columns.",
        "They form a basis exactly when $W \\neq 0$. A nonzero constant factor from scaling an eigenvector doesn't matter.",
    ))
    y_ivp = None
    if ic is not None:
        solved = sp.solve(list(general.subs(t, t0) - sp.Matrix(ic)), [c1, c2], dict=True)
        if len(solved) != 1:
            raise NotApplicable("the initial conditions don't determine the constants")
        y_ivp = general.subs(solved[0]).applyfunc(sp.simplify).applyfunc(sp.expand)
        steps.append(make_step(
            "ivp", "apply_initial_condition", "Apply the initial conditions",
            f"Use $y_1({tex(t0)}) = {tex(ic[0])},\\ y_2({tex(t0)}) = {tex(ic[1])}$ to find $c_1, c_2$. Write $y_1 = \\dots,\\ y_2 = \\dots$",
            f"y_1 = {tex(y_ivp[0])},\\ y_2 = {tex(y_ivp[1])}",
            {"kind": "assignments", "key": {"y_1": dump(y_ivp[0]), "y_2": dump(y_ivp[1])}},
            "Set $t$ to the initial time in your general solution.",
            "That gives two linear equations in $c_1, c_2$.",
        ))
    facts = f"p = {p}, q = {q}, Delta = {delta}; correct type {kind}; correct stability {stability}"
    steps += [
        make_step(
            "criteria", "critical_point_criteria", "Criteria: $p$, $q$, $\\Delta$",
            "Find the trace $p$, the determinant $q$, and the discriminant $\\Delta$. Write $p = \\dots,\\ q = \\dots,\\ \\Delta = \\dots$",
            f"p = {tex(p)},\\ q = {tex(q)},\\ \\Delta = {tex(delta)}",
            {"kind": "assignments", "key": {"p": dump(p), "q": dump(q), "Delta": dump(delta)}},
            "All three come straight from the entries of $A$.",
            "$p = a_{11} + a_{22}$, $q = \\det A$, $\\Delta = p^2 - 4q$.",
        ),
        choice_step(
            "type", "critical_point_type", "Type of critical point",
            "What type of critical point is the origin?",
            kind, TYPES,
            "Start with the sign of $q$, then $\\Delta$ and $p$.",
            "$q < 0$: saddle. $p = 0, q > 0$: center. $q > 0, \\Delta < 0, p \\neq 0$: spiral. "
            "$q > 0, \\Delta \\geq 0$: node. A node with $\\Delta = 0$ is proper if every direction is an eigenvector and degenerate otherwise. "
            "$q = 0$: the critical points aren't isolated.",
            note=f"$p = {tex(p)}$, $q = {tex(q)}$, $\\Delta = {tex(delta)}$.",
            facts=facts,
        ),
        choice_step(
            "stability", "stability", "Stability",
            "How stable is the origin?",
            stability, STABILITIES,
            "Does a solution starting nearby stay nearby? Does it also go to the origin?",
            "$p < 0, q > 0$: stable and attractive. $p = 0, q > 0$: stable but not attractive. $p > 0$ or $q < 0$: unstable. "
            "With $q = 0$, the sign of $p$ decides between stable and unstable.",
            facts=facts,
        ),
    ]
    numeric = _floats(A)
    start = [float(v) for v in ic] if ic is not None else None
    figure_list = [figures.phase_portrait(
        numeric, directions, start,
        caption="Each curve is a numerically integrated solution, with arrows pointing forward in time. "
                "Thick lines are the eigenvector directions." if directions else
                "Each curve is a numerically integrated solution, with arrows pointing forward in time.",
        alt=f"Phase portrait in the y_1, y_2 plane of a {kind.lower()} at the origin.")]
    if start is not None:
        figure_list.append(figures.time_plot(
            numeric, start, caption="Integrated numerically from the initial condition.",
            alt="y_1 and y_2 against t from the initial condition."))
    steps.append(text_step(
        "discuss", "phase_plane_discussion", "Discuss the plots",
        "Use the plots and your work to discuss the result. What does the phase portrait show about how trajectories "
        "move, and how do the eigenvalues and eigenvectors explain it?"
        + (" What happens to this solution as $t \\to \\infty$?" if start is not None else ""),
        system_rubric(A, kind, stability, eigen, y_ivp, t), figure_list,
        "Follow a few curves in the direction of their arrows, then look at the eigenvector lines.",
        "Name the critical point and its stability, explain them with the eigenvalues, and say what happens for large $t$.",
    ))
    result = {
        "method": method_id, "kind": "system", "x": "t", "order": 2, "domain": "real",
        "matrix": matrix_dump(A), "steps": steps,
        "general": [dump(general[0]), dump(general[1])],
        "ivp": [dump(y_ivp[0]), dump(y_ivp[1])] if y_ivp is not None else None,
        "ic": [dump(ic[0]), dump(ic[1])] if ic is not None else None, "t0": dump(t0),
        "classification": [kind, stability],
    }
    if statement_ode is not None:
        result["second_order_residual"] = dump(statement_ode.residual)
    return result


def build_linear_system(spec: dict, notation: dict) -> dict:
    try:
        A = systems.parse_system(spec["statement"])
        t0, ic = systems.parse_system_ic(spec["ic"]) if spec.get("ic") else (sp.Integer(0), None)
    except systems.NotASystem as reason:
        raise NotApplicable(str(reason)) from reason
    first = make_step(
        "matrix", "coefficient_matrix", "Coefficient matrix",
        "Write the system as $y' = Ay$. Enter the entries of $A$ row by row: $a_{11}, a_{12}, a_{21}, a_{22}$.",
        matrix_tex(A),
        {"kind": "matrix", "key": [dump(v) for v in A], "size": 2},
        "Row $i$ holds the coefficients of $y_1$ and $y_2$ in the equation for $y_i'$.",
        "A term that's missing has coefficient 0.",
    )
    return build_system_core(A, ic, t0, [first], "linear_system", notation=notation)


def build_ode_to_system(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    ode.x = sp.Symbol("t")
    if ode.order != 2:
        raise NotApplicable("this template converts second-order ODEs")
    coefficients, g = ode.linear_form()
    if g != 0 or any(c.free_symbols for c in coefficients):
        raise NotApplicable("this template handles homogeneous constant-coefficient ODEs")
    a0, a1, a2 = coefficients
    A = sp.Matrix([[0, 1], [-a0 / a2, -a1 / a2]])
    ic = None
    t0 = sp.Integer(0)
    if ode.ics:
        if len(ode.ics) != 2 or {i[0] for i in ode.ics} != {0, 1}:
            raise NotApplicable("give y and y' at one point")
        by_order = {k: value for k, _, value in ode.ics}
        t0 = ode.ics[0][1]
        ic = [by_order[0], by_order[1]]
    first = [
        make_step(
            "convert", "convert_to_system", "Convert to a system",
            "Set $y_1 = y$ and $y_2 = y'$. Then $y_1' = y_2$, and the ODE gives $y_2'$. Enter $A$ for $y' = Ay$ row by row: $a_{11}, a_{12}, a_{21}, a_{22}$.",
            matrix_tex(A),
            {"kind": "matrix", "key": [dump(v) for v in A], "size": 2},
            "Solve the ODE for $y''$, then write it in terms of $y_1$ and $y_2$.",
            "The first row is always $0, 1$. The second comes from $y'' = \\dots$ with the leading coefficient divided out.",
        ),
    ]
    result = build_system_core(A, ic, t0, first, "ode_to_system", ode, notation)
    general = sp.sympify(result["general"][0])
    connect = make_step(
        "back", "general_solution", "Back to the original ODE",
        "Since $y = y_1$, write the general solution of the original second-order ODE.",
        f"y = {tex(general)}",
        {"kind": "general_solution", "ode": dump(ode.residual), "order": 2, "x": "t", "domain": "real"},
        "It's the first component of the system's solution.",
        "The eigenvalues of $A$ are the roots of the ODE's characteristic equation, so the forms match.",
    )
    position = next(k for k, s in enumerate(result["steps"]) if s["id"] == "general") + 1
    result["steps"].insert(position, connect)
    return result


METHODS = [
    Method(
        "course_items", "Course check items (experimental)",
        "Multiple choice and numeric items graded exactly by the server, and short answers judged by Claude.",
        "any subject: a section's check in an experimental course, each item tagged with its objective",
        "no derivations: math problems use the ordinary repertoire methods; short answers aren't checked by SymPy",
        ["course_choice", "course_numeric", "course_short_answer"],
        build_course_items,
        COURSE_SAMPLES,
    ),
    Method(
        "concept_questions", "Concept questions",
        "Short written questions on when and why methods apply. Claude judges each answer.",
        "what a method is for, when it applies, how methods relate, and what a result means in an application",
        "no computed answers: reference answers are written by Claude and not checked by SymPy",
        ["concept_question"],
        build_concept_questions,
        [{"statement": "\\text{Concept check}", "questions": [
            {"question": "Why does a nonhomogeneous solution need $y_h$ as well as $y_p$?",
             "answer": "$y_p$ alone has no free constants, and adding $y_h$ gives the general solution.",
             "nudge": "Count the free constants.", "method_hint": "Compare $y_p$ with $y_h+y_p$."}]},
         {"statement": "\\text{Concept check}", "questions": [
             {"question": "Which methods get $y_p$ once $y_h$ is known?",
              "answer": "Undetermined coefficients and variation of parameters.",
              "nudge": "There are two standard ones.", "method_hint": "One guesses a form, and one varies the constants."},
             {"question": "When can't undetermined coefficients be used?",
              "answer": "When the coefficients aren't constant, or the right side isn't a polynomial, exponential, sine or cosine, or a sum or product of those.",
              "nudge": "Think about what the trial forms cover.", "method_hint": "Try writing a trial form for $\\sec x$."}]}],
    ),
    Method(
        "euler_cauchy", "Euler-Cauchy (any order)",
        "Substitute y = x^m, auxiliary equation, roots, basis, Wronskian at third order and up, general solution, IVP, plot and discussion.",
        "a_n x^n y^(n) + ... + a_1 x y' + a_0 y = 0 on x > 0, order 2 and up, with distinct, repeated or complex roots",
        "homogeneous only; use variation_of_parameters_higher for a right side",
        ["indicial_equation", "characteristic_roots", "wronskian", "general_solution", "apply_initial_condition", "solution_discussion"],
        build_euler_cauchy,
        [{"statement": "x^2y''-2y=0"}, {"statement": "x^2y''+xy'+y=0"}, {"statement": "x^2y''+3xy'+y=0"},
         {"statement": "x^3y'''-3x^2y''+6xy'-6y=0"},
         {"statement": "x^2y''-2xy'+2y=0", "ic": ["y(1)=1", "y'(1)=0"]},
         {"statement": "x^2y''+xy'+4y=0", "ic": ["y(1)=1", "y'(1)=0"]}],
    ),
    Method(
        "variation_of_parameters_higher", "Variation of parameters (Wronskian determinants)",
        "y_h, standard form, Wronskian W, determinants W_j, y_p = sum y_j times the integral of W_j r / W.",
        "second- and third-order linear ODEs with constant coefficients or of Euler-Cauchy form, with closed-form integrals (Kreyszig 3.3)",
        "order 2 and 3 only",
        ["indicial_equation", "characteristic_equation", "characteristic_roots", "homogeneous_solution", "standard_form", "wronskian",
         "wronskian_minors", "particular_solution", "general_solution", "apply_initial_condition"],
        build_variation_higher,
        [{"statement": "x^3y'''-3x^2y''+6xy'-6y=x^4"}, {"statement": "x^2y''-2xy'+2y=x^3"},
         {"statement": "y'''-y'=e^{2x}"}],
    ),
    Method(
        "linear_system", "Linear systems and the phase plane",
        "Matrix, eigenvalues, eigenvectors, general solution, Wronskian, IVP, p, q and Delta, critical point type, stability, phase portrait and discussion.",
        "y' = Ay with constant 2x2 A: real distinct, complex, double and zero eigenvalues",
        "homogeneous and 2x2 only; nonlinear systems (4.5) and forcing terms (4.6) aren't covered",
        ["coefficient_matrix", "characteristic_equation", "eigenvalues", "eigenvector", "generalized_vector",
         "system_general_solution", "system_wronskian", "apply_initial_condition", "critical_point_criteria",
         "critical_point_type", "stability", "phase_plane_discussion"],
        build_linear_system,
        [{"statement": "y_1'=-4y_1+y_2,\\quad y_2'=y_1-4y_2", "ic": ["y_1(0)=2", "y_2(0)=0"]},
         {"statement": "y_1'=y_1+2y_2,\\quad y_2'=2y_1+y_2", "ic": ["y_1(0)=1", "y_2(0)=0"]},
         {"statement": "y_1'=-y_1+2y_2,\\quad y_2'=-2y_1-y_2", "ic": ["y_1(0)=2", "y_2(0)=1"]},
         {"statement": "y_1'=y_2,\\quad y_2'=-9y_1", "ic": ["y_1(0)=1", "y_2(0)=0"]},
         {"statement": "y_1'=3y_1+y_2,\\quad y_2'=-y_1+y_2"},
         {"statement": "y_1'=-2y_1,\\quad y_2'=-2y_2", "ic": ["y_1(0)=1", "y_2(0)=2"]},
         {"statement": "y_1'=-\\frac{1}{25}y_1+\\frac{1}{25}y_2,\\quad y_2'=\\frac{1}{25}y_1-\\frac{1}{25}y_2",
          "ic": ["y_1(0)=0", "y_2(0)=100"]}],
    ),
    Method(
        "ode_to_system", "Second-order ODE as a system",
        "y_1 = y, y_2 = y', companion matrix, then the full system solution, back to y, and the phase portrait.",
        "homogeneous constant-coefficient second-order ODEs, including IVPs",
        "second order and homogeneous only",
        ["convert_to_system", "characteristic_equation", "eigenvalues", "eigenvector", "generalized_vector",
         "system_general_solution", "general_solution", "system_wronskian", "apply_initial_condition",
         "critical_point_criteria", "critical_point_type", "stability", "phase_plane_discussion"],
        build_ode_to_system,
        [{"statement": "y''+y'-2y=0", "ic": ["y(0)=1", "y'(0)=0"]},
         {"statement": "y''+2y'+5y=0", "ic": ["y(0)=1", "y'(0)=1"]}],
    ),
]
