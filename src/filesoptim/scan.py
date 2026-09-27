"""Big files, empty folders and broken symbolic links."""

from __future__ import annotations

import heapq
import os
import re
import stat
from collections.abc import Iterable, Sequence
from pathlib import Path

from filesoptim.config import Config, config_home
from filesoptim.fsutils import WalkOptions, iter_files, iter_tree

_USER_DIR_RE = re.compile(r'^XDG_\w+_DIR="(.*)"\s*$')
DEFAULT_USER_DIRS = ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Public",
                     "Templates", "Videos")


def big_files(
    roots: Iterable[Path], walk: WalkOptions, *, top: int, min_size: int = 0
) -> list[tuple[int, Path]]:
    candidates = ((st.st_size, path) for path, st in iter_files(roots, walk)
                  if st.st_size >= min_size)
    return heapq.nlargest(top, candidates)


def broken_links(roots: Iterable[Path], walk: WalkOptions) -> list[tuple[Path, str]]:
    found = []
    for path, st in iter_tree(roots, walk):
        if stat.S_ISLNK(st.st_mode) and not path.exists():
            found.append((path, str(path.readlink())))
    return found


def protected_dirs(config: Config) -> set[Path]:
    """Folders never removed even when empty: home, XDG user folders, configured ones."""
    home = Path.home()
    result = {home.absolute(), *(home / name for name in DEFAULT_USER_DIRS)}
    user_dirs = config_home() / "user-dirs.dirs"
    if user_dirs.is_file():
        for line in user_dirs.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _USER_DIR_RE.match(line.strip())
            if match:
                result.add(Path(match.group(1).replace("$HOME", str(home))).absolute())
    result.update(Path(p).expanduser().absolute() for p in config.scan.protected)
    return result


def _entries(directory: Path) -> list[os.DirEntry[str]] | None:
    try:
        with os.scandir(directory) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:
        return None


def _visitable(entry: os.DirEntry[str], path: Path, walk: WalkOptions, root_dev: int) -> bool:
    """Real sub-folder we may look into (otherwise the entry counts as content)."""
    if not entry.is_dir(follow_symlinks=False) or walk.is_excluded(entry.name):
        return False
    try:
        other_fs = entry.stat(follow_symlinks=False).st_dev != root_dev
    except OSError:
        return False
    return not (walk.one_file_system and other_fs) and not os.path.ismount(path)


def empty_dirs(
    roots: Iterable[Path], walk: WalkOptions, protected: Sequence[Path] | set[Path]
) -> list[Path]:
    """Folders containing nothing but (removable) empty folders, deepest first.

    Anything else counts as content: files, links, hidden or excluded entries, unreadable or
    mounted folders. The roots and protected folders are never reported.
    """
    keep = {p.absolute() for p in protected}
    found: list[Path] = []
    for root in roots:
        root_entries = _entries(root)
        if root_entries is None:
            continue
        root_dev = root.lstat().st_dev
        stack: list[tuple[Path, list[os.DirEntry[str]]]] = [(root, root_entries)]
        has_content: dict[Path, bool] = {root: False}
        while stack:
            directory, pending = stack[-1]
            descended = False
            while pending:
                entry = pending.pop(0)
                child = Path(entry.path)
                if _visitable(entry, child, walk, root_dev):
                    child_entries = _entries(child)
                    if child_entries is None:
                        has_content[directory] = True
                        continue
                    has_content[child] = False
                    stack.append((child, child_entries))
                    descended = True
                    break
                has_content[directory] = True
            if descended:
                continue
            stack.pop()
            removable = not has_content[directory] and directory.absolute() not in keep
            if directory != root and removable:
                found.append(directory)
            if stack and not removable:
                has_content[stack[-1][0]] = True
    found.sort(key=lambda p: (-len(p.parts), str(p)))
    return found


def topmost(paths: Sequence[Path]) -> list[Path]:
    """Only the highest folders of a nested list (for display)."""
    chosen = set(paths)
    return sorted(p for p in paths if not any(parent in chosen for parent in p.parents))
