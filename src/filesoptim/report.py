"""Disk usage report: where the space goes, and what could be recovered."""

from __future__ import annotations

import heapq
import shutil
import stat
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filesoptim.categories import category_of, extension
from filesoptim.dupes import DuplicateGroup
from filesoptim.fsutils import WalkOptions, allocated_size, display_path, iter_tree
from filesoptim.optimize.engine import OPTIMIZER_CLASSES, OptimizeSummary
from filesoptim.ui import Console, human_size, percent


@dataclass
class DiskUsage:
    path: Path
    total: int
    used: int
    free: int


@dataclass
class Report:
    roots: list[Path]
    files: int = 0
    dirs: int = 0
    size: int = 0
    categories: dict[str, list[int]] = field(default_factory=dict)  # name -> [count, size]
    children: Counter[Path] = field(default_factory=Counter)
    biggest: list[tuple[int, Path]] = field(default_factory=list)
    disks: list[DiskUsage] = field(default_factory=list)
    optimizable: Counter[str] = field(default_factory=Counter)


def _optimizer_for(path: Path) -> str | None:
    ext = extension(path)
    return next((c.name for c in OPTIMIZER_CLASSES if ext in c.extensions), None)


def build_report(roots: Sequence[Path], walk: WalkOptions, *, top: int = 10) -> Report:
    report = Report(list(roots))
    seen: set[tuple[int, int]] = set()
    heap: list[tuple[int, str]] = []
    for root in roots:
        usage = shutil.disk_usage(root)
        report.disks.append(DiskUsage(root, usage.total, usage.used, usage.free))
        for path, st in iter_tree([root], walk):
            if stat.S_ISDIR(st.st_mode):
                report.dirs += 1
                continue
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            size = allocated_size(st)
            report.files += 1
            report.size += size
            entry = report.categories.setdefault(category_of(path), [0, 0])
            entry[0] += 1
            entry[1] += size
            parts = path.relative_to(root).parts
            if len(parts) > 1:  # inside a top-level folder
                report.children[root / parts[0]] += size
            kind = _optimizer_for(path)
            if kind:
                report.optimizable[kind] += 1
            item = (st.st_size, str(path))
            if len(heap) < top:
                heapq.heappush(heap, item)
            elif top:
                heapq.heappushpop(heap, item)
    report.biggest = [(size, Path(p)) for size, p in sorted(heap, reverse=True)]
    return report


def render_report(report: Report, console: Console, *, top: int = 10) -> None:
    base = report.roots[0] if len(report.roots) == 1 else None
    console.heading("Disks")
    console.table(
        ["folder", "disk size", "used", "free"],
        [[str(d.path), human_size(d.total), f"{human_size(d.used)} "
          f"({percent(d.used, d.total):.0f} %)", human_size(d.free)] for d in report.disks],
        align="lrrr",
    )
    console.heading(f"Content: {report.files} files, {report.dirs} folders, "
                    f"{human_size(report.size)} on disk")
    rows = [[name, count, human_size(size), f"{percent(size, report.size):.1f} %"]
            for name, (count, size) in sorted(report.categories.items(),
                                               key=lambda kv: -kv[1][1])]
    console.table(["category", "files", "size", "share"], rows, align="lrrr")
    if report.children:
        console.heading("Biggest folders")
        console.table(["folder", "size"],
                      [[display_path(p, base), human_size(s)]
                       for p, s in report.children.most_common(top)],
                      align="lr")
    if report.biggest:
        console.heading("Biggest files")
        console.table(["file", "size"],
                      [[display_path(p, base), human_size(s)] for s, p in report.biggest],
                      align="lr")
    if report.optimizable:
        console.heading("Files the optimiser can examine")
        console.table(["type", "files"], sorted(report.optimizable.items()), align="lr")
        console.print("Run `filesoptim report --estimate` (or `filesoptim optimize "
                      "--estimate-only`) for the exact gain.", "dim")


def estimate_totals(summary: OptimizeSummary) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for est in summary.candidates:
        entry = by_kind.setdefault(est.kind, {"files": 0, "size": 0, "saving": 0})
        entry["files"] += 1
        entry["size"] += est.original_size
        entry["saving"] += est.saving
    return {
        "by_type": by_kind,
        "total_saving": sum(e["saving"] for e in by_kind.values()),
        "skipped": len(summary.estimates) - len(summary.candidates),
    }


def report_to_dict(
    report: Report,
    *,
    estimate: OptimizeSummary | None = None,
    duplicates: Sequence[DuplicateGroup] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "roots": [str(r) for r in report.roots],
        "disks": [{"path": str(d.path), "total": d.total, "used": d.used, "free": d.free}
                  for d in report.disks],
        "files": report.files,
        "folders": report.dirs,
        "size": report.size,
        "categories": {k: {"files": c, "size": s} for k, (c, s) in report.categories.items()},
        "biggest_folders": [{"path": str(p), "size": s} for p, s in report.children.most_common()],
        "biggest_files": [{"path": str(p), "size": s} for s, p in report.biggest],
        "optimizable": dict(report.optimizable),
    }
    if estimate is not None:
        data["estimate"] = estimate_totals(estimate)
    if duplicates is not None:
        data["duplicates"] = {
            "groups": len(duplicates),
            "wasted": sum(g.wasted for g in duplicates),
        }
    return data
