"""The parser decides what a student's answer means, so it is tested hardest."""
import random

import pytest
import sympy as sp

from equiv import compare
from mathparse import ParseFailure, has_decimal, parse_answer, parse_list, to_sympy

x, y, t, r, A, B, c1, c2 = sp.symbols("x y t r A B c_1 c_2")
yp, ypp = sp.Symbol("y'"), sp.Symbol("y''")
E = sp.E

HAND_CASES = [
    # Implicit multiplication next to functions: what lark got wrong.
    (r"A\cos 2x+B\sin 2x", A * sp.cos(2 * x) + B * sp.sin(2 * x)),
    (r"\cos x \sin x", sp.cos(x) * sp.sin(x)),
    (r"e^x\sin 2x", sp.exp(x) * sp.sin(2 * x)),
    (r"\sin 2x e^{x}", sp.sin(2 * x) * sp.exp(x)),
    (r"e^{x}e^{x}", sp.exp(2 * x)),
    (r"\cosh x", sp.cosh(x)),
    (r"\sinh 2x+\cosh 2x", sp.sinh(2 * x) + sp.cosh(2 * x)),
    (r"xe^{-x}\cos 3x", x * sp.exp(-x) * sp.cos(3 * x)),
    (r"(A+Bx)e^{2x}", (A + B * x) * sp.exp(2 * x)),
    # Fractions and exact numbers.
    (r"\frac{1}{2}y", y / 2),
    (r"y/2", y / 2),
    (r"\frac12", sp.Rational(1, 2)),
    (r"\frac12x", x / 2),
    (r"\tfrac{3}{4}e^{2x}", sp.Rational(3, 4) * sp.exp(2 * x)),
    (r"-\frac{3}{4}e^{2x}", -sp.Rational(3, 4) * sp.exp(2 * x)),
    (r"1/2x", x / 2),
    # Euler's e and powers.
    (r"e^{rt}", sp.exp(r * t)),
    (r"\exp(2x)", sp.exp(2 * x)),
    (r"\exponentialE^{2x}", sp.exp(2 * x)),
    (r"\mathrm{e}^{2x}", sp.exp(2 * x)),
    (r"x^2y", x**2 * y),
    (r"x^{-1}", 1 / x),
    (r"-x^2", -(x**2)),
    (r"2^{x}", 2**x),
    # Primes, in every spelling MathLive or a person might use.
    (r"y'", yp),
    (r"y''", ypp),
    (r"y^{\prime}", yp),
    (r"y^\prime", yp),
    (r"y^{\prime\prime}", ypp),
    (r"y^{\doubleprime}", ypp),
    (r"y_p^{\doubleprime}+y_p^{\prime}", sp.Symbol("y_p''") + sp.Symbol("y_p'")),
    (r"y''+3y'+2y", ypp + 3 * yp + 2 * y),
    (r"\frac{dy}{dx}", yp),
    (r"\frac{d^2y}{dx^2}+y", ypp + y),
    (r"y_p'", sp.Symbol("y_p'")),
    (r"y_{p}''", sp.Symbol("y_p''")),
    (r"y_p^{\prime}", sp.Symbol("y_p'")),
    # What MathLive really writes for typed input, captured from the browser.
    (r"\sin\,x", sp.sin(x)),
    (r"\tan\,x", sp.tan(x)),
    (r"A\,\cos\,2x\,+\,B\,\sin\,2x", A * sp.cos(2 * x) + B * sp.sin(2 * x)),
    (r"e^{x}\sin\,x", sp.exp(x) * sp.sin(x)),
    (r"e^{-x}\left(A\,\cos2x+B\,\sin2x\right)", sp.exp(-x) * (A * sp.cos(2 * x) + B * sp.sin(2 * x))),
    (r"y_{p^{\doubleprime}}", sp.Symbol("y_p''")),
    (r"y_{p^{\prime}}", sp.Symbol("y_p'")),
    (r"\ln\left|x\right|", sp.log(sp.Abs(x))),
    (r"\left|x\right|+1", sp.Abs(x) + 1),
    (r"\sqrt{\left(x\right)}", sp.sqrt(x)),
    (r"\frac14e^{2x}", sp.Rational(1, 4) * sp.exp(2 * x)),
    (r"\sin^2x", sp.sin(x) ** 2),
    # Function notation is not multiplication.
    (r"\mu(x)", sp.Symbol("mu")),
    (r"y(x)", y),
    (r"y'(x)", yp),
    (r"p(x)y", sp.Symbol("p") * y),
    (r"x(x+1)", x * (x + 1)),
    # Subscripts.
    (r"c_1e^{-x}+c_2e^{-2x}", c1 * sp.exp(-x) + c2 * sp.exp(-2 * x)),
    (r"c_{1}e^{-x}", c1 * sp.exp(-x)),
    (r"C_1", sp.Symbol("C_1")),
    # Everything on the cheat sheet.
    (r"\sqrt{x}", sp.sqrt(x)),
    (r"\sqrt[3]{x}", x ** sp.Rational(1, 3)),
    (r"\sin^2 x", sp.sin(x) ** 2),
    (r"\sin^{2}(x)", sp.sin(x) ** 2),
    (r"\sin^{-1}x", sp.asin(x)),
    (r"\int x dx", x**2 / 2),
    (r"\int e^{2x}\,dx", sp.exp(2 * x) / 2),
    (r"\int_0^1 x\,dx", sp.Rational(1, 2)),
    (r"\ln|x|", sp.log(sp.Abs(x))),
    (r"\ln x", sp.log(x)),
    (r"\left|x\right|", sp.Abs(x)),
    (r"\arctan x", sp.atan(x)),
    (r"\sec x\tan x", sp.sec(x) * sp.tan(x)),
    (r"\pi", sp.pi),
    (r"\sin\pi x", sp.sin(sp.pi * x)),
    (r"\sin\frac{x}{2}", sp.sin(x / 2)),
    (r"\alpha+\beta", sp.Symbol("alpha") + sp.Symbol("beta")),
    (r"\lambda^2+1", sp.Symbol("lambda") ** 2 + 1),
    (r"3\cdot 4", sp.Integer(12)),
    (r"(x+1)(x-1)", (x + 1) * (x - 1)),
    (r"2(x+1)", 2 * (x + 1)),
    (r"--x", x),
]


@pytest.mark.parametrize("latex,expected", HAND_CASES, ids=[c[0] for c in HAND_CASES])
def test_reads_hand_cases(latex, expected):
    got = to_sympy(latex)
    assert compare(got, expected).equal, f"{latex} read as {got}"


@pytest.mark.parametrize("bad", [r"\int_{\,x\,dx}^{\placeholder{}}", r"y_{h=c_1e^{x}}", r"\frac{1}{", r"(x+1", r"x+", r"\unknownmacro{x}", r"\placeholder{}+1",
                                 r"\frac{1}{0}", r"\sin", r"\int x", "@"])
def test_refuses_what_it_cannot_read(bad):
    with pytest.raises(ParseFailure):
        to_sympy(bad)


def test_every_sympy_latex_roundtrips():
    """Random expressions, rendered by SymPy and read back, must be unchanged."""
    rng = random.Random(692)
    atoms = [x, sp.Rational(1, 2), sp.Rational(-3, 4), sp.Integer(2), A, B, c1]
    functions = [sp.sin, sp.cos, sp.exp, sp.sinh, sp.cosh, sp.tan, sp.atan,
                 lambda u: sp.log(sp.Abs(u)), sp.sqrt]

    def build(depth):
        if depth == 0:
            return rng.choice(atoms)
        roll = rng.random()
        if roll < 0.35:
            return build(depth - 1) + build(depth - 1)
        if roll < 0.7:
            return build(depth - 1) * build(depth - 1)
        if roll < 0.85:
            return rng.choice(functions)(rng.randint(1, 3) * x + build(depth - 1))
        return build(depth - 1) ** rng.randint(2, 3)

    tried = 0
    for _ in range(300):
        expr = build(rng.randint(1, 3))
        if expr.has(sp.zoo, sp.nan):
            continue
        rendered = sp.latex(expr)
        tried += 1
        read = to_sympy(rendered)
        assert compare(read, expr, prove=False).equal, f"{expr} -> {rendered} -> {read}"
    assert tried > 200


def test_splits_equations_on_top_level_equals():
    assert len(parse_answer(r"y=e^{2x}=e^{x}e^{x}").parts) == 3
    assert not parse_answer(r"e^{2x}").is_equation


@pytest.mark.parametrize("latex,count", [
    (r"-1,-2", 2), (r"r=-1,\ r=-2", 2), (r"r_1=-1, r_2=-2", 2), (r"-1\pm 2i", 2),
    (r"-1\text{ and }-2", 2), (r"A=\frac14, B=0", 2), (r"\{1,2\}", 2), (r"\pm 2", 2),
])
def test_splits_lists(latex, count):
    assert len(parse_list(latex, allow_i=True)) == count


def test_plus_minus_expands_to_conjugates():
    items = parse_list(r"-1\pm 2i", allow_i=True)
    assert {i.parts[0] for i in items} == {-1 + 2 * sp.I, -1 - 2 * sp.I}


def test_flags_decimals_only_when_they_are_decimals():
    assert has_decimal(r"0.5x")
    assert has_decimal(r".25")
    assert not has_decimal(r"\frac{1}{2}x")
    assert not has_decimal(r"x_1")


def test_equivalence_verdicts_are_honest():
    assert compare(x / 2, sp.Rational(1, 2) * x).verdict == "proved"
    assert compare(sp.sin(x) ** 2 + sp.cos(x) ** 2, sp.Integer(1)).verdict == "proved"
    assert compare(x, x + 1).verdict == "different"
    assert compare(sp.exp(x), sp.exp(x) + sp.Rational(1, 10**20)).verdict == "different"
    # sqrt(x^2) is |x|, not x; only x > 0 makes them equal.
    assert compare(sp.sqrt(x**2), x).verdict == "different"
    assert compare(sp.sqrt(x**2), x, domain="positive").equal
    assert compare(sp.log(sp.Abs(x)), sp.log(x), domain="positive").equal


@pytest.mark.parametrize("latex,name", [
    (r"y\left(x\right)", "y"),
    (r"y\left(t\right)", "y"),
    (r"y_1\left(t\right)", "y_1"),
    (r"y_{p}\left(x\right)", "y_p"),
    (r"\mu\left(x\right)", "mu"),
    (r"u_1\left(x\right)", "u_1"),
    (r"y\mleft(x\mright)", "y"),
])
def test_function_notation_from_the_answer_box_is_not_a_product(latex, name):
    assert to_sympy(latex) == sp.Symbol(name), latex


def test_a_real_product_with_parentheses_stays_a_product():
    assert to_sympy(r"y\left(x+1\right)") == sp.Symbol("y") * (sp.Symbol("x") + 1)
    assert to_sympy(r"x\left(y\right)") == sp.Symbol("x") * sp.Symbol("y")
