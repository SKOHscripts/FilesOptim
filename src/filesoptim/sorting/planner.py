"""Build a sorting plan (nothing is changed here) and preview it."""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from filesoptim.categories import CATEGORY_LABELS, extension
from filesoptim.config import Config, ConfigError
from filesoptim.fsutils import (
    WalkOptions,
    display_path,
    is_within,
    iter_files,
    same_content,
    sanitize_component,
)
from filesoptim.sorting.metadata import MediaInfo, MetadataReader, can_write_metadata
from filesoptim.tools import Tools
from filesoptim.ui import Console

_GENERIC_FOLDER_RE = re.compile(r"\d{3}[a-z_]*\w*|(19|20)\d{2}([-_ .]?\d{2}){0,2}")


@dataclass
class SortOptions:
    source: Path
    destination: Path
    mode: str = "move"
    rename: bool = False
    fix_dates: bool = False
    tag: bool = False
    extra_tags: list[str] = field(default_factory=list)
    only: list[str] = field(default_factory=list)
    set_mtime: bool = False
    prune_empty: bool = False
    recursive: bool = True


@dataclass
class SortOp:
    source: Path
    destination: Path
    action: str  # "move" | "copy" | "keep" (metadata only)
    category: str
    date: datetime | None = None
    date_source: str = ""
    set_date: datetime | None = None
    add_keywords: list[str] = field(default_factory=list)
    set_mtime: float | None = None
    sidecar_of: Path | None = None

    @property
    def changes_metadata(self) -> bool:
        return self.set_date is not None or bool(self.add_keywords)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "source": str(self.source),
            "destination": str(self.destination),
            "category": self.category,
            "date": self.date.isoformat() if self.date else None,
            "date_source": self.date_source,
            "set_date": self.set_date.isoformat() if self.set_date else None,
            "add_keywords": self.add_keywords,
            "set_mtime": self.set_mtime,
            "sidecar_of": str(self.sidecar_of) if self.sidecar_of else None,
        }


@dataclass
class SortPlan:
    options: SortOptions
    ops: list[SortOp] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def moves(self) -> list[SortOp]:
        return [op for op in self.ops if op.action != "keep"]


class _Lenient(string.Formatter):
    """Missing template values (``None``) become ``unknown`` instead of raising."""

    def __init__(self, unknown: str) -> None:
        self.unknown = unknown

    def get_value(self, key: int | str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> Any:
        value = kwargs.get(str(key))
        return self.unknown if value is None else value

    def format_field(self, value: Any, format_spec: str) -> Any:
        try:
            return format(value, format_spec)
        except (ValueError, TypeError):  # e.g. "{date:%Y}" without a date
            return str(value)


def check_template(template: str) -> None:
    try:
        list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ConfigError(f"invalid template {template!r}: {exc}") from exc


class Planner:
    def __init__(self, config: Config, tools: Tools, reader: MetadataReader | None = None) -> None:
        self.config = config
        self.tools = tools
        self.reader = reader or MetadataReader(tools, use_mtime=config.sort.use_mtime)

    # -- discovery --------------------------------------------------------------------------
    def _files(self, options: SortOptions) -> list[Path]:
        source = options.source
        destination = options.destination
        skip = [destination] if destination != source and is_within(destination, source) else []
        walk = WalkOptions.from_config(self.config.general)
        if options.recursive:
            return [p for p, _ in iter_files([source], walk, skip_dirs=skip)]
        return sorted(
            p for p in source.iterdir()
            if p.is_file() and not p.is_symlink() and not walk.is_excluded(p.name)
        )

    def split_sidecars(self, files: Sequence[Path]) -> tuple[list[Path], dict[Path, list[Path]]]:
        """Separate ``IMG_1.xmp`` / ``IMG_1.JPG.xmp`` sidecars from the files they describe."""
        sidecar_ext = {e.lower() for e in self.config.sort.sidecar_extensions}
        by_name = {(p.parent, p.name.lower()): p for p in files}
        by_stem: dict[tuple[Path, str], Path] = {}
        for p in files:
            if extension(p) not in sidecar_ext:
                by_stem.setdefault((p.parent, p.stem.lower()), p)
        sidecars: dict[Path, list[Path]] = {}
        mains: list[Path] = []
        for p in files:
            main = None
            if extension(p) in sidecar_ext:
                key = (p.parent, p.stem.lower())
                main = by_stem.get(key)
                named = by_name.get(key)
                if main is None and named is not None and extension(named) not in sidecar_ext:
                    main = named
            if main is None:
                mains.append(p)
            else:
                sidecars.setdefault(main, []).append(p)
        return mains, sidecars

    # -- values -----------------------------------------------------------------------------
    def values(self, info: MediaInfo) -> dict[str, Any]:
        cfg = self.config.sort
        date = info.date
        return {
            "year": f"{date:%Y}" if date else cfg.undated,
            "month": f"{date:%m}" if date else "",
            "day": f"{date:%d}" if date else "",
            "date": date,
            "category": CATEGORY_LABELS[info.category],
            "ext": extension(info.path) or "noext",
            "EXT": (extension(info.path) or "noext").upper(),
            "camera": info.camera or None,
            "artist": info.album_artist or info.artist or None,
            "album": info.album or None,
            "title": info.title or None,
            "genre": info.genre or None,
            "track": info.track,
            "folder": info.path.parent.name,
            "stem": info.path.stem,
        }

    def folder_for(self, info: MediaInfo, values: dict[str, Any]) -> Path | None:
        template = self.config.sort.templates.get(info.category, "")
        if not template:
            return None
        check_template(template)
        try:
            rendered = _Lenient(self.config.sort.unknown).format(template, **values)
        except (ValueError, TypeError, IndexError, AttributeError) as exc:
            raise ConfigError(f"cannot use template {template!r}: {exc}") from exc
        parts = [sanitize_component(p) for p in rendered.split("/") if p.strip(" .")]
        return Path(*parts) if parts else Path()

    def name_for(self, info: MediaInfo, values: dict[str, Any], rename: bool) -> str:
        path = info.path
        suffix = path.suffix.lower() if self.config.sort.lowercase_extension else path.suffix
        template = self.config.sort.rename.get(info.category, "") if rename else ""
        stem = path.stem
        if template:
            check_template(template)
            present = {k: v for k, v in values.items() if v not in (None, "")}
            if info.date_source not in ("metadata", "filename"):
                present.pop("date", None)  # never rename from a guessed date
            try:
                stem = sanitize_component(template.format(**present))
            except (KeyError, ValueError, TypeError, IndexError, AttributeError):
                stem = path.stem
        return f"{stem}{suffix}"

    def is_generic_folder(self, name: str) -> bool:
        lowered = name.strip().lower()
        generic = {g.lower() for g in self.config.sort.tags.generic_folders}
        return not lowered or lowered in generic or bool(_GENERIC_FOLDER_RE.fullmatch(lowered))

    def keywords_for(self, info: MediaInfo, options: SortOptions) -> list[str]:
        tags = self.config.sort.tags
        wanted: list[str] = []
        if tags.category:
            wanted.append(CATEGORY_LABELS[info.category])
        if tags.camera and info.camera:
            wanted.append(info.camera)
        folder = info.path.parent
        # The folder the user created inside the source (event name...), never the source.
        if tags.folder and folder != options.source and not self.is_generic_folder(folder.name):
            wanted.append(folder.name)
        wanted += [*tags.extra, *options.extra_tags]
        existing = {k.lower() for k in info.keywords}
        result: list[str] = []
        for keyword in (w.strip() for w in wanted):
            if keyword and keyword.lower() not in existing:
                existing.add(keyword.lower())
                result.append(keyword)
        return result

    # -- planning ---------------------------------------------------------------------------
    def _resolve(
        self, source: Path, target: Path, taken: dict[Path, Path]
    ) -> tuple[Path | None, str]:
        """Free destination for ``source``; ``None`` when it would duplicate a file."""
        candidate, counter = target, 1
        while True:
            if candidate == source:
                return candidate, ""
            other = taken.get(candidate)
            if other is not None:
                if same_content(other, source):
                    return None, f"duplicate of {other.name} (same content)"
            elif candidate.exists() or candidate.is_symlink():
                if candidate.is_file() and same_content(candidate, source):
                    return None, f"duplicate of {candidate} (same content)"
            else:
                return candidate, ""
            candidate = target.with_name(f"{target.stem}_{counter}{target.suffix}")
            counter += 1

    def _metadata_changes(self, op: SortOp, info: MediaInfo, options: SortOptions) -> None:
        if info.category not in ("image", "video") or not can_write_metadata(info.path):
            return
        if not self.tools.has("exiftool"):
            return
        if options.fix_dates and info.date_source == "filename":
            op.set_date = info.date
        if options.tag:
            op.add_keywords = self.keywords_for(info, options)

    def plan(self, options: SortOptions) -> SortPlan:
        options.source = options.source.absolute()
        options.destination = options.destination.absolute()
        plan = SortPlan(options)
        mains, sidecars = self.split_sidecars(self._files(options))
        infos = self.reader.read(mains)
        taken: dict[Path, Path] = {}
        for path in mains:
            info = infos[path]
            if options.only and info.category not in options.only:
                plan.skipped.append((path, "category not selected"))
                continue
            values = self.values(info)
            folder = self.folder_for(info, values)
            directory = path.parent if folder is None else options.destination / folder
            target = directory / self.name_for(info, values, options.rename)
            resolved, reason = self._resolve(path, target, taken)
            if resolved is None:
                plan.skipped.append((path, reason))
                continue
            action = "keep" if resolved == path else options.mode
            op = SortOp(path, resolved, action, info.category, info.date, info.date_source)
            self._metadata_changes(op, info, options)
            if options.set_mtime and info.date and info.date_source in ("metadata", "filename"):
                stamp = info.date.timestamp()
                if abs(path.stat().st_mtime - stamp) > 1:
                    op.set_mtime = stamp
            if action == "keep" and not op.changes_metadata and op.set_mtime is None:
                plan.skipped.append((path, "already in place"))
                continue
            taken[resolved] = path
            plan.ops.append(op)
            if action != "keep":
                self._plan_sidecars(plan, op, sidecars.get(path, []), taken)
        return plan

    def _plan_sidecars(
        self, plan: SortPlan, main: SortOp, files: Sequence[Path], taken: dict[Path, Path]
    ) -> None:
        lower = self.config.sort.lowercase_extension
        for sidecar in files:
            suffix = sidecar.suffix.lower() if lower else sidecar.suffix
            if sidecar.stem.lower() == main.source.name.lower():  # IMG_1.JPG.xmp style
                name = f"{main.destination.name}{suffix}"
            else:
                name = f"{main.destination.stem}{suffix}"
            resolved, reason = self._resolve(sidecar, main.destination.with_name(name), taken)
            if resolved is None:
                plan.skipped.append((sidecar, reason))
                continue
            taken[resolved] = sidecar
            plan.ops.append(
                SortOp(sidecar, resolved, main.action, "other", sidecar_of=main.source)
            )


# ------------------------------------------------------------------------------------------
# Preview
# ------------------------------------------------------------------------------------------
def describe_op(op: SortOp) -> str:
    notes = []
    if op.set_date:
        notes.append(f"+date {op.set_date:%Y-%m-%d %H:%M}")
    if op.add_keywords:
        notes.append("+tags " + ", ".join(op.add_keywords))
    if op.set_mtime is not None:
        notes.append("+mtime")
    if op.sidecar_of:
        notes.append(f"sidecar of {op.sidecar_of.name}")
    return "; ".join(notes)


def preview(plan: SortPlan, console: Console, *, limit: int | None = 30) -> None:
    options = plan.options
    console.heading("Sorting plan (preview: nothing has been changed yet)")
    moves = plan.moves
    meta = [op for op in plan.ops if op.changes_metadata]
    renamed = [op for op in moves if op.source.name != op.destination.name]
    console.info(
        f"{len(moves)} file(s) to {options.mode}, {len(renamed)} renamed, "
        f"{len(meta)} metadata update(s), {len(plan.skipped)} left untouched."
    )
    if moves:
        folders = Counter(display_path(op.destination.parent, options.destination) for op in moves)
        console.table(["destination folder", "files"],
                      [[f, n] for f, n in sorted(folders.items())], align="lr")
    shown = plan.ops if limit is None else plan.ops[:limit]
    if shown:
        rows = []
        for op in shown:
            date = f"{op.date:%Y-%m-%d %H:%M} ({op.date_source})" if op.date else ""
            rows.append([
                op.action,
                display_path(op.source, options.source),
                "" if op.action == "keep" else display_path(op.destination, options.destination),
                date,
                describe_op(op),
            ])
        console.table(["action", "from", "to", "date (source)", "changes"], rows, max_width=48)
        if len(shown) < len(plan.ops):
            console.info(f"… and {len(plan.ops) - len(shown)} more (use --show-all).")
    if plan.skipped:
        console.info("Left untouched:")
        for reason, count in Counter(r.split(" (")[0] for _, r in plan.skipped).most_common():
            console.info(f"  {count:>6}  {reason}")
        for path, reason in plan.skipped:
            console.debug(f"  {display_path(path, options.source)}: {reason}")
    undated = [op for op in plan.ops if op.date_source == "mtime"]
    if undated:
        console.warn(f"{len(undated)} file(s) dated from their modification time only "
                     "(no metadata nor date in the name): check them in the preview.")


def export_plan(plan: SortPlan, path: Path) -> None:
    data = {
        "source": str(plan.options.source),
        "destination": str(plan.options.destination),
        "mode": plan.options.mode,
        "operations": [op.to_dict() for op in plan.ops],
        "skipped": [{"path": str(p), "reason": r} for p, r in plan.skipped],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
