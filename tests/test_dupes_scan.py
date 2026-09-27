from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from filesoptim import dupes as dupes_mod
from filesoptim import scan as scan_mod
from filesoptim.config import Config
from filesoptim.dupes import (
    PARTIAL,
    DuplicateGroup,
    apply_actions,
    choose_keeper,
    find_duplicates,
    render_groups,
    resolve_duplicate,
)
from filesoptim.fsutils import WalkOptions
from filesoptim.scan import (
    _entries,
    _visitable,
    big_files,
    broken_links,
    empty_dirs,
    protected_dirs,
    topmost,
)
from filesoptim.trash import trash_root
from tests.conftest import Out

WALK = WalkOptions()


def touch(path: Path, content: bytes = b"same content", mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def group_names(groups: list[DuplicateGroup], root: Path) -> list[list[str]]:
    return [[str(p.relative_to(root)) for p in g.files] for g in groups]


# -- duplicates ---------------------------------------------------------------------------
def test_find_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"A" * (PARTIAL + 10)
    touch(tmp_path / "a" / "one.txt")
    touch(tmp_path / "b" / "two.txt")
    os.link(tmp_path / "a" / "one.txt", tmp_path / "a" / "hardlink.txt")
    touch(tmp_path / "unique.txt", b"unique!")
    touch(tmp_path / "big1.bin", big)
    touch(tmp_path / "big2.bin", big)
    touch(tmp_path / "big3.bin", big[:-1] + b"B")  # same start, different end
    touch(tmp_path / "big4.bin", b"C" + big[1:])  # different start
    touch(tmp_path / "empty1", b"")
    touch(tmp_path / "empty2", b"")
    touch(tmp_path / "unreadable1.txt", b"12345678901X")
    touch(tmp_path / "unreadable2.txt", b"12345678901X")
    real = dupes_mod.file_digest

    def digest(path: Path, **kwargs: Any) -> str:
        if path.name == "unreadable2.txt":
            raise PermissionError("denied")
        return real(path, **kwargs)

    monkeypatch.setattr(dupes_mod, "file_digest", digest)
    groups = find_duplicates([tmp_path], WALK)
    assert group_names(groups, tmp_path) == [["big1.bin", "big2.bin"], ["a/hardlink.txt",
                                                                          "b/two.txt"]]
    assert find_duplicates([tmp_path], WALK, min_size=PARTIAL) != []
    assert find_duplicates([tmp_path], WALK, min_size=PARTIAL * 2) == []


def test_keepers(tmp_path: Path) -> None:
    old = touch(tmp_path / "zz" / "old.txt", mtime=1000)
    new = touch(tmp_path / "a" / "b" / "c" / "new.txt", mtime=2000)
    short = touch(tmp_path / "s.txt", mtime=1500)
    group = find_duplicates([tmp_path], WALK)[0]
    assert choose_keeper(group, "oldest") == old
    assert choose_keeper(group, "newest") == new
    assert choose_keeper(group, "shortest") == short
    assert choose_keeper(group, "first") == group.files[0]
    assert choose_keeper(group, "oldest", [tmp_path / "a"]) == new
    group.keep = short
    assert group.remove == [p for p in group.files if p != short]
    assert group.wasted == sum(p.stat().st_blocks * 512 for p in group.remove)


def make_group(tmp_path: Path, *names: str) -> DuplicateGroup:
    for name in names:
        touch(tmp_path / name)
    group = find_duplicates([tmp_path], WALK)[0]
    group.keep = tmp_path / names[0]
    return group


@pytest.mark.parametrize("action", ["trash", "delete", "hardlink", "symlink"])
def test_actions(tmp_path: Path, action: str) -> None:
    group = make_group(tmp_path, "keep.txt", "dup.txt")
    freed, errors = apply_actions([group], action, Out())
    dup = tmp_path / "dup.txt"
    assert errors == [] and freed > 0
    if action == "trash":
        assert not dup.exists() and (trash_root() / "files" / "dup.txt").exists()
    elif action == "delete":
        assert not dup.exists()
    elif action == "hardlink":
        assert dup.stat().st_ino == (tmp_path / "keep.txt").stat().st_ino
    else:
        assert dup.is_symlink() and dup.read_bytes() == b"same content"


def test_action_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    group = make_group(tmp_path, "keep.txt", "changed.txt", "sneaky.txt")
    touch(tmp_path / "changed.txt", b"now different content")
    sneaky = tmp_path / "sneaky.txt"
    st = sneaky.stat()
    sneaky.write_bytes(b"SAME CONTENT")  # same size...
    os.utime(sneaky, ns=(st.st_atime_ns, st.st_mtime_ns))  # ...and same mtime
    out = Out()
    freed, errors = apply_actions([group], "delete", out)
    assert freed == 0 and len(errors) == 2
    assert "changed since the scan" in out.text and "content differs" in out.text

    group = make_group(tmp_path / "x", "keep.txt", "dup.txt")
    keeper = group.keep
    real_stat = Path.stat

    def stat(self: Path, **kwargs: Any) -> os.stat_result:
        st = real_stat(self, **kwargs)
        if self == keeper:
            values = list(st)
            values[2] += 1
            return os.stat_result(values)
        return st

    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(RuntimeError, match="same filesystem"):
        resolve_duplicate(group, tmp_path / "x" / "dup.txt", "hardlink")
    monkeypatch.undo()

    def broken_replace(self: Path, target: Any) -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "replace", broken_replace)
    with pytest.raises(OSError, match="read-only"):
        resolve_duplicate(group, tmp_path / "x" / "dup.txt", "symlink")
    assert sorted(p.name for p in (tmp_path / "x").iterdir()) == ["dup.txt", "keep.txt"]


def test_render_groups(tmp_path: Path) -> None:
    out = Out()
    render_groups([], out)
    assert "No duplicate found." in out.text
    groups = [make_group(tmp_path / str(i), "a", "b") for i in range(3)]
    out = Out()
    render_groups(groups, out, limit=2)
    assert "… and 1 more group(s)" in out.text
    assert "3 group(s), 3 redundant copy(ies)" in out.text
    everything = Out()
    render_groups(groups, everything, limit=None)
    assert "more group" not in everything.text


# -- scans --------------------------------------------------------------------------------
def test_big_files_and_broken_links(tmp_path: Path) -> None:
    touch(tmp_path / "small", b"1")
    touch(tmp_path / "medium", b"1" * 100)
    touch(tmp_path / "d" / "large", b"1" * 1000)
    (tmp_path / "ok").symlink_to(tmp_path / "small")
    (tmp_path / "d" / "dead").symlink_to(tmp_path / "gone")
    assert big_files([tmp_path], WALK, top=2) == [(1000, tmp_path / "d" / "large"),
                                                   (100, tmp_path / "medium")]
    assert big_files([tmp_path], WALK, top=5, min_size=50) == [
        (1000, tmp_path / "d" / "large"), (100, tmp_path / "medium")]
    assert broken_links([tmp_path], WALK) == [(tmp_path / "d" / "dead", str(tmp_path / "gone"))]


def test_protected_dirs(isolated_env: Path, config: Config) -> None:
    (isolated_env / ".config").mkdir()
    (isolated_env / ".config" / "user-dirs.dirs").write_text(
        '# comment\nXDG_DESKTOP_DIR="$HOME/Bureau"\nXDG_MUSIC_DIR="/data/Musique"\nbad line\n')
    config.scan.protected = ["~/Keep"]
    protected = protected_dirs(config)
    for path in (isolated_env, isolated_env / "Bureau", Path("/data/Musique"),
                 isolated_env / "Keep", isolated_env / "Templates"):
        assert path in protected


def test_protected_dirs_without_user_dirs(config: Config, isolated_env: Path) -> None:
    assert isolated_env / "Downloads" in protected_dirs(config)


def test_empty_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "a" / "empty2").mkdir()
    (root / "full" / "sub").mkdir(parents=True)
    touch(root / "full" / "file")
    (root / "hidden").mkdir()
    touch(root / "hidden" / ".directory")
    (root / "repo" / ".git" / "refs").mkdir(parents=True)
    (root / "protected" / "inner").mkdir(parents=True)
    (root / "locked").mkdir()
    (root / "link").symlink_to(root / "a")
    walk = WalkOptions(skip_hidden=True)
    real_entries = scan_mod._entries
    monkeypatch.setattr(scan_mod, "_entries",
                        lambda d: None if d.name == "locked" else real_entries(d))
    found = empty_dirs([root, tmp_path / "missing"], walk, {root / "protected"})
    rel = [str(p.relative_to(root)) for p in found]
    assert rel == ["a/b/c", "a/b", "a/empty2", "full/sub", "protected/inner", "a"]
    assert topmost(found) == [root / "a", root / "full" / "sub", root / "protected" / "inner"]
    alone = tmp_path / "alone"
    alone.mkdir()
    assert empty_dirs([alone], walk, set()) == []  # the root itself is never reported


class FakeEntry:
    def __init__(self, name: str, *, is_dir: bool = True, fail: bool = False,
                 dev: int = 1) -> None:
        self.name, self._dir, self._fail, self._dev = name, is_dir, fail, dev

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return self._dir

    def stat(self, follow_symlinks: bool = True) -> Any:
        if self._fail:
            raise OSError("gone")
        return os.stat_result((0, 0, self._dev, 0, 0, 0, 0, 0, 0, 0))


def test_visitable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    walk = WalkOptions(exclude=[".git"])
    assert not _visitable(FakeEntry("f", is_dir=False), tmp_path, walk, 1)  # type: ignore[arg-type]
    assert not _visitable(FakeEntry(".git"), tmp_path, walk, 1)  # type: ignore[arg-type]
    assert not _visitable(FakeEntry("x", fail=True), tmp_path, walk, 1)  # type: ignore[arg-type]
    assert not _visitable(FakeEntry("x", dev=2), tmp_path, walk, 1)  # type: ignore[arg-type]
    walk.one_file_system = False
    assert _visitable(FakeEntry("x", dev=2), tmp_path, walk, 1)  # type: ignore[arg-type]
    monkeypatch.setattr(scan_mod.os.path, "ismount", lambda p: True)
    assert not _visitable(FakeEntry("x"), tmp_path, walk, 1)  # type: ignore[arg-type]
    assert _entries(tmp_path / "nope") is None
    assert time.time() > 0
