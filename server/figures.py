"""Numbers for the plots. The page draws them, so they follow its light or dark theme.

Trajectories are integrated numerically from the matrix alone, never read off an
answer key, so a plot can't quietly agree with a wrong key.
"""
from __future__ import annotations

import cmath
import math

MAX_STEPS = 6000
PATH_STARTS = 12
INNER_STARTS = 6


def _rates(A) -> tuple[complex, complex]:
    trace = A[0][0] + A[1][1]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    root = cmath.sqrt(trace * trace - 4 * det)
    return (trace + root) / 2, (trace - root) / 2


def _step(A, y, dt):
    def f(v):
        return (A[0][0] * v[0] + A[0][1] * v[1], A[1][0] * v[0] + A[1][1] * v[1])

    k1 = f(y)
    k2 = f((y[0] + dt * k1[0] / 2, y[1] + dt * k1[1] / 2))
    k3 = f((y[0] + dt * k2[0] / 2, y[1] + dt * k2[1] / 2))
    k4 = f((y[0] + dt * k3[0], y[1] + dt * k3[1]))
    return (y[0] + dt * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6,
            y[1] + dt * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]) / 6)


def _clean(value: float) -> float:
    return round(value, 4)


def _trace(A, start, duration, dt, limit, spacing):
    """One direction of time from start, kept only while it stays in the window."""
    points = [start]
    y = start
    steps = min(MAX_STEPS, int(abs(duration / dt)))
    for _ in range(steps):
        y = _step(A, y, dt)
        if abs(y[0]) > limit or abs(y[1]) > limit:
            break
        last = points[-1]
        if math.hypot(y[0] - last[0], y[1] - last[1]) >= spacing:
            points.append(y)
        if math.hypot(y[0], y[1]) < spacing / 2:
            break
    return points


def _path(A, start, duration, dt, limit, spacing):
    """Ordered forward in time, so the page can put an arrow on it."""
    forward = _trace(A, start, duration, dt, limit, spacing)
    backward = _trace(A, start, duration, -dt, limit, spacing)
    joined = list(reversed(backward[1:])) + forward
    return [[_clean(a), _clean(b)] for a, b in joined]


def phase_portrait(A, eigen_directions, ic=None, caption="", alt="") -> dict:
    """A as floats [[a, b], [c, d]]; eigen_directions as real (dx, dy) pairs."""
    one, two = _rates(A)
    rate = max(abs(one), abs(two), 1e-6)
    window = max(3.0, 1.35 * max(abs(ic[0]), abs(ic[1]))) if ic else 3.0
    limit = window * 1.02
    spacing = window * 0.015
    dt = 0.02 / rate
    # A center needs one full orbit and a slow spiral several; a node just needs time to leave.
    duration = 16 / rate if abs(one.imag) > 1e-9 else 10 / rate
    paths = []
    for ring, count, offset in ((0.95, PATH_STARTS, 0.13), (0.45, INNER_STARTS, 0.4)):
        for k in range(count):
            angle = 2 * math.pi * k / count + offset
            start = (ring * window * math.cos(angle), ring * window * math.sin(angle))
            path = _path(A, start, duration, dt, limit, spacing)
            if len(path) >= 2:
                paths.append(path)
    figure = {"kind": "phase", "title": "Phase portrait", "window": window, "paths": paths,
              "eigen": [[_clean(a), _clean(b)] for a, b in eigen_directions],
              "x": "y_1", "y": "y_2", "caption": caption, "alt": alt}
    if ic:
        figure["ic"] = [_clean(ic[0]), _clean(ic[1])]
        figure["ic_path"] = [[_clean(a), _clean(b)] for a, b in
                             _trace(A, (ic[0], ic[1]), duration, dt, limit, spacing)]
    return figure


def time_plot(A, ic, caption="", alt="") -> dict:
    """y_1(t) and y_2(t) from the initial condition, numerically."""
    one, two = _rates(A)
    rate = max(abs(one), abs(two), 1e-6)
    oscillates = abs(one.imag) > 1e-9
    duration = 4 * math.pi / abs(one.imag) if oscillates else 6 / rate
    bound = 12 * max(1.0, abs(ic[0]), abs(ic[1]))
    samples = 240
    dt = duration / samples
    substeps = max(1, int(dt * rate / 0.02) + 1)
    y = (float(ic[0]), float(ic[1]))
    rows = [(0.0, y[0], y[1])]
    for k in range(1, samples + 1):
        for _ in range(substeps):
            y = _step(A, y, dt / substeps)
        if abs(y[0]) > bound or abs(y[1]) > bound:
            break
        rows.append((k * dt, y[0], y[1]))
    return {"kind": "lines", "title": "Solution from the initial condition", "x": "t",
            "series": [{"name": "y_1", "points": [[_clean(r[0]), _clean(r[1])] for r in rows]},
                       {"name": "y_2", "points": [[_clean(r[0]), _clean(r[2])] for r in rows]}],
            "caption": caption, "alt": alt}


def curve_plot(function, start: float, stop: float, name: str, x_name: str, marker=None,
               title: str = "", caption: str = "", alt: str = "", log_x: bool = False) -> dict:
    """One curve, like y(x) for an Euler-Cauchy problem. Sampled evenly in ln x, the
    variable these solutions oscillate in, which also keeps detail near x = 0."""
    samples = 240
    points = []
    for k in range(samples + 1):
        x = start * (stop / start) ** (k / samples)
        value = complex(function(x)).real
        if math.isfinite(value):
            points.append([_clean(x), _clean(value)])
    figure = {"kind": "lines", "title": title or f"{name}({x_name})", "x": x_name,
              "series": [{"name": name, "points": points}], "caption": caption, "alt": alt, "log_x": log_x}
    if marker:
        figure["marker"] = [_clean(marker[0]), _clean(marker[1])]
    return figure
