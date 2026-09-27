from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from filesoptim import fsutils
from filesoptim.config import GeneralConfig
from filesoptim.fsutils import (
    TEMP_MARK,
    WalkOptions,
    allocated_size,
    display_path,
    file_digest,
    free_space,
    install_file,
    is_within,
    iter_files,
    iter_tree,
    prune_empty_dirs,
    same_content,
    sanitize_component,
    stat_key,
    unique_path,
)


def touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    touch(root / "b.txt")
    touch(root / "a" / "one.txt")
    touch(root / "a" / "deep" / "two.txt")
    touch(root / ".hidden" / "secret.txt")
    touch(root / ".dotfile")
    touch(root / "node_modules" / "lib.js")
    touch(root / f".x{TEMP_MARK}")
    (root / "link").symlink_to(root / "a")
    (root / "filelink").symlink_to(root / "b.txt")
    return root


def names(items: Any, root: Path) -> list[str]:
    return [str(p.relative_to(root)) for p, _ in items]


def test_walk_options(tree: Path) -> None:
    general = GeneralConfig()
    walk = WalkOptions.from_config(general, include_hidden=True, extra_exclude=["*.js"])
    assert not walk.skip_hidden
    assert walk.is_excluded("lib.js")
    assert walk.is_excluded(f"a{TEMP_MARK}")
    assert not walk.is_excluded(".dotfile")
    default = WalkOptions.from_config(general)
    assert default.is_excluded(".dotfile")
    assert default.is_excluded("node_modules")
    assert not WalkOptions().is_excluded("normal")


def test_iter_tree_order_and_exclusions(tree: Path) -> None:
    walk = WalkOptions.from_config(GeneralConfig())
    assert names(iter_tree([tree], walk), tree) == [
        "a", "b.txt", "filelink", "link", "a/deep", "a/one.txt", "a/deep/two.txt",
    ]
    assert names(iter_files([tree], walk), tree) == ["b.txt", "a/one.txt", "a/deep/two.txt"]
    assert names(iter_files([tree], walk, skip_dirs=[tree / "a"]), tree) == ["b.txt"]


def test_iter_tree_roots_that_are_files_or_missing(tree: Path) -> None:
    walk = WalkOptions()
    found = list(iter_tree([tree / "b.txt", tree / "missing"], walk))
    assert [p for p, _ in found] == [tree / "b.txt"]


def test_iter_tree_errors(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_scandir = os.scandir

    class BadEntry:
        name = "bad"
        path = str(tree / "bad")

        def stat(self, follow_symlinks: bool = True) -> os.stat_result:
            raise OSError("gone")

    class FakeScandir:
        def __init__(self, entries: list[Any]) -> None:
            self.entries = entries

        def __enter__(self) -> list[Any]:
            return self.entries

        def __exit__(self, *args: object) -> None:
            return None

    def fake_scandir(path: Any) -> Any:
        if Path(path) == tree / "a":
            raise PermissionError("denied")
        if Path(path) == tree:
            with real_scandir(path) as it:
                return FakeScandir([*it, BadEntry()])
        return real_scandir(path)

    monkeypatch.setattr(fsutils.os, "scandir", fake_scandir)
    found = names(iter_files([tree], WalkOptions.from_config(GeneralConfig())), tree)
    assert found == ["b.txt"]


def test_one_file_system(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_lstat = Path.lstat

    def fake_lstat(self: Path) -> os.stat_result:
        st = real_lstat(self)
        if self == tree:
            values = list(st)
            values[2] = st.st_dev + 1  # pretend the root is on another device
            return os.stat_result(values)
        return st

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    same_fs = WalkOptions(one_file_system=True)
    assert names(iter_files([tree], same_fs), tree) == ["b.txt"]
    all_fs = WalkOptions(one_file_system=False)
    assert "a/one.txt" in names(iter_files([tree], all_fs), tree)


def test_sizes_digests_and_comparison(tmp_path: Path) -> None:
    a = touch(tmp_path / "a", b"hello world" * 1000)
    b = touch(tmp_path / "b", b"hello world" * 1000)
    c = touch(tmp_path / "c", b"hello world" * 999 + b"HELLO WORLD")
    st = a.stat()
    assert allocated_size(st) == st.st_blocks * 512
    assert stat_key(st) == (st.st_size, st.st_mtime_ns)
    assert file_digest(a) == file_digest(b, chunk_size=7)
    assert file_digest(a) != file_digest(c)
    assert file_digest(a, limit=100) == file_digest(c, limit=100, chunk_size=33)
    assert file_digest(touch(tmp_path / "empty", b""), limit=10) == file_digest(tmp_path / "empty")
    assert same_content(a, b)
    assert not same_content(a, c)


def test_install_file_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = touch(tmp_path / "photo.jpg", b"original")
    original.chmod(0o640)
    os.utime(original, (1_000_000, 1_000_000))
    new = touch(tmp_path / "work" / "new", b"smaller")
    monkeypatch.setattr(fsutils.os, "chown", lambda *a: (_ for _ in ()).throw(PermissionError()))
    assert install_file(new, original) == original
    assert original.read_bytes() == b"smaller"
    assert original.stat().st_mtime == 1_000_000
    assert original.stat().st_mode & 0o777 == 0o640
    assert not new.exists()
    assert not list(tmp_path.glob(f"*{TEMP_MARK}"))


def test_install_file_with_new_name(tmp_path: Path) -> None:
    original = touch(tmp_path / "clip.avi", b"old")
    new = touch(tmp_path / "work" / "new.mkv", b"new")
    target = tmp_path / "clip.mkv"
    assert install_file(new, original, target) == target
    assert target.read_bytes() == b"new"
    assert not original.exists()
    touch(tmp_path / "exists.mkv")
    with pytest.raises(FileExistsError):
        install_file(touch(tmp_path / "n2"), touch(tmp_path / "exists.avi"),
                     tmp_path / "exists.mkv")


def test_install_file_failure_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = touch(tmp_path / "doc.pdf", b"original")
    new = touch(tmp_path / "work" / "new", b"new")

    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copystat", broken)
    with pytest.raises(OSError, match="disk full"):
        install_file(new, original)
    assert original.read_bytes() == b"original"
    assert not list(tmp_path.glob(f".*{TEMP_MARK}"))


def test_sanitize_component() -> None:
    assert sanitize_component('a/b\\c:d*e?f"g<h>i|j\x01') == "a_b_c_d_e_f_g_h_i_j_"
    assert sanitize_component("  ..name.. ") == "name"
    assert sanitize_component("...") == "_"
    long = sanitize_component("é" * 300)
    assert len(long.encode()) <= 200


def test_unique_path(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    assert unique_path(target) == target
    touch(target)
    assert unique_path(target) == tmp_path / "a_1.txt"
    assert unique_path(target, {tmp_path / "a_1.txt"}) == tmp_path / "a_2.txt"
    (tmp_path / "l.txt").symlink_to(tmp_path / "missing")
    assert unique_path(tmp_path / "l.txt") == tmp_path / "l_1.txt"


def test_path_helpers(tmp_path: Path) -> None:
    assert is_within(tmp_path / "a" / "b", tmp_path)
    assert not is_within(tmp_path, tmp_path / "a")
    assert display_path(tmp_path / "a" / "b", tmp_path) == "a/b"
    assert display_path(tmp_path / "a", tmp_path / "zzz") == str(tmp_path / "a")
    assert display_path(tmp_path / "a", None) == str(tmp_path / "a")
    assert free_space(tmp_path) > 0


def test_prune_empty_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "root" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    touch(tmp_path / "root" / "a" / "keep.txt")
    removed = prune_empty_dirs(deep, tmp_path / "root")
    assert removed == [deep, deep.parent]
    assert (tmp_path / "root" / "a").exists()
    empty_root = tmp_path / "solo"
    empty_root.mkdir()
    assert prune_empty_dirs(empty_root, empty_root) == []
    assert prune_empty_dirs(tmp_path / "elsewhere", tmp_path / "root") == []


def test_run_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys
    import time

    from filesoptim.fsutils import make_run_dir, pid_alive, stale_run_dirs, tree_bytes

    assert pid_alive(os.getpid())
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert not pid_alive(finished.pid)
    cache = tmp_path / "cache"
    assert stale_run_dirs(cache, time.time()) == []
    mine = make_run_dir(cache)
    assert mine.name.startswith(f"run-{os.getpid()}-")
    dead = cache / f"run-{finished.pid}-abc"
    dead.mkdir()
    touch(dead / "x.jpg", b"1" * 10_000)
    old_style = cache / "run-abcdef"
    old_style.mkdir()
    os.utime(old_style, (0, 0))
    recent_old_style = cache / "run-zzz"
    recent_old_style.mkdir()
    (cache / "run-file").write_text("not a folder")
    (cache / "other").mkdir()
    assert stale_run_dirs(cache, time.time()) == [dead, old_style]
    assert tree_bytes(dead) >= 10_000
    assert tree_bytes(dead / "x.jpg") >= 10_000

    def denied(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(fsutils.os, "kill", denied)
    assert pid_alive(1)
    real_lstat = Path.lstat

    def flaky(self: Path) -> os.stat_result:
        if self.name == "x.jpg":
            raise FileNotFoundError(self)
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", flaky)
    assert tree_bytes(dead) < 10_000  # vanished files are ignored


# -- data safety primitives ------------------------------------------------------------------
def _fail_link_with(monkeypatch: pytest.MonkeyPatch, code: int, *, times: int = 99) -> None:
    import errno as _errno  # noqa: F401

    real = os.link
    calls = {"n": 0}

    def link(src: Any, dst: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] <= times:
            raise OSError(code, os.strerror(code))
        real(src, dst)

    monkeypatch.setattr(fsutils.os, "link", link)


def test_rename_no_clobber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from filesoptim.fsutils import rename_no_clobber

    a = touch(tmp_path / "a", b"A")
    rename_no_clobber(a, tmp_path / "b")
    assert (tmp_path / "b").read_bytes() == b"A" and not a.exists()
    touch(tmp_path / "c", b"C")
    with pytest.raises(FileExistsError):
        rename_no_clobber(tmp_path / "b", tmp_path / "c")
    assert (tmp_path / "c").read_bytes() == b"C" and (tmp_path / "b").exists()
    _fail_link_with(monkeypatch, errno.EPERM)  # FAT/exFAT: no hard links
    rename_no_clobber(tmp_path / "b", tmp_path / "d")
    assert (tmp_path / "d").read_bytes() == b"A"
    with pytest.raises(FileExistsError):
        rename_no_clobber(tmp_path / "d", tmp_path / "c")
    assert (tmp_path / "c").read_bytes() == b"C" and (tmp_path / "d").exists()
    monkeypatch.undo()
    _fail_link_with(monkeypatch, errno.EIO)
    with pytest.raises(OSError, match="Input/output"):
        rename_no_clobber(tmp_path / "d", tmp_path / "e")
    assert (tmp_path / "d").exists()


def test_safe_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from filesoptim.fsutils import safe_move

    src = touch(tmp_path / "src" / "a.jpg", b"photo")
    (tmp_path / "dst").mkdir()
    touch(tmp_path / "dst" / "taken.jpg", b"other")
    with pytest.raises(FileExistsError):
        safe_move(src, tmp_path / "dst" / "taken.jpg")
    safe_move(src, tmp_path / "dst" / "a.jpg")
    assert (tmp_path / "dst" / "a.jpg").read_bytes() == b"photo" and not src.exists()
    # another disk: copied, flushed, renamed, and only then the source is removed
    _fail_link_with(monkeypatch, errno.EXDEV, times=1)
    safe_move(tmp_path / "dst" / "a.jpg", tmp_path / "src" / "b.jpg")
    assert (tmp_path / "src" / "b.jpg").read_bytes() == b"photo"
    assert not (tmp_path / "dst" / "a.jpg").exists()
    assert not list(tmp_path.rglob(f"*{TEMP_MARK}*"))
    monkeypatch.undo()
    # interrupted while giving the copy its final name: the source is still there
    _fail_link_with(monkeypatch, errno.EXDEV, times=1)
    real_link = fsutils.rename_no_clobber
    calls = {"n": 0}

    def flaky(source: Path, destination: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        real_link(source, destination)

    monkeypatch.setattr(fsutils, "rename_no_clobber", flaky)
    with pytest.raises(KeyboardInterrupt):
        safe_move(tmp_path / "src" / "b.jpg", tmp_path / "dst" / "c.jpg")
    assert (tmp_path / "src" / "b.jpg").read_bytes() == b"photo"
    assert not (tmp_path / "dst" / "c.jpg").exists()
    assert not list(tmp_path.rglob(f"*{TEMP_MARK}*"))
    monkeypatch.undo()
    _fail_link_with(monkeypatch, errno.EIO)
    with pytest.raises(OSError):
        safe_move(tmp_path / "src" / "b.jpg", tmp_path / "dst" / "d.jpg")
    assert (tmp_path / "src" / "b.jpg").exists()


def test_durable_copy_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from filesoptim.fsutils import durable_copy, fsync_dir, fsync_file

    src = touch(tmp_path / "a", b"data")
    durable_copy(src, tmp_path / "b")
    assert (tmp_path / "b").read_bytes() == b"data"
    fsync_file(tmp_path / "b")
    fsync_dir(tmp_path)

    def interrupted(fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(fsutils.os, "fsync", interrupted)
    with pytest.raises(KeyboardInterrupt):
        durable_copy(src, tmp_path / "c")
    assert not (tmp_path / "c").exists() and src.read_bytes() == b"data"


def test_install_file_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from filesoptim.fsutils import FileChangedError

    original = touch(tmp_path / "photo.jpg", b"original")
    expected = stat_key(original.stat())
    new = touch(tmp_path / "work" / "new", b"smaller")
    original.write_bytes(b"edited meanwhile")  # the user changed it during the processing
    with pytest.raises(FileChangedError):
        install_file(new, original, expected=expected)
    assert original.read_bytes() == b"edited meanwhile"
    assert not list(tmp_path.glob(f".*{TEMP_MARK}"))
    # staged copy on another disk: copied next to the original, then swapped
    new = touch(tmp_path / "work" / "new2", b"smaller")
    real_rename = Path.rename

    def cross_device(self: Path, target: Any) -> Any:
        if self == new:
            raise OSError(errno.EXDEV, "cross-device")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", cross_device)
    install_file(new, original, expected=stat_key(original.stat()))
    assert original.read_bytes() == b"smaller" and not new.exists()

    def broken(self: Path, target: Any) -> Any:
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(Path, "rename", broken)
    with pytest.raises(OSError, match="I/O"):
        install_file(touch(tmp_path / "work" / "new3", b"x"), original)
    assert original.read_bytes() == b"smaller"
