"""Deciding whether two expressions are equal, as rigorously as can be done.

The order matters. Evaluating both sides at exact rational points to 60 digits
goes first: a real difference there is a rigorous disproof, and it is fast.
A symbolic proof comes next and may not finish, because simplify has no time
bound on hard input. If it runs out of time, agreement at every point to 45
digits stands as the answer, and the result says "numeric" rather than "proved"
so nothing claims more than was shown.
"""
from __future__ import annotations

import random
import signal
import threading
from dataclasses import dataclass
from fractions import Fraction

import sympy as sp

PROVED = "proved"
NUMERIC = "numeric"
DIFFERENT = "different"
UNDECIDED = "undecided"

DIGITS = 60
TOLERANCE = sp.Rational(1, 10) ** 45
POINTS = 12
PROOF_SECONDS = 5


class _OutOfTime(Exception):
    pass


class time_limit:
    """Interrupts symbolic work on the worker's main thread; a no-op elsewhere."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.armed = threading.current_thread() is threading.main_thread()

    def __enter__(self):
        if self.armed:
            signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def _fire(self, *_):
        raise _OutOfTime()

    def __exit__(self, kind, value, trace):
        if self.armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return kind is _OutOfTime


@dataclass
class Compared:
    verdict: str
    witness: dict | None = None
    detail: str = ""

    @property
    def equal(self) -> bool:
        return self.verdict in (PROVED, NUMERIC)


def sample_points(symbols, count: int, domain: str, seed: int = 20261101):
    """Exact rational points. Fixed seed, so a grade can always be reproduced."""
    rng = random.Random(seed)
    ordered = sorted(symbols, key=lambda s: s.name)
    points = []
    for _ in range(count):
        point = {}
        for symbol in ordered:
            value = Fraction(rng.randint(7, 61), rng.randint(11, 29))
            if domain == "small_positive":
                # Keeps sec, tan and ln(cos) on one branch, away from their poles.
                value = Fraction(rng.randint(1, 13), 10)
            elif domain == "real" and rng.random() < 0.5:
                value = -value
            point[symbol] = sp.Rational(value.numerator, value.denominator)
        points.append(point)
    return points


def evaluate(expr: sp.Expr, point: dict):
    try:
        value = expr.subs(point)
        if value.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
            return None
        value = sp.N(value, DIGITS, maxn=800)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError, AttributeError, RecursionError):
        return None
    if not value.is_number or value.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        return None
    return value


def _close(a, b) -> bool:
    scale = max(sp.Integer(1), abs(a), abs(b))
    return bool(abs(a - b) <= TOLERANCE * scale)


def prove_zero(difference: sp.Expr, seconds: float = PROOF_SECONDS) -> bool:
    attempts = (
        lambda d: sp.expand(d),
        lambda d: sp.cancel(sp.together(d)),
        lambda d: sp.trigsimp(sp.expand(d)),
        lambda d: sp.powsimp(sp.expand(d), force=True),
        lambda d: sp.logcombine(sp.expand_log(d, force=True), force=True),
        lambda d: sp.expand(d.rewrite(sp.exp)),
        lambda d: sp.simplify(d),
    )
    with time_limit(seconds):
        for attempt in attempts:
            if attempt(difference) == 0:
                return True
        if difference.equals(0) is True:
            return True
    return False


def compare(a: sp.Expr, b: sp.Expr, domain: str = "real", prove: bool = True) -> Compared:
    a, b = sp.sympify(a), sp.sympify(b)
    if a == b:
        return Compared(PROVED)
    symbols = (a.free_symbols | b.free_symbols)
    if not symbols:
        points = [{}]
    else:
        points = sample_points(symbols, POINTS, domain)
    agreed = 0
    for point in points:
        left, right = evaluate(a, point), evaluate(b, point)
        if left is None or right is None:
            continue
        if not _close(left, right):
            return Compared(DIFFERENT, {str(k): str(v) for k, v in point.items()},
                            f"at this point the two sides are {sp.N(left, 8)} and {sp.N(right, 8)}")
        agreed += 1
    if prove and prove_zero(a - b):
        return Compared(PROVED)
    if agreed >= min(4, len(points)):
        return Compared(NUMERIC, detail=f"equal at {agreed} exact points to 45 digits")
    return Compared(UNDECIDED, detail="too few points where both sides are defined")


def is_zero(expr: sp.Expr, domain: str = "real") -> Compared:
    return compare(expr, sp.Integer(0), domain)


def numeric_rank(rows: list[list[sp.Expr]]) -> int:
    """Rank of a matrix of numbers known to 60 digits, by elimination with pivoting."""
    matrix = [[sp.N(v, DIGITS) for v in row] for row in rows]
    if not matrix:
        return 0
    rank, cols = 0, len(matrix[0])
    for col in range(cols):
        pivot = max(range(rank, len(matrix)), key=lambda r: abs(matrix[r][col]), default=None)
        if pivot is None or abs(matrix[pivot][col]) < sp.Rational(1, 10) ** 35:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        for r in range(rank + 1, len(matrix)):
            factor = matrix[r][col] / matrix[rank][col]
            matrix[r] = [matrix[r][c] - factor * matrix[rank][c] for c in range(cols)]
        rank += 1
        if rank == len(matrix):
            break
    return rank
