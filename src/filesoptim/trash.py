"""Minimal freedesktop.org trash implementation (home trash only)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from filesoptim.config import data_home

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


def send_to_trash(path: Path, *, keep_original: bool = False) -> Path:
    """Move ``path`` to the trash (or put a copy there with ``keep_original``).

    A hard link is used for copies when possible, so keeping a copy costs no time.
    Returns the location inside the trash.
    """
    root = trash_root()
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "info").mkdir(parents=True, exist_ok=True)
    absolute = path.absolute()
    name, info = _reserve_name(root, absolute.name)
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    info.write_text(
        f"[Trash Info]\nPath={quote(str(absolute))}\nDeletionDate={stamp}\n", encoding="utf-8"
    )
    destination = root / "files" / name
    try:
        if keep_original:
            try:
                os.link(absolute, destination)
            except OSError:
                shutil.copy2(absolute, destination)
        else:
            shutil.move(absolute, destination)
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
