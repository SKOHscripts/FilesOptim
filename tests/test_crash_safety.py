"""Crash tests: real FilesOptim processes killed (kill -9) in the middle of their work.

The kill is triggered when a random amount of work has been done, so it always lands during
the operation. After every brutal stop:

* the content of every original file still exists, complete and unchanged;
* no file with a final (non temporary) name ever holds a partial copy.
"""

from __future__ import annotations

import hashlib
import random
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from filesoptim.fsutils import TEMP_MARK
from filesoptim.optimize.images import images_identical
from tests.conftest import age, make_image

pytestmark = pytest.mark.integration


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def final_files(root: Path) -> list[Path]:
    """Files with a final name (our hidden temporary files are excluded)."""
    if not root.exists():
        return []
    return [p for p in root.rglob("*")
            if p.is_file() and not p.is_symlink() and TEMP_MARK not in p.name]


def contents(root: Path) -> set[str]:
    return {digest(p) for p in final_files(root)}


def kill_when(args: list[str], condition: Callable[[], bool], timeout: float = 60) -> bool:
    """Start ``filesoptim args`` and kill -9 it as soon as ``condition()`` holds.

    Returns whether the kill happened while the process was still working.
    """
    proc = subprocess.Popen([sys.executable, "-m", "filesoptim", *args],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    try:
        while proc.poll() is None and time.time() < deadline:
            if condition():
                proc.send_signal(signal.SIGKILL)
                proc.wait()
                return True
            time.sleep(0.001)
        return False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def run(args: list[str]) -> int:
    return subprocess.run([sys.executable, "-m", "filesoptim", *args],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def make_library(root: Path, count: int, rng: random.Random) -> set[str]:
    for i in range(count):
        folder = root / f"event{i % 5}"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"IMG_2019{i % 12 + 1:02d}{i % 28 + 1:02d}_10{i % 60:02d}{i % 59:02d}_{i}.jpg"
        (folder / name).write_bytes(rng.randbytes(rng.randint(50_000, 1_500_000)))
    return contents(root)


def test_sort_copy_move_and_undo_never_lose_data(tmp_path: Path) -> None:
    rng = random.Random(1234)
    area = tmp_path / "area"
    src = area / "src"
    original = make_library(src, 250, rng)
    assert len(original) == 250

    # --copy: killed after a random number of copies
    copies = area / "copies"
    mid_run = 0
    for _ in range(6):
        target = len(final_files(copies)) + rng.randint(1, 30)
        mid_run += kill_when(["sort", str(src), "--dest", str(copies), "--copy", "-y",
                              "--no-mtime"], lambda t=target: len(final_files(copies)) >= t)
        assert contents(src) == original  # the sources are never touched by a copy
        assert contents(copies) <= original  # no partial copy under a final name
    assert mid_run >= 5

    # move: killed after a random number of moves
    dst = area / "dst"
    mid_run = 0
    for _ in range(8):
        target = len(final_files(dst)) + rng.randint(1, 25)
        mid_run += kill_when(["sort", str(src), "--dest", str(dst), "--rename", "--prune-empty",
                              "-y", "--no-mtime"], lambda t=target: len(final_files(dst)) >= t)
        assert original <= contents(area)
        assert contents(dst) <= original
    assert mid_run >= 6
    assert run(["sort", str(src), "--dest", str(dst), "--rename", "-y", "--no-mtime"]) in (0, 1)
    assert original <= contents(area)

    # undo, killed while files are being moved back
    mid_run = 0
    for _ in range(6):
        target = len(final_files(src)) + rng.randint(1, 25)
        mid_run += kill_when(["undo", "-y"], lambda t=target: len(final_files(src)) >= t)
        assert original <= contents(area)
    assert mid_run >= 4
    for _ in range(30):  # let every interrupted journal be undone completely
        if run(["undo", "-y"]) == 0 and not list(tmp_path.rglob("sort-*[0-9].jsonl")):
            break
    assert contents(src) == original  # everything is back, intact


def test_optimize_never_loses_data(tmp_path: Path) -> None:
    for tool in ("jpegoptim", "optipng"):
        if shutil.which(tool) is None:
            pytest.skip(f"missing: {tool}")
    rng = random.Random(99)
    photos = tmp_path / "photos"
    keep = tmp_path / "reference"
    for i in range(60):
        size = (rng.randint(300, 900), rng.randint(300, 700))
        if i % 2:
            path = make_image(photos / f"p{i}.png", size=size, compress_level=0)
        else:
            path = make_image(photos / f"p{i}.jpg", size=size, fmt="JPEG", quality=95)
            with path.open("ab") as fh:
                fh.write(b"\0" * 50_000)
        age(path)
    shutil.copytree(photos, keep)
    sizes = {p.name: p.stat().st_size for p in keep.iterdir()}

    def optimised() -> int:
        return sum(1 for p in photos.iterdir()
                   if TEMP_MARK not in p.name and p.stat().st_size != sizes.get(p.name))

    mid_run = 0
    for _ in range(8):
        target = optimised() + rng.randint(1, 8)
        mid_run += kill_when(["optimize", str(photos), "-y", "-q", "--jobs", "2"],
                             lambda t=target: optimised() >= t)
        for reference in keep.iterdir():
            current = photos / reference.name
            assert current.exists(), current
            assert images_identical(current, reference), current
    assert mid_run >= 5
    assert run(["optimize", str(photos), "-y", "-q"]) == 0
    for reference in keep.iterdir():
        assert images_identical(photos / reference.name, reference)
    assert optimised() == 60
