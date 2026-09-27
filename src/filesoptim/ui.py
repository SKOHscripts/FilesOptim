"""Terminal output helpers: colours, tables, sizes and confirmation prompts."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)(i?b?)\s*$", re.IGNORECASE)

STYLES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}


def human_size(value: float) -> str:
    """Format a number of bytes with binary units (``1.5 MiB``)."""
    sign = "-" if value < 0 else ""
    amount = abs(float(value))
    unit = 0
    while amount >= 1024 and unit < len(UNITS) - 1:
        amount /= 1024
        unit += 1
    if unit == 0:
        return f"{sign}{int(amount)} B"
    return f"{sign}{amount:.1f} {UNITS[unit]}"


def parse_size(text: str) -> int:
    """Parse ``"100"``, ``"10k"``, ``"1.5G"`` or ``"20MiB"`` into bytes (binary units)."""
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"invalid size: {text!r}")
    number, prefix = float(match.group(1)), match.group(2).lower()
    power = " kmgtp".index(prefix) if prefix else 0
    return int(number * 1024**power)


def percent(part: float, whole: float) -> float:
    """Percentage of ``part`` in ``whole`` (0 when ``whole`` is 0)."""
    return 100.0 * part / whole if whole else 0.0


def shorten(text: str, width: int) -> str:
    """Shorten ``text`` to ``width`` characters with an ellipsis in the middle."""
    if len(text) <= width:
        return text
    if width < 5:
        return text[:width]
    keep = width - 1
    head = keep // 2
    return f"{text[:head]}…{text[len(text) - (keep - head):]}"


class Console:
    """Small output facade so every command prints consistently (and tests can capture it)."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        color: bool | None = None,
        quiet: bool = False,
        verbose: bool = False,
        interactive: bool | None = None,
        input_func: Callable[[str], str] | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        if color is None:
            color = "NO_COLOR" not in os.environ and _isatty(self.stream)
        self.color = color
        self.quiet = quiet
        self.verbose = verbose
        self.interactive = _isatty(sys.stdin) if interactive is None else interactive
        self._input: Callable[[str], str] = input_func or input
        self._progress_active = False

    # -- basic output -------------------------------------------------------------------------
    def style(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(STYLES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"

    def _write(self, text: str) -> None:
        self._clear_progress()
        self.stream.write(text + "\n")
        self.stream.flush()

    def print(self, text: str = "", *styles: str) -> None:
        if not self.quiet:
            self._write(self.style(text, *styles))

    def heading(self, text: str) -> None:
        self.print()
        self.print(text, "bold", "blue")

    def success(self, text: str) -> None:
        self.print(f"✔ {text}", "green")

    def info(self, text: str) -> None:
        self.print(text)

    def debug(self, text: str) -> None:
        if self.verbose:
            self.print(text, "dim")

    def warn(self, text: str) -> None:
        # Warnings stay visible even in quiet mode: they are about safety.
        self._write(self.style(f"⚠ {text}", "yellow"))

    def error(self, text: str) -> None:
        self._write(self.style(f"✘ {text}", "red"))

    # -- progress -----------------------------------------------------------------------------
    def progress(self, current: int, total: int, label: str) -> None:
        """Single-line progress indicator (only on interactive terminals)."""
        if self.quiet or not _isatty(self.stream):
            return
        line = shorten(f"[{current}/{total}] {label}", 100)
        self.stream.write("\r\033[K" + line)
        self.stream.flush()
        self._progress_active = True

    def _clear_progress(self) -> None:
        if self._progress_active:
            self.stream.write("\r\033[K")
            self._progress_active = False

    # -- tables -------------------------------------------------------------------------------
    def table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[object]],
        *,
        align: str = "",
        max_width: int = 70,
    ) -> None:
        """Print an aligned table. ``align`` holds one char per column: ``l`` or ``r``."""
        if self.quiet:
            return
        cells = [[shorten(str(c), max_width) for c in row] for row in rows]
        widths = [len(h) for h in headers]
        for row in cells:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))
        aligns = align.ljust(len(headers), "l")

        def fmt(row: Sequence[str]) -> str:
            parts = [
                cell.rjust(widths[i]) if aligns[i] == "r" else cell.ljust(widths[i])
                for i, cell in enumerate(row)
            ]
            return "  ".join(parts).rstrip()

        self._write(self.style(fmt(list(headers)), "bold"))
        self._write(self.style("  ".join("─" * w for w in widths), "dim"))
        for row in cells:
            self._write(fmt(row))

    # -- interaction --------------------------------------------------------------------------
    def confirm(self, question: str, *, assume_yes: bool = False) -> bool:
        """Ask a yes/no question. Defaults to *no*; never blocks when not interactive."""
        if assume_yes:
            return True
        if not self.interactive:
            self.warn("Not running interactively: nothing was changed (use --yes to apply).")
            return False
        self._clear_progress()
        try:
            answer = self._input(self.style(f"{question} [y/N] ", "bold"))
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes", "o", "oui"}


def _isatty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except ValueError:  # closed stream
        return False
