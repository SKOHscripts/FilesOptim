"""Cache/trash/log cleaning with an exact estimate of the space freed before deleting."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from filesoptim.config import CleanConfig, ConfigError, cache_home, data_home, work_cache_dir
from filesoptim.fsutils import allocated_size, is_within, stale_run_dirs
from filesoptim.trash import list_trash
from filesoptim.ui import Console, human_size

DAY = 86400
_PACMAN_RE = re.compile(
    r"^(?P<name>.+)-(?P<ver>[^-]+)-(?P<rel>[^-]+)-(?P<arch>[^-]+)\.pkg\.tar(\.\w+)?$"
)


@dataclass
class CleanItem:
    path: Path
    size: int
    files: int = 1
    is_dir: bool = False
    extra: list[Path] = field(default_factory=list)


@dataclass
class CleanContext:
    config: CleanConfig
    home: Path
    cache: Path
    data: Path
    root: Path = Path("/")
    now: float = field(default_factory=time.time)

    def system(self, path: str) -> Path:
        return self.root / path.lstrip("/")


@dataclass
class CleanTarget:
    key: str
    description: str
    roots: Callable[[CleanContext], list[Path]]
    collect: Callable[[CleanContext, list[Path]], list[CleanItem]]
    system: bool = False
    default: bool = True
    prune: bool = True


@dataclass
class TargetEstimate:
    target: CleanTarget
    roots: list[Path]
    items: list[CleanItem] = field(default_factory=list)
    note: str = ""

    @property
    def size(self) -> int:
        return sum(i.size for i in self.items)

    @property
    def files(self) -> int:
        return sum(i.files for i in self.items)


# ------------------------------------------------------------------------------------------
# Collectors
# ------------------------------------------------------------------------------------------
def _usable_root(root: Path) -> bool:
    """False when ``root`` does not exist; ``PermissionError`` when it cannot be read."""
    if not root.is_dir():
        return False
    next(root.iterdir(), None)  # raises PermissionError when unreadable
    return True


def _walk_files(root: Path, skip: Sequence[Path] = ()) -> Iterable[tuple[Path, os.stat_result]]:
    skipped = {p.absolute() for p in skip}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        dirnames[:] = [d for d in dirnames if (base / d).absolute() not in skipped]
        for name in filenames:
            path = base / name
            try:
                yield path, path.lstat()
            except OSError:
                continue


def _unused_for(st: os.stat_result, now: float) -> float:
    if stat.S_ISLNK(st.st_mode):  # resolving a link updates its access time: ignore it
        return now - st.st_mtime
    return now - max(st.st_mtime, st.st_atime)


def files_older_than(
    roots: Sequence[Path], days: int, now: float, skip: Sequence[Path] = ()
) -> list[CleanItem]:
    """Regular files and symlinks not used for ``days`` days (0 = all), hard links counted once."""
    items: list[CleanItem] = []
    seen: set[tuple[int, int]] = set()
    for root in roots:
        if not _usable_root(root):
            continue
        for path, st in _walk_files(root, skip):
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                continue
            if days and _unused_for(st, now) < days * DAY:
                continue
            key = (st.st_dev, st.st_ino)
            size = 0 if key in seen else allocated_size(st)
            seen.add(key)
            items.append(CleanItem(path, size))
    return items


def tree_size(path: Path) -> tuple[int, int]:
    """(allocated bytes, number of files) of a file or directory tree."""
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode):
        return allocated_size(st), 1
    size, count = allocated_size(st), 0
    for _, entry in _walk_files(path):
        size += allocated_size(entry)
        count += 1
    return size, count


Collector = Callable[[CleanContext, list[Path]], list[CleanItem]]


def _collect_age(days: Callable[[CleanConfig], int]) -> Collector:
    def collect(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
        return files_older_than(roots, days(ctx.config), ctx.now)

    return collect


def _collect_all(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    return files_older_than(roots, 0, ctx.now)


def _collect_user_cache(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    skip = [ctx.cache / "thumbnails", work_cache_dir()]
    return files_older_than(roots, ctx.config.cache_max_age_days, ctx.now, skip)


def _collect_trash(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    items: list[CleanItem] = []
    limit = ctx.config.trash_max_age_days * DAY
    for root in roots:
        for entry in list_trash(root):
            exists = entry.files_path.exists() or entry.files_path.is_symlink()
            if entry.deleted_at is not None:
                age = ctx.now - entry.deleted_at.timestamp()
            elif exists:
                age = ctx.now - entry.files_path.lstat().st_mtime
            else:
                age = limit  # dangling .trashinfo: always removable
            if age < limit:
                continue
            extra = [entry.info_path] if entry.info_path else []
            if exists:
                size, count = tree_size(entry.files_path)
                is_dir = entry.files_path.is_dir() and not entry.files_path.is_symlink()
                items.append(CleanItem(entry.files_path, size, count, is_dir, extra))
            else:  # dangling .trashinfo: an entry always has a file or an info
                assert entry.info_path is not None
                items.append(CleanItem(entry.info_path, allocated_size(entry.info_path.lstat()), 0))
    return items


def _collect_apt(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    items = []
    for root in roots:
        if _usable_root(root):
            for path in sorted(root.glob("*.deb")) + sorted(root.glob("partial/*")):
                if path.is_file():
                    items.append(CleanItem(path, allocated_size(path.lstat())))
    return items


def _collect_dnf(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    items = []
    for root in roots:
        if _usable_root(root):
            for path in sorted(root.glob("**/packages/*.rpm")):
                items.append(CleanItem(path, allocated_size(path.lstat())))
    return items


def _collect_pacman(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    """Keep the ``pacman_keep`` most recent versions of each package (like ``paccache``)."""
    items: list[CleanItem] = []
    for root in roots:
        if not _usable_root(root):
            continue
        groups: dict[tuple[str, str], list[Path]] = {}
        for path in root.iterdir():
            match = _PACMAN_RE.match(path.name)
            if match and path.is_file():
                groups.setdefault((match["name"], match["arch"]), []).append(path)
        for versions in groups.values():
            versions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for path in versions[ctx.config.pacman_keep:]:
                signature = path.with_name(path.name + ".sig")
                extra = [signature] if signature.exists() else []
                size = allocated_size(path.lstat()) + sum(allocated_size(s.lstat()) for s in extra)
                items.append(CleanItem(path, size, 1 + len(extra), extra=extra))
    return sorted(items, key=lambda i: i.path)


def _collect_journal(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    """Archived journal files only (the active ones are never touched)."""
    limit = ctx.config.journal_max_age_days * DAY
    items = []
    for root in roots:
        if not _usable_root(root):
            continue
        for path in sorted(root.glob("*/*.journal*")):
            archived = "@" in path.name or path.name.endswith(".journal~")
            st = path.lstat()
            if archived and stat.S_ISREG(st.st_mode) and ctx.now - st.st_mtime >= limit:
                items.append(CleanItem(path, allocated_size(st)))
    return items


def _collect_leftovers(ctx: CleanContext, roots: list[Path]) -> list[CleanItem]:
    items = []
    for root in roots:
        for folder in stale_run_dirs(root, ctx.now):
            size, count = tree_size(folder)
            items.append(CleanItem(folder, size, count, is_dir=True))
    return items


def default_targets() -> list[CleanTarget]:
    return [
        CleanTarget("filesoptim", "work folders left by interrupted FilesOptim runs",
                    lambda c: [c.cache / "filesoptim"], _collect_leftovers, prune=False),
        CleanTarget("thumbnails", "file manager thumbnails",
                    lambda c: [c.cache / "thumbnails", c.home / ".thumbnails"],
                    _collect_age(lambda cfg: cfg.thumbnail_max_age_days)),
        CleanTarget("trash", "trash items deleted long ago",
                    lambda c: [c.data / "Trash"], _collect_trash, prune=False),
        CleanTarget("cache", "application cache files unused for a long time",
                    lambda c: [c.cache], _collect_user_cache),
        CleanTarget("pip", "pip download cache", lambda c: [c.cache / "pip"], _collect_all,
                    default=False),
        CleanTarget("npm", "npm cache", lambda c: [c.home / ".npm" / "_cacache"], _collect_all,
                    default=False),
        CleanTarget("yarn", "yarn cache", lambda c: [c.cache / "yarn"], _collect_all,
                    default=False),
        CleanTarget("go", "Go build cache", lambda c: [c.cache / "go-build"], _collect_all,
                    default=False),
        CleanTarget("cargo", "Cargo downloaded crates",
                    lambda c: [c.home / ".cargo" / "registry" / "cache"], _collect_all,
                    default=False),
        CleanTarget("browsers", "web browser caches (Firefox, Chrome, Chromium, Brave)",
                    lambda c: sorted(
                        [*c.cache.glob("mozilla/firefox/*/cache2"),
                         *c.cache.glob("google-chrome/*/Cache"),
                         *c.cache.glob("chromium/*/Cache"),
                         *c.cache.glob("BraveSoftware/Brave-Browser/*/Cache")]),
                    _collect_all, default=False),
        CleanTarget("apt", "downloaded .deb packages (apt clean)",
                    lambda c: [c.system("/var/cache/apt/archives")], _collect_apt,
                    system=True, prune=False),
        CleanTarget("dnf", "downloaded .rpm packages (dnf clean packages)",
                    lambda c: [c.system("/var/cache/dnf")], _collect_dnf, system=True),
        CleanTarget("pacman", "old package versions in the pacman cache",
                    lambda c: [c.system("/var/cache/pacman/pkg")], _collect_pacman,
                    system=True, prune=False),
        CleanTarget("journal", "archived systemd journal files",
                    lambda c: [c.system("/var/log/journal")], _collect_journal,
                    system=True, prune=False),
        CleanTarget("coredumps", "old crash dumps",
                    lambda c: [c.system("/var/lib/systemd/coredump")],
                    _collect_age(lambda cfg: cfg.coredump_max_age_days), system=True),
    ]


# ------------------------------------------------------------------------------------------
# Cleaner
# ------------------------------------------------------------------------------------------
def _prune_empty_subdirs(root: Path) -> None:
    for dirpath, _, _ in sorted(os.walk(root), key=lambda w: len(w[0]), reverse=True):
        if Path(dirpath) != root:
            with contextlib.suppress(OSError):
                Path(dirpath).rmdir()


class Cleaner:
    def __init__(
        self,
        config: CleanConfig,
        console: Console,
        *,
        context: CleanContext | None = None,
        is_root: bool | None = None,
    ) -> None:
        self.console = console
        self.context = context or CleanContext(config, Path.home(), cache_home(), data_home())
        self.is_root = os.geteuid() == 0 if is_root is None else is_root
        self.targets = default_targets()

    def select(self, only: Sequence[str], system: bool) -> list[CleanTarget]:
        known = {t.key: t for t in self.targets}
        if only:
            unknown = [k for k in only if k not in known]
            if unknown:
                raise ConfigError(
                    f"unknown clean target(s): {', '.join(unknown)} "
                    f"(available: {', '.join(known)})"
                )
            return [known[k] for k in only]
        return [t for t in self.targets if t.default and (system or not t.system)]

    def estimate(self, targets: Sequence[CleanTarget]) -> list[TargetEstimate]:
        results = []
        for target in targets:
            roots = target.roots(self.context)
            estimate = TargetEstimate(target, roots)
            try:
                estimate.items = target.collect(self.context, roots)
            except PermissionError:
                estimate.note = "permission denied (run with sudo)"
            if target.system and not self.is_root and not estimate.note:
                estimate.note = "needs root (sudo filesoptim clean --system)"
            results.append(estimate)
        return results

    def render(self, estimates: Sequence[TargetEstimate]) -> None:
        self.console.heading("Cleaning estimate (exact space freed, before any deletion)")
        rows = [[e.target.key, e.target.description, e.files, human_size(e.size), e.note]
                for e in estimates]
        total = sum(e.size for e in estimates if self.can_apply(e))
        rows.append(["total", "", sum(e.files for e in estimates if self.can_apply(e)),
                     human_size(total), ""])
        self.console.table(["target", "what", "files", "size", "note"], rows, align="llrrl")

    def can_apply(self, estimate: TargetEstimate) -> bool:
        return not estimate.note and (self.is_root or not estimate.target.system)

    def _safe(self, item: CleanItem, roots: Sequence[Path]) -> bool:
        return any(is_within(item.path.parent, root) for root in roots)

    def apply(self, estimates: Sequence[TargetEstimate]) -> tuple[int, list[str]]:
        freed, errors = 0, []
        for estimate in estimates:
            if not self.can_apply(estimate):
                continue
            for item in estimate.items:
                if not self._safe(item, estimate.roots):
                    errors.append(f"{item.path}: outside the cleaned folders, skipped")
                    continue
                try:
                    if item.is_dir:
                        shutil.rmtree(item.path)
                    else:
                        item.path.unlink(missing_ok=True)
                    for extra in item.extra:
                        extra.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(f"{item.path}: {exc}")
                else:
                    freed += item.size
            if estimate.target.prune:
                for root in estimate.roots:
                    if root.is_dir():
                        _prune_empty_subdirs(root)
        return freed, errors

    def list_targets(self) -> None:
        rows = [[t.key, t.description, "system" if t.system else "user",
                 "yes" if t.default else "opt-in"] for t in self.targets]
        self.console.table(["target", "what", "scope", "default"], rows)

    def run(
        self, only: Sequence[str], *, system: bool, assume_yes: bool, dry_run: bool
    ) -> tuple[int, list[str]]:
        estimates = self.estimate(self.select(only, system))
        self.render(estimates)
        applicable = [e for e in estimates if self.can_apply(e) and e.items]
        if dry_run or not applicable:
            if not applicable:
                self.console.info("Nothing to clean.")
            return 0, []
        size = sum(e.size for e in applicable)
        if not self.console.confirm(f"Delete these files and free {human_size(size)}?",
                                    assume_yes=assume_yes):
            return 0, []
        freed, errors = self.apply(applicable)
        for error in errors:
            self.console.error(error)
        self.console.success(f"{human_size(freed)} freed.")
        return freed, errors
