from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from filesoptim.config import Config
from filesoptim.fsutils import WalkOptions
from filesoptim.optimize import engine as engine_mod
from filesoptim.optimize.base import Estimate, FileOptimizer, Optimizer, Outcome
from filesoptim.optimize.engine import (
    Candidate,
    Engine,
    OptimizeSummary,
    build_optimizers,
    reason_group,
)
from filesoptim.optimize.images import JpegOptimizer, PngOptimizer
from filesoptim.optimize.video import VideoOptimizer
from filesoptim.state import StateDB
from filesoptim.trash import trash_root
from tests.conftest import FakeTools, Out, age


class Halver(FileOptimizer):
    """Lossless dummy: keeps the first half of the file."""

    name = "jpeg"
    extensions = frozenset({"dat"})
    ratio = 0.5

    def missing_tools(self) -> list[str]:
        return []

    def produce(self, source: Path, output: Path) -> None:
        data = source.read_bytes()
        output.write_bytes(data[: int(len(data) * self.ratio)])

    def verify(self, source: Path, output: Path) -> str | None:
        return None


class LossyHalver(Halver):
    name = "pdf"
    extensions = frozenset({"lossy"})

    @property
    def lossy(self) -> bool:
        return True


class Exploding(Halver):
    name = "gif"
    extensions = frozenset({"boom"})

    def estimate(self, path: Path, st: os.stat_result, workdir: Path) -> Estimate:
        raise ValueError("unexpected")


class FakeVideo(VideoOptimizer):
    extensions = frozenset({"vid"})
    metric = "ssim"  # type: ignore[assignment]
    reason: str | None = None
    rename = False

    def unavailable_reason(self) -> str | None:
        return self.reason

    def missing_tools(self) -> list[str]:
        return []

    def estimate(self, path: Path, st: os.stat_result, workdir: Path) -> Estimate:
        est = self.new_estimate(path, st)
        est.metric, est.quality, est.exact = "ssim", 0.991, False
        est.estimated_size = st.st_size // 4
        if self.rename:
            est.target = path.with_suffix(".mkv")
        if self.progress:
            self.progress(0.5)
        return est

    def materialize(self, est: Estimate, workdir: Path) -> Estimate:
        target = est.target or est.path
        output = target.with_name(f".{target.name}.part")
        output.write_bytes(est.path.read_bytes()[: est.original_size // 4])
        est.staged, est.exact = output, True
        return est


def data_file(path: Path, size: int = 10_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
    return age(path)


def make_engine(config: Config, out: Out, optimizers: list[Optimizer] | None = None,
                state: StateDB | None = None, **kwargs: Any) -> Engine:
    engine = Engine(config, FakeTools(), out, state or StateDB(None), **kwargs)
    engine.optimizers = optimizers if optimizers is not None else [Halver(config, FakeTools())]
    engine._by_name = {o.name: o for o in engine.optimizers}
    return engine


def test_build_optimizers_and_helpers(config: Config) -> None:
    built = build_optimizers(config, FakeTools(), ["png", "video"])
    assert [type(o) for o in built] == [PngOptimizer, VideoOptimizer]
    assert reason_group("no significant gain (0.3 %)") == "no significant gain"
    summary = OptimizeSummary(
        [Estimate(Path("a"), "x", 1, (1, 1), estimated_size=1)],
        [Outcome(Path("a"), "x", "failed", 1, 1), Outcome(Path("b"), "x", "optimized", 4, 1)],
    )
    assert len(summary.candidates) == 1 and len(summary.failed) == 1
    assert summary.outcomes[1].saving == 3 and summary.outcomes[0].saving == 0
    engine = Engine(config, FakeTools(), Out(), StateDB(None), types=["gif"])
    assert [o.name for o in engine.optimizers] == ["gif"]
    assert isinstance(engine.walk, WalkOptions)


def test_usable_optimizers_and_policy(config: Config, out: Out) -> None:
    video = FakeVideo(config, FakeTools())
    video.reason = "ffmpeg was built without libx265"
    engine = make_engine(config, out, [JpegOptimizer(config, FakeTools(["apt-get"])), video])
    engine.tools = FakeTools(["apt-get"])
    assert engine.usable_optimizers() == []
    assert "jpeg: disabled (missing jpegoptim)" in out.text
    assert "sudo apt install jpegoptim" in out.text
    silent = Out()
    make_engine(config, silent, [video]).usable_optimizers()
    assert "install" not in silent.text
    assert engine.keep_policy(video) == "trash"
    assert engine.keep_policy(Halver(config, FakeTools())) == "never"
    config.optimize.keep_originals = "backup"
    assert engine.keep_policy(video) == "backup"


def test_discover_guards(tmp_path: Path, config: Config, out: Out,
                         monkeypatch: pytest.MonkeyPatch) -> None:
    config.general.min_age_seconds = 60
    root = tmp_path / "root"
    good = data_file(root / "good.dat")
    (root / "empty.dat").write_bytes(b"")
    age(root / "empty.dat")
    linked = data_file(root / "linked.dat")
    os.link(linked, root / "linked2.dat")
    data_file(root / "recent.dat").touch()
    readonly = data_file(root / "readonly.dat")
    processed = data_file(root / "processed.dat")
    data_file(root / "ignored.txt")
    state = StateDB(None)
    halver = Halver(config, FakeTools())
    st = processed.stat()
    state.record(processed, st.st_size, st.st_mtime_ns, halver.signature(), "optimised")
    real_access = os.access
    monkeypatch.setattr(engine_mod.os, "access",
                        lambda p, mode: Path(p) != readonly and real_access(p, mode))
    engine = make_engine(config, out, [halver], state)
    candidates, rejected = engine.discover([root], [halver])
    assert [c.path.name for c in candidates] == ["good.dat"]
    reasons = {e.path.name: e.skip_reason for e in rejected}
    assert reasons == {
        "empty.dat": "empty file",
        "linked.dat": "hard-linked file (replacing it would free nothing)",
        "linked2.dat": "hard-linked file (replacing it would free nothing)",
        "recent.dat": "modified less than 60 s ago",
        "readonly.dat": "no write permission",
        "processed.dat": "already processed (optimised)",
    }
    forced = make_engine(config, out, [halver], state, force=True)
    names = sorted(c.path.name for c in forced.discover([root], [halver])[0])
    assert names == ["good.dat", "processed.dat"]
    assert good.exists()


def test_full_run(tmp_path: Path, config: Config) -> None:
    out = Out(answers=["y"])
    good = data_file(tmp_path / "a.dat")
    state = StateDB(None)
    engine = make_engine(config, out, [Halver(config, FakeTools()),
                                       LossyHalver(config, FakeTools())], state)
    summary = engine.run([tmp_path])
    assert summary.applied
    assert good.stat().st_size == 5000
    assert "Estimate (before any change)" in out.text
    assert "1 file(s) optimised, 4.9 KiB saved (50.0 % of their size)" in out.text
    st = good.stat()
    assert state.lookup(good, st.st_size, st.st_mtime_ns, "jpeg:v1") == "optimised"
    assert not list((tmp_path / "home" / ".cache" / "filesoptim").iterdir())  # workdir removed


def test_estimate_only_and_declined(tmp_path: Path, config: Config) -> None:
    good = data_file(tmp_path / "a.dat")
    out = Out()
    summary = make_engine(config, out).run([tmp_path], estimate_only=True)
    assert not summary.applied and len(summary.candidates) == 1
    assert good.stat().st_size == 10_000
    declined = Out(answers=["n"])
    assert not make_engine(config, declined).run([good]).applied
    assert good.stat().st_size == 10_000


def test_nothing_to_do(tmp_path: Path, config: Config) -> None:
    out = Out()
    summary = make_engine(config, out).run([tmp_path])
    assert summary.estimates == [] and "No file to optimise here." in out.text
    halver = Halver(config, FakeTools())
    halver.ratio = 1.0
    data_file(tmp_path / "a.dat")
    out = Out(verbose=True)
    summary = make_engine(config, out, [halver]).run([tmp_path])
    assert summary.candidates == [] and "no significant gain" in out.text
    assert "a.dat: no significant gain (0.0 %)" in out.text  # verbose detail


def test_no_usable_optimizer(tmp_path: Path, config: Config) -> None:
    out = Out()
    assert make_engine(config, out, []).run([tmp_path]).estimates == []
    assert "No optimiser is usable" in out.text


def test_video_flow_with_trash_and_rename(tmp_path: Path, config: Config) -> None:
    config.video.metric = "vmaf"
    clip = data_file(tmp_path / "clip.vid", 40_000)
    video = FakeVideo(config, FakeTools())
    video.rename = True
    out = Out()
    state = StateDB(None)
    summary = make_engine(config, out, [video], state).run([tmp_path], assume_yes=True)
    final = tmp_path / "clip.mkv"
    assert summary.outcomes[0].final_path == final
    assert final.stat().st_size == 10_000 and not clip.exists()
    assert "ffmpeg has no libvmaf" in out.text
    assert "clip.vid → clip.mkv: 39.1 KiB → ~9.8 KiB (-75.0 %, SSIM 0.9910)" in out.text
    assert "✔ clip.vid: 39.1 KiB → 9.8 KiB (-75.0 %)" in out.text
    trashed = trash_root() / "files" / "clip.vid"
    assert trashed.stat().st_size == 40_000  # lossy: the original is kept in the trash


def test_backup_policy(tmp_path: Path, config: Config) -> None:
    config.optimize.keep_originals = "backup"
    config.optimize.backup_dir = str(tmp_path / "backup")
    item = data_file(tmp_path / "src" / "a.dat")
    make_engine(config, Out()).run([tmp_path / "src"], assume_yes=True)
    copy = tmp_path / "backup" / item.absolute().relative_to("/")
    assert copy.stat().st_size == 10_000
    assert item.stat().st_size == 5000


def test_staging_budget(tmp_path: Path, config: Config) -> None:
    config.optimize.staging_limit_mb = 0
    item = data_file(tmp_path / "a.dat")
    engine = make_engine(config, Out())
    work = tmp_path / "work"
    work.mkdir()
    estimates = engine.estimate([Candidate(item, item.stat(), engine.optimizers[0])], work)
    assert estimates[0].candidate and estimates[0].staged is None
    outcome = engine.apply_one(estimates[0], work)
    assert outcome.status == "optimized" and item.stat().st_size == 5000


def test_apply_one_edge_cases(tmp_path: Path, config: Config,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(config, Out())
    work = tmp_path / "work"
    work.mkdir()
    halver = engine.optimizers[0]

    def estimate(path: Path) -> Estimate:
        return halver.estimate(path, path.stat(), work)

    changed = data_file(tmp_path / "changed.dat")
    est = estimate(changed)
    data_file(changed, 20_000)
    assert engine.apply_one(est, work).after == 10_000  # re-estimated on the new content

    gone = data_file(tmp_path / "gone.dat")
    est = estimate(gone)
    gone.unlink()
    outcome = engine.apply_one(est, work)
    assert outcome.status == "failed"
    assert est.staged is not None and not est.staged.exists()  # staged result cleaned up

    became_optimal = data_file(tmp_path / "opt.dat")
    est = estimate(became_optimal)
    data_file(became_optimal, 30_000)
    halver_ratio = type(halver).ratio
    type(halver).ratio = 1.0
    try:
        outcome = engine.apply_one(est, work)
    finally:
        type(halver).ratio = halver_ratio
    assert outcome.status == "skipped" and outcome.message.startswith("no significant gain")

    broken = data_file(tmp_path / "broken.dat")
    est = estimate(broken)
    staged = est.staged

    def fail(*args: Any, **kwargs: Any) -> Path:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(engine_mod, "install_file", fail)
    outcome = engine.apply_one(est, work)
    assert outcome.status == "failed" and outcome.message == "read-only filesystem"
    assert staged is not None and not staged.exists()


def test_apply_reports_every_outcome(tmp_path: Path, config: Config) -> None:
    out = Out(verbose=True)
    state = StateDB(None)
    engine = make_engine(config, out, None, state)
    halver = engine.optimizers[0]
    work = tmp_path / "work"
    work.mkdir()
    ok = data_file(tmp_path / "ok.dat")
    missing = data_file(tmp_path / "missing.dat")
    skipped = data_file(tmp_path / "skip.dat")
    ests = [halver.estimate(p, p.stat(), work) for p in (ok, missing, skipped)]
    remembered = halver.new_estimate(skipped, skipped.stat()).skip("no significant gain (0 %)")
    forgotten = halver.new_estimate(ok, ok.stat()).skip("modified recently", remember=False)
    missing.unlink()
    data_file(skipped, 5)  # now below the minimum gain in bytes
    outcomes = engine.apply([*ests, remembered, forgotten], work, tmp_path)
    assert [o.status for o in outcomes] == ["optimized", "failed", "skipped"]
    assert "✔ ok.dat" in out.text and "✘ missing.dat" in out.text
    assert "⚠ skip.dat: no significant gain" in out.text
    st = skipped.stat()
    assert state.lookup(skipped, st.st_size, st.st_mtime_ns, "jpeg:v1") == "no significant gain"
    engine.render_outcomes(outcomes)
    assert "1 file(s) failed" in out.text and "1 file(s) skipped" in out.text


def test_unexpected_errors_are_contained(tmp_path: Path, config: Config) -> None:
    item = tmp_path / "x.boom"
    item.write_bytes(b"data")
    age(item)
    out = Out()
    engine = make_engine(config, out, [Exploding(config, FakeTools())])
    summary = engine.run([tmp_path])
    assert summary.estimates[0].skip_reason == "error (unexpected)"


def test_lossy_default_policy_and_render(tmp_path: Path, config: Config) -> None:
    item = data_file(tmp_path / "doc.lossy")
    out = Out(verbose=True)
    make_engine(config, out, [LossyHalver(config, FakeTools())]).run([item], assume_yes=True)
    assert (trash_root() / "files" / "doc.lossy").stat().st_size == 10_000
    assert "doc.lossy: 9.8 KiB → 4.9 KiB (-50.0 %)" in out.text


# -- interruption, disk space and leftovers --------------------------------------------------
def test_leftovers_are_cleaned(tmp_path: Path, config: Config) -> None:
    from filesoptim.config import work_cache_dir
    from filesoptim.fsutils import TEMP_MARK

    cache = work_cache_dir()
    dead = cache / "run-999999999-abc"
    dead.mkdir(parents=True)
    (dead / "staged.jpg").write_bytes(b"x" * 5000)
    old_tmp = data_file(tmp_path / "src" / f".clip.mkv{TEMP_MARK}", 3000)
    age(old_tmp, 7200)
    new_tmp = data_file(tmp_path / "src" / f".other{TEMP_MARK}.dat")
    new_tmp.touch()
    data_file(tmp_path / "src" / "a.dat")
    out = Out()
    make_engine(config, out).run([tmp_path / "src"], estimate_only=True)
    assert not dead.exists() and not old_tmp.exists() and new_tmp.exists()
    assert out.text.count("Removed a leftover of an interrupted run") == 2
    assert [p.name for p in cache.iterdir()] == []  # this run's folder is removed too


def test_leftover_that_cannot_be_removed(tmp_path: Path, config: Config,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    item = data_file(tmp_path / "x")

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "unlink", refuse)
    out = Out()
    make_engine(config, out)._remove_leftover(item)
    assert "cannot remove leftover" in out.text and item.exists()


def test_disk_reserve(tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    item = data_file(tmp_path / "a.dat")
    engine = make_engine(config, Out())
    assert engine.reserve == 2048 * 1024 * 1024
    free = {"value": 10**15}
    monkeypatch.setattr(engine_mod, "free_space", lambda path: free["value"])
    work = tmp_path / "work"
    work.mkdir()
    candidate = Candidate(item, item.stat(), engine.optimizers[0])
    staged = engine.estimate([candidate], work)[0]
    assert staged.staged is not None
    free["value"] = 0
    est = engine._safe_estimate(candidate, work)
    assert est.skip_reason == "not enough free disk space (keeping 2.0 GiB free)"
    assert not est.remember
    # the staged result is dropped when the disk gets full during the estimate...
    calls = iter([10**15, 0])
    monkeypatch.setattr(engine_mod, "free_space", lambda path: next(calls))
    dropped = engine.estimate([candidate], work)[0]
    assert dropped.candidate and dropped.staged is None
    # ...and re-creating it at apply time is refused while the disk is full
    free["value"] = 0
    monkeypatch.setattr(engine_mod, "free_space", lambda path: free["value"])
    outcome = engine.apply_one(dropped, work)
    assert outcome.status == "skipped" and "free disk space" in outcome.message
    assert item.stat().st_size == 10_000


def test_staged_on_another_disk(tmp_path: Path, config: Config,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    item = data_file(tmp_path / "a.dat")
    engine = make_engine(config, Out())
    work = tmp_path / "work"
    work.mkdir()
    est = engine.optimizers[0].estimate(item, item.stat(), work)
    staged = est.staged
    assert staged is not None
    real_stat = Path.stat

    def other_device(self: Path, **kwargs: Any) -> os.stat_result:
        st = real_stat(self, **kwargs)
        if self == staged:
            values = list(st)
            values[2] += 1
            return os.stat_result(values)
        return st

    monkeypatch.setattr(Path, "stat", other_device)
    monkeypatch.setattr(engine_mod, "free_space", lambda path: 0)
    outcome = engine.apply_one(est, work)
    assert outcome.status == "skipped" and not staged.exists()
    monkeypatch.setattr(engine_mod, "free_space", lambda path: 10**15)
    est = engine.optimizers[0].estimate(item, item.stat(), work)
    staged = est.staged
    assert engine.apply_one(est, work).status == "optimized"


def test_interrupt_stops_everything(tmp_path: Path, config: Config,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    config.optimize.jobs = 1
    items = [data_file(tmp_path / f"f{i}.dat") for i in range(20)]
    engine = make_engine(config, Out())
    started: list[Path] = []
    release = threading.Event()
    terminated: list[bool] = []

    class Slow(Halver):
        def estimate(self, path: Path, st: os.stat_result, workdir: Path) -> Estimate:
            started.append(path)
            release.wait(5)
            return super().estimate(path, st, workdir)

    slow = Slow(config, FakeTools())
    engine.tools.terminate_all = lambda: (terminated.append(True), release.set())  # type: ignore[method-assign]

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(engine_mod, "as_completed", interrupt)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(KeyboardInterrupt):
        engine.estimate([Candidate(p, p.stat(), slow) for p in items], work)
    assert terminated == [True]
    assert len(started) <= 1  # queued files never start after Ctrl+C
    stop = threading.Event()
    stop.set()
    est = engine._safe_estimate(Candidate(items[0], items[0].stat(), slow), work, stop)
    assert est.skip_reason == "interrupted"
