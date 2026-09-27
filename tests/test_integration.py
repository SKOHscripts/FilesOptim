"""End-to-end tests with the real external programs (skipped when they are missing)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from filesoptim.config import Config
from filesoptim.optimize.engine import Engine
from filesoptim.optimize.images import images_identical
from filesoptim.sorting.executor import SortExecutor, iter_undo, plan_undo, read_journal
from filesoptim.sorting.metadata import MetadataReader
from filesoptim.sorting.planner import Planner, SortOptions
from filesoptim.state import StateDB
from filesoptim.tools import Tools
from filesoptim.trash import trash_root
from tests.conftest import Out, age, make_image

pytestmark = pytest.mark.integration


def need(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        pytest.skip(f"missing: {', '.join(missing)}")


def exif_value(path: Path, tag: str) -> str:
    out = subprocess.run(["exiftool", "-s3", f"-{tag}", str(path)], capture_output=True,
                         text=True, check=True).stdout
    return out.strip()


def ffprobe_json(path: Path) -> dict[str, object]:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True,
                         check=True).stdout
    return json.loads(out)  # type: ignore[no-any-return]


def engine(config: Config, out: Out) -> Engine:
    return Engine(config, Tools(), out, StateDB(None))


def test_images_are_optimised_losslessly(tmp_path: Path, config: Config) -> None:
    need("jpegoptim", "optipng", "gifsicle")
    config.optimize.jpeg_progressive = True
    photos = tmp_path / "photos"
    exif = Image.Exif()
    exif[272] = "Test Camera"
    jpeg = make_image(photos / "a.jpg", size=(640, 480), fmt="JPEG", quality=92, exif=exif)
    png = make_image(photos / "b.png", size=(640, 480), compress_level=0)
    gif = make_image(photos / "c.gif", size=(320, 240), mode="P")
    originals = {p: tmp_path / f"orig-{p.name}" for p in (jpeg, png, gif)}
    for path, copy in originals.items():
        shutil.copy2(path, copy)
        age(path)
    mtime = jpeg.stat().st_mtime
    out = Out()
    summary = engine(config, out).run([photos], assume_yes=True)
    assert summary.applied and not summary.failed
    for path, copy in originals.items():
        assert images_identical(path, copy), path  # pixel-identical
        assert path.stat().st_size <= copy.stat().st_size
    assert png.stat().st_size < originals[png].stat().st_size
    assert jpeg.stat().st_mtime == mtime  # timestamps preserved
    need("exiftool")
    assert exif_value(jpeg, "Model") == "Test Camera"  # metadata preserved


def test_pdf_lossless_and_guards(tmp_path: Path, config: Config) -> None:
    need("qpdf")
    pages = [Image.new("RGB", (400, 300), (i * 40, 100, 200)) for i in range(3)]
    source = tmp_path / "doc.pdf"
    pages[0].save(source, save_all=True, append_images=pages[1:])
    age(source)
    encrypted = tmp_path / "secret.pdf"
    subprocess.run(["qpdf", "--encrypt", "u", "o", "256", "--", str(source), str(encrypted)],
                   check=True)
    age(encrypted)
    out = Out(verbose=True)
    summary = engine(config, out).run([tmp_path], estimate_only=True)
    reasons = {e.path.name: e.skip_reason for e in summary.estimates}
    assert reasons["secret.pdf"] == "encrypted PDF (left untouched)"
    assert "doc.pdf" in reasons  # either a verified gain or "no significant gain"


def test_video_estimate_is_precise(tmp_path: Path, config: Config) -> None:
    need("ffmpeg", "ffprobe")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=20",
         "-f", "lavfi", "-i", "sine=f=440", "-t", "24", "-c:v", "libx264", "-crf", "0",
         "-preset", "ultrafast", "-g", "40", "-c:a", "aac", "-shortest",
         "-metadata", "creation_time=2020-05-06T07:08:09Z", str(clip)], check=True)
    age(clip)
    config.video.min_size_mb = 0
    config.video.preset = "veryfast"
    config.video.sample_count, config.video.sample_seconds = 2, 3
    out = Out()
    summary = engine(config, out).run([tmp_path], assume_yes=True)
    estimate = summary.estimates[0]
    assert estimate.candidate and not estimate.exact, estimate.skip_reason
    outcome = summary.outcomes[0]
    assert outcome.status == "optimized", outcome.message
    # the sampled estimate is close to the real result
    error = abs(estimate.estimated_size - outcome.after) / outcome.after
    print(f"estimated {estimate.estimated_size}, real {outcome.after}, error {error:.1%}")
    assert error < 0.25
    info = ffprobe_json(clip)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")  # type: ignore[union-attr,index]
    tags = info["format"]["tags"]  # type: ignore[index]
    assert video["codec_name"] == "hevc" and video["codec_tag_string"] == "hvc1"
    assert "filesoptim" in tags and tags["creation_time"].startswith("2020-05-06T07:08:09")
    assert (trash_root() / "files" / "clip.mp4").exists()  # lossy: original kept in the trash
    again = engine(config, Out()).run([tmp_path], estimate_only=True)
    assert again.estimates[0].skip_reason == "already re-encoded by FilesOptim"


def test_short_video_is_converted_to_mkv(tmp_path: Path, config: Config) -> None:
    need("ffmpeg", "ffprobe")
    clip = tmp_path / "old.avi"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=20",
         "-t", "4", "-c:v", "mpeg4", "-q:v", "1", str(clip)], check=True)
    age(clip)
    config.video.min_size_mb = 0
    config.video.preset = "ultrafast"
    config.optimize.keep_originals = "never"
    summary = engine(config, Out()).run([tmp_path], assume_yes=True)
    assert summary.estimates[0].exact
    assert summary.outcomes[0].final_path == tmp_path / "old.mkv"
    assert not clip.exists() and (tmp_path / "old.mkv").exists()


def test_sort_with_real_metadata_and_undo(tmp_path: Path, config: Config) -> None:
    need("exiftool")
    src = tmp_path / "src"
    photo = make_image(src / "Holidays" / "IMG_20190612_101530.jpg", fmt="JPEG")
    snapshot = photo.read_bytes()
    tools = Tools()
    plan = Planner(config, tools).plan(SortOptions(src, tmp_path / "out", rename=True,
                                                   fix_dates=True, tag=True))
    result = SortExecutor(tools, Out()).execute(plan)
    moved = tmp_path / "out" / "Photos" / "2019" / "06" / "2019-06-12_10-15-30.jpg"
    assert result.done == 1 and moved.exists()
    assert exif_value(moved, "DateTimeOriginal") == "2019:06:12 10:15:30"
    assert exif_value(moved, "Subject") == "Photo, Holidays"
    _, entries = read_journal(result.journal)
    errors = [e for _, e in iter_undo(plan_undo(entries), tools)]
    assert errors == [""] * len(errors)
    assert photo.read_bytes() == snapshot  # byte-identical after undo


def test_metadata_readers(tmp_path: Path) -> None:
    need("ffmpeg", "exiftool")
    song = tmp_path / "song.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440", "-t", "1",
                    "-metadata", "artist=Daft Punk", "-metadata", "album=Discovery",
                    "-metadata", "title=One More Time", "-metadata", "track=1/14",
                    str(song)], check=True)
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64",
                    "-t", "1", "-metadata", "creation_time=2018-03-04T05:06:07Z", str(clip)],
                   check=True)
    infos = MetadataReader(Tools()).read([song, clip])
    assert (infos[song].artist, infos[song].album, infos[song].track) == (
        "Daft Punk", "Discovery", 1)
    assert infos[clip].date is not None and infos[clip].date.year == 2018
    no_exiftool = MetadataReader(Tools(path=str(Path(shutil.which("ffprobe") or "").parent)))
    assert no_exiftool.read([clip])[clip].date_source == "metadata"


def test_command_line_entry_point() -> None:
    proc = subprocess.run([sys.executable, "-m", "filesoptim", "--version"],
                          capture_output=True, text=True, check=True)
    assert proc.stdout.startswith("filesoptim ")
