from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from filesoptim.sorting.executor import (
    Journal,
    SortExecutor,
    UndoStep,
    _apply_undo,
    _stat_matches,
    iter_undo,
    journal_dir,
    list_journals,
    mark_undone,
    new_journal_path,
    plan_undo,
    read_journal,
)
from filesoptim.sorting.planner import SortOp, SortOptions, SortPlan
from tests.conftest import FakeTools, Out

DATE = datetime(2019, 6, 12, 10, 15, 30)


def touch(path: Path, content: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def entries_of(path: Path) -> list[dict[str, object]]:
    return read_journal(path)[1]


def test_journal_basics() -> None:
    path = new_journal_path("sort")
    assert path.parent == journal_dir() and path.name.startswith("sort-")
    journal = Journal(path, {"source": "/x"})
    journal.add({"type": "mkdir", "path": "/x/y"})
    assert path.read_text().count("\n") == 2  # flushed immediately
    journal.close()
    with path.open("a") as fh:
        fh.write("\n{broken json\n")
    header, entries = read_journal(path)
    assert header["source"] == "/x" and header["version"] == 1
    assert entries == [{"type": "mkdir", "path": "/x/y"}]
    with pytest.raises(FileExistsError):
        Journal(path, {})


def test_list_and_mark_undone() -> None:
    assert list_journals() == []
    first = new_journal_path("sort")
    Journal(first, {}).close()
    second = new_journal_path("sort")
    Journal(second, {}).close()
    assert list_journals() == [first, second]
    done = mark_undone(second)
    assert done.name.endswith(".undone.jsonl")
    assert list_journals() == [first]
    assert list_journals(include_undone=True) == [first, done]


def test_execute_move_copy_keep(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    moved = touch(src / "sub" / "a.jpg")
    copied = touch(src / "b.jpg")
    kept = touch(src / "c.jpg")
    stamp = DATE.timestamp()
    plan = SortPlan(SortOptions(src, dst, prune_empty=True), [
        SortOp(moved, dst / "Photos" / "2019" / "a.jpg", "move", "image", set_date=DATE,
               add_keywords=["k"], set_mtime=stamp),
        SortOp(copied, dst / "Photos" / "b.jpg", "copy", "image", add_keywords=["k"],
               set_mtime=stamp),
        SortOp(kept, kept, "keep", "image", set_mtime=stamp),
    ])
    tools = FakeTools(["exiftool"])
    result = SortExecutor(tools, Out()).execute(plan)
    assert result.done == 3 and result.failures == []
    assert (dst / "Photos" / "2019" / "a.jpg").exists() and not moved.exists()
    assert copied.exists() and (dst / "Photos" / "b.jpg").exists()
    assert kept.stat().st_mtime == stamp
    assert not (src / "sub").exists()  # pruned
    kinds = [e["type"] for e in entries_of(result.journal)]
    assert kinds == ["mkdir", "mkdir", "mkdir", "meta", "mtime", "move",
                     "copy", "meta", "mtime", "copied", "mtime", "rmdir"]
    assert [c[-1] for c in tools.calls] == [str(moved), str(dst / "Photos" / "b.jpg")]


def test_execute_failures(tmp_path: Path) -> None:
    src = tmp_path / "src"
    a = touch(src / "a.jpg")
    b = touch(src / "b.jpg")
    touch(tmp_path / "taken.jpg")
    plan = SortPlan(SortOptions(src, tmp_path, mode="copy", prune_empty=True), [
        SortOp(a, tmp_path / "taken.jpg", "copy", "image"),
        SortOp(b, tmp_path / "b.jpg", "move", "image", add_keywords=["x"]),
    ])
    tools = FakeTools(["exiftool"], exiftool=lambda argv: (1, "", "not writable"))
    out = Out()
    result = SortExecutor(tools, out).execute(plan)
    assert result.done == 0 and len(result.failures) == 2
    assert "appeared in the meantime" in out.text and "not writable" in out.text
    assert b.exists()


def test_round_trip_undo(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    photo = touch(src / "event" / "a.jpg", b"photo")
    copy_src = touch(src / "b.jpg", b"copy")
    os.utime(photo, (1000, 1000))
    plan = SortPlan(SortOptions(src, dst, prune_empty=True), [
        SortOp(photo, dst / "P" / "a.jpg", "move", "image", set_date=DATE, add_keywords=["k"],
               set_mtime=DATE.timestamp()),
        SortOp(copy_src, dst / "P" / "b.jpg", "copy", "image", add_keywords=["k"]),
    ])
    tools = FakeTools(["exiftool"])
    journal = SortExecutor(tools, Out()).execute(plan).journal
    header, entries = read_journal(journal)
    steps = plan_undo(entries, Path(header["source"]))
    descriptions = [s.description for s in steps]
    assert descriptions[0] == "recreate folder event"
    assert f"move back {dst / 'P' / 'a.jpg'} → event/a.jpg" in descriptions
    assert all(not s.blocked for s in steps)
    errors = [e for _, e in iter_undo(steps, tools)]
    assert errors == [""] * len(steps)
    assert photo.read_bytes() == b"photo" and photo.stat().st_mtime == 1000
    assert not (dst / "P").exists() and copy_src.exists()
    reverts = [c for c in tools.calls if "-XMP-dc:Subject+=k" not in c]
    assert reverts[-1][-1] == str(photo)  # metadata reverted once the file is back


def test_plan_undo_blocked_cases(tmp_path: Path) -> None:
    moved_away = tmp_path / "gone.jpg"
    occupied_src = touch(tmp_path / "src.jpg")
    occupied_dst = touch(tmp_path / "dst.jpg")
    copy_ok = touch(tmp_path / "copy_ok.jpg")
    copy_changed = touch(tmp_path / "copy_changed.jpg")
    copy_unknown = touch(tmp_path / "copy_unknown.jpg")
    st_ok, st_changed = copy_ok.stat(), copy_changed.stat()
    entries = [
        {"type": "move", "src": str(tmp_path / "a.jpg"), "dst": str(moved_away)},
        {"type": "move", "src": str(occupied_src), "dst": str(occupied_dst)},
        {"type": "copy", "src": "/x", "dst": str(copy_ok)},
        {"type": "copied", "path": str(copy_ok), "size": st_ok.st_size,
         "mtime_ns": st_ok.st_mtime_ns},
        {"type": "copy", "src": "/x", "dst": str(copy_changed)},
        {"type": "meta", "path": str(copy_changed), "category": "image", "set_date": False,
         "keywords": []},
        {"type": "copied", "path": str(copy_changed), "size": st_changed.st_size + 1,
         "mtime_ns": st_changed.st_mtime_ns},
        {"type": "copy", "src": "/x", "dst": str(copy_unknown)},
        {"type": "copy", "src": "/x", "dst": str(tmp_path / "vanished.jpg")},
        {"type": "unknown"},
    ]
    steps = {s.description: s.blocked for s in plan_undo(entries)}
    assert steps == {
        f"move back {moved_away} → {tmp_path / 'a.jpg'}": "file no longer at its sorted location",
        f"move back {occupied_dst} → {occupied_src}": "original location is occupied",
        f"delete copy {copy_ok}": "",
        f"delete copy {copy_changed}": "copy was modified since, kept",
        f"delete copy {copy_unknown}": "copy was modified since, kept",
        f"delete copy {tmp_path / 'vanished.jpg'}": "copy already removed",
    }


def test_apply_undo_steps(tmp_path: Path) -> None:
    tools = FakeTools(["exiftool"])
    empty = tmp_path / "empty"
    empty.mkdir()
    full = tmp_path / "full"
    touch(full / "x")
    recreate = tmp_path / "re" / "created"
    stamped = touch(tmp_path / "stamped")
    steps = [
        UndoStep("a", {"type": "mkdir", "path": str(empty)}),
        UndoStep("b", {"type": "mkdir", "path": str(full)}),
        UndoStep("c", {"type": "rmdir", "path": str(recreate)}),
        UndoStep("d", {"type": "mtime", "path": str(stamped), "old": [5, 6]}),
        UndoStep("e", {"type": "meta", "path": str(tmp_path / "nope"), "category": "image",
                       "set_date": True, "keywords": []}),
        UndoStep("f", {"type": "move", "src": str(tmp_path / "back"),
                       "dst": str(tmp_path / "missing")}),
        UndoStep("g", {"type": "copy", "dst": str(tmp_path / "x")}, blocked="kept"),
    ]
    results = [(s.description, e) for s, e in iter_undo(steps, tools)]
    assert results[:4] == [("a", ""), ("b", ""), ("c", ""), ("d", "")]
    assert "not found" in results[4][1]
    assert results[5][1] != "" and results[6] == ("g", "kept")
    assert not empty.exists() and full.exists() and recreate.is_dir()
    assert stamped.stat().st_mtime == 6
    copy = touch(tmp_path / "a_copy")
    _apply_undo(UndoStep("h", {"type": "copy", "dst": str(copy)}), tools)
    assert not copy.exists()
    json.dumps([s.entry for s in steps])  # entries stay serialisable
    assert not _stat_matches(tmp_path / "does-not-exist", 1, 1)
