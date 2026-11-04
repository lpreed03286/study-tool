"""The method repertoire: how each method breaks a problem into graded steps.

A method only supplies steps and the mathematics behind them; every answer key
is computed here by SymPy, never typed in. verify.py then checks the result by
independent routes before anyone sees it.

Add a method by writing a module to the repertoire folder that defines METHOD,
a Method object. See skills/make-set/SKILL.md for the required format and for
how add_method validates it before it can be used.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import sympy as sp

from grade import dump
from mathparse import ParseFailure, clean_latex, parse_answer, split_top, to_sympy

COEFFICIENT_NAMES = ["A", "B", "C", "D", "G", "H", "J", "K", "L", "M", "N", "P"]


class NotApplicable(Exception):
    """This problem isn't one the chosen method can solve."""


def dy(k: int, name: str = "y") -> sp.Symbol:
    return sp.Symbol(name + "'" * k)


@dataclass
class ODE:
    source: str
    x: sp.Symbol
    residual: sp.Expr          # lhs - rhs in symbols y, y', y'', x
    order: int
    ics: list[tuple[int, sp.Expr, sp.Expr]] = field(default_factory=list)  # (derivative, x0, value)
    domain: str = "real"

    @classmethod
    def parse(cls, statement: str, ics: list[str] | None = None) -> "ODE":
        try:
            pieces = split_top(clean_latex(statement), ("=",))
            if len(pieces) != 2:
                raise NotApplicable("write the ODE as one equation with a single =")
            left, right = to_sympy(pieces[0]), to_sympy(pieces[1])
        except ParseFailure as failure:
            raise NotApplicable(f"couldn't read the ODE: {failure}") from failure
        residual = left - right
        names = {str(s) for s in residual.free_symbols}
        order = max((n.count("'") for n in names if n.startswith("y")), default=0)
        if order == 0:
            raise NotApplicable("no derivative of y found in the ODE")
        x = sp.Symbol("t") if "t" in names and "x" not in names else sp.Symbol("x")
        extras = names - {"x", "t", "y"} - {"y" + "'" * k for k in range(1, order + 1)}
        if extras:
            raise NotApplicable(f"unexpected symbols in the ODE: {', '.join(sorted(extras))}")
        return cls(statement, x, residual, order, [_parse_ic(ic) for ic in ics or []])

    def linear_form(self) -> tuple[list[sp.Expr], sp.Expr]:
        """Coefficients [a_0 .. a_n] and g with a_n y^(n) + ... + a_0 y = g."""
        symbols = [dy(k) for k in range(self.order + 1)]
        coefficients = [sp.simplify(sp.diff(self.residual, s)) for s in symbols]
        if any(c.free_symbols & set(symbols) for c in coefficients):
            raise NotApplicable("the ODE isn't linear in y and its derivatives")
        g = sp.simplify(-self.residual.subs({s: 0 for s in symbols}))
        return coefficients, g

    def homogeneous_residual(self) -> sp.Expr:
        coefficients, _ = self.linear_form()
        return sum(c * dy(k) for k, c in enumerate(coefficients))

    def as_function_equation(self) -> sp.Eq:
        f = sp.Function("y")(self.x)
        swaps = {dy(k): f.diff(self.x, k) if k else f for k in range(self.order + 1)}
        return sp.Eq(self.residual.subs(swaps), 0)


def _parse_ic(text: str) -> tuple[int, sp.Expr, sp.Expr]:
    match = re.fullmatch(r"\s*y('*)\(([^)]*)\)\s*=\s*(.+)", clean_latex(text))
    if not match:
        raise NotApplicable(f"couldn't read the initial condition {text!r}: use a form like y(0)=1")
    return len(match.group(1)), to_sympy(match.group(2)), to_sympy(match.group(3))


def expanded(expr: sp.Expr) -> sp.Expr:
    return sp.expand(expr)


def tex(expr) -> str:
    return sp.latex(expr)


def terms_tex(terms: list[sp.Expr]) -> str:
    """Terms in the order given. SymPy would write y' + 2y as 2y + y'."""
    out = ""
    for term in terms:
        piece = tex(term)
        if not out:
            out = piece
        elif piece.startswith("-"):
            out += " " + piece
        else:
            out += " + " + piece
    return out


def make_step(step_id, step_type, label, prompt, answer, check, nudge, method, note="") -> dict:
    return {
        "id": step_id, "type": step_type, "label": label, "prompt": prompt,
        "answer": answer, "check": check, "hints": [nudge, method], "note": note,
    }


def expr_check(key, **extra) -> dict:
    return {"kind": "expr", "key": dump(key), **extra}


# --- Shared pieces -----------------------------------------------------------

def constant_coefficients(ode: ODE) -> tuple[list[sp.Expr], sp.Expr]:
    coefficients, g = ode.linear_form()
    if any(c.free_symbols for c in coefficients):
        raise NotApplicable("the coefficients aren't constant")
    if coefficients[-1] == 0:
        raise NotApplicable("the leading coefficient is zero")
    return coefficients, g


def characteristic_roots(coefficients: list[sp.Expr], r: sp.Symbol) -> tuple[sp.Expr, dict]:
    poly = sum(c * r**k for k, c in enumerate(coefficients))
    roots = sp.roots(sp.Poly(poly, r))
    if sum(roots.values()) != len(coefficients) - 1:
        raise NotApplicable("the characteristic polynomial has no closed-form roots")
    return poly, roots


def homogeneous_basis(roots: dict, x: sp.Symbol) -> list[sp.Expr]:
    basis = []
    used = set()
    for root, multiplicity in sorted(roots.items(), key=lambda kv: sp.default_sort_key(kv[0])):
        if root in used:
            continue
        a, b = sp.re(root), sp.im(root)
        for k in range(multiplicity):
            power = x**k
            if b == 0:
                basis.append(power * sp.exp(a * x))
            else:
                basis.append(power * sp.exp(a * x) * sp.cos(abs(b) * x))
                basis.append(power * sp.exp(a * x) * sp.sin(abs(b) * x))
        used.update({root, sp.conjugate(root)})
    return basis


def combine(basis: list[sp.Expr], names: list[str]) -> sp.Expr:
    return sum(sp.Symbol(n) * f for n, f in zip(names, basis))


def constant_names(count: int, notation: dict) -> list[str]:
    letter = notation.get("constant", "c")
    return [f"{letter}_{k + 1}" for k in range(count)]


def char_steps(ode: ODE, coefficients, r_name: str, notation: dict) -> tuple[list[dict], dict, sp.Expr]:
    """Characteristic equation, roots and y_h: shared by three methods."""
    r_tex = notation.get("char", "r")
    r = sp.Symbol(r_name)
    poly, roots = characteristic_roots(coefficients, r)
    root_list = [root for root, m in roots.items() for _ in range(m)]
    roots_answer = ", ".join(tex(v) for v in root_list)
    basis = homogeneous_basis(roots, ode.x)
    names = constant_names(len(basis), notation)
    y_h = combine(basis, names)
    h_residual = ode.homogeneous_residual()
    steps = [
        make_step(
            "char_eq", "characteristic_equation", "Characteristic equation",
            f"Write the characteristic equation for the homogeneous part, using ${r_tex}$.",
            f"{tex(poly)} = 0",
            {"kind": "equation", "key_lhs": dump(poly), "key_rhs": dump(0), "any_variable": r_name},
            f"Try $y = e^{{{r_tex} {ode.x}}}$ in the homogeneous part.",
            f"Each derivative $y^{{(k)}}$ becomes ${r_tex}^k$, and $y$ becomes $1$.",
        ),
        make_step(
            "roots", "characteristic_roots", "Characteristic roots",
            "Find the roots. List a repeated root twice.",
            roots_answer,
            {"kind": "roots", "key": [dump(v) for v in root_list]},
            "Factor, or use the quadratic formula. Complex roots come in conjugate pairs.",
            f"For $a{r_tex}^2 + b{r_tex} + c = 0$, ${r_tex} = \\frac{{-b \\pm \\sqrt{{b^2 - 4ac}}}}{{2a}}$.",
        ),
        make_step(
            "y_h", "homogeneous_solution", "Homogeneous solution $y_h$",
            "Write $y_h$, the general solution of the homogeneous equation.",
            f"y_h = {tex(y_h)}",
            {"kind": "general_solution", "ode": dump(h_residual), "order": ode.order,
             "x": str(ode.x), "aliases": ["y_h", "y"], "domain": ode.domain},
            "The form depends on whether the roots are real and distinct, repeated, or complex.",
            f"Distinct: $e^{{r{ode.x}}}$ for each. Repeated: multiply by ${ode.x}$. "
            f"Complex $a \\pm bi$: $e^{{a{ode.x}}}(c_1 \\cos b{ode.x} + c_2 \\sin b{ode.x})$.",
        ),
    ]
    return steps, roots, y_h


def general_step(ode: ODE, y_general: sp.Expr, hint_form: str) -> dict:
    return make_step(
        "general", "general_solution", "General solution",
        f"Write the general solution $y({ode.x})$.",
        f"y = {tex(sp.expand(y_general))}",
        {"kind": "general_solution", "ode": dump(ode.residual), "order": ode.order,
         "x": str(ode.x), "domain": ode.domain},
        f"Put the pieces together: {hint_form}.",
        "It should contain as many arbitrary constants as the order of the ODE.",
    )


def ivp_steps(ode: ODE, y_general: sp.Expr) -> tuple[list[dict], sp.Expr | None]:
    if not ode.ics:
        return [], None
    constants = sorted(y_general.free_symbols - {ode.x}, key=str)
    if len(ode.ics) != len(constants):
        raise NotApplicable(f"expected {len(constants)} initial condition(s), got {len(ode.ics)}")
    equations = [sp.diff(y_general, ode.x, k).subs(ode.x, x0) - value for k, x0, value in ode.ics]
    solution = sp.solve(equations, constants, dict=True)
    if len(solution) != 1:
        raise NotApplicable("the initial conditions don't determine the constants uniquely")
    y_ivp = sp.simplify(y_general.subs(solution[0]))
    if y_ivp.has(sp.Float):
        raise NotApplicable("the solution came out with decimals")
    conditions = ", ".join(_ic_tex(ic) for ic in ode.ics)
    return [make_step(
        "ivp", "apply_initial_condition", "Apply the initial conditions",
        f"Use ${conditions}$ to find the constants. Write the final solution.",
        f"y = {tex(sp.expand(y_ivp))}",
        expr_check(y_ivp, aliases=["y"], domain=ode.domain),
        "Differentiate the general solution first if a condition involves $y'$.",
        "Substitute each condition to get one equation in the constants, then solve the system.",
    )], y_ivp


def _ic_tex(ic) -> str:
    k, x0, value = ic
    return f"y{chr(39) * k}({tex(x0)}) = {tex(value)}"


def plan(method_id: str, ode: ODE, steps: list[dict], y_general: sp.Expr | None, y_ivp=None,
         y_implicit=None, slope=None) -> dict:
    return {
        "method": method_id,
        "x": str(ode.x),
        "order": ode.order,
        "residual": dump(ode.residual),
        "domain": ode.domain,
        "ics": [[k, dump(x0), dump(v)] for k, x0, v in ode.ics],
        "steps": steps,
        "general": dump(y_general) if y_general is not None else None,
        "ivp": dump(y_ivp) if y_ivp is not None else None,
        "implicit": dump(y_implicit) if y_implicit is not None else None,
        "slope": dump(slope) if slope is not None else None,
    }


# --- first_order_linear ------------------------------------------------------

def build_first_order_linear(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    if ode.order != 1:
        raise NotApplicable("this method is for first-order ODEs")
    x = ode.x
    (b, a), g = ode.linear_form()
    p, r = sp.simplify(b / a), sp.simplify(g / a)
    exponent = sp.integrate(p, x)
    mu = sp.simplify(sp.exp(exponent))
    rhs = sp.simplify(mu * r)
    integral = sp.integrate(rhs, x)
    if exponent.has(sp.Integral) or integral.has(sp.Integral):
        raise NotApplicable("an integral in this method has no closed form")
    if p.has(sp.log) or (1 / x) in sp.preorder_traversal(p) or mu.has(sp.log) or _has_negative_power(p, x) or _has_negative_power(r, x):
        ode.domain = "positive"
    p_name = notation.get("p", "p")
    r_name = notation.get("r", "r")
    mu_tex = notation.get("mu", r"\mu")
    mu_name = mu_tex.replace("\\", "")
    c_name = notation.get("constant", "C")
    C = sp.Symbol(c_name)
    y_general = sp.simplify((integral + C) / mu)
    y_prime = dy(1)
    standard_lhs = y_prime + p * dy(0)
    steps = [
        make_step(
            "standard", "standard_form", "Standard form",
            f"Rewrite the ODE as $y' + {p_name}({x})\\,y = {r_name}({x})$.",
            f"{terms_tex([y_prime, p * dy(0)])} = {tex(r)}",
            {"kind": "equation", "key_lhs": dump(standard_lhs), "key_rhs": dump(r),
             "monic": "y'", "domain": ode.domain},
            "The coefficient of $y'$ has to be exactly 1.",
            f"Divide through by the coefficient of $y'$, then move every $y$ term to the left.",
        ),
        make_step(
            "identify", "identify_coefficients", f"Identify ${p_name}({x})$ and ${r_name}({x})$",
            f"Read off ${p_name}({x})$ and ${r_name}({x})$ from standard form. Write them as ${p_name} = \\dots,\\ {r_name} = \\dots$",
            f"{p_name} = {tex(p)},\\ {r_name} = {tex(r)}",
            {"kind": "assignments", "key": {p_name: dump(p), r_name: dump(r)}, "domain": ode.domain},
            f"${p_name}({x})$ multiplies $y$; ${r_name}({x})$ is everything on the right.",
            f"Signs carry over: a minus in front of $y$ makes ${p_name}({x})$ negative.",
        ),
        make_step(
            "mu", "integrating_factor", "Integrating factor",
            f"Find the integrating factor ${mu_tex}({x})$. No arbitrary constant is needed.",
            f"{mu_tex} = {tex(mu)}",
            {"kind": "integrating_factor", "p": dump(p), "x": str(x),
             "aliases": [mu_name, "mu", "F", "I"], "domain": ode.domain},
            f"It's built from the integral of ${p_name}({x})$.",
            f"${mu_tex} = e^{{\\int {p_name}\\,d{x}}}$.",
        ),
        make_step(
            "multiply", "multiply_through", "Multiply through",
            f"Multiplying by ${mu_tex}$ makes the left side $({mu_tex} y)'$. What is the right side, ${mu_tex}\\,{r_name}$?",
            tex(rhs),
            expr_check(rhs, domain=ode.domain, aliases=[r_name]),
            f"Multiply your ${mu_tex}$ by ${r_name}({x})$ and simplify.",
            f"The left side collapses by the product rule; only the right side changes form: $({mu_tex}y)' = {mu_tex} {r_name}$.",
        ),
        make_step(
            "integrate", "integrate_both_sides", "Integrate both sides",
            f"Integrate $\\int {mu_tex}\\,{r_name}\\,d{x}$. Include the arbitrary constant ${c_name}$.",
            f"{tex(integral)} + {c_name}",
            {"kind": "antiderivative", "integrand": dump(rhs), "var": str(x),
             "require_constant": True, "domain": ode.domain},
            "Differentiate your answer to check it.",
            f"This gives ${mu_tex} y = \\int {mu_tex} {r_name}\\,d{x} + {c_name}$.",
        ),
        general_step(ode, y_general, f"divide by ${mu_tex}$"),
    ]
    extra, y_ivp = ivp_steps(ode, y_general)
    return plan("first_order_linear", ode, steps + extra, y_general, y_ivp)


def _has_negative_power(expr: sp.Expr, x: sp.Symbol) -> bool:
    return any(base.has(x) and exponent.is_negative for base, exponent in
               (node.as_base_exp() for node in sp.preorder_traversal(expr) if isinstance(node, sp.Pow)))


# --- constant_coefficient_homogeneous ----------------------------------------

def build_constant_coefficient(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    coefficients, g = constant_coefficients(ode)
    if g != 0:
        raise NotApplicable("the ODE isn't homogeneous; use undetermined_coefficients or variation_of_parameters")
    if ode.order < 2:
        raise NotApplicable("this method is for second order and up")
    r_name = notation.get("char", "r").replace("\\", "")
    steps, _, y_h = char_steps(ode, coefficients, r_name, notation)
    steps[-1]["label"] = "General solution"
    steps[-1]["prompt"] = "Write the general solution."
    steps[-1]["answer"] = f"y = {tex(y_h)}"
    steps[-1]["type"] = "general_solution"
    extra, y_ivp = ivp_steps(ode, y_h)
    return plan("constant_coefficient_homogeneous", ode, steps + extra, y_h, y_ivp)


# --- undetermined_coefficients ------------------------------------------------

def _decompose_uc_term(term: sp.Expr, x: sp.Symbol) -> tuple[int, sp.Expr, sp.Expr]:
    """x^k e^{ax} {cos,sin}(bx) -> (k, a, b). Anything else isn't in the class."""
    _, func = term.as_independent(x)
    k, a, b = 0, sp.Integer(0), sp.Integer(0)
    trig_seen = False
    for factor in sp.Mul.make_args(func):
        base, exponent = factor.as_base_exp()
        if factor == 1:
            continue
        if base == x and exponent.is_Integer and exponent > 0:
            k = int(exponent)
        elif isinstance(factor, sp.exp) or base == sp.E:
            argument = sp.log(factor) if not isinstance(factor, sp.exp) else factor.args[0]
            if not sp.diff(argument, x).is_number or argument.subs(x, 0) != 0:
                raise NotApplicable(f"{factor} isn't of the form e^(ax)")
            a = sp.diff(argument, x)
        elif isinstance(factor, (sp.cos, sp.sin)) and not trig_seen:
            argument = factor.args[0]
            if not sp.diff(argument, x).is_number or argument.subs(x, 0) != 0:
                raise NotApplicable(f"{factor} isn't of the form cos(bx) or sin(bx)")
            b = abs(sp.diff(argument, x))
            trig_seen = True
        else:
            raise NotApplicable(
                f"the term {term} isn't a polynomial, exponential, sine or cosine product. "
                "Undetermined coefficients doesn't apply; use variation_of_parameters.")
    return k, a, b


def uc_trial(g: sp.Expr, x: sp.Symbol, roots: dict) -> tuple[sp.Expr, list[str], list[dict]]:
    groups: dict[tuple, int] = {}
    for term in sp.Add.make_args(sp.expand(g.rewrite(sp.exp) if g.has(sp.sinh, sp.cosh) else g)):
        k, a, b = _decompose_uc_term(term, x)
        groups[(a, b)] = max(groups.get((a, b), 0), k)
    names = iter(COEFFICIENT_NAMES)
    used: list[str] = []
    trial = sp.Integer(0)
    reasons = []

    def next_name():
        name = next(names)
        used.append(name)
        return sp.Symbol(name)

    for (a, b), degree in sorted(groups.items(), key=lambda kv: sp.default_sort_key(kv[0])):
        s = roots.get(a + b * sp.I, 0)
        first = sum(next_name() * x**k for k in range(degree + 1))
        if b == 0:
            block = first
        else:
            block = first * sp.cos(b * x) + sum(next_name() * x**k for k in range(degree + 1)) * sp.sin(b * x)
        trial += x**s * sp.exp(a * x) * block
        reasons.append({"a": a, "b": b, "s": s, "degree": degree})
    return trial, used, reasons


def uc_equations(expression: sp.Expr, unknowns: list[sp.Symbol], x: sp.Symbol) -> tuple[list[sp.Expr], list[sp.Expr]]:
    """Group a linear combination by its functions of x: one equation per function."""
    by_function: dict[sp.Expr, sp.Expr] = {}
    for term in sp.Add.make_args(sp.expand(expression)):
        coefficient, function = term.as_independent(x)
        by_function[function] = by_function.get(function, 0) + coefficient
    functions = sorted(by_function, key=sp.default_sort_key)
    return [sp.expand(by_function[f]) for f in functions], functions


def _tidy(expr: sp.Expr, x: sp.Symbol) -> sp.Expr:
    """Group by function of x so a revealed key is readable."""
    expression = sp.expand(expr)
    by_function: dict[sp.Expr, sp.Expr] = {}
    for term in sp.Add.make_args(expression):
        coefficient, function = term.as_independent(x)
        by_function[function] = by_function.get(function, 0) + coefficient
    return sum(sp.factor(c) * f for f, c in by_function.items())


def build_undetermined_coefficients(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    x = ode.x
    coefficients, g = constant_coefficients(ode)
    if g == 0:
        raise NotApplicable("the ODE is homogeneous; use constant_coefficient_homogeneous")
    if g.has(sp.Float):
        raise NotApplicable("use exact fractions in the ODE")
    r_name = notation.get("char", "r").replace("\\", "")
    steps, roots, y_h = char_steps(ode, coefficients, r_name, notation)
    trial, names, reasons = uc_trial(g, x, roots)
    unknowns = [sp.Symbol(n) for n in names]
    h_residual = ode.homogeneous_residual()
    modified = any(r["s"] > 0 for r in reasons)
    trial_note = ("A term of the usual form already solves the homogeneous equation, so it is multiplied by "
                  f"${x}$ (modification rule)." if modified else
                  "No term of this form solves the homogeneous equation, so no extra factor is needed.")
    example = tex(trial)
    steps.append(make_step(
        "y_p", "trial_form", "Trial form $y_p$",
        f"Write the trial form of $y_p$ for the right side ${tex(g)}$, with unknown coefficients ${', '.join(names)}$.",
        f"y_p = {tex(trial)}",
        {"kind": "trial_form", "key": dump(trial), "coefficients": names, "x": str(x),
         "homogeneous_ode": dump(h_residual), "order": ode.order,
         "aliases": ["y_p", "Y", "y"], "answer_example": f"${example}$"},
        "Match the family of the right side, then compare it to your roots.",
        "Polynomial of degree $m$: use a full polynomial of degree $m$. $\\cos$ or $\\sin$: use both. "
        f"If a term already solves the homogeneous equation, multiply by ${x}$ until none does.",
        trial_note,
    ))
    derivative = trial
    derivatives = [trial]
    for k in range(1, ode.order + 1):
        derivative = sp.diff(derivative, x)
        derivatives.append(derivative)
        primes = "'" * k
        previous = f"y_p{chr(39) * (k - 1)}"
        steps.append(make_step(
            f"y_p_d{k}", "derivative_of_trial", f"Derivative $y_p{primes}$",
            f"Differentiate to get $y_p{primes}$. Keep the coefficients as letters.",
            f"y_p{primes} = {tex(_tidy(derivative, x))}",
            expr_check(derivative, aliases=[f"y_p{primes}", f"Y{primes}", f"y{primes}"], domain=ode.domain),
            f"Differentiate ${previous}$ term by term.",
            "Use the product rule on any product like $xe^{ax}$ or $e^{ax}\\cos bx$.",
        ))
    left = sum(c * d for c, d in zip(coefficients, derivatives))
    left_tidy = _tidy(left, x)
    ode_lhs = " + ".join(f"({tex(c)})\\,y_p{chr(39) * k}" if c != 1 else f"y_p{chr(39) * k}"
                         for k, c in reversed(list(enumerate(coefficients))) if c != 0)
    steps.append(make_step(
        "substitute", "full_substitution", "Full substitution",
        f"Substitute into the left side, ${ode_lhs}$, and collect like functions. Write the full equation with the right side.",
        f"{tex(left_tidy)} = {tex(g)}",
        {"kind": "equation", "key_lhs": dump(left), "key_rhs": dump(g), "domain": ode.domain},
        "Add the pieces, each multiplied by its coefficient from the ODE.",
        f"Group terms by function of ${x}$, because the next step matches each function separately.",
    ))
    equations, functions = uc_equations(left - g, unknowns, x)
    pairs = [(sp.expand(e - e.subs({u: 0 for u in unknowns})), -e.subs({u: 0 for u in unknowns})) for e in equations]
    system_tex = ",\\ ".join(f"{tex(l)} = {tex(r)}" for l, r in pairs)
    steps.append(make_step(
        "match", "coefficient_matching", "Coefficient matching",
        f"Match coefficients of each independent function of ${x}$. Write the resulting equations, separated by commas.",
        system_tex,
        {"kind": "linear_system", "key": [dump(e) for e in equations], "unknowns": names},
        "Each distinct function of $x$ gives its own equation.",
        "Functions like $1$, $x$, $e^{ax}$, $\\cos bx$, $\\sin bx$ are independent, so their coefficients must match separately.",
    ))
    solved = sp.solve(equations, unknowns, dict=True)
    if len(solved) != 1 or len(solved[0]) != len(unknowns):
        raise NotApplicable("the coefficient equations don't have a unique solution")
    values = solved[0]
    if any(v.has(sp.Float) for v in values.values()):
        raise NotApplicable("a coefficient came out as a decimal")
    steps.append(make_step(
        "solve", "coefficient_values", "Solve for the coefficients",
        f"Solve the system. Write ${', '.join(n + ' = ' + chr(92) + 'dots' for n in names)}$ as exact fractions.",
        ",\\ ".join(f"{n} = {tex(values[sp.Symbol(n)])}" for n in names),
        {"kind": "assignments", "key": {n: dump(values[sp.Symbol(n)]) for n in names}},
        "Solve one equation at a time if the system is triangular.",
        "Keep fractions exact throughout. A coefficient of 0 still has to be written.",
    ))
    y_p = trial.subs(values)
    steps.append(make_step(
        "y_p_final", "particular_solution", "Particular solution",
        "Put the coefficients into your trial form.",
        f"y_p = {tex(y_p)}",
        {"kind": "particular_of", "ode": dump(ode.residual), "order": ode.order, "x": str(x),
         "aliases": ["y_p", "Y", "y"], "domain": ode.domain},
        "Substitute the values into the trial form.",
        "You can check it by substituting back into the ODE.",
    ))
    y_general = y_h + y_p
    steps.append(general_step(ode, y_general, "$y = y_h + y_p$"))
    extra, y_ivp = ivp_steps(ode, y_general)
    return plan("undetermined_coefficients", ode, steps + extra, y_general, y_ivp)


# --- variation_of_parameters -------------------------------------------------

def build_variation_of_parameters(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    x = ode.x
    coefficients, g = constant_coefficients(ode)
    if ode.order != 2:
        raise NotApplicable("this template handles second-order ODEs")
    if g == 0:
        raise NotApplicable("the ODE is homogeneous")
    ode.domain = "small_positive"
    leading = coefficients[-1]
    r = sp.simplify(g / leading)
    r_name = notation.get("char", "r").replace("\\", "")
    steps, roots, y_h = char_steps(ode, coefficients, r_name, notation)
    basis = homogeneous_basis(roots, x)
    y1, y2 = basis
    wronskian = sp.simplify(y1 * sp.diff(y2, x) - y2 * sp.diff(y1, x))
    u1_prime = sp.simplify(-y2 * r / wronskian)
    u2_prime = sp.simplify(y1 * r / wronskian)
    u1, u2 = sp.integrate(u1_prime, x), sp.integrate(u2_prime, x)
    if u1.has(sp.Integral) or u2.has(sp.Integral):
        raise NotApplicable("the integrals for u_1 or u_2 have no closed form")
    y_p = sp.simplify(u1 * y1 + u2 * y2)
    pair = f"y_1 = {tex(y1)},\\ y_2 = {tex(y2)}"
    steps += [
        make_step(
            "wronskian", "wronskian", "Wronskian",
            f"With ${pair}$, compute $W = y_1 y_2' - y_2 y_1'$.",
            f"W = {tex(wronskian)}",
            expr_check(wronskian, aliases=["W"], domain=ode.domain),
            "Differentiate $y_1$ and $y_2$ first.",
            "$W = y_1 y_2' - y_2 y_1'$; simplify before moving on.",
        ),
        make_step(
            "u1_prime", "parameter_derivative", "Find $u_1'$",
            f"The ODE in standard form has right side $r(x) = {tex(r)}$. Find $u_1' = -y_2 r / W$.",
            f"u_1' = {tex(u1_prime)}",
            expr_check(u1_prime, aliases=["u_1'"], domain=ode.domain),
            "The leading coefficient has to be 1 before you read off $r(x)$.",
            "$u_1' = -\\frac{y_2\\, r}{W}$.",
        ),
        make_step(
            "u2_prime", "parameter_derivative", "Find $u_2'$",
            "Find $u_2' = y_1 r / W$.",
            f"u_2' = {tex(u2_prime)}",
            expr_check(u2_prime, aliases=["u_2'"], domain=ode.domain),
            "The formula has the same denominator and no minus sign.",
            "$u_2' = \\frac{y_1\\, r}{W}$.",
        ),
        make_step(
            "u1", "integrate_parameter", "Integrate for $u_1$",
            "Integrate $u_1'$. No constant is needed here.",
            f"u_1 = {tex(u1)}",
            {"kind": "antiderivative", "integrand": dump(u1_prime), "var": str(x),
             "aliases": ["u_1"], "domain": ode.domain},
            "Differentiate your answer to check it.",
            "Constants of integration here only re-create $y_h$, so they can be dropped.",
        ),
        make_step(
            "u2", "integrate_parameter", "Integrate for $u_2$",
            "Integrate $u_2'$. No constant is needed here.",
            f"u_2 = {tex(u2)}",
            {"kind": "antiderivative", "integrand": dump(u2_prime), "var": str(x),
             "aliases": ["u_2"], "domain": ode.domain},
            "Differentiate your answer to check it.",
            "Constants of integration here only re-create $y_h$, so they can be dropped.",
        ),
        make_step(
            "y_p_final", "particular_solution", "Particular solution",
            "Form $y_p = u_1 y_1 + u_2 y_2$.",
            f"y_p = {tex(y_p)}",
            {"kind": "particular_of", "ode": dump(ode.residual), "order": 2, "x": str(x),
             "aliases": ["y_p", "y"], "domain": ode.domain},
            "Multiply each $u$ by its $y$ and add.",
            "Simplify if you can, but any equivalent form is accepted.",
        ),
    ]
    y_general = y_h + y_p
    steps.append(general_step(ode, y_general, "$y = y_h + y_p$"))
    extra, y_ivp = ivp_steps(ode, y_general)
    return plan("variation_of_parameters", ode, steps + extra, y_general, y_ivp)


# --- separable ---------------------------------------------------------------

def build_separable(spec: dict, notation: dict) -> dict:
    ode = ODE.parse(spec["statement"], spec.get("ic"))
    if ode.order != 1:
        raise NotApplicable("this method is for first-order ODEs")
    x, y = ode.x, sp.Symbol("y")
    y_prime = dy(1)
    solved = sp.solve(ode.residual, y_prime)
    if len(solved) != 1:
        raise NotApplicable("couldn't solve the ODE for y'")
    slope = sp.simplify(solved[0])
    parts = sp.separatevars(slope, symbols=[x, y], dict=True)
    if not parts:
        raise NotApplicable("the right side doesn't factor into f(x) g(y)")
    f = parts["coeff"] * parts[x]
    g = parts[y]
    h = sp.simplify(1 / g)
    ode.domain = "positive"
    c_name = notation.get("constant", "C")
    left_integral, right_integral = sp.integrate(h, y), sp.integrate(f, x)
    if left_integral.has(sp.Integral) or right_integral.has(sp.Integral):
        raise NotApplicable("an integral in this method has no closed form")
    C = sp.Symbol(c_name)
    relation = sp.Eq(left_integral, right_integral + C)
    steps = [
        make_step(
            "separate", "separation", "Separate the variables",
            f"Write the ODE as $h(y)\\,dy = f({x})\\,d{x}$ with any constant factors on the right. What is $h(y)$?",
            f"h = {tex(h)}",
            expr_check(h, aliases=["h"], domain=ode.domain),
            f"Move everything containing $y$ to the side with $dy$, and everything containing ${x}$ to the other side.",
            "Divide by the $y$ part of the right side.",
        ),
        make_step(
            "right_side", "separation", f"The $f({x})$ side",
            f"What is $f({x})$, the factor multiplying $d{x}$?",
            f"f = {tex(f)}",
            expr_check(f, aliases=["f"], domain=ode.domain),
            "What's left over on the right after moving the $y$ part?",
            "Constants stay on this side.",
        ),
        make_step(
            "int_left", "integrate_both_sides", "Integrate the $y$ side",
            "Evaluate $\\int h(y)\\,dy$. Leave out the constant; it goes on the other side.",
            tex(left_integral),
            {"kind": "antiderivative", "integrand": dump(h), "var": "y", "domain": ode.domain},
            "Differentiate your answer with respect to $y$ to check it.",
            "One constant on the right side is enough.",
        ),
        make_step(
            "int_right", "integrate_both_sides", f"Integrate the ${x}$ side",
            f"Evaluate $\\int f({x})\\,d{x}$, including the arbitrary constant ${c_name}$.",
            f"{tex(right_integral)} + {c_name}",
            {"kind": "antiderivative", "integrand": dump(f), "var": str(x),
             "require_constant": True, "domain": ode.domain},
            f"Differentiate with respect to ${x}$ to check it.",
            "This is where the arbitrary constant comes in.",
        ),
        make_step(
            "general", "general_solution", "General solution",
            "Write the general solution, in implicit form or solved for $y$.",
            f"{tex(left_integral)} = {tex(right_integral)} + {c_name}",
            {"kind": "implicit_solution", "slope": dump(slope), "x": str(x), "domain": ode.domain},
            "Set the two integrals equal.",
            "Either form is fine as long as there's one arbitrary constant.",
        ),
    ]
    return plan("separable", ode, steps, None, None, relation.lhs - relation.rhs, slope)


# --- exact -------------------------------------------------------------------

def parse_exact_statement(statement: str) -> tuple[sp.Expr, sp.Expr]:
    body = clean_latex(statement)
    body = re.sub(r"(?<![a-zA-Z\\])d([xy])(?![a-zA-Z])", r" D_\1 ", body)
    pieces = split_top(body, ("=",))
    if len(pieces) != 2:
        raise NotApplicable("write it as M dx + N dy = 0")
    try:
        expression = to_sympy(pieces[0]) - to_sympy(pieces[1])
    except ParseFailure as failure:
        raise NotApplicable(f"couldn't read the ODE: {failure}") from failure
    dx, dy_symbol = sp.Symbol("D_x"), sp.Symbol("D_y")
    expression = sp.expand(expression)
    M, N = sp.diff(expression, dx), sp.diff(expression, dy_symbol)
    if (M.free_symbols | N.free_symbols) & {dx, dy_symbol}:
        raise NotApplicable("write it as M dx + N dy = 0")
    return sp.simplify(M), sp.simplify(N)


def build_exact(spec: dict, notation: dict) -> dict:
    M, N = parse_exact_statement(spec["statement"])
    x, y = sp.Symbol("x"), sp.Symbol("y")
    if sp.simplify(sp.diff(M, y) - sp.diff(N, x)) != 0:
        raise NotApplicable("the equation isn't exact: M_y differs from N_x")
    M_y = sp.simplify(sp.diff(M, y))
    N_x = sp.simplify(sp.diff(N, x))
    inner = sp.integrate(M, x)
    k_prime = sp.simplify(N - sp.diff(inner, y))
    if k_prime.has(x):
        raise NotApplicable("k'(y) still depends on x, which shouldn't happen for an exact equation")
    k = sp.integrate(k_prime, y)
    if inner.has(sp.Integral) or k.has(sp.Integral):
        raise NotApplicable("an integral in this method has no closed form")
    potential = inner + k
    slope = sp.simplify(-M / N)
    c_name = notation.get("constant", "C")
    C = sp.Symbol(c_name)
    residual = M + N * dy(1)
    ode = ODE(spec["statement"], x, residual, 1, [], "positive")
    steps = [
        make_step(
            "M_y", "exactness_test", "Exactness test: $M_y$",
            f"With $M = {tex(M)}$ and $N = {tex(N)}$, compute $\\partial M/\\partial y$.",
            f"M_y = {tex(M_y)}",
            expr_check(M_y, aliases=["M_y"], domain=ode.domain),
            "Treat $x$ as a constant.",
            "Exact means $M_y = N_x$.",
        ),
        make_step(
            "N_x", "exactness_test", "Exactness test: $N_x$",
            "Compute $\\partial N/\\partial x$.",
            f"N_x = {tex(N_x)}",
            expr_check(N_x, aliases=["N_x"], domain=ode.domain),
            "Treat $y$ as a constant.",
            "It should match $M_y$.",
        ),
        make_step(
            "int_M", "potential_integration", "Integrate $M$",
            "Integrate $M$ with respect to $x$, leaving out the function $k(y)$ for now.",
            tex(inner),
            {"kind": "antiderivative", "integrand": dump(M), "var": "x", "parameters": ["y"], "domain": ode.domain},
            "Treat $y$ as a constant.",
            "$F = \\int M\\,dx + k(y)$.",
        ),
        make_step(
            "k_prime", "potential_integration", "Find $k'(y)$",
            "Differentiate your $F$ with respect to $y$ and set it equal to $N$. What is $k'(y)$?",
            f"k' = {tex(k_prime)}",
            expr_check(k_prime, aliases=["k'"], domain=ode.domain),
            "$F_y$ must equal $N$.",
            "$k'(y) = N - \\frac{\\partial}{\\partial y} \\int M\\,dx$.",
        ),
        make_step(
            "k", "potential_integration", "Find $k(y)$",
            "Integrate to get $k(y)$. No constant is needed here.",
            f"k = {tex(k)}",
            {"kind": "antiderivative", "integrand": dump(k_prime), "var": "y", "aliases": ["k"], "domain": ode.domain},
            "It depends only on $y$.",
            "The arbitrary constant appears at the very end, in $F(x,y) = C$.",
        ),
        make_step(
            "general", "general_solution", "General solution",
            "Write the general solution in the form $F(x, y) = C$.",
            f"{tex(potential)} = {c_name}",
            {"kind": "implicit_solution", "slope": dump(slope), "x": "x", "domain": ode.domain},
            "Put $\\int M\\,dx$ and $k(y)$ together.",
            "$F(x, y) = C$ is the answer, no need to solve for $y$.",
        ),
    ]
    return plan("exact", ode, steps, None, None, potential - C, slope)


# --- Registry ----------------------------------------------------------------

@dataclass
class Method:
    id: str
    name: str
    description: str
    handles: str
    limits: str
    step_types: list[str]
    build: Callable[[dict, dict], dict]
    samples: list[dict]
    origin: str = "built-in"

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "handles": self.handles, "limits": self.limits,
                "step_types": self.step_types, "origin": self.origin}


CHAR_TYPES = ["characteristic_equation", "characteristic_roots", "homogeneous_solution"]

BUILT_IN = [
    Method(
        "first_order_linear", "First-order linear (integrating factor)",
        "Standard form, integrating factor, integrate both sides.",
        "y' + p(x)y = r(x), any p and r with closed-form integrals, with or without an initial condition",
        "first order only; p and r must be integrable in closed form",
        ["standard_form", "identify_coefficients", "integrating_factor", "multiply_through",
         "integrate_both_sides", "general_solution", "apply_initial_condition"],
        build_first_order_linear,
        [{"statement": "y'+2y=e^{x}"}, {"statement": "xy'+y=x^2", "ic": ["y(1)=1"]},
         {"statement": "y'-y=\\sin x"}],
    ),
    Method(
        "constant_coefficient_homogeneous", "Constant-coefficient homogeneous (characteristic equation)",
        "Characteristic equation and roots, then the form for distinct, repeated, or complex roots.",
        "a_n y^(n) + ... + a_0 y = 0 with constant coefficients, order 2 and up",
        "the characteristic polynomial must have closed-form roots",
        CHAR_TYPES[:2] + ["general_solution", "apply_initial_condition"],
        build_constant_coefficient,
        [{"statement": "y''+3y'+2y=0"}, {"statement": "y''+2y'+y=0", "ic": ["y(0)=1", "y'(0)=0"]},
         {"statement": "y''+2y'+5y=0"}],
    ),
    Method(
        "undetermined_coefficients", "Undetermined coefficients",
        "y_h, then trial y_p, y_p', y_p'', full substitution, coefficient matching, in separate steps.",
        "constant-coefficient linear ODEs whose right side is a sum of polynomial, exponential, sine and cosine products, "
        "including the modification rule",
        "right sides such as sec x, tan x, ln x or 1/x need variation_of_parameters",
        CHAR_TYPES + ["trial_form", "derivative_of_trial", "full_substitution", "coefficient_matching",
                      "coefficient_values", "particular_solution", "general_solution", "apply_initial_condition"],
        build_undetermined_coefficients,
        [{"statement": "y''+3y'+2y=3e^{2x}"}, {"statement": "y''-y=e^{x}"},
         {"statement": "y''+4y=\\cos 2x"}, {"statement": "y''+y'=x^2+1", "ic": ["y(0)=0", "y'(0)=1"]}],
    ),
    Method(
        "variation_of_parameters", "Variation of parameters",
        "y_h, Wronskian, u_1' and u_2', integrate, y_p = u_1 y_1 + u_2 y_2.",
        "second-order constant-coefficient linear ODEs with any right side whose integrals have closed form",
        "second order with constant coefficients only in this template",
        CHAR_TYPES + ["wronskian", "parameter_derivative", "integrate_parameter", "particular_solution", "general_solution"],
        build_variation_of_parameters,
        [{"statement": "y''+y=\\sec x"}, {"statement": "y''-2y'+y=\\frac{e^{x}}{x}"}],
    ),
    Method(
        "separable", "Separable equations",
        "Separate variables, integrate each side, general solution in implicit or explicit form.",
        "y' = f(x) g(y)",
        "first order; no initial-condition step in this template",
        ["separation", "integrate_both_sides", "general_solution"],
        build_separable,
        [{"statement": "y'=xy"}, {"statement": "y'=\\frac{x^2}{y}"}],
    ),
    Method(
        "exact", "Exact equations",
        "Test M_y = N_x, integrate M, find k(y), write F(x, y) = C.",
        "M dx + N dy = 0 with M_y = N_x",
        "no integrating-factor reduction for non-exact equations yet; no initial-condition step",
        ["exactness_test", "potential_integration", "general_solution"],
        build_exact,
        [{"statement": "(2xy+1)dx+(x^2+3)dy=0"}, {"statement": "(y+2x)dx+(x+2y)dy=0"}],
    ),
]


def load_repertoire_module(path: Path) -> Method:
    module_name = f"repertoire_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"can't load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    method = getattr(module, "METHOD", None)
    if not isinstance(method, Method):
        raise ImportError(f"{path.name} must define METHOD = Method(...)")
    method.origin = "added"
    return method


def all_methods(validated_paths: list[Path] | None = None) -> dict[str, Method]:
    from methods_extra import METHODS as EXTRA  # It builds on this module.
    methods = {m.id: m for m in [*BUILT_IN, *EXTRA]}
    for path in validated_paths or []:
        method = load_repertoire_module(path)
        methods[method.id] = method
    return methods
