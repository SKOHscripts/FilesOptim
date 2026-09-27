"""Duplicate files: size -> partial hash -> full hash -> byte-by-byte check before acting."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from filesoptim.fsutils import (
    TEMP_MARK,
    WalkOptions,
    allocated_size,
    file_digest,
    is_within,
    iter_files,
    same_content,
    stat_key,
)
from filesoptim.trash import send_to_trash
from filesoptim.ui import Console, human_size

PARTIAL = 64 * 1024
KEEP_STRATEGIES = ("oldest", "newest", "shortest", "first")
ACTIONS = ("report", "trash", "delete", "hardlink", "symlink")


@dataclass
class DuplicateGroup:
    size: int
    files: list[Path]
    stats: dict[Path, os.stat_result] = field(default_factory=dict)
    keep: Path | None = None

    @property
    def remove(self) -> list[Path]:
        return [f for f in self.files if f != self.keep]

    @property
    def wasted(self) -> int:
        """Exact disk space freed by removing every copy but the kept one."""
        return sum(allocated_size(self.stats[f]) for f in self.remove)


def _split(paths: Sequence[Path], key: Callable[[Path], str]) -> list[list[Path]]:
    buckets: dict[str, list[Path]] = {}
    for path in paths:
        try:
            buckets.setdefault(key(path), []).append(path)
        except OSError:
            continue
    return [b for b in buckets.values() if len(b) > 1]


def find_duplicates(
    roots: Iterable[Path], walk: WalkOptions, *, min_size: int = 1
) -> list[DuplicateGroup]:
    by_size: dict[int, dict[tuple[int, int], tuple[Path, os.stat_result]]] = {}
    for path, st in iter_files(roots, walk):
        if st.st_size >= max(min_size, 1):
            # Hard links share an inode: they are not wasting space, keep one of them.
            by_size.setdefault(st.st_size, {}).setdefault((st.st_dev, st.st_ino), (path, st))
    groups: list[DuplicateGroup] = []
    for size, inodes in by_size.items():
        if len(inodes) < 2:
            continue
        stats = dict(inodes.values())
        paths = sorted(stats)
        buckets = [paths]
        if size > PARTIAL:  # cheap first pass on the beginning of big files
            buckets = _split(paths, lambda p: file_digest(p, limit=PARTIAL))
        for bucket in buckets:
            for same in _split(bucket, file_digest):
                groups.append(DuplicateGroup(size, same, {p: stats[p] for p in same}))
    groups.sort(key=lambda g: (-g.size * (len(g.files) - 1), str(g.files[0])))
    return groups


def choose_keeper(group: DuplicateGroup, strategy: str, prefer: Sequence[Path] = ()) -> Path:
    preferred = [f for f in group.files if any(is_within(f, p) for p in prefer)]
    pool = preferred or group.files
    if strategy == "oldest":
        return min(pool, key=lambda f: (group.stats[f].st_mtime, str(f)))
    if strategy == "newest":
        return max(pool, key=lambda f: (group.stats[f].st_mtime, str(f)))
    if strategy == "shortest":
        return min(pool, key=lambda f: (len(str(f)), str(f)))
    return pool[0]


def _replace_with_link(keeper: Path, duplicate: Path, *, symbolic: bool) -> None:
    tmp = duplicate.with_name(f".{duplicate.name}{TEMP_MARK}")
    if symbolic:
        tmp.symlink_to(keeper.absolute())
    else:
        os.link(keeper, tmp)
    try:
        tmp.replace(duplicate)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def resolve_duplicate(group: DuplicateGroup, duplicate: Path, action: str) -> None:
    """Remove/link one duplicate after re-checking it is still identical to the keeper."""
    keeper = group.keep
    assert keeper is not None
    current = duplicate.lstat()
    if stat_key(current) != stat_key(group.stats[duplicate]):
        raise RuntimeError("file changed since the scan")
    if not same_content(keeper, duplicate):
        raise RuntimeError("content differs from the kept file")
    if action == "trash":
        send_to_trash(duplicate)
    elif action == "delete":
        duplicate.unlink()
    elif action == "hardlink":
        if keeper.stat().st_dev != current.st_dev:
            raise RuntimeError("not on the same filesystem as the kept file")
        _replace_with_link(keeper, duplicate, symbolic=False)
    else:
        _replace_with_link(keeper, duplicate, symbolic=True)


def render_groups(
    groups: Sequence[DuplicateGroup], console: Console, *, limit: int | None = 20
) -> None:
    console.heading("Duplicates")
    if not groups:
        console.info("No duplicate found.")
        return
    shown = groups if limit is None else groups[:limit]
    for group in shown:
        console.print(f"{human_size(group.size)} × {len(group.files)}", "bold")
        for path in group.files:
            mark = "keep  " if path == group.keep else "remove"
            console.info(f"  {mark} {path}")
    if len(shown) < len(groups):
        console.info(f"… and {len(groups) - len(shown)} more group(s) (use --show-all).")
    total = sum(g.wasted for g in groups)
    copies = sum(len(g.remove) for g in groups)
    console.info(f"{len(groups)} group(s), {copies} redundant copy(ies): "
                 f"{human_size(total)} can be freed.")


def apply_actions(
    groups: Sequence[DuplicateGroup], action: str, console: Console
) -> tuple[int, list[str]]:
    freed, errors = 0, []
    for group in groups:
        for duplicate in group.remove:
            try:
                resolve_duplicate(group, duplicate, action)
            except (OSError, RuntimeError) as exc:
                errors.append(f"{duplicate}: {exc}")
                console.error(f"{duplicate}: {exc}")
            else:
                freed += allocated_size(group.stats[duplicate])
    return freed, errors
