from __future__ import annotations

import io
import sys

import pytest

from filesoptim.ui import Console, _isatty, human_size, parse_size, percent, shorten
from tests.conftest import Out


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class Closed:
    def isatty(self) -> bool:
        raise ValueError("closed")


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KiB"), (1536 * 1024, "1.5 MiB"),
     (-2048, "-2.0 KiB"), (1024**6, "1024.0 PiB")],
)
def test_human_size(value: int, text: str) -> None:
    assert human_size(value) == text


@pytest.mark.parametrize(
    ("text", "value"),
    [("100", 100), ("10k", 10240), ("1.5G", int(1.5 * 1024**3)), ("20MiB", 20 * 1024**2),
     (" 3 kb ", 3072), ("2T", 2 * 1024**4)],
)
def test_parse_size(text: str, value: int) -> None:
    assert parse_size(text) == value


def test_parse_size_invalid() -> None:
    with pytest.raises(ValueError, match="invalid size"):
        parse_size("lots")


def test_percent_and_shorten() -> None:
    assert percent(1, 4) == 25.0
    assert percent(1, 0) == 0.0
    assert shorten("abc", 10) == "abc"
    assert shorten("abcdefgh", 3) == "abc"
    assert shorten("abcdefghij", 7) == "abc…hij"


def test_console_defaults_and_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    console = Console()
    assert console.stream is sys.stdout
    assert console.interactive is False  # pytest's stdin is not a terminal
    assert Console(io.StringIO()).color is False
    assert Console(TTY()).color is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert Console(TTY()).color is False
    colored = Console(io.StringIO(), color=True)
    assert colored.style("x", "red", "bold") == "\033[31;1mx\033[0m"
    assert colored.style("x") == "x"


def test_console_levels() -> None:
    stream = io.StringIO()
    console = Console(stream, color=False, quiet=True)
    console.print("hidden")
    console.info("hidden")
    console.debug("hidden")
    console.table(["a"], [["b"]])
    console.warn("careful")
    console.error("broken")
    assert stream.getvalue() == "⚠ careful\n✘ broken\n"
    loud = Out(verbose=True)
    loud.heading("Title")
    loud.success("done")
    loud.debug("detail")
    assert loud.text == "\nTitle\n✔ done\ndetail\n"


def test_progress_only_on_terminals() -> None:
    plain = Out()
    plain.progress(1, 2, "file")
    assert plain.text == ""
    stream = TTY()
    console = Console(stream, color=False)
    console.progress(1, 2, "file")
    assert stream.getvalue().endswith("[1/2] file")
    console.info("next")
    assert stream.getvalue().endswith("\r\033[Knext\n")
    quiet = Console(TTY(), color=False, quiet=True)
    quiet.progress(1, 1, "x")
    assert quiet.stream.getvalue() == ""


def test_table_alignment_and_truncation() -> None:
    console = Out()
    console.table(["name", "size"], [["a", "1"], ["bbbbbbbbbbbb", "22"]], align="lr",
                  max_width=8)
    lines = console.text.splitlines()
    assert lines[0] == "name      size"
    assert lines[2] == "a            1"
    assert lines[3] == "bbb…bbbb    22"


def test_confirm_answers() -> None:
    assert Out().confirm("?", assume_yes=True)
    assert Out(answers=["y"]).confirm("?")
    assert Out(answers=["oui"]).confirm("?")
    assert not Out(answers=["no"]).confirm("?")
    non_interactive = Out(interactive=False)
    assert not non_interactive.confirm("?")
    assert "use --yes" in non_interactive.text

    def eof(_: str) -> str:
        raise EOFError

    assert not Console(io.StringIO(), interactive=True, input_func=eof).confirm("?")


def test_confirm_clears_progress() -> None:
    stream = TTY()
    console = Console(stream, color=False, interactive=True, input_func=lambda _: "y")
    console.progress(1, 1, "x")
    assert console.confirm("go?")
    assert stream.getvalue().endswith("\r\033[K")


def test_isatty_edge_cases() -> None:
    assert _isatty(object()) is False
    assert _isatty(Closed()) is False
