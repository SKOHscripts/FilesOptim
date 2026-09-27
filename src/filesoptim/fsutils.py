"""Filesystem helpers: safe traversal, atomic replacement, hashing, naming."""

from __future__ import annotations

import contextlib
import filecmp
import hashlib
import os
import re
import shutil
import stat
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from filesoptim.config import GeneralConfig

TEMP_MARK = ".filesoptim-tmp"
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f/\\<>:"|?*]')
_MAX_NAME_BYTES = 200


@dataclass
class WalkOptions:
    """What a traversal visits. Symbolic links are never followed."""

    exclude: Sequence[str] = field(default_factory=tuple)
    skip_hidden: bool = True
    one_file_system: bool = True

    @classmethod
    def from_config(
        cls,
        general: GeneralConfig,
        *,
        include_hidden: bool = False,
        extra_exclude: Sequence[str] = (),
    ) -> WalkOptions:
        return cls(
            exclude=(*general.exclude, *extra_exclude),
            skip_hidden=general.skip_hidden and not include_hidden,
            one_file_system=general.one_file_system,
        )

    def is_excluded(self, name: str) -> bool:
        if TEMP_MARK in name:
            return True
        if self.skip_hidden and name.startswith("."):
            return True
        return any(fnmatch(name, pattern) for pattern in self.exclude)


def iter_tree(
    roots: Iterable[Path],
    options: WalkOptions,
    *,
    skip_dirs: Iterable[Path] = (),
) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield ``(path, lstat)`` for every entry (files, dirs, links...) below the roots.

    Directories are yielded before their content; excluded names are not yielded nor visited.
    """
    skipped = {p.absolute() for p in skip_dirs}
    for root in roots:
        try:
            root_stat = root.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(root_stat.st_mode):
            yield root, root_stat
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            subdirs: list[Path] = []
            for entry in entries:
                if options.is_excluded(entry.name):
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                path = Path(entry.path)
                if stat.S_ISDIR(entry_stat.st_mode):
                    if options.one_file_system and entry_stat.st_dev != root_stat.st_dev:
                        continue
                    if path.absolute() in skipped:
                        continue
                    subdirs.append(path)
                yield path, entry_stat
            stack.extend(reversed(subdirs))


def iter_files(
    roots: Iterable[Path],
    options: WalkOptions,
    *,
    skip_dirs: Iterable[Path] = (),
) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield regular files only (never symlinks) with their ``lstat``."""
    for path, st in iter_tree(roots, options, skip_dirs=skip_dirs):
        if stat.S_ISREG(st.st_mode):
            yield path, st


def allocated_size(st: os.stat_result) -> int:
    """Bytes actually used on disk (what deleting the file would free)."""
    return st.st_blocks * 512


def stat_key(st: os.stat_result) -> tuple[int, int]:
    return st.st_size, st.st_mtime_ns


def file_digest(path: Path, *, limit: int | None = None, chunk_size: int = 1 << 20) -> str:
    """BLAKE2b digest of the file (or of its first ``limit`` bytes)."""
    digest = hashlib.blake2b(digest_size=32)
    remaining = limit
    with path.open("rb") as fh:
        while remaining is None or remaining > 0:
            size = chunk_size if remaining is None else min(chunk_size, remaining)
            block = fh.read(size)
            if not block:
                break
            digest.update(block)
            if remaining is not None:
                remaining -= len(block)
    return digest.hexdigest()


def same_content(first: Path, second: Path) -> bool:
    """Byte-by-byte comparison."""
    return filecmp.cmp(first, second, shallow=False)


def install_file(new: Path, original: Path, target: Path | None = None) -> Path:
    """Atomically put ``new`` in place of ``original``.

    The result keeps the permissions, timestamps and (when allowed) ownership of the original.
    With ``target`` (a different name, e.g. a new extension) the original is removed afterwards.
    """
    dest = target or original
    if dest != original and dest.exists():
        raise FileExistsError(f"{dest} already exists")
    original_stat = original.stat()
    tmp = dest.with_name(f".{dest.name}{TEMP_MARK}")
    try:
        shutil.move(new, tmp)
        shutil.copystat(original, tmp)
        with contextlib.suppress(OSError):
            os.chown(tmp, original_stat.st_uid, original_stat.st_gid)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if dest != original:
        original.unlink()
    return dest


def sanitize_component(name: str) -> str:
    """Make ``name`` a safe, portable single path component."""
    cleaned = _UNSAFE_CHARS.sub("_", name).strip(" .")
    while len(cleaned.encode("utf-8")) > _MAX_NAME_BYTES:
        cleaned = cleaned[:-1]
    return cleaned or "_"


def unique_path(path: Path, taken: set[Path] | None = None) -> Path:
    """``path`` or ``stem_1.ext``, ``stem_2.ext``... avoiding existing files and ``taken``."""
    taken = taken or set()
    candidate = path
    counter = 1
    while candidate.exists() or candidate.is_symlink() or candidate in taken:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return candidate


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def display_path(path: Path, base: Path | None) -> str:
    """Path relative to ``base`` when possible (for compact tables)."""
    if base is not None:
        try:
            return str(path.relative_to(base))
        except ValueError:
            pass
    return str(path)


def prune_empty_dirs(start: Path, stop: Path) -> list[Path]:
    """Remove ``start`` and its parents while they are empty, never removing ``stop``."""
    removed: list[Path] = []
    current = start
    stop = stop.absolute()
    while current.absolute() != stop and is_within(current, stop):
        try:
            current.rmdir()
        except OSError:
            break
        removed.append(current)
        current = current.parent
    return removed


def free_space(path: Path) -> int:
    return shutil.disk_usage(path).free
