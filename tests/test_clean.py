from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from filesoptim import clean as clean_mod
from filesoptim.clean import (
    DAY,
    CleanContext,
    Cleaner,
    CleanItem,
    TargetEstimate,
    default_targets,
    files_older_than,
    tree_size,
)
from filesoptim.config import CleanConfig, ConfigError, work_cache_dir
from tests.conftest import Out

NOW = time.time()


def touch(path: Path, days_old: float = 0, content: bytes = b"x" * 5000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    stamp = NOW - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def ctx(tmp_path: Path, isolated_env: Path) -> CleanContext:
    return CleanContext(CleanConfig(), isolated_env, isolated_env / ".cache",
                        isolated_env / ".local/share", root=tmp_path / "sysroot", now=NOW)


def cleaner(ctx: CleanContext, out: Out | None = None, *, root: bool = False) -> Cleaner:
    return Cleaner(ctx.config, out or Out(), context=ctx, is_root=root)


def estimate_of(ctx: CleanContext, key: str, *, root: bool = True) -> TargetEstimate:
    c = cleaner(ctx, root=root)
    return c.estimate(c.select([key], True))[0]


def names(estimate: TargetEstimate, base: Path) -> list[str]:
    return sorted(str(i.path.relative_to(base)) for i in estimate.items)


def test_files_older_than(tmp_path: Path) -> None:
    root = tmp_path / "c"
    old = touch(root / "old.bin", 100)
    touch(root / "new.bin", 1)
    os.link(old, root / "hardlink.bin")
    (root / "link").symlink_to("/nowhere")
    os.utime(root / "link", (0, 0), follow_symlinks=False)
    os.mkfifo(root / "fifo")
    touch(root / "skip" / "old.bin", 100)
    items = files_older_than([root, tmp_path / "missing"], 30, NOW, skip=[root / "skip"])
    by_name = {i.path.name: i for i in items}
    assert set(by_name) == {"old.bin", "hardlink.bin", "link"}
    assert sorted(i.size for i in items)[0] == 0  # the second hard link frees nothing
    assert len(files_older_than([root], 0, NOW)) == 5  # everything but the fifo


def test_walk_skips_vanishing_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "c"
    touch(root / "ok", 100)
    touch(root / "vanishing", 100)
    real = Path.lstat

    def lstat(self: Path) -> os.stat_result:
        if self.name == "vanishing":
            raise FileNotFoundError(self)
        return real(self)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert [i.path.name for i in files_older_than([root], 30, NOW)] == ["ok"]


def test_tree_size(tmp_path: Path) -> None:
    single = touch(tmp_path / "f")
    assert tree_size(single) == (single.lstat().st_blocks * 512, 1)
    touch(tmp_path / "d" / "a")
    touch(tmp_path / "d" / "b" / "c")
    size, count = tree_size(tmp_path / "d")
    assert count == 2 and size > 0


def test_user_targets(ctx: CleanContext) -> None:
    cache = ctx.cache
    touch(cache / "thumbnails" / "large" / "t.png")
    touch(ctx.home / ".thumbnails" / "old.png", 400)
    touch(cache / "app" / "old.dat", 200)
    touch(cache / "app" / "fresh.dat", 1)
    touch(work_cache_dir() / "run" / "keep.dat", 500)
    touch(cache / "pip" / "wheel.whl")
    touch(ctx.home / ".npm" / "_cacache" / "blob")
    touch(cache / "yarn" / "pkg")
    touch(cache / "go-build" / "obj")
    touch(ctx.home / ".cargo" / "registry" / "cache" / "crate")
    touch(cache / "mozilla" / "firefox" / "abc.default" / "cache2" / "entry")
    touch(cache / "google-chrome" / "Default" / "Cache" / "data")
    assert names(estimate_of(ctx, "thumbnails"), ctx.home) == [
        ".cache/thumbnails/large/t.png", ".thumbnails/old.png"]
    assert names(estimate_of(ctx, "cache"), cache) == ["app/old.dat"]
    for key in ("pip", "npm", "yarn", "go", "cargo"):
        assert len(estimate_of(ctx, key).items) == 1, key
    assert len(estimate_of(ctx, "browsers").items) == 2


def test_trash_target(ctx: CleanContext) -> None:
    trash = ctx.data / "Trash"
    touch(trash / "files" / "old.txt")
    touch(trash / "info" / "old.txt.trashinfo",
          content=b"[Trash Info]\nPath=/x\nDeletionDate=2000-01-01T00:00:00\n")
    touch(trash / "files" / "olddir" / "inner.txt")
    touch(trash / "info" / "olddir.trashinfo",
          content=b"[Trash Info]\nDeletionDate=2000-01-01T00:00:00\n")
    touch(trash / "files" / "recent.txt")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    touch(trash / "info" / "recent.txt.trashinfo",
          content=f"[Trash Info]\nDeletionDate={stamp}\n".encode())
    touch(trash / "files" / "orphan-old.txt", 100)
    touch(trash / "files" / "orphan-new.txt", 1)
    touch(trash / "info" / "dangling.trashinfo", content=b"[Trash Info]\n")
    touch(trash / "info" / "dangling-recent.trashinfo",
          content=f"[Trash Info]\nDeletionDate={stamp}\n".encode())
    (trash / "files" / "link").symlink_to("/nowhere")
    os.utime(trash / "files" / "link", (0, 0), follow_symlinks=False)
    estimate = estimate_of(ctx, "trash")
    items = {i.path.name: i for i in estimate.items}
    assert set(items) == {"old.txt", "olddir", "orphan-old.txt", "dangling.trashinfo", "link"}
    assert items["olddir"].is_dir and items["olddir"].files == 1
    assert items["old.txt"].extra == [trash / "info" / "old.txt.trashinfo"]
    assert items["dangling.trashinfo"].files == 0
    assert not items["link"].is_dir
    freed, errors = cleaner(ctx).apply([estimate])
    assert errors == [] and freed == estimate.size
    assert sorted(p.name for p in (trash / "files").iterdir()) == ["orphan-new.txt", "recent.txt"]
    assert (trash / "files").is_dir()  # the trash structure itself is kept


def test_system_targets(ctx: CleanContext) -> None:
    root = ctx.root
    apt = root / "var/cache/apt/archives"
    touch(apt / "a.deb")
    touch(apt / "partial" / "b.deb.part")
    (apt / "partial" / "sub").mkdir()
    touch(apt / "lock")
    touch(root / "var/cache/dnf/fedora-123/packages/x.rpm")
    pac = root / "var/cache/pacman/pkg"
    for age, version in ((30, "1.0-1"), (20, "1.1-1"), (10, "1.2-1")):
        touch(pac / f"vim-{version}-x86_64.pkg.tar.zst", age)
    touch(pac / "vim-1.0-1-x86_64.pkg.tar.zst.sig")
    touch(pac / "python-3.12-1-any.pkg.tar.zst")
    touch(pac / "README")
    (pac / "dir-1-1-any.pkg.tar.zst").mkdir()
    journal = root / "var/log/journal/machine"
    touch(journal / "system.journal", 100)
    touch(journal / "system@0001-0002.journal", 100)
    touch(journal / "user-1000@abc.journal~", 100)
    touch(journal / "system@0003-0004.journal", 1)
    (journal / "link@x.journal").symlink_to("/nowhere")
    touch(root / "var/lib/systemd/coredump/core.1", 30)
    touch(root / "var/lib/systemd/coredump/core.2", 1)
    assert names(estimate_of(ctx, "apt"), apt) == ["a.deb", "partial/b.deb.part"]
    assert len(estimate_of(ctx, "dnf").items) == 1
    pacman = estimate_of(ctx, "pacman")
    assert [i.path.name for i in pacman.items] == ["vim-1.0-1-x86_64.pkg.tar.zst"]
    assert pacman.items[0].files == 2 and pacman.items[0].extra
    assert names(estimate_of(ctx, "journal"), journal) == [
        "system@0001-0002.journal", "user-1000@abc.journal~"]
    assert names(estimate_of(ctx, "coredumps"), root) == ["var/lib/systemd/coredump/core.1"]


def test_missing_roots_give_nothing(ctx: CleanContext) -> None:
    for target in default_targets():
        assert estimate_of(ctx, target.key).items == [], target.key


def test_permission_errors(ctx: CleanContext, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = ctx.root / "var/log/journal"
    touch(journal / "m" / "x@y.journal", 100)
    real = Path.iterdir

    def iterdir(self: Path) -> Any:
        if self == journal:
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    estimate = estimate_of(ctx, "journal", root=False)
    assert estimate.note == "permission denied (run with sudo)"


def test_select_and_notes(ctx: CleanContext) -> None:
    c = cleaner(ctx)
    assert [t.key for t in c.select([], False)] == ["thumbnails", "trash", "cache"]
    assert "journal" in [t.key for t in c.select([], True)]
    with pytest.raises(ConfigError, match="unknown clean target"):
        c.select(["nope"], False)
    estimate = c.estimate(c.select(["apt"], False))[0]
    assert estimate.note.startswith("needs root")
    assert not c.can_apply(estimate)
    assert ctx.system("/var/x") == ctx.root / "var/x"


def test_apply_safety_and_errors(ctx: CleanContext, monkeypatch: pytest.MonkeyPatch) -> None:
    c = cleaner(ctx)
    target = c.select(["cache"], False)[0]
    inside = touch(ctx.cache / "app" / "old.dat", 200)
    stubborn = touch(ctx.cache / "app" / "stubborn.dat", 200)
    outside = touch(ctx.home / "precious.txt")
    estimate = TargetEstimate(target, [ctx.cache], [
        CleanItem(inside, 10), CleanItem(outside, 10), CleanItem(stubborn, 10),
    ])
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "stubborn.dat":
            raise PermissionError("busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    freed, errors = c.apply([estimate, TargetEstimate(target, [], note="skip me")])
    assert freed == 10 and not inside.exists() and outside.exists()
    assert any("outside the cleaned folders" in e for e in errors)
    assert any("busy" in e for e in errors)
    assert (ctx.cache / "app").exists()  # still holds stubborn.dat
    monkeypatch.setattr(Path, "unlink", real_unlink)
    stubborn.unlink()
    empty_root = ctx.cache
    c.apply([TargetEstimate(target, [empty_root, ctx.home / "missing"], [])])
    assert not (ctx.cache / "app").exists() and empty_root.exists()


def test_run_flow(ctx: CleanContext) -> None:
    out = Out()
    cleaner(ctx, out).list_targets()
    assert "coredumps" in out.text and "opt-in" in out.text
    nothing = Out()
    assert cleaner(ctx, nothing).run([], system=False, assume_yes=True, dry_run=False) == (0, [])
    assert "Nothing to clean." in nothing.text
    old = touch(ctx.cache / "app" / "old.dat", 200)
    dry = Out()
    assert cleaner(ctx, dry).run([], system=False, assume_yes=True, dry_run=True) == (0, [])
    assert old.exists() and "Cleaning estimate" in dry.text
    declined = Out(answers=["n"])
    cleaner(ctx, declined).run([], system=False, assume_yes=False, dry_run=False)
    assert old.exists()
    done = Out(answers=["y"])
    freed, _ = cleaner(ctx, done).run(["cache"], system=False, assume_yes=False,
                                      dry_run=False)
    assert freed > 0 and not old.exists() and "freed." in done.text
    touch(ctx.cache / "app" / "old2.dat", 200)
    noisy = Out()
    c = cleaner(ctx, noisy)
    c.targets[2].roots = lambda context: [context.data]  # items end up "outside" the roots
    c.targets[2].collect = lambda context, roots: [CleanItem(context.cache / "app" / "old2.dat",
                                                             1)]
    c.run(["cache"], system=False, assume_yes=True, dry_run=False)
    assert "✘" in noisy.text


def test_default_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clean_mod.os, "geteuid", lambda: 0)
    c = Cleaner(CleanConfig(), Out())
    assert c.is_root and c.context.home == Path.home()
