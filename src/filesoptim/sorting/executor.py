"""Execute a sorting plan with a journal, and undo it later."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from filesoptim.config import state_dir
from filesoptim.fsutils import (
    TEMP_MARK,
    display_path,
    durable_copy,
    fsync_dir,
    prune_empty_dirs,
    rename_no_clobber,
    safe_move,
)
from filesoptim.sorting.metadata import revert_metadata, write_metadata
from filesoptim.sorting.planner import SortOp, SortPlan
from filesoptim.tools import ToolError, Tools
from filesoptim.ui import Console

JOURNAL_VERSION = 1
UNDONE_SUFFIX = ".undone"


def journal_dir() -> Path:
    return state_dir() / "journals"


class Journal:
    """Append-only JSON-lines log, written to the disk before every operation.

    Each step is recorded *before* it is performed, so whatever the moment of an interruption
    (Ctrl+C, kill, power cut), the journal knows everything that may have been done.
    """

    def __init__(self, path: Path, header: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh: IO[str] = path.open("x", encoding="utf-8")
        self.add({"type": "header", "version": JOURNAL_VERSION, **header})

    def add(self, entry: dict[str, Any]) -> None:
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()


def new_journal_path(prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return journal_dir() / f"{prefix}-{stamp}.jsonl"


@dataclass
class ExecutionResult:
    journal: Path
    done: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)


class SortExecutor:
    def __init__(self, tools: Tools, console: Console) -> None:
        self.tools = tools
        self.console = console

    def _ensure_dir(self, directory: Path, journal: Journal) -> None:
        missing = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        for folder in reversed(missing):
            folder.mkdir()
            journal.add({"type": "mkdir", "path": str(folder)})

    def _change_in_place(self, op: SortOp, path: Path, journal: Journal) -> None:
        if op.changes_metadata:
            journal.add({"type": "meta", "path": str(path), "category": op.category,
                         "set_date": op.set_date is not None, "keywords": op.add_keywords})
            write_metadata(self.tools, path, op.category, op.set_date, op.add_keywords)
        if op.set_mtime is not None:
            st = path.stat()
            journal.add({"type": "mtime", "path": str(path), "old": [st.st_atime, st.st_mtime]})
            os.utime(path, (st.st_atime, op.set_mtime))

    def _run(self, op: SortOp, journal: Journal) -> None:
        if op.action == "keep":
            self._change_in_place(op, op.source, journal)
            return
        if op.destination.exists() or op.destination.is_symlink():
            raise FileExistsError(f"{op.destination} appeared in the meantime")
        self._ensure_dir(op.destination.parent, journal)
        if op.action == "copy":
            journal.add({"type": "copy", "src": str(op.source), "dst": str(op.destination)})
            tmp = op.destination.with_name(f".{op.destination.name}{TEMP_MARK}")
            durable_copy(op.source, tmp)
            try:
                rename_no_clobber(tmp, op.destination)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            fsync_dir(op.destination.parent)
            self._change_in_place(op, op.destination, journal)
            st = op.destination.stat()
            journal.add({"type": "copied", "path": str(op.destination),
                         "size": st.st_size, "mtime_ns": st.st_mtime_ns})
        else:
            self._change_in_place(op, op.source, journal)
            journal.add({"type": "move", "src": str(op.source), "dst": str(op.destination)})
            safe_move(op.source, op.destination)

    def execute(self, plan: SortPlan) -> ExecutionResult:
        options = plan.options
        journal = Journal(new_journal_path("sort"), {
            "created": datetime.now().isoformat(timespec="seconds"),
            "source": str(options.source), "destination": str(options.destination),
            "mode": options.mode,
        })
        result = ExecutionResult(journal.path)
        try:
            for index, op in enumerate(plan.ops, start=1):
                self.console.progress(index, len(plan.ops), op.source.name)
                try:
                    self._run(op, journal)
                except (OSError, ToolError) as exc:
                    result.failures.append((op.source, str(exc)))
                    self.console.error(f"{display_path(op.source, options.source)}: {exc}")
                else:
                    result.done += 1
            if options.prune_empty and options.mode == "move":
                parents = {op.source.parent for op in plan.ops if op.action == "move"}
                for parent in sorted(parents, key=lambda p: len(p.parts), reverse=True):
                    for removed in prune_empty_dirs(parent, options.source):
                        journal.add({"type": "rmdir", "path": str(removed)})
        finally:
            journal.close()
        return result


# ------------------------------------------------------------------------------------------
# Undo
# ------------------------------------------------------------------------------------------
@dataclass
class UndoStep:
    description: str
    entry: dict[str, Any]
    blocked: str = ""


def list_journals(*, include_undone: bool = False) -> list[Path]:
    folder = journal_dir()
    if not folder.is_dir():
        return []
    journals = sorted(p for p in folder.iterdir() if p.suffix == ".jsonl")
    if include_undone:
        return journals
    return [p for p in journals if not p.stem.endswith(UNDONE_SUFFIX)]


def read_journal(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a crash may have left a partial last line
        if entry.get("type") == "header":
            header = entry
        else:
            entries.append(entry)
    return header, entries


def _stat_matches(path: Path, size: int, mtime_ns: int) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size == size and st.st_mtime_ns == mtime_ns


def plan_undo(entries: list[dict[str, Any]], base: Path | None = None) -> list[UndoStep]:
    """Reverse steps. Paths in descriptions are shown relative to ``base`` when possible."""

    def show(value: str) -> str:
        return display_path(Path(value), base)

    steps: list[UndoStep] = []
    copied = {e["path"]: e for e in entries if e.get("type") == "copied"}
    for entry in reversed(entries):
        kind = entry.get("type")
        if kind in ("meta", "mtime") and entry["path"] in copied:
            continue  # the copy itself is deleted
        if kind == "move":
            src, dst = Path(entry["src"]), Path(entry["dst"])
            blocked = ""
            if not dst.exists() and src.exists():
                blocked = "was not moved (interrupted before): nothing to undo"
            elif not dst.exists():
                blocked = "file no longer at its sorted location"
            elif src.exists() or src.is_symlink():
                blocked = "original location is occupied"
            steps.append(UndoStep(f"move back {show(str(dst))} → {show(str(src))}", entry,
                                  blocked))
        elif kind == "copy":
            dst = Path(entry["dst"])
            info = copied.get(entry["dst"])
            blocked = ""
            if not dst.exists():
                blocked = "copy already removed"
            elif info is None or not _stat_matches(dst, info["size"], info["mtime_ns"]):
                blocked = "copy was modified since, kept"
            steps.append(UndoStep(f"delete copy {show(str(dst))}", entry, blocked))
        elif kind == "mkdir":
            steps.append(UndoStep(f"remove folder {show(entry['path'])} (if empty)", entry))
        elif kind == "rmdir":
            steps.append(UndoStep(f"recreate folder {show(entry['path'])}", entry))
        elif kind == "meta":
            # Checked when applied: the file is usually moved back by an earlier step.
            steps.append(UndoStep(f"revert metadata of {show(entry['path'])}", entry))
        elif kind == "mtime":
            steps.append(UndoStep(f"restore modification time of {show(entry['path'])}", entry))
    return steps


def _apply_undo(step: UndoStep, tools: Tools) -> None:
    entry = step.entry
    kind = entry["type"]
    if kind == "move":
        Path(entry["src"]).parent.mkdir(parents=True, exist_ok=True)
        safe_move(Path(entry["dst"]), Path(entry["src"]))
    elif kind == "copy":
        Path(entry["dst"]).unlink()
    elif kind == "mkdir":
        with contextlib.suppress(OSError):  # not empty (user added files) or gone: keep it
            Path(entry["path"]).rmdir()
    elif kind == "rmdir":
        Path(entry["path"]).mkdir(parents=True, exist_ok=True)
    elif kind == "meta":
        path = Path(entry["path"])
        if not path.exists():
            raise FileNotFoundError(f"{path} not found")
        revert_metadata(tools, path, entry["category"], entry["set_date"], entry["keywords"])
    else:  # mtime
        atime, mtime = entry["old"]
        os.utime(entry["path"], (atime, mtime))


def iter_undo(steps: list[UndoStep], tools: Tools) -> Iterator[tuple[UndoStep, str]]:
    """Apply the steps, yielding ``(step, error)`` (empty error on success)."""
    for step in steps:
        if step.blocked:
            yield step, step.blocked
            continue
        try:
            _apply_undo(step, tools)
        except (OSError, ToolError) as exc:
            yield step, str(exc)
        else:
            yield step, ""


def mark_undone(journal: Path) -> Path:
    target = journal.with_name(f"{journal.stem}{UNDONE_SUFFIX}{journal.suffix}")
    journal.rename(target)
    return target
