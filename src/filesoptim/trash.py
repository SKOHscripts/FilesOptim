"""Minimal freedesktop.org trash implementation (home trash and per-disk trashes)."""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from filesoptim.config import data_home
from filesoptim.fsutils import durable_copy, free_space, fsync_dir, fsync_file, safe_move

INFO_SUFFIX = ".trashinfo"


def trash_root() -> Path:
    return data_home() / "Trash"


@dataclass
class TrashEntry:
    name: str
    files_path: Path
    info_path: Path | None
    original_path: Path | None
    deleted_at: datetime | None


def _reserve_name(root: Path, name: str) -> tuple[str, Path]:
    """Atomically create the ``.trashinfo`` file for a free name (spec requirement)."""
    info_dir = root / "info"
    stem, dot, ext = name.partition(".")
    counter = 1
    candidate = name
    while True:
        info = info_dir / f"{candidate}{INFO_SUFFIX}"
        if not (root / "files" / candidate).exists():
            try:
                fd = os.open(info, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
                return candidate, info
        counter += 1
        candidate = f"{stem}.{counter}{dot}{ext}"


TRASH_RESERVE = 1024**3  # never fill a disk with trash copies: keep 1 GiB free


def _mount_point(path: Path) -> Path:
    current = path.absolute()
    while not os.path.ismount(current):
        current = current.parent
    return current


def _existing(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def trash_for(path: Path) -> tuple[Path, Path | None]:
    """Trash folder for ``path`` and its top directory (``None`` for the home trash).

    Files on another disk go to that disk's own ``.Trash-UID`` folder (freedesktop spec), so
    trashing them never copies data onto the system disk.
    """
    home = trash_root()
    if path.lstat().st_dev == _existing(home).stat().st_dev:
        return home, None
    top = _mount_point(path.parent)
    return top / f".Trash-{os.getuid()}", top


def _prepare(root: Path) -> bool:
    try:
        (root / "files").mkdir(parents=True, exist_ok=True)
        (root / "info").mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def send_to_trash(path: Path, *, keep_original: bool = False) -> Path:
    """Move ``path`` to the trash (or put a copy there with ``keep_original``).

    Nothing can be lost on the way: on the same disk the file is only renamed (or hard linked
    for ``keep_original``); a real copy is written completely and flushed to the disk before
    the original is ever removed. Returns the location inside the trash.
    """
    absolute = path.absolute()
    root, top = trash_for(absolute)
    if top is not None and not _prepare(root):  # cannot create the disk's own trash
        root, top = trash_root(), None
        size = absolute.lstat().st_size
        if free_space(_existing(root)) - size < TRASH_RESERVE:
            raise OSError(errno.ENOSPC, f"not enough space to put {absolute.name} in the trash")
    _prepare(root)
    name, info = _reserve_name(root, absolute.name)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    shown = absolute if top is None else absolute.relative_to(top)
    info.write_text(
        f"[Trash Info]\nPath={quote(str(shown))}\nDeletionDate={stamp}\n", encoding="utf-8"
    )
    fsync_file(info)
    destination = root / "files" / name
    try:
        if keep_original:
            try:
                os.link(absolute, destination)
            except OSError:
                durable_copy(absolute, destination)
        elif absolute.is_dir() and not absolute.is_symlink():
            absolute.rename(destination)  # folders are only ever renamed, never copied
        else:
            safe_move(absolute, destination)
        fsync_dir(destination.parent)
    except BaseException:
        info.unlink(missing_ok=True)
        if absolute.exists() or absolute.is_symlink():  # original intact: drop partial copies
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination, ignore_errors=True)
            else:
                destination.unlink(missing_ok=True)
        raise
    return destination


def _parse_info(info: Path) -> tuple[Path | None, datetime | None]:
    original: Path | None = None
    deleted: datetime | None = None
    try:
        lines = info.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None
    for line in lines:
        key, _, value = line.partition("=")
        if key == "Path":
            original = Path(unquote(value))
        elif key == "DeletionDate":
            try:
                deleted = datetime.fromisoformat(value.strip())
            except ValueError:
                deleted = None
    return original, deleted


def list_trash(root: Path | None = None) -> list[TrashEntry]:
    """Entries of the trash, including orphans (files without info and info without files)."""
    root = root or trash_root()
    entries: dict[str, TrashEntry] = {}
    files_dir, info_dir = root / "files", root / "info"
    if info_dir.is_dir():
        for info in sorted(info_dir.iterdir()):
            if info.name.endswith(INFO_SUFFIX):
                name = info.name[: -len(INFO_SUFFIX)]
                original, deleted = _parse_info(info)
                entries[name] = TrashEntry(name, files_dir / name, info, original, deleted)
    if files_dir.is_dir():
        for item in sorted(files_dir.iterdir()):
            if item.name not in entries:
                entries[item.name] = TrashEntry(item.name, item, None, None, None)
    return list(entries.values())
