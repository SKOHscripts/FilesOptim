from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Any

import pytest

from filesoptim.config import Config
from filesoptim.optimize import video as video_mod
from filesoptim.optimize.base import Estimate
from filesoptim.optimize.video import VideoInfo, VideoOptimizer, parse_probe, parse_rate
from filesoptim.tools import ToolError
from tests.conftest import FakePopen, FakeTools

FPS = 10
PACKET = 1000


def probe_data(*, duration: float = 100.0, codec: str = "h264", width: int = 1920,
               height: int = 1080, fps: str = "30/1", bit_rate: str | None = "8000000",
               transfer: str = "bt709", tags: dict[str, str] | None = None,
               extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    stream: dict[str, Any] = {"codec_type": "video", "codec_name": codec, "width": width,
                              "height": height, "avg_frame_rate": fps,
                              "color_transfer": transfer, "disposition": {"attached_pic": 0}}
    if bit_rate is not None:
        stream["bit_rate"] = bit_rate
    return {
        "format": {"duration": str(duration), "start_time": "0.000000",
                   "tags": {"creation_time": "2021-01-01T00:00:00.000000Z"}
                   if tags is None else tags},
        "streams": [stream, {"codec_type": "audio", "bit_rate": "128000"}, *(extra or [])],
    }


class Lab:
    """Scriptable ffmpeg/ffprobe pair."""

    def __init__(self, tmp_path: Path, *, duration: float = 100.0, encoders: str = "libx265",
                 filters: str = "ssim", ssim: tuple[str, ...] = ("0.99",),
                 sample_bytes: int = 30_000, out_size: int = 400_000,
                 out_duration: float | None = None, popen_code: int = 0,
                 source_probe: dict[str, Any] | None = None, name: str = "clip.mp4") -> None:
        self.source = tmp_path / name
        self.source.write_bytes(b"\0" * 1_100_000)
        self.duration = duration
        self.encoders, self.filters = encoders, filters
        self.ssim = list(ssim)
        self.sample_bytes, self.out_size = sample_bytes, out_size
        self.out_duration = duration if out_duration is None else out_duration
        self.popen_code = popen_code
        self.source_probe = source_probe or probe_data(duration=duration)
        self.output_probe: dict[str, Any] | None = probe_data(duration=self.out_duration,
                                                                codec="hevc")
        self.tools = FakeTools(["ffmpeg", "ffprobe", "exiftool"], ffmpeg=self.ffmpeg,
                               ffprobe=self.ffprobe)
        self.tools.popen_handler = self.popen
        self.progress: list[float] = []

    def ffprobe(self, argv: list[str]) -> tuple[int, str, str]:
        path = Path(argv[-1])
        if "-show_format" in argv:
            data = self.source_probe if path == self.source else self.output_probe
            return 0, json.dumps(data or {"streams": []}), ""
        if path != self.source:  # an encoded sample
            return 0, f"0.000000,{self.sample_bytes}\n", ""
        lines = [f"{i / FPS:.6f},{PACKET}" for i in range(int(self.duration * FPS))]
        return 0, "\n".join([*lines, "N/A,5", "garbage", ""]), ""

    def ffmpeg(self, argv: list[str]) -> tuple[int, str, str]:
        if "-encoders" in argv:
            return 0, f" V..... {self.encoders}   some encoder\n", ""
        if "-filters" in argv:
            return 0, f" ... {self.filters}   VV->V  metric\n", ""
        if "-lavfi" in argv:
            value = self.ssim.pop(0) if len(self.ssim) > 1 else self.ssim[0]
            if "libvmaf" in argv[argv.index("-lavfi") + 1]:
                return 0, "", f"VMAF score: {value}\n"
            return 0, "", f"[Parsed_ssim_0] SSIM Y:0.9 All:{value} (20.0)\n"
        Path(argv[-1]).write_bytes(b"s" * 10)  # sample encode
        return 0, "", ""

    def popen(self, argv: list[str], stderr: IO[Any]) -> FakePopen:
        Path(argv[-1]).write_bytes(b"v" * self.out_size)
        lines = ["frame=1\n", "out_time_us=N/A\n", f"out_time_us={int(self.duration * 5e5)}\n",
                 "progress=end\n"]
        return FakePopen(lines, self.popen_code, "x265 error: bad\n" if self.popen_code else "",
                         stderr)

    def optimizer(self, config: Config) -> VideoOptimizer:
        config.video.min_size_mb = 0
        opt = VideoOptimizer(config, self.tools)
        opt.progress = self.progress.append
        return opt


def run_estimate(lab: Lab, opt: VideoOptimizer, tmp_path: Path) -> Estimate:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return opt.estimate(lab.source, lab.source.stat(), work)


# -- parsing ------------------------------------------------------------------------------
@pytest.mark.parametrize(("text", "value"),
                         [("30000/1001", 29.97), ("25", 25.0), ("0/0", 0.0), ("abc", 0.0),
                          ("-5/1", 0.0)])
def test_parse_rate(text: str, value: float) -> None:
    assert parse_rate(text) == pytest.approx(value, abs=0.01)


def test_parse_probe_selection_and_fallbacks() -> None:
    assert parse_probe({}, 0) is None
    cover = {"codec_type": "video", "disposition": {"attached_pic": 1}, "width": 500}
    assert parse_probe({"streams": [cover, "junk"]}, 0) is None
    data = {
        "format": {"bit_rate": "1000000", "tags": {"CREATION_TIME": "x", "FilesOptim": "1"}},
        "streams": [
            cover,
            {"codec_type": "video", "codec_name": "mpeg4", "width": 320, "height": 240,
             "duration": "10", "r_frame_rate": "25/1"},
            {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 480,
             "duration": "12", "avg_frame_rate": "0/0", "r_frame_rate": "30/1"},
            {"codec_type": "audio", "bit_rate": "200000"},
            {"codec_type": "data"},
        ],
    }
    info = parse_probe(data, 0)
    assert info is not None
    assert (info.video_index, info.codec, info.duration, info.fps) == (2, "h264", 12.0, 30.0)
    assert info.bit_rate == 800_000
    assert info.tags == {"creation_time": "x", "filesoptim": "1"}
    assert info.creation_time == "x"
    assert (info.stream_count, info.data_streams) == (5, 1)
    by_size = parse_probe({"format": {"duration": "4"},
                           "streams": [{"codec_type": "video", "width": 10, "height": 10}]}, 1000)
    assert by_size is not None and by_size.bit_rate == 2000
    assert by_size.bits_per_pixel is None  # no frame rate
    no_duration = parse_probe({"streams": [{"codec_type": "video"}]}, 1000)
    assert no_duration is not None and no_duration.bit_rate == 0.0
    assert video_mod._float(None) == 0.0


# -- capabilities -------------------------------------------------------------------------
def test_encoder_metric_and_settings(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path)
    opt = lab.optimizer(config)
    assert opt.lossy
    assert opt.encoder() == "libx265"
    assert opt.metric == "ssim" and opt.threshold == 0.98
    assert opt.crf_preset() == (22, "medium")
    assert opt.missing_tools() == [] and opt.unavailable_reason() is None
    assert opt.min_saving() == (20.0, 2048)
    assert "hevc:crf22:medium:ssim0.98" in opt.signature()
    config.video.crf, config.video.preset = 18, "slow"
    assert opt.crf_preset() == (18, "slow")
    config.video.metric = "vmaf"
    assert opt.metric == "ssim"  # ffmpeg without libvmaf
    lab2 = Lab(tmp_path, filters="libvmaf", encoders="libaom-av1")
    config.video.codec = "av1"
    opt2 = lab2.optimizer(config)
    assert opt2.metric == "vmaf" and opt2.threshold == 95.0
    assert opt2.encoder() == "libaom-av1"


def test_unavailable(tmp_path: Path, config: Config) -> None:
    assert VideoOptimizer(config, FakeTools()).unavailable_reason() == "missing ffmpeg, ffprobe"
    lab = Lab(tmp_path, encoders="libx264")
    assert lab.optimizer(config).unavailable_reason() == "ffmpeg was built without libx265"
    broken = FakeTools(["ffmpeg", "ffprobe"], ffmpeg=lambda argv: (1, "", "boom"))
    assert VideoOptimizer(config, broken).encoder() is None


def test_target_path(config: Config) -> None:
    opt = VideoOptimizer(config, FakeTools())
    assert opt.target_path(Path("a.MP4")) == Path("a.MP4")
    assert opt.target_path(Path("a.mkv")) == Path("a.mkv")
    assert opt.target_path(Path("a.avi")) == Path("a.mkv")
    assert opt.target_path(Path("a.webm")) == Path("a.mkv")
    config.video.codec = "av1"
    assert opt.target_path(Path("a.webm")) == Path("a.webm")


# -- analysis -----------------------------------------------------------------------------
def test_probe_invalid_json(tmp_path: Path, config: Config) -> None:
    tools = FakeTools(["ffprobe"], ffprobe=lambda argv: (0, "{nope", ""))
    with pytest.raises(ToolError, match="unreadable ffprobe output"):
        VideoOptimizer(config, tools).probe(tmp_path)


def info_for(**kwargs: Any) -> VideoInfo | None:
    return parse_probe(probe_data(**kwargs), 1_000_000)


def test_prechecks(tmp_path: Path, config: Config) -> None:
    config.video.min_size_mb = 0
    opt = VideoOptimizer(config, FakeTools())
    clip = tmp_path / "clip.mp4"
    assert opt.precheck(clip, 10, None) == "no video stream"
    assert opt.precheck(clip, 10, info_for(tags={"filesoptim": "x"})) == (
        "already re-encoded by FilesOptim")
    assert "efficient codec (hevc)" in str(opt.precheck(clip, 10, info_for(codec="hevc")))
    assert opt.precheck(clip, 10, info_for(transfer="smpte2084")) == "HDR video (left untouched)"
    assert opt.precheck(clip, 10, info_for(duration=0)) == "unknown duration"
    assert "heavily compressed" in str(opt.precheck(clip, 10, info_for(bit_rate="100000")))
    assert opt.precheck(clip, 10, info_for()) is None
    assert opt.precheck(clip, 10, info_for(bit_rate=None, fps="0/0")) is None
    assert "WebM" in str(opt.precheck(tmp_path / "v.webm", 10, info_for()))
    (tmp_path / "old.mkv").write_bytes(b"")
    assert "old.mkv already exists" in str(opt.precheck(tmp_path / "old.avi", 10, info_for()))
    config.video.min_size_mb = 5
    assert "too small" in str(opt.precheck(clip, 10, info_for()))
    config.video.skip_hdr = False
    config.video.min_size_mb = 0
    assert opt.precheck(clip, 10, info_for(transfer="smpte2084")) is None


def test_stream_bytes_and_windows(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path)
    opt = lab.optimizer(config)
    assert opt.stream_bytes(lab.source, 0) == 100 * FPS * PACKET + 5  # "N/A" pts still counts
    assert opt.stream_bytes(lab.source, 0, (10.0, 6.0)) == 60 * PACKET
    assert opt.stream_bytes(lab.source, 0, (0.5, 1.0)) == 10 * PACKET
    windows = opt.sample_windows(100.0)
    assert windows == [(7.0, 6.0), (27.0, 6.0), (47.0, 6.0), (67.0, 6.0), (87.0, 6.0)]
    assert opt.sample_windows(8.0)[-1] == (2.0, 6.0)  # clamped inside the video


# -- command lines ------------------------------------------------------------------------
def test_codec_arguments(tmp_path: Path, config: Config) -> None:
    for encoders, codec, expected in (
        ("libx265", "hevc", ["-preset", "medium", "-x265-params", "log-level=error"]),
        ("libaom-av1", "av1", ["-b:v", "0", "-cpu-used", "6"]),
        ("libsvtav1", "av1", ["-preset", "6"]),
    ):
        config.video.codec = codec
        args = Lab(tmp_path, encoders=encoders).optimizer(config)._codec_args("v:0")
        assert args[:2] == ["-c:v:0", encoders]
        assert args[4:] == expected


def test_encode_args(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path)
    opt = lab.optimizer(config)
    info = info_for()
    assert info is not None
    sample = opt.encode_args(lab.source, info, tmp_path / "s.mkv", (7.0, 6.0))
    assert sample[6:10] == ["-ss", "7.000", "-t", "6.000"]
    assert "-an" in sample and "-metadata" not in sample
    full = opt.encode_args(lab.source, info, tmp_path / "o.mp4")
    assert full[full.index("-map", full.index("-map") + 1):][:2] == ["-map", "-0:d"]
    assert "hvc1" in full and "+faststart+use_metadata_tags" in full
    assert "creation_time=2021-01-01T00:00:00.000000Z" in full
    config.video.data_streams = "keep"
    info.creation_time = ""
    mkv = opt.encode_args(lab.source, info, tmp_path / "o.mkv")
    assert "-0:d" not in mkv and "hvc1" not in mkv and "-movflags" not in mkv
    assert not any(a.startswith("creation_time") for a in mkv)
    config.video.codec = "av1"
    av1 = Lab(tmp_path, encoders="libsvtav1").optimizer(config)
    assert "hvc1" not in av1.encode_args(lab.source, info, tmp_path / "o.mp4")


def test_run_with_progress(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path, duration=10)
    opt = lab.optimizer(config)
    opt.run_with_progress(["ffmpeg", str(tmp_path / "o.mkv")], 10)
    assert lab.progress == [0.5]
    assert lab.tools.calls[-1][-4:-1] == ["-progress", "pipe:1", "-nostats"]
    opt.progress = None
    opt.run_with_progress(["ffmpeg", str(tmp_path / "o.mkv")], 10)
    lab.popen_code = 1
    with pytest.raises(ToolError, match="x265 error: bad"):
        opt.run_with_progress(["ffmpeg", str(tmp_path / "o.mkv")], 10)

    def silent_failure(argv: list[str], stderr: IO[Any]) -> FakePopen:
        return FakePopen([], 7, "", stderr)

    lab.tools.popen_handler = silent_failure
    with pytest.raises(ToolError, match="exit code 7"):
        opt.run_with_progress(["ffmpeg", "out"], 10)
    created: list[FakePopen] = []

    def interrupted(argv: list[str], stderr: IO[Any]) -> FakePopen:
        created.append(FakePopen(["a\n", "b\n"], 0, "", stderr, raise_after=1))
        return created[-1]

    lab.tools.popen_handler = interrupted
    with pytest.raises(KeyboardInterrupt):
        opt.run_with_progress(["ffmpeg", "out"], 10)
    assert created[0].killed


def test_measure(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path, ssim=("0.9912",))
    opt = lab.optimizer(config)
    info = info_for()
    assert info is not None
    assert opt.measure(tmp_path / "s.mkv", lab.source, info, (7.0, 6.0)) == 0.9912
    graph = lab.tools.calls[-1][lab.tools.calls[-1].index("-lavfi") + 1]
    assert graph.startswith("[0:v:0]setpts=PTS-STARTPTS[d];[1:v:0]")
    assert opt.measure(tmp_path / "o.mkv", lab.source, info) == 0.9912
    config.video.metric = "vmaf"
    vmaf = Lab(tmp_path, filters="libvmaf", ssim=("96.5",)).optimizer(config)
    assert vmaf.measure(tmp_path / "o.mkv", lab.source, info) == 96.5
    silent = FakeTools(["ffmpeg"], ffmpeg=lambda argv: (0, "", "nothing"))
    with pytest.raises(ToolError, match="SSIM measurement gave no result"):
        VideoOptimizer(Config(), silent).measure(tmp_path, tmp_path, info)
    assert opt.judge_quality(0.99) is None
    assert "quality too low" in str(opt.judge_quality(0.5))


def test_copy_metadata(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path)
    opt = lab.optimizer(config)
    opt.copy_metadata(lab.source, tmp_path / "o.mp4")
    assert lab.tools.calls[-1][0] == "exiftool"
    count = len(lab.tools.calls)
    opt.copy_metadata(lab.source, tmp_path / "o.mkv")
    config.video.copy_metadata = False
    opt.copy_metadata(lab.source, tmp_path / "o.mp4")
    assert len(lab.tools.calls) == count
    config.video.copy_metadata = True
    lab.tools.available.discard("exiftool")
    opt.copy_metadata(lab.source, tmp_path / "o.mp4")
    assert len(lab.tools.calls) == count
    lab.tools.available.add("exiftool")
    lab.tools.handlers["exiftool"] = lambda argv: (1, "", "cannot write")
    opt.copy_metadata(lab.source, tmp_path / "o.mp4")  # errors are ignored


# -- estimate -----------------------------------------------------------------------------
def test_estimate_from_samples(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path)
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert est.candidate and not est.exact
    # 5 samples of 60 packets (60 000 bytes) encoded into 30 000 bytes each: ratio 0.5
    video_bytes = 1_000_005
    assert est.estimated_size == round(1_100_000 - video_bytes + video_bytes * 0.5)
    assert est.quality == 0.99 and est.metric == "ssim"
    assert lab.progress == [0.0, 0.2, 0.4, 0.6, 0.8]
    assert not list((tmp_path / "work").iterdir())  # samples removed
    silent = lab.optimizer(config)
    silent.progress = None
    assert run_estimate(lab, silent, tmp_path).candidate


def test_estimate_rejections(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path, ssim=("0.99", "0.95", "0.99"))
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert est.quality == 0.95 and "quality too low" in str(est.skip_reason)
    lab = Lab(tmp_path, sample_bytes=58_000)
    assert "no significant gain" in str(run_estimate(lab, lab.optimizer(config), tmp_path)
                                        .skip_reason)
    lab = Lab(tmp_path, sample_bytes=0)
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert "could not measure" in str(est.skip_reason)
    lab = Lab(tmp_path)
    lab.tools.handlers["ffmpeg"] = lambda argv: (1, "", "encoder crashed")
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert str(est.skip_reason).startswith("estimation failed") and not est.remember
    lab = Lab(tmp_path)
    lab.tools.handlers["ffprobe"] = lambda argv: (1, "", "moov atom not found")
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert str(est.skip_reason).startswith("cannot analyse video") and est.remember
    lab = Lab(tmp_path, source_probe=probe_data(codec="av1"))
    assert "efficient codec" in str(run_estimate(lab, lab.optimizer(config), tmp_path)
                                    .skip_reason)


def test_short_video_is_fully_encoded(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path, duration=20, name="old.avi")
    lab.source_probe = probe_data(duration=20)
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert est.candidate and est.exact
    assert est.staged is not None and est.staged.suffix == ".mkv"
    assert est.target == tmp_path / "old.mkv"
    assert est.estimated_size == 400_000


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [({"out_size": 1_090_000}, "no significant gain"),
     ({"out_duration": 15}, "duration does not match"),
     ({"ssim": ("0.90",)}, "quality too low"),
     ({"popen_code": 1}, "encoding failed")],
)
def test_full_encode_checks(tmp_path: Path, config: Config, kwargs: dict[str, Any],
                            reason: str) -> None:
    lab = Lab(tmp_path, duration=20, **kwargs)
    lab.source_probe = probe_data(duration=20)
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert reason in str(est.skip_reason)
    assert est.staged is None
    assert not list((tmp_path / "work").iterdir())


def test_full_encode_output_without_video(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path, duration=20)
    lab.source_probe = probe_data(duration=20)
    lab.output_probe = None
    est = run_estimate(lab, lab.optimizer(config), tmp_path)
    assert "duration does not match" in str(est.skip_reason)


def test_full_encode_interrupted(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path, duration=20)
    lab.source_probe = probe_data(duration=20)

    def interrupted(argv: list[str], stderr: IO[Any]) -> FakePopen:
        Path(argv[-1]).write_bytes(b"partial")
        return FakePopen(["x\n"], 0, "", stderr, raise_after=0)

    lab.tools.popen_handler = interrupted
    with pytest.raises(KeyboardInterrupt):
        run_estimate(lab, lab.optimizer(config), tmp_path)
    assert not list((tmp_path / "work").iterdir())


# -- materialise --------------------------------------------------------------------------
def test_materialize(tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    lab = Lab(tmp_path, ssim=("0.99",))
    opt = lab.optimizer(config)
    est = run_estimate(lab, opt, tmp_path)
    lab.tools.calls.clear()
    final = opt.materialize(est, tmp_path / "work")
    assert final.candidate and final.exact and final.staged is not None
    assert final.staged.parent == tmp_path  # next to the original
    assert final.staged.name.startswith(".clip") and final.estimated_size == 400_000
    assert any("-lavfi" in call for call in lab.tools.calls)  # full quality check
    final.discard()

    config.video.verify = "sample"
    lab.tools.calls.clear()
    est = run_estimate(lab, opt, tmp_path)
    lab.tools.calls.clear()
    assert opt.materialize(est, tmp_path / "work").candidate
    assert not any("-lavfi" in call for call in lab.tools.calls)

    monkeypatch.setattr(video_mod, "free_space", lambda path: 10)
    assert "free disk space" in str(opt.materialize(run_estimate(lab, opt, tmp_path),
                                                    tmp_path / "work").skip_reason)


def test_materialize_probe_failures(tmp_path: Path, config: Config) -> None:
    lab = Lab(tmp_path)
    opt = lab.optimizer(config)
    est = run_estimate(lab, opt, tmp_path)
    lab.source_probe = {"streams": []}
    assert opt.materialize(est, tmp_path).skip_reason == "no video stream"
    est.skip_reason = None
    lab.tools.handlers["ffprobe"] = lambda argv: (1, "", "gone")
    result = opt.materialize(est, tmp_path)
    assert str(result.skip_reason).startswith("cannot analyse video") and not result.remember
