"""LaTeX from MathLive (or typed by hand) to SymPy.

This is a recursive-descent parser written for ODE notation, not parse_latex.
SymPy's lark backend reads A\\cos 2x+B\\sin 2x as A*cos(B*sin(2x)+2x) and
\\cosh x as cos(h*x), and rejects e^{x}e^{x}, which would mark correct
undetermined-coefficients answers wrong.

A function written without parentheses takes as its argument an optional
number followed by plain letters: \\sin 2x e^{x} is sin(2x)*e^x and
\\cos x \\sin x is cos(x)*sin(x). Division is left to right, so 1/2x is x/2.
The grader shows its reading of every wrong answer, so a surprise here can be seen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sympy as sp


class ParseFailure(Exception):
    pass


DECIMAL = re.compile(r"(?<![\w}])\d*\.\d+")

# Names that mean "a function of x" in this course's notation, never a product.
FUNCTION_NAMES = (
    r"y_\{?[a-z0-9]+\}?", r"u_\{?[0-9]\}?", r"W_\{?[0-9]\}?", r"\\mu", r"\\phi", r"\\psi",
    "y", "u", "v", "p", "q", "r", "f", "g", "h", "k", "F", "G", "M", "N", "W",
)
_FUNC_CALL = re.compile(
    r"(?<![a-zA-Z\\])(?P<name>" + "|".join(FUNCTION_NAMES) + r")(?P<primes>'*)\s*\(\s*(?P<var>x|t|y)\s*\)"  # Spaces: clean_latex turns \\left( into " (".
)

_SPACING = [r"\left", r"\right", r"\mleft", r"\mright", r"\displaystyle", r"\textstyle",
            r"\bigl", r"\bigr", r"\Bigl", r"\Bigr", r"\big", r"\Big",
            r"\,", r"\;", r"\:", r"\!", r"\ ", r"\quad", r"\qquad", "~", r"\limits", r"\nolimits"]
_RENAMES = [
    (r"\dfrac", r"\frac"), (r"\tfrac", r"\frac"), (r"\cfrac", r"\frac"),
    (r"\exponentialE", "e"), (r"\imaginaryI", "i"), (r"\differentialD", "d"),
    (r"\lvert", "|"), (r"\rvert", "|"), (r"\vert", "|"),
    (r"\lbrack", "("), (r"\rbrack", ")"), (r"\times", r"\cdot"), ("\u2212", "-"),
]

_FUNCTIONS = {
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "sec": sp.sec, "csc": sp.csc, "cot": sp.cot,
    "arcsin": sp.asin, "arccos": sp.acos, "arctan": sp.atan,
    "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
    "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
    "ln": sp.log, "log": sp.log, "exp": sp.exp,
}
_INVERSES = {"sin": sp.asin, "cos": sp.acos, "tan": sp.atan}
_GREEK = {"alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta",
          "kappa", "lambda", "mu", "nu", "xi", "rho", "sigma", "tau", "phi", "varphi", "chi",
          "psi", "omega", "Delta", "Phi", "Psi", "Omega"}


def clean_latex(text: str) -> str:
    s = text.strip()
    if r"\placeholder" in s:
        raise ParseFailure("the answer still has an empty box in it")
    s = s.replace("\\\\", ";")  # A row break. Before spacing, whose "\\ " would eat half of it.
    s = re.sub(r"\\(?:mathrm|mathit|operatorname|mathbf)\{([a-zA-Z]+)\}", _roman, s)
    for token in sorted(_SPACING, key=len, reverse=True):
        s = _drop_token(s, token)
    for old, new in _RENAMES:
        s = _swap_token(s, old, new)
    s = re.sub(r"\^\{?\\tripleprime\}?", "'''", s)
    s = re.sub(r"\^\{?\\doubleprime\}?", "''", s)
    s = re.sub(r"\^\{?\\prime\\prime\\prime\}?", "'''", s)
    s = re.sub(r"\^\{?\\prime\\prime\}?", "''", s)
    s = re.sub(r"\^\{?\\prime\}?", "'", s)
    s = re.sub(r"\^\{('+)\}", r"\1", s)
    # Typing y_p'' without leaving the subscript puts the primes inside it.
    s = re.sub(r"_\{([a-zA-Z0-9]+)('+)\}", r"_{\1}\2", s)
    s = re.sub(r"_\{([a-zA-Z0-9]+)\^\{?('+)\}?\}", r"_{\1}\2", s)
    s = re.sub(r"\\frac\{d\^\{?2\}?([a-z])\}\{d([a-z])\^\{?2\}?\}", r"\1''", s)
    s = re.sub(r"\\frac\{d([a-z])\}\{d([a-z])\}", r"\1'", s)
    s = s.rstrip(".,; ")
    return s.strip()


def _roman(match: re.Match) -> str:
    word = match.group(1)
    if word in _FUNCTIONS:
        return "\\" + word
    return word if len(word) == 1 else match.group(0)


def _drop_token(s: str, token: str) -> str:
    # A space, never nothing: \\sin\\,x must not fuse into the command \\sinx.
    if token[-1].isalpha():
        return re.sub(re.escape(token) + r"(?![a-zA-Z])", " ", s)
    return s.replace(token, " ")


def _swap_token(s: str, old: str, new: str) -> str:
    if old[-1].isalpha():
        return re.sub(re.escape(old) + r"(?![a-zA-Z])", lambda _: new, s)
    return s.replace(old, new)


def has_decimal(text: str) -> bool:
    return bool(DECIMAL.search(text))


def split_top(s: str, separators: tuple[str, ...]) -> list[str]:
    """Split on any separator outside (), {} and []."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(s):
        ch = s[i]
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        elif depth == 0:
            for sep in separators:
                if s.startswith(sep, i):
                    parts.append(s[start:i])
                    i += len(sep)
                    start = i
                    break
            else:
                i += 1
                continue
            continue
        i += 1
    parts.append(s[start:])
    return [p.strip() for p in parts]


_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<number>\d+(?:\.\d*)?|\.\d+)
  | (?P<command>\\[a-zA-Z]+)
  | (?P<brace>\\[{}])
  | (?P<letter>[a-zA-Z])
  | (?P<symbol>[-+*/^_(){}\[\]|'])
""", re.VERBOSE)


def _tokenise(text: str) -> list[tuple[str, str]]:
    tokens, position = [], 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise ParseFailure(f"can't read the character {text[position]!r}")
        position = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        tokens.append((kind, match.group()))
    return tokens


class _Parser:
    def __init__(self, text: str, allow_i: bool):
        self.tokens = _tokenise(text)
        self.at = 0
        self.allow_i = allow_i
        self.abs_depth = 0
        self.integral_depth = 0

    def peek(self, ahead: int = 0) -> tuple[str, str] | None:
        index = self.at + ahead
        return self.tokens[index] if index < len(self.tokens) else None

    def value(self, ahead: int = 0) -> str | None:
        token = self.peek(ahead)
        return token[1] if token else None

    def advance(self) -> tuple[str, str]:
        token = self.tokens[self.at]
        self.at += 1
        return token

    def expect(self, text: str) -> None:
        if self.value() != text:
            raise ParseFailure(f"expected {text!r} but found {self.value()!r}")
        self.at += 1

    def parse(self) -> sp.Expr:
        result = self.expression()
        if self.at != len(self.tokens):
            raise ParseFailure(f"didn't understand {self.value()!r}")
        return result

    def expression(self) -> sp.Expr:
        sign = 1
        while self.value() in ("+", "-"):
            sign = -sign if self.advance()[1] == "-" else sign
        result = sign * self.term()
        while self.value() in ("+", "-"):
            sign = 1 if self.advance()[1] == "+" else -1
            while self.value() in ("+", "-"):
                sign = -sign if self.advance()[1] == "-" else sign
            result += sign * self.term()
        return result

    def term(self) -> sp.Expr:
        result = self.factor()
        while True:
            current = self.value()
            if current in ("*", r"\cdot"):
                self.at += 1
                result *= self.factor()
            elif current in ("/", r"\div"):
                self.at += 1
                divisor = self.factor()
                if divisor == 0:
                    raise ParseFailure("division by zero")
                result /= divisor
            elif self.starts_factor():
                result *= self.factor()
            else:
                return result

    def factor(self) -> sp.Expr:
        if self.value() == "-":
            self.at += 1
            return -self.factor()
        if self.value() == "+":
            self.at += 1
            return self.factor()
        return self.power()

    def starts_factor(self) -> bool:
        token = self.peek()
        if token is None:
            return False
        kind, text = token
        if self.integral_depth and self.is_differential():
            return False
        if kind in ("number", "letter"):
            return True
        if kind == "command":
            return text not in (r"\cdot", r"\div", r"\pm", r"\mp")
        if text == "|":
            return self.abs_depth == 0
        return text in ("(", "{", "[", r"\{")

    def is_differential(self) -> bool:
        following = self.peek(1)
        return self.value() == "d" and following is not None and following[0] == "letter"

    def power(self) -> sp.Expr:
        base = self.atom()
        while self.value() == "^":
            self.at += 1
            exponent = self.group()
            base = base ** exponent
        return base

    def group(self) -> sp.Expr:
        """One LaTeX argument: {...}, or a single digit, letter or command."""
        token = self.peek()
        if token is None:
            raise ParseFailure("something is missing at the end")
        if token[1] == "{":
            self.at += 1
            inner = self.expression()
            self.expect("}")
            return inner
        if token[0] == "number" and len(token[1]) > 1:
            self.tokens[self.at] = ("number", token[1][1:])
            return sp.Integer(token[1][0])
        return self.atom()

    def atom(self) -> sp.Expr:
        token = self.peek()
        if token is None:
            raise ParseFailure("something is missing at the end")
        kind, text = token
        if kind == "number":
            self.at += 1
            return sp.Rational(text)
        if kind == "letter":
            return self.symbol()
        if kind == "command":
            return self.command()
        if text in ("(", "[", "{", r"\{"):
            self.at += 1
            inner = self.expression()
            self.expect({"(": ")", "[": "]", "{": "}", r"\{": r"\}"}[text])
            return inner
        if text == "|":
            self.at += 1
            self.abs_depth += 1
            inner = self.expression()
            self.abs_depth -= 1
            self.expect("|")
            return sp.Abs(inner)
        raise ParseFailure(f"didn't expect {text!r} here")

    def symbol(self) -> sp.Expr:
        name = self.advance()[1]
        return self.finish_symbol(name)

    def finish_symbol(self, name: str) -> sp.Expr:
        subscript = self.subscript()
        primes = self.primes()
        if subscript:
            name += "_" + subscript
        elif not primes:
            if name == "e":
                return sp.E
            if name == "i" and self.allow_i:
                return sp.I
        return sp.Symbol(name + primes)

    def subscript(self) -> str:
        if self.value() != "_":
            return ""
        self.at += 1
        token = self.peek()
        if token is None:
            raise ParseFailure("a subscript is missing")
        if token[1] == "{":
            self.at += 1
            parts = []
            while self.value() != "}":
                if self.peek() is None:
                    raise ParseFailure("a subscript isn't closed")
                parts.append(self.advance()[1])
            self.at += 1
            return "".join(parts)
        self.at += 1
        return token[1]

    def primes(self) -> str:
        count = 0
        while self.value() == "'":
            self.at += 1
            count += 1
        return "'" * count

    def command(self) -> sp.Expr:
        name = self.advance()[1][1:]
        if name == "frac":
            top = self.group()
            bottom = self.group()
            if bottom == 0:
                raise ParseFailure("division by zero")
            return top / bottom
        if name == "sqrt":
            return self.root()
        if name == "pi":
            return sp.pi
        if name == "int":
            return self.integral()
        if name in _FUNCTIONS:
            return self.function(name)
        if name in _GREEK:
            return self.finish_symbol(name)
        raise ParseFailure(f"\\{name} isn't supported here")

    def root(self) -> sp.Expr:
        degree = sp.Integer(2)
        if self.value() == "[":
            self.at += 1
            degree = self.expression()
            self.expect("]")
        return self.group() ** (1 / degree)

    def function(self, name: str) -> sp.Expr:
        apply = _FUNCTIONS[name]
        if name == "log" and self.value() == "_":
            self.at += 1
            base = self.group()
            return sp.log(self.function_argument()) / sp.log(base)
        exponent = None
        if self.value() == "^":
            self.at += 1
            exponent = self.group()
        argument = self.function_argument()
        if exponent == -1 and name in _INVERSES:
            return _INVERSES[name](argument)
        result = apply(argument)
        return result if exponent is None else result ** exponent

    def function_argument(self) -> sp.Expr:
        current = self.value()
        if current in ("(", "[", "{"):
            self.at += 1
            inner = self.expression()
            self.expect({"(": ")", "[": "]", "{": "}"}[current])
            return inner
        if current == "|":
            return self.atom()
        return self.bare_argument()

    def bare_argument(self) -> sp.Expr:
        result = None
        if self.value() in ("-", "+"):
            sign = -1 if self.advance()[1] == "-" else 1
        else:
            sign = 1
        while self.starts_bare_factor():
            part = self.power()
            result = part if result is None else result * part
        if result is None:
            raise ParseFailure("a function is missing its argument")
        return sign * result

    def starts_bare_factor(self) -> bool:
        token = self.peek()
        if token is None:
            return False
        kind, text = token
        if self.integral_depth and self.is_differential():
            return False
        if kind == "number":
            return True
        if kind == "letter":
            # e^{...} after a bare argument starts a new factor: \sin 2x e^{x}.
            return text != "e"
        return text in (r"\pi", r"\frac") or text.lstrip("\\") in _GREEK

    def integral(self) -> sp.Expr:
        lower = upper = None
        for _ in range(2):
            if self.value() == "_":
                self.at += 1
                lower = self.group()
            elif self.value() == "^":
                self.at += 1
                upper = self.group()
        self.integral_depth += 1
        integrand = sp.Integer(1) if self.is_differential() else self.expression()
        self.integral_depth -= 1
        if not self.is_differential():
            raise ParseFailure("an integral needs its dx")
        self.at += 1
        variable = sp.Symbol(self.advance()[1])
        if lower is not None and upper is not None:
            return sp.integrate(integrand, (variable, lower, upper))
        return sp.integrate(integrand, variable)


def _canonical_subscripts(body: str) -> str:
    return _FUNC_CALL.sub(lambda m: m.group("name") + m.group("primes"), body)


def _to_sympy(body: str, allow_i: bool) -> sp.Expr:
    body = _canonical_subscripts(body)
    if not body.strip():
        raise ParseFailure("the answer is empty")
    parser = _Parser(body, allow_i)
    try:
        return sp.sympify(parser.parse())
    except ParseFailure:
        raise
    except (ValueError, TypeError, ZeroDivisionError, RecursionError) as exc:
        raise ParseFailure(f"couldn't work with this expression: {type(exc).__name__}") from exc


def to_sympy(latex: str, allow_i: bool = False) -> sp.Expr:
    return _to_sympy(clean_latex(latex), allow_i)


@dataclass
class Parsed:
    """An answer split on '=': one part for an expression, two for an equation."""
    parts: list[sp.Expr]
    source: str
    notes: list[str] = field(default_factory=list)

    @property
    def is_equation(self) -> bool:
        return len(self.parts) >= 2

    @property
    def readback(self) -> str:
        return " = ".join(sp.latex(p) for p in self.parts)


def parse_answer(latex: str, allow_i: bool = False) -> Parsed:
    cleaned = clean_latex(latex)
    pieces = split_top(cleaned, ("=",))
    if any(not p for p in pieces):
        raise ParseFailure("one side of the equation is empty")
    return Parsed([_to_sympy(p, allow_i) for p in pieces], latex)


def parse_list(latex: str, allow_i: bool = False) -> list[Parsed]:
    """Answers separated by commas, semicolons, line breaks or the word 'and'."""
    cleaned = clean_latex(latex)
    cleaned = re.sub(r"\\text\{\s*(and|or|,)\s*\}", ",", cleaned)
    cleaned = cleaned.replace(r"\\", ",")
    # The braces of a set written with \{ \} are noise here.
    cleaned = cleaned.replace(r"\{", "").replace(r"\}", "")
    items = [p for p in split_top(cleaned, (",", ";")) if p]
    if not items:
        raise ParseFailure("the answer is empty")
    expanded: list[str] = []
    for item in items:
        if r"\pm" in item or r"\mp" in item:
            expanded.append(item.replace(r"\pm", "+").replace(r"\mp", "-"))
            expanded.append(item.replace(r"\pm", "-").replace(r"\mp", "+"))
        else:
            expanded.append(item)
    return [Parsed([_to_sympy(p, allow_i) for p in split_top(item, ("=",))], item)
            for item in expanded]


_MATRIX_MARK = re.compile(r"\\(?:begin|end)\{[pbBvV]?matrix\*?\}")


def parse_vector(latex: str, allow_i: bool = False) -> list[sp.Expr]:
    """1, 2 or (1, 2) or [1, 2]^T or a pmatrix, with or without 'x = ' in front."""
    cleaned = clean_latex(latex)
    cleaned = split_top(cleaned, ("=",))[-1]
    cleaned = _MATRIX_MARK.sub("", cleaned).replace("&", ",")
    cleaned = re.sub(r"\^\{?T\}?\s*$", "", cleaned.strip()).strip()
    while cleaned[:1] in ("(", "[") and _wraps_whole(cleaned):
        cleaned = cleaned[1:-1].strip()
    items = [p for p in split_top(cleaned, (",", ";")) if p]
    if not items:
        raise ParseFailure("the answer is empty")
    return [_to_sympy(item, allow_i) for item in items]


def _wraps_whole(text: str) -> bool:
    depth = 0
    for index, ch in enumerate(text):
        depth += ch in "([{"
        depth -= ch in ")]}"
        if depth == 0:
            return index == len(text) - 1
    return False


def normalise_string(latex: str) -> str:
    """The last resort comparison, used only when parsing fails."""
    try:
        s = clean_latex(latex)
    except ParseFailure:
        s = latex
    return re.sub(r"\s+", "", s).replace("{", "").replace("}", "").replace(r"\cdot", "")


def tex(expr: sp.Expr) -> str:
    return sp.latex(expr)
