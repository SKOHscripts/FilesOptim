"""Optimisation workflow: discover -> estimate (always) -> confirm -> apply -> report."""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

from filesoptim.config import Config, work_cache_dir
from filesoptim.fsutils import (
    STALE_AFTER,
    TEMP_MARK,
    WalkOptions,
    display_path,
    durable_copy,
    free_space,
    install_file,
    iter_files,
    make_run_dir,
    stale_run_dirs,
    stat_key,
    tree_bytes,
    unique_path,
)
from filesoptim.optimize.base import Estimate, Optimizer, Outcome
from filesoptim.optimize.images import GifOptimizer, JpegOptimizer, PngOptimizer
from filesoptim.optimize.pdf import PdfOptimizer
from filesoptim.optimize.video import VideoOptimizer
from filesoptim.state import StateDB
from filesoptim.tools import ToolError, Tools, install_hint
from filesoptim.trash import send_to_trash
from filesoptim.ui import Console, human_size, percent

OPTIMIZER_CLASSES: tuple[type[Optimizer], ...] = (
    JpegOptimizer, PngOptimizer, GifOptimizer, PdfOptimizer, VideoOptimizer,
)


def build_optimizers(config: Config, tools: Tools, types: Sequence[str]) -> list[Optimizer]:
    return [cls(config, tools) for cls in OPTIMIZER_CLASSES if cls.name in types]


@dataclass
class Candidate:
    path: Path
    stat: os.stat_result
    optimizer: Optimizer


@dataclass
class OptimizeSummary:
    estimates: list[Estimate] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    applied: bool = False

    @property
    def candidates(self) -> list[Estimate]:
        return [e for e in self.estimates if e.candidate]

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == "failed"]


def reason_group(reason: str) -> str:
    """``"no significant gain (0.3 %)"`` -> ``"no significant gain"``."""
    return reason.split(" (", 1)[0]


class Engine:
    def __init__(
        self,
        config: Config,
        tools: Tools,
        console: Console,
        state: StateDB,
        *,
        types: Sequence[str] | None = None,
        force: bool = False,
        walk: WalkOptions | None = None,
    ) -> None:
        self.config = config
        self.tools = tools
        self.console = console
        self.state = state
        self.force = force
        self.walk = walk or WalkOptions.from_config(config.general)
        self.optimizers = build_optimizers(config, tools, types or config.optimize.types)
        self._by_name = {o.name: o for o in self.optimizers}

    # -- setup ------------------------------------------------------------------------------
    def usable_optimizers(self) -> list[Optimizer]:
        usable, missing = [], []
        for optimizer in self.optimizers:
            reason = optimizer.unavailable_reason()
            if reason is None:
                usable.append(optimizer)
            else:
                self.console.warn(f"{optimizer.name}: disabled ({reason})")
                missing.extend(optimizer.missing_tools())
        if missing:
            self.console.info(f"  → {install_hint(missing, self.tools)}")
        return usable

    def keep_policy(self, optimizer: Optimizer) -> str:
        policy = self.config.optimize.keep_originals
        if policy == "auto":
            return "trash" if optimizer.lossy else "never"
        return policy

    # -- discovery --------------------------------------------------------------------------
    def discover(
        self, roots: Iterable[Path], optimizers: Sequence[Optimizer]
    ) -> tuple[list[Candidate], list[Estimate]]:
        """Candidate files, plus files rejected by the generic safety guards."""
        candidates: list[Candidate] = []
        rejected: list[Estimate] = []
        now = time.time()
        min_age = self.config.general.min_age_seconds
        for path, st in iter_files(roots, self.walk, include_temp=True):
            if TEMP_MARK in path.name:
                if now - st.st_mtime > STALE_AFTER:  # left behind by a killed run
                    self._remove_leftover(path)
                continue
            optimizer = next((o for o in optimizers if o.accepts(path)), None)
            if optimizer is None:
                continue
            est = optimizer.new_estimate(path, st)
            if st.st_size == 0:
                rejected.append(est.skip("empty file"))
            elif st.st_nlink > 1:
                rejected.append(est.skip("hard-linked file (replacing it would free nothing)"))
            elif now - st.st_mtime < min_age:
                rejected.append(est.skip(f"modified less than {min_age} s ago", remember=False))
            elif not (os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)):
                rejected.append(est.skip("no write permission", remember=False))
            elif not self.force and (
                outcome := self.state.lookup(path, *stat_key(st), optimizer.signature())
            ):
                rejected.append(est.skip(f"already processed ({outcome})", remember=False))
            else:
                candidates.append(Candidate(path, st, optimizer))
        return candidates, rejected

    # -- disk space -------------------------------------------------------------------------
    @property
    def reserve(self) -> int:
        """Free space (bytes) that must always remain on the disks FilesOptim writes to."""
        return self.config.optimize.min_free_mb * 1024 * 1024

    def has_room(self, folder: Path, needed: int) -> bool:
        return free_space(folder) - needed >= self.reserve

    def _no_room(self, est: Estimate) -> Estimate:
        return est.skip(f"not enough free disk space (keeping {human_size(self.reserve)} free)",
                        remember=False)

    def _remove_leftover(self, path: Path) -> None:
        try:
            size = tree_bytes(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            self.console.warn(f"cannot remove leftover {path}: {exc}")
            return
        self.console.info(f"Removed a leftover of an interrupted run: {path} "
                          f"({human_size(size)})")

    def cleanup_leftovers(self, cache: Path) -> None:
        """Delete work folders of runs that were killed before they could clean up."""
        for folder in stale_run_dirs(cache, time.time()):
            self._remove_leftover(folder)

    # -- estimation -------------------------------------------------------------------------
    def _safe_estimate(
        self, candidate: Candidate, workdir: Path, stop: threading.Event | None = None
    ) -> Estimate:
        est = candidate.optimizer.new_estimate(candidate.path, candidate.stat)
        if stop is not None and stop.is_set():
            return est.skip("interrupted", remember=False)
        if not self.has_room(workdir, candidate.stat.st_size):
            return self._no_room(est)
        try:
            return candidate.optimizer.estimate(candidate.path, candidate.stat, workdir)
        except (OSError, ToolError, ValueError) as exc:
            return est.skip(f"error ({exc})", remember=False)

    def estimate(self, candidates: Sequence[Candidate], workdir: Path) -> list[Estimate]:
        """Precise estimate of every candidate (exact trial runs, sampled for videos)."""
        results: list[Estimate] = []
        budget = self.config.optimize.staging_limit_mb * 1024 * 1024
        used = 0
        total = len(candidates)

        def keep_or_drop(est: Estimate) -> None:
            nonlocal used
            if est.staged is not None:
                size = est.staged.stat().st_size
                if used + size > budget or not self.has_room(workdir, 0):
                    est.discard()  # recomputed at apply time
                else:
                    used += size
            results.append(est)

        quick = [c for c in candidates if c.optimizer.name != "video"]
        slow = [c for c in candidates if c.optimizer.name == "video"]
        jobs = self.config.optimize.jobs or os.cpu_count() or 1
        stop = threading.Event()
        pool = ThreadPoolExecutor(max_workers=jobs)
        try:
            futures: dict[Future[Estimate], Candidate] = {
                pool.submit(self._safe_estimate, c, workdir, stop): c for c in quick
            }
            for done, future in enumerate(as_completed(futures), start=1):
                self.console.progress(done, total, f"estimating {futures[future].path.name}")
                keep_or_drop(future.result())
        except BaseException:
            # Ctrl+C (or any error): nothing queued may start, running tools are stopped.
            stop.set()
            self.tools.terminate_all()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        for offset, candidate in enumerate(slow, start=len(quick) + 1):
            label = f"estimating {candidate.path.name}"
            self._attach_progress(candidate.optimizer, offset, total, label)
            self.console.progress(offset, total, label)
            keep_or_drop(self._safe_estimate(candidate, workdir))
        results.sort(key=lambda e: str(e.path))
        return results

    def _attach_progress(self, optimizer: Optimizer, index: int, total: int, label: str) -> None:
        def report(fraction: float) -> None:
            self.console.progress(index, total, f"{label} {fraction:.0%}")

        optimizer.progress = report

    def render_estimate(self, estimates: Sequence[Estimate], base: Path | None) -> None:
        console = self.console
        console.heading("Estimate (before any change)")
        if not estimates:
            console.info("No file to optimise here.")
            return
        rows = []
        kinds = [o.name for o in self.optimizers]
        for kind in kinds:
            of_kind = [e for e in estimates if e.kind == kind]
            if not of_kind:
                continue
            todo = [e for e in of_kind if e.candidate]
            before = sum(e.original_size for e in todo)
            saving = sum(e.saving for e in todo)
            approx = "~" if any(not e.exact for e in todo) else ""
            rows.append([
                kind, len(of_kind), len(todo), human_size(before),
                f"{approx}{human_size(before - saving)}",
                f"{approx}{human_size(saving)} ({percent(saving, before):.1f} %)",
            ])
        todo_all = [e for e in estimates if e.candidate]
        before_all = sum(e.original_size for e in todo_all)
        saving_all = sum(e.saving for e in todo_all)
        rows.append([
            "total", len(estimates), len(todo_all), human_size(before_all),
            human_size(before_all - saving_all),
            f"{human_size(saving_all)} ({percent(saving_all, before_all):.1f} %)",
        ])
        console.table(
            ["type", "files", "to optimise", "current size", "after", "saving"],
            rows, align="lrrrrr",
        )
        console.print("Images and PDF: exact (real trial runs). Videos marked ~: measured on "
                      "samples spread over each video.", "dim")
        for est in todo_all:
            if est.kind == "video" or console.verbose:
                quality = f", {est.metric.upper()} {est.quality:.4f}" if est.quality else ""
                target = f" → {est.target.name}" if est.target else ""
                console.info(
                    f"  {display_path(est.path, base)}{target}: {human_size(est.original_size)}"
                    f" → {'' if est.exact else '~'}{human_size(est.original_size - est.saving)}"
                    f" (-{est.saving_percent:.1f} %{quality})"
                )
        skipped = [e for e in estimates if e.skip_reason]
        if skipped:
            groups = Counter(reason_group(e.skip_reason or "") for e in skipped)
            console.info(f"Left untouched: {len(skipped)} file(s)")
            for reason, count in groups.most_common():
                console.info(f"  {count:>6}  {reason}")
            for est in skipped:
                console.debug(f"  {display_path(est.path, base)}: {est.skip_reason}")

    # -- application ------------------------------------------------------------------------
    def _keep_original(self, optimizer: Optimizer, path: Path) -> None:
        policy = self.keep_policy(optimizer)
        if policy == "trash":
            send_to_trash(path, keep_original=True)
        elif policy == "backup":
            backup_root = Path(self.config.optimize.backup_dir).expanduser()
            destination = unique_path(backup_root / path.absolute().relative_to("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            durable_copy(path, destination)  # on the disk before the original is replaced

    def apply_one(self, estimate: Estimate, workdir: Path) -> Outcome:
        optimizer = self._by_name[estimate.kind]
        path = estimate.path
        est = replace(estimate)  # the estimate itself stays as shown to the user
        try:
            st = path.stat()
            if stat_key(st) != est.stat_key:  # changed since the estimate: start again
                est.discard()
                est = optimizer.estimate(path, st, workdir)
            if est.candidate and est.staged is None:
                if not self.has_room(path.parent, est.original_size):
                    return self._skipped(self._no_room(est))
                est = optimizer.materialize(est, workdir)
            elif est.staged is not None and est.staged.stat().st_dev != st.st_dev:
                # the result will be copied next to the original before replacing it
                if not self.has_room(path.parent, est.staged.stat().st_size):
                    est.discard()
                    return self._skipped(self._no_room(est))
            if not est.candidate or est.staged is None:
                self._remember(est, optimizer)
                return self._skipped(est)
            self._keep_original(optimizer, path)
            # Checked again right before replacing: an edit made meanwhile is never lost.
            final = install_file(est.staged, path, est.target, expected=est.stat_key)
        except (OSError, ToolError) as exc:
            est.discard()
            return Outcome(path, est.kind, "failed", est.original_size, est.original_size,
                           str(exc))
        new_stat = final.stat()
        self.state.record(final, *stat_key(new_stat), optimizer.signature(), "optimised")
        if final != path:
            self.state.forget(path)
        return Outcome(path, est.kind, "optimized", est.original_size, new_stat.st_size,
                       final_path=final)

    @staticmethod
    def _skipped(est: Estimate) -> Outcome:
        return Outcome(est.path, est.kind, "skipped", est.original_size, est.original_size,
                       est.skip_reason or "")

    def _remember(self, est: Estimate, optimizer: Optimizer) -> None:
        if est.remember and est.skip_reason:
            self.state.record(est.path, *est.stat_key, optimizer.signature(),
                              reason_group(est.skip_reason))

    def apply(
        self, estimates: Sequence[Estimate], workdir: Path, base: Path | None
    ) -> list[Outcome]:
        outcomes: list[Outcome] = []
        todo = [e for e in estimates if e.candidate]
        for est in estimates:
            if not est.candidate:
                self._remember(est, self._by_name[est.kind])
        for index, est in enumerate(todo, start=1):
            label = f"optimising {est.path.name}"
            self._attach_progress(self._by_name[est.kind], index, len(todo), label)
            self.console.progress(index, len(todo), label)
            outcome = self.apply_one(est, workdir)
            outcomes.append(outcome)
            shown = display_path(outcome.path, base)
            if outcome.status == "optimized":
                if est.kind == "video" or self.console.verbose:
                    self.console.success(
                        f"{shown}: {human_size(outcome.before)} → {human_size(outcome.after)}"
                        f" (-{percent(outcome.saving, outcome.before):.1f} %)"
                    )
            elif outcome.status == "failed":
                self.console.error(f"{shown}: {outcome.message}")
            else:
                self.console.warn(f"{shown}: {outcome.message}")
        return outcomes

    def render_outcomes(self, outcomes: Sequence[Outcome]) -> None:
        done = [o for o in outcomes if o.status == "optimized"]
        saved = sum(o.saving for o in done)
        before = sum(o.before for o in done)
        self.console.heading("Result")
        self.console.success(
            f"{len(done)} file(s) optimised, {human_size(saved)} saved"
            f" ({percent(saved, before):.1f} % of their size)"
        )
        others = Counter(o.status for o in outcomes if o.status != "optimized")
        for status, count in sorted(others.items()):
            self.console.info(f"{count} file(s) {status}")

    # -- entry point ------------------------------------------------------------------------
    def run(
        self,
        roots: Sequence[Path],
        *,
        estimate_only: bool = False,
        assume_yes: bool = False,
    ) -> OptimizeSummary:
        optimizers = self.usable_optimizers()
        if not optimizers:
            self.console.error("No optimiser is usable: install the missing tools first.")
            return OptimizeSummary()
        wanted_metric = self.config.video.metric
        for optimizer in optimizers:
            if isinstance(optimizer, VideoOptimizer) and optimizer.metric != wanted_metric:
                self.console.warn("ffmpeg has no libvmaf: video quality is measured with SSIM.")
        base = roots[0] if len(roots) == 1 and roots[0].is_dir() else None
        cache = work_cache_dir()
        self.cleanup_leftovers(cache)
        workdir = make_run_dir(cache)
        try:
            candidates, rejected = self.discover(roots, optimizers)
            estimates = rejected + self.estimate(candidates, workdir)
            self.render_estimate(estimates, base)
            summary = OptimizeSummary(estimates)
            if estimate_only or not summary.candidates:
                return summary
            count = len(summary.candidates)
            if not self.console.confirm(f"Optimise these {count} file(s)?", assume_yes=assume_yes):
                return summary
            summary.outcomes = self.apply(estimates, workdir, base)
            summary.applied = True
            self.render_outcomes(summary.outcomes)
            return summary
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
