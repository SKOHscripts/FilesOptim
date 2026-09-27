from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from filesoptim import trash
from filesoptim.categories import category_of, extension
from filesoptim.state import StateDB
from filesoptim.trash import list_trash, send_to_trash, trash_root


def test_send_to_trash_moves_and_writes_info(tmp_path: Path, isolated_env: Path) -> None:
    doc = tmp_path / "my doc.txt"
    doc.write_text("hello")
    trashed = send_to_trash(doc)
    assert not doc.exists()
    assert trashed == trash_root() / "files" / "my doc.txt"
    info = (trash_root() / "info" / "my doc.txt.trashinfo").read_text()
    assert f"Path={quote(str(doc))}" in info
    assert "DeletionDate=" in info
    doc.write_text("again")
    assert send_to_trash(doc).name == "my doc.2.txt"


def test_send_to_trash_keep_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    linked = send_to_trash(video, keep_original=True)
    assert video.exists()
    assert linked.stat().st_ino == video.stat().st_ino

    def no_link(*args: Any) -> None:
        raise OSError("cross-device")

    monkeypatch.setattr(trash.os, "link", no_link)
    copied = send_to_trash(video, keep_original=True)
    assert copied.read_bytes() == b"data"
    assert copied.stat().st_ino != video.stat().st_ino


def test_send_to_trash_failure_removes_info(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    item = tmp_path / "x.bin"
    item.write_bytes(b"1")

    def broken(*args: Any) -> None:
        raise OSError("boom")

    monkeypatch.setattr(trash, "safe_move", broken)
    with pytest.raises(OSError):
        send_to_trash(item)
    assert not list((trash_root() / "info").iterdir())
    assert item.read_bytes() == b"1"


def test_partial_copies_are_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = tmp_path / "big.mkv"
    item.write_bytes(b"video")
    folder = tmp_path / "album"
    (folder / "sub").mkdir(parents=True)

    def no_link(*args: Any) -> None:
        raise OSError("cross-device")

    def partial_copy(src: Any, dst: Any, **kwargs: Any) -> None:
        Path(dst).write_bytes(b"vi")
        raise OSError("No space left on device")

    monkeypatch.setattr(trash.os, "link", no_link)
    monkeypatch.setattr(shutil, "copy2", partial_copy)
    with pytest.raises(OSError, match="No space"):
        send_to_trash(item, keep_original=True)
    assert item.read_bytes() == b"video"
    assert not list((trash_root() / "files").iterdir())

    real_rename = Path.rename

    def partial_tree(self: Path, target: Any) -> Any:
        if self == folder:
            (Path(target) / "sub").mkdir(parents=True)
            raise OSError("No space left on device")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", partial_tree)
    with pytest.raises(OSError):
        send_to_trash(folder)
    assert folder.is_dir() and not list((trash_root() / "files").iterdir())
    monkeypatch.undo()

    def moved_then_failed(src: Path, dst: Path) -> None:
        src.rename(dst)
        raise OSError("could not sync the folder")

    monkeypatch.setattr(trash, "safe_move", moved_then_failed)
    with pytest.raises(OSError):
        send_to_trash(item)
    # the original is gone: its only copy (in the trash) must never be deleted
    assert (trash_root() / "files" / "big.mkv").read_bytes() == b"video"


def test_folders_are_renamed_into_the_trash(tmp_path: Path) -> None:
    folder = tmp_path / "album"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "a.txt").write_text("x")
    trashed = send_to_trash(folder)
    assert (trashed / "sub" / "a.txt").read_text() == "x" and not folder.exists()


def test_other_disks_use_their_own_trash(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    disk = tmp_path / "disk"
    photo = disk / "photos" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"photo")
    home_dev = trash._existing(trash_root()).stat().st_dev
    real_lstat = Path.lstat

    def lstat(self: Path) -> os.stat_result:
        st = real_lstat(self)
        if self == photo:
            values = list(st)
            values[2] = home_dev + 1  # pretend the photo lives on another disk
            return os.stat_result(values)
        return st

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(trash.os.path, "ismount", lambda p: Path(p) in (disk, Path("/")))
    root, top = trash.trash_for(photo)
    assert (root, top) == (disk / f".Trash-{os.getuid()}", disk)
    trashed = send_to_trash(photo)
    assert trashed == root / "files" / "a.jpg" and trashed.read_bytes() == b"photo"
    info = (root / "info" / "a.jpg.trashinfo").read_text()
    assert "Path=photos/a.jpg" in info  # relative to the disk, as the spec requires


def test_unwritable_disk_trash_falls_back_safely(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"photo")
    monkeypatch.setattr(trash, "trash_for", lambda p: (tmp_path / "ro" / ".Trash-0", tmp_path))
    real_mkdir = Path.mkdir

    def mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
        if "ro" in self.parts:
            raise PermissionError("read-only")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(trash, "free_space", lambda p: 10)
    with pytest.raises(OSError, match="not enough space"):
        send_to_trash(photo)
    assert photo.exists()
    monkeypatch.setattr(trash, "free_space", lambda p: 10**15)
    assert send_to_trash(photo) == trash_root() / "files" / "a.jpg"


def test_reserved_name_race(tmp_path: Path) -> None:
    root = trash_root()
    (root / "info").mkdir(parents=True)
    (root / "files").mkdir()
    (root / "info" / "a.txt.trashinfo").write_text("[Trash Info]\n")  # info without file
    (root / "files" / "a.2.txt").write_text("")  # file without info
    item = tmp_path / "a.txt"
    item.write_text("x")
    assert send_to_trash(item).name == "a.3.txt"
    noext = tmp_path / "README"
    noext.write_text("x")
    assert send_to_trash(noext).name == "README"
    noext.write_text("y")
    assert send_to_trash(noext).name == "README.2"


def test_list_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert list_trash() == []
    root = trash_root()
    (root / "files").mkdir(parents=True)
    (root / "info").mkdir()
    (root / "files" / "good").write_text("x")
    (root / "info" / "good.trashinfo").write_text(
        "[Trash Info]\nPath=/home/me/good\nDeletionDate=2020-01-02T03:04:05\n")
    (root / "info" / "baddate.trashinfo").write_text("[Trash Info]\nDeletionDate=yesterday\n")
    (root / "info" / "unreadable.trashinfo").write_text("")
    (root / "info" / "notes.txt").write_text("not an info file")
    (root / "files" / "orphan").mkdir()
    real_read = Path.read_text

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "unreadable.trashinfo":
            raise OSError("io error")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    entries = {e.name: e for e in list_trash()}
    assert set(entries) == {"good", "baddate", "unreadable", "orphan"}
    assert entries["good"].original_path == Path("/home/me/good")
    assert entries["good"].deleted_at == datetime(2020, 1, 2, 3, 4, 5)
    assert entries["baddate"].deleted_at is None
    assert entries["unreadable"].original_path is None
    assert entries["orphan"].info_path is None


@pytest.mark.parametrize(
    ("name", "category"),
    [("a.JPG", "image"), ("b.nef", "image"), ("c.MKV", "video"), ("d.flac", "audio"),
     ("e.pdf", "document"), ("f.tar", "archive"), ("g.xyz", "other"), ("noext", "other")],
)
def test_categories(name: str, category: str) -> None:
    assert category_of(Path(name)) == category


def test_extension() -> None:
    assert extension(Path("a.TaR")) == "tar"
    assert extension(Path("a")) == ""


def test_state_db(tmp_path: Path) -> None:
    db_path = tmp_path / "sub" / "state.sqlite"
    photo = tmp_path / "photo.jpg"
    with StateDB(db_path) as state:
        assert state.lookup(photo, 10, 20, "sig") is None
        state.record(photo, 10, 20, "sig", "optimised")
        assert state.lookup(photo, 10, 20, "sig") == "optimised"
        assert state.lookup(photo, 11, 20, "sig") is None
        assert state.lookup(photo, 10, 20, "other") is None
        state.record(photo, 11, 21, "sig", "no gain")
        assert state.count() == 1
    with StateDB(db_path) as again:
        assert again.lookup(photo, 11, 21, "sig") == "no gain"
        again.forget(photo)
        assert again.count() == 0
    memory = StateDB(None)
    memory.record(Path("relative.jpg"), 1, 1, "s", "x")
    assert memory.lookup(Path.cwd() / "relative.jpg", 1, 1, "s") == "x"
    memory.close()
