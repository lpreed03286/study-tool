"""The dark theme can follow a base16 desktop palette, such as the one Stylix writes.

The page asks for /palette.css after style.css. With no palette, or one that
isn't dark, it gets an empty sheet and keeps the built-in dark theme.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

HEX = re.compile(r"#?([0-9a-fA-F]{6})")
NEEDED = [f"base0{digit}" for digit in "0123456789ABCDEF"]


def palette_path() -> Path:
    if os.environ.get("WORKSHEET_PALETTE"):
        return Path(os.environ["WORKSHEET_PALETTE"]).expanduser()
    config = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config) / "stylix" / "palette.json"


def _rgb(value) -> tuple[int, int, int] | None:
    match = HEX.fullmatch(str(value).strip())
    if not match:
        return None
    text = match.group(1)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(channel):02x}" for channel in rgb)


def _mix(a, b, share: float) -> str:
    """`share` of b over a."""
    return _hex(x + (y - x) * share for x, y in zip(a, b))


def _luminance(rgb) -> float:
    def linear(channel):
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette_css(path: Path | None = None) -> str:
    try:
        raw = json.loads((path or palette_path()).read_text())
        colors = {key: _rgb(raw[key]) for key in NEEDED}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ""
    if None in colors.values():
        return ""
    c = colors
    # A light scheme would fight the light/dark switch, so only a dark one is used.
    if _luminance(c["base00"]) > 0.2 or _luminance(c["base05"]) < _luminance(c["base00"]):
        return ""
    variables = {
        "--bg": _hex(c["base00"]),
        "--panel": _mix(c["base00"], c["base01"], 0.3),
        "--soft": _mix(c["base00"], c["base01"], 0.6),
        "--line": _hex(c["base01"]),
        "--ink": _hex(c["base05"]),
        "--muted": _hex(c["base04"]),
        "--accent": _hex(c["base0D"]),
        "--accent-ink": _hex(c["base00"]),
        "--good": _hex(c["base0B"]),
        "--good-bg": _mix(c["base00"], c["base0B"], 0.15),
        "--bad": _hex(c["base08"]),
        "--bad-bg": _mix(c["base00"], c["base08"], 0.15),
        "--warn": _hex(c["base0A"]),
        "--warn-bg": _mix(c["base00"], c["base0A"], 0.15),
        "--series-1": _hex(c["base0C"]),
        "--series-2": _hex(c["base09"]),
        "--grid": _mix(c["base00"], c["base01"], 0.5),
        "--axis": _hex(c["base03"]),
        "--curve": _mix(c["base03"], c["base04"], 0.4),
    }
    name = str(raw.get("scheme", "desktop palette")).replace("*/", "")
    body = "".join(f"    {key}: {value};\n" for key, value in variables.items())
    # Screen only, so printing keeps its black-on-white colors.
    return f"/* {name} */\n@media screen {{\n  :root[data-theme=\"dark\"] {{\n{body}  }}\n}}\n"
