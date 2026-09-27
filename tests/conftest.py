"""Shared fixtures: an isolated HOME/XDG environment, fake external tools, sample files."""

from __future__ import annotations

import io
import os
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import IO, Any

import pytest
from PIL import Image, ImageDraw

from filesoptim.config import Config
from filesoptim.tools import ToolError, Tools
from filesoptim.ui import Console

Result = tuple[int, str, str]
Handler = Callable[[list[str]], Result]


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the real home: HOME and every XDG directory live in the test folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"),
                      ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state")):
        monkeypatch.setenv(name, str(home / sub))
    monkeypatch.delenv("NO_COLOR", raising=False)
    return home


class FakePopen:
    def __init__(self, lines: Sequence[str], code: int, stderr_text: str, stderr: IO[Any],
                 raise_after: int | None = None) -> None:
        self._lines = list(lines)
        self._code = code
        self._raise_after = raise_after
        self.killed = False
        stderr.write(stderr_text)
        self.stdout: Iterable[str] = self._iter()

    def _iter(self) -> Iterable[str]:
        for index, line in enumerate(self._lines):
            if self._raise_after is not None and index >= self._raise_after:
                raise KeyboardInterrupt
            yield line

    def wait(self) -> int:
        return self._code

    def __enter__(self) -> FakePopen:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def kill(self) -> None:
        self.killed = True


class FakeTools(Tools):
    """Drop-in replacement for :class:`Tools` driven by Python handlers."""

    def __init__(self, available: Iterable[str] = (), **handlers: Handler) -> None:
        super().__init__(path="")
        self.available = set(available)
        self.handlers: dict[str, Handler] = dict(handlers)
        self.popen_handler: Callable[[list[str], IO[Any]], FakePopen] | None = None
        self.calls: list[list[str]] = []

    def which(self, name: str) -> str | None:
        return f"/fake/bin/{name}" if name in self.available else None

    def run(self, args: Sequence[str | Path], *, check: bool = True,
            timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        if argv[0] not in self.available:
            raise ToolError(f"'{argv[0]}' is not installed")
        handler = self.handlers.get(argv[0])
        code, out, err = handler(argv) if handler else (0, "", "")
        if check and code != 0:
            raise ToolError(f"{argv[0]} failed: {err}")
        return subprocess.CompletedProcess(argv, code, out, err)

    def popen(self, args: Sequence[str | Path], *, stderr: IO[Any]) -> Any:
        argv = [str(a) for a in args]
        self.calls.append(argv)
        assert self.popen_handler is not None
        return self.popen_handler(argv, stderr)


class Out(Console):
    """Console writing into a buffer; ``answers`` feed confirmation prompts."""

    def __init__(self, *, answers: Sequence[str] = (), verbose: bool = False,
                 interactive: bool = True, quiet: bool = False) -> None:
        self.buffer = io.StringIO()
        replies = list(answers)
        super().__init__(self.buffer, color=False, verbose=verbose, quiet=quiet,
                         interactive=interactive, input_func=lambda _: replies.pop(0))

    @property
    def text(self) -> str:
        return self.buffer.getvalue()


@pytest.fixture
def out() -> Out:
    return Out()


@pytest.fixture
def config() -> Config:
    cfg = Config()
    cfg.general.min_age_seconds = 0
    return cfg


def make_image(path: Path, *, size: tuple[int, int] = (120, 80), fmt: str | None = None,
               mode: str = "RGB", **save: Any) -> Path:
    """Deterministic picture with some structure (so compressors have work to do)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, (30, 60, 90))
    draw = ImageDraw.Draw(image)
    for i in range(0, size[0], 7):
        draw.line([(i, 0), (size[0] - i, size[1])], fill=(i * 2 % 255, 120, 200 - i % 200))
    image = image.convert(mode)
    image.save(path, format=fmt, **save)
    return path


def age(path: Path, seconds: float = 3600) -> Path:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))
    return path
