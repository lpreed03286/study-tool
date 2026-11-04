"""Linear systems y' = Ay with 2x2 constant A, and their critical points.

Criteria and eigenvalues classify a critical point by separate routes. The method
uses the p, q, Delta table (Kreyszig 4.4) and verify.py checks it against the
eigenvalues, so an error in either one is caught before anyone sees the key.
"""
from __future__ import annotations

import re

import sympy as sp

from mathparse import ParseFailure, clean_latex, split_top, to_sympy

IMPROPER, PROPER, DEGENERATE = "Improper node", "Proper node", "Degenerate node"
SADDLE, CENTER, SPIRAL = "Saddle point", "Center", "Spiral point"
LINE = "Line of critical points"
TYPES = [IMPROPER, PROPER, DEGENERATE, SADDLE, CENTER, SPIRAL, LINE]

ATTRACTIVE, STABLE, UNSTABLE = "Stable and attractive", "Stable but not attractive", "Unstable"
STABILITIES = [ATTRACTIVE, STABLE, UNSTABLE]

Y1, Y2 = sp.Symbol("y_1"), sp.Symbol("y_2")
Y1P, Y2P = sp.Symbol("y_1'"), sp.Symbol("y_2'")


class NotASystem(Exception):
    pass


def parse_system(statement: str) -> sp.Matrix:
    """y_1' = a y_1 + b y_2, y_2' = c y_1 + d y_2 to the matrix A."""
    pieces = [p for p in split_top(clean_latex(statement), (",", ";")) if p]
    if len(pieces) != 2:
        raise NotASystem("write two equations separated by a comma")
    rows: dict[sp.Symbol, sp.Expr] = {}
    for piece in pieces:
        sides = split_top(piece, ("=",))
        if len(sides) != 2:
            raise NotASystem("each equation needs one =")
        try:
            left, right = to_sympy(sides[0]), to_sympy(sides[1])
        except ParseFailure as failure:
            raise NotASystem(f"couldn't read the system: {failure}") from failure
        if left not in (Y1P, Y2P):
            raise NotASystem("each left side should be y_1' or y_2'")
        rows[left] = sp.expand(right)
    if set(rows) != {Y1P, Y2P}:
        raise NotASystem("the system needs one equation for y_1' and one for y_2'")
    matrix = []
    for left in (Y1P, Y2P):
        right = rows[left]
        row = [sp.diff(right, Y1), sp.diff(right, Y2)]
        if right.subs({Y1: 0, Y2: 0}) != 0:
            raise NotASystem("this template handles homogeneous systems; the right side has a forcing term")
        if any(entry.free_symbols for entry in row):
            raise NotASystem("the coefficients have to be constants")
        matrix.append(row)
    return sp.Matrix(matrix)


def parse_system_ic(texts: list[str]) -> tuple[sp.Expr, list[sp.Expr]]:
    values: dict[int, sp.Expr] = {}
    starts = set()
    for text in texts:
        match = re.fullmatch(r"\s*y_\{?([12])\}?\(([^)]*)\)\s*=\s*(.+)", clean_latex(text))
        if not match:
            raise NotASystem(f"couldn't read the initial condition {text!r}: use y_1(0)=1")
        starts.add(to_sympy(match.group(2)))
        values[int(match.group(1))] = to_sympy(match.group(3))
    if set(values) != {1, 2} or len(starts) != 1:
        raise NotASystem("give y_1 and y_2 at the same time")
    return starts.pop(), [values[1], values[2]]


def criteria(A: sp.Matrix) -> tuple[sp.Expr, sp.Expr, sp.Expr]:
    p = A.trace()
    q = A.det()
    return p, q, sp.nsimplify(p**2 - 4 * q)


def classify_by_criteria(A: sp.Matrix) -> tuple[str, str]:
    """Kreyszig's table in 4.4, in terms of p, q and Delta."""
    p, q, delta = criteria(A)
    if q == 0:
        if p == 0:
            raise NotASystem("both eigenvalues are 0, which the criteria don't cover")
        return LINE, STABLE if p < 0 else UNSTABLE
    if q < 0:
        return SADDLE, UNSTABLE
    if p == 0:
        return CENTER, STABLE
    stability = ATTRACTIVE if p < 0 else UNSTABLE
    if delta < 0:
        return SPIRAL, stability
    if delta > 0:
        return IMPROPER, stability
    return (PROPER if A[0, 1] == 0 and A[1, 0] == 0 else DEGENERATE), stability


def classify_by_eigenvalues(A: sp.Matrix) -> tuple[str, str]:
    """The same question from the eigenvalues, for the verifier."""
    eigenvalues = list(A.eigenvals(multiple=True))
    one, two = eigenvalues
    real_one, real_two = sp.re(one), sp.re(two)
    if sp.im(one) != 0:
        if real_one == 0:
            return CENTER, STABLE
        return SPIRAL, ATTRACTIVE if real_one < 0 else UNSTABLE
    if one == 0 or two == 0:
        other = two if one == 0 else one
        return LINE, STABLE if other < 0 else UNSTABLE
    if real_one * real_two < 0:
        return SADDLE, UNSTABLE
    stability = ATTRACTIVE if real_one < 0 else UNSTABLE
    if one != two:
        return IMPROPER, stability
    independent = len((A - one * sp.eye(2)).nullspace())
    return (PROPER if independent == 2 else DEGENERATE), stability


def integer_vector(vector: sp.Matrix) -> sp.Matrix:
    """A tidy eigenvector: whole numbers where possible, leading entry positive."""
    denominators = [sp.fraction(sp.nsimplify(entry))[1] for entry in vector]
    scale = sp.ilcm(*denominators) if all(d.is_Integer for d in denominators) else 1
    scaled = (vector * scale).applyfunc(sp.simplify)
    numbers = [e for e in scaled if e.is_Integer and e != 0]
    if len(numbers) == len([e for e in scaled if e != 0]) and numbers:
        scaled = scaled / sp.igcd(*numbers)
    first = next((e for e in scaled if e != 0), 1)
    if first.is_real and first < 0:
        scaled = -scaled
    return scaled.applyfunc(sp.simplify)
