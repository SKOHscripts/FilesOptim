from __future__ import annotations

import os
from pathlib import Path

import pytest

from filesoptim import doctor as doctor_mod
from filesoptim.config import Config, default_config_path
from filesoptim.doctor import _version, run_doctor
from filesoptim.dupes import DuplicateGroup
from filesoptim.fsutils import WalkOptions
from filesoptim.optimize.base import Estimate
from filesoptim.optimize.engine import OptimizeSummary
from filesoptim.report import build_report, estimate_totals, render_report, report_to_dict
from tests.conftest import FakeTools, Out


def touch(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    touch(root / "photos" / "a.jpg", 5000)
    touch(root / "photos" / "deep" / "b.png", 3000)
    touch(root / "videos" / "c.mp4", 20000)
    touch(root / "notes.txt", 10)
    os.link(root / "videos" / "c.mp4", root / "videos" / "hardlink.mp4")
    (root / "link").symlink_to(root / "notes.txt")
    return root


def test_build_and_render(tree: Path) -> None:
    report = build_report([tree], WalkOptions(), top=2)
    assert report.files == 4 and report.dirs == 3  # hard links and symlinks count once/never
    assert report.categories["image"][0] == 2
    assert set(report.categories) == {"image", "video", "document"}
    assert report.children[tree / "photos"] > 0 and tree / "notes.txt" not in report.children
    assert [p.name for _, p in report.biggest] == ["c.mp4", "a.jpg"]
    assert dict(report.optimizable) == {"jpeg": 1, "png": 1, "video": 1}
    out = Out()
    render_report(report, out, top=5)
    text = out.text
    for title in ("Disks", "Content: 4 files", "Biggest folders", "Biggest files",
                  "Files the optimiser can examine"):
        assert title in text
    assert "photos/deep/b.png" not in text and "videos/c.mp4" in text  # relative paths
    assert build_report([tree], WalkOptions(), top=0).biggest == []


def test_render_empty_and_multi_roots(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    report = build_report([tmp_path / "a", tmp_path / "b"], WalkOptions())
    out = Out()
    render_report(report, out)
    assert "Biggest" not in out.text and "optimiser" not in out.text
    assert str(tmp_path / "a") in out.text


def test_json_payload(tree: Path) -> None:
    report = build_report([tree], WalkOptions())
    est = Estimate(tree / "photos/a.jpg", "jpeg", 5000, (5000, 1), estimated_size=4000)
    skipped = Estimate(tree / "x.png", "png", 10, (10, 1)).skip("no gain")
    summary = OptimizeSummary([est, skipped])
    totals = estimate_totals(summary)
    assert totals == {"by_type": {"jpeg": {"files": 1, "size": 5000, "saving": 1000}},
                      "total_saving": 1000, "skipped": 1}
    notes = tree / "notes.txt"
    group = DuplicateGroup(10, [notes], {notes: notes.stat()}, keep=notes)
    data = report_to_dict(report, estimate=summary, duplicates=[group])
    assert data["files"] == 4 and data["estimate"]["total_saving"] == 1000
    assert data["duplicates"] == {"groups": 1, "wasted": 0}
    assert "estimate" not in report_to_dict(report)


def test_doctor_all_good(tmp_path: Path) -> None:
    config = Config()
    names = ["jpegoptim", "jpegtran", "oxipng", "optipng", "gifsicle", "qpdf", "gs", "ffmpeg",
             "ffprobe", "exiftool"]
    tools = FakeTools(names, ffmpeg=lambda argv: (0, " V..... libx265  HEVC\n", ""))
    path = default_config_path()
    path.parent.mkdir(parents=True)
    path.write_text("")
    out = Out()
    assert run_doctor(config, tools, out, path) == 0
    assert "optimize video (libx265, quality metric: SSIM)" in out.text
    assert "sort: dates and XMP keywords can be written" in out.text
    assert "not created" not in out.text and "missing" not in out.text


def test_doctor_missing_tools(tmp_path: Path) -> None:
    out = Out()
    status = run_doctor(Config(), FakeTools(["apt-get"]), out, tmp_path / "config.toml")
    assert status == 1
    assert "sudo apt install" in out.text
    assert "optimize jpeg: unavailable (missing jpegoptim)" in out.text
    assert "exiftool missing" in out.text and "not created" in out.text


def test_version_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _version("definitely-not-installed-package") == "missing"
    assert _version("Pillow") != "missing"
    assert doctor_mod.state_dir().name == "filesoptim"
