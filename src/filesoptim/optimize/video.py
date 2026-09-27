"""Visually-lossless video re-encoding with measured quality (SSIM or VMAF).

Estimation encodes short samples spread over the whole video and compares the exact number
of bytes of the source video packets in the same time windows, so the predicted size is
precise without encoding everything. Quality is measured on those samples too. Only files
passing both the gain *and* the quality thresholds are re-encoded, and the final encode is
checked again (duration, gain, quality) before replacing anything.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filesoptim.categories import CATEGORY_EXTENSIONS, extension
from filesoptim.fsutils import TEMP_MARK, free_space
from filesoptim.optimize.base import Estimate, Optimizer
from filesoptim.tools import ToolError

HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})
MP4_FAMILY = frozenset({"mp4", "m4v", "mov"})
CODEC_DEFAULTS = {"hevc": (22, "medium"), "av1": (30, "6")}
ENCODERS = {"hevc": ("libx265",), "av1": ("libsvtav1", "libaom-av1")}
MARKER_TAG = "filesoptim"
_SSIM_RE = re.compile(r"All:\s*([0-9.]+)")
_VMAF_RE = re.compile(r"VMAF score:\s*([0-9.]+)")
_MB = 1024 * 1024


def parse_rate(text: str) -> float:
    """``"30000/1001"`` -> 29.97; invalid or ``0/0`` -> 0."""
    num, _, den = text.partition("/")
    try:
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if value > 0 else 0.0


def _float(value: object) -> float:
    try:
        return float(str(value))
    except ValueError:
        return 0.0


@dataclass
class VideoInfo:
    duration: float
    start_time: float
    video_index: int
    codec: str
    width: int
    height: int
    fps: float
    bit_rate: float
    color_transfer: str
    stream_count: int
    data_streams: int
    creation_time: str
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def bits_per_pixel(self) -> float | None:
        pixels = self.width * self.height * self.fps
        if pixels <= 0 or self.bit_rate <= 0:
            return None
        return self.bit_rate / pixels


def parse_probe(data: dict[str, Any], file_size: int) -> VideoInfo | None:
    """Build :class:`VideoInfo` from ``ffprobe -show_format -show_streams`` JSON."""
    streams: list[dict[str, Any]] = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    fmt: dict[str, Any] = data.get("format") or {}
    videos = [s for s in streams if s.get("codec_type") == "video"]
    candidates = [
        (i, s) for i, s in enumerate(videos) if not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not candidates:
        return None
    index, main = max(
        candidates, key=lambda c: int(c[1].get("width") or 0) * int(c[1].get("height") or 0)
    )
    duration = _float(fmt.get("duration")) or _float(main.get("duration"))
    bit_rate = _float(main.get("bit_rate"))
    if bit_rate <= 0:
        total = _float(fmt.get("bit_rate")) or (file_size * 8 / duration if duration else 0.0)
        audio = sum(_float(s.get("bit_rate")) for s in streams if s.get("codec_type") == "audio")
        bit_rate = max(total - audio, 0.0)
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    fps = parse_rate(str(main.get("avg_frame_rate") or "")) or parse_rate(
        str(main.get("r_frame_rate") or "")
    )
    return VideoInfo(
        duration=duration,
        start_time=_float(fmt.get("start_time")),
        video_index=index,
        codec=str(main.get("codec_name") or "unknown"),
        width=int(main.get("width") or 0),
        height=int(main.get("height") or 0),
        fps=fps,
        bit_rate=bit_rate,
        color_transfer=str(main.get("color_transfer") or ""),
        stream_count=len(streams),
        data_streams=sum(1 for s in streams if s.get("codec_type") == "data"),
        creation_time=tags.get("creation_time", ""),
        tags=tags,
    )


class VideoOptimizer(Optimizer):
    name = "video"
    extensions = CATEGORY_EXTENSIONS["video"]

    _encoders: str | None = None
    _filters: str | None = None

    # -- settings ---------------------------------------------------------------------------
    @property
    def lossy(self) -> bool:
        return True

    @property
    def codec(self) -> str:
        return self.config.video.codec

    def _ffmpeg_list(self, what: str) -> str:
        try:
            return self.tools.run(["ffmpeg", "-hide_banner", f"-{what}"]).stdout
        except ToolError:
            return ""

    def encoder(self) -> str | None:
        if self._encoders is None:
            self._encoders = self._ffmpeg_list("encoders")
        for name in ENCODERS[self.codec]:
            if re.search(rf"\s{re.escape(name)}\s", self._encoders):
                return name
        return None

    @property
    def metric(self) -> str:
        if self.config.video.metric == "vmaf":
            if self._filters is None:
                self._filters = self._ffmpeg_list("filters")
            if re.search(r"\slibvmaf\s", self._filters):
                return "vmaf"
        return "ssim"

    @property
    def threshold(self) -> float:
        return self.config.video.min_vmaf if self.metric == "vmaf" else self.config.video.min_ssim

    def crf_preset(self) -> tuple[int, str]:
        default_crf, default_preset = CODEC_DEFAULTS[self.codec]
        return (self.config.video.crf or default_crf, self.config.video.preset or default_preset)

    def missing_tools(self) -> list[str]:
        return [t for t in ("ffmpeg", "ffprobe") if not self.tools.has(t)]

    def unavailable_reason(self) -> str | None:
        reason = super().unavailable_reason()
        if reason is None and self.encoder() is None:
            names = " or ".join(ENCODERS[self.codec])
            reason = f"ffmpeg was built without {names}"
        return reason

    def signature(self) -> str:
        crf, preset = self.crf_preset()
        return (
            f"{super().signature()}:{self.codec}:crf{crf}:{preset}:{self.metric}{self.threshold}"
            f":gain{self.config.video.min_saving_percent}"
        )

    def min_saving(self) -> tuple[float, int]:
        return self.config.video.min_saving_percent, self.config.optimize.min_saving_bytes

    def target_path(self, path: Path) -> Path:
        ext = extension(path)
        if ext in MP4_FAMILY or ext == "mkv" or (ext == "webm" and self.codec == "av1"):
            return path
        return path.with_suffix(".mkv")

    # -- analysis ---------------------------------------------------------------------------
    def probe(self, path: Path) -> VideoInfo | None:
        proc = self.tools.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
             str(path)]
        )
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ToolError(f"unreadable ffprobe output ({exc})") from exc
        return parse_probe(data, path.stat().st_size)

    def precheck(self, path: Path, size: int, info: VideoInfo | None) -> str | None:
        cfg = self.config.video
        if info is None:
            return "no video stream"
        if MARKER_TAG in info.tags:
            return "already re-encoded by FilesOptim"
        if info.codec in cfg.skip_codecs:
            return f"already uses an efficient codec ({info.codec})"
        if cfg.skip_hdr and info.color_transfer in HDR_TRANSFERS:
            return "HDR video (left untouched)"
        if info.duration <= 0:
            return "unknown duration"
        if size < cfg.min_size_mb * _MB:
            return f"too small to be worth re-encoding (< {cfg.min_size_mb} MiB)"
        if extension(path) == "webm" and self.codec != "av1":
            return "WebM container cannot hold HEVC (use codec = \"av1\")"
        target = self.target_path(path)
        if target != path and (target.exists() or target.is_symlink()):
            return f"cannot convert: {target.name} already exists"
        bpp = info.bits_per_pixel
        if bpp is not None and bpp < cfg.min_bits_per_pixel:
            return f"already heavily compressed ({bpp:.3f} bits/pixel)"
        return None

    def stream_bytes(
        self, path: Path, index: int, window: tuple[float, float] | None = None
    ) -> int:
        """Exact size of the packets of video stream ``index`` (optionally in a time window)."""
        args = ["ffprobe", "-v", "error", "-select_streams", f"v:{index}",
                "-show_entries", "packet=pts_time,size", "-of", "csv=p=0"]
        if window is not None:
            start, length = window
            # Read a little around the window: packets are stored in decoding order.
            args += ["-read_intervals", f"{max(start - 1, 0):.3f}%+{length + 3:.3f}"]
        args.append(str(path))
        total = 0
        for line in self.tools.run(args).stdout.splitlines():
            pts_text, _, size_text = line.strip().partition(",")
            try:
                size = int(size_text)
            except ValueError:
                continue
            if window is not None:
                try:
                    pts = float(pts_text)
                except ValueError:
                    continue
                if not window[0] <= pts < window[0] + window[1]:
                    continue
            total += size
        return total

    def sample_windows(self, duration: float) -> list[tuple[float, float]]:
        count, length = self.config.video.sample_count, float(self.config.video.sample_seconds)
        windows = []
        for i in range(count):
            centre = duration * (i + 0.5) / count
            start = max(0.0, min(centre - length / 2, duration - length))
            windows.append((round(start, 3), length))
        return sorted(set(windows))

    # -- encoding ---------------------------------------------------------------------------
    def _codec_args(self, stream: str) -> list[str]:
        encoder = self.encoder()
        crf, preset = self.crf_preset()
        args = [f"-c:{stream}", str(encoder), "-crf", str(crf)]
        if encoder == "libx265":
            return [*args, "-preset", preset, "-x265-params", "log-level=error"]
        if encoder == "libaom-av1":
            return [*args, "-b:v", "0", "-cpu-used", preset]
        return [*args, "-preset", preset]

    def encode_args(
        self,
        source: Path,
        info: VideoInfo,
        output: Path,
        window: tuple[float, float] | None = None,
    ) -> list[str]:
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error"]
        if window is not None:
            args += ["-ss", f"{window[0]:.3f}", "-t", f"{window[1]:.3f}"]
        args += ["-i", str(source)]
        if window is not None:  # sample: video only
            return [*args, "-map", f"0:v:{info.video_index}", "-an", "-sn", "-dn",
                    *self._codec_args("v:0"), str(output)]
        index = info.video_index
        args += ["-map", "0"]
        if self.config.video.data_streams == "drop":
            args += ["-map", "-0:d"]
        args += ["-c", "copy", *self._codec_args(f"v:{index}")]
        ext = extension(output)
        if self.codec == "hevc" and ext in MP4_FAMILY:
            args += [f"-tag:v:{index}", "hvc1"]
        args += ["-metadata", f"{MARKER_TAG}={self.signature()}"]
        if info.creation_time:
            args += ["-metadata", f"creation_time={info.creation_time}"]
        if ext in MP4_FAMILY:
            args += ["-movflags", "+faststart+use_metadata_tags"]
        return [*args, str(output)]

    def run_with_progress(self, args: list[str], duration: float) -> None:
        """Run ffmpeg reporting progress (fraction 0..1) to ``self.progress``."""
        args = [*args[:-1], "-progress", "pipe:1", "-nostats", args[-1]]
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errors:
            with self.tools.popen(args, stderr=errors) as proc:
                try:
                    for line in proc.stdout or ():
                        key, _, value = line.strip().partition("=")
                        if key == "out_time_us" and value.isdigit() and self.progress and duration:
                            self.progress(min(int(value) / 1e6 / duration, 1.0))
                    code = proc.wait()
                except BaseException:
                    proc.kill()
                    proc.wait()
                    raise
            if code != 0:
                errors.seek(0)
                lines = errors.read().strip().splitlines()
                raise ToolError(f"ffmpeg failed: {lines[-1] if lines else f'exit code {code}'}")

    def measure(
        self,
        distorted: Path,
        reference: Path,
        info: VideoInfo,
        window: tuple[float, float] | None = None,
    ) -> float:
        """SSIM (0..1) or VMAF (0..100) of ``distorted`` against ``reference``."""
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(distorted)]
        if window is not None:
            args += ["-ss", f"{window[0]:.3f}", "-t", f"{window[1]:.3f}"]
        args += ["-i", str(reference)]
        first = "[0:v:0]" if window is not None else f"[0:v:{info.video_index}]"
        second = f"[1:v:{info.video_index}]"
        metric = self.metric
        name, pattern = ("libvmaf", _VMAF_RE) if metric == "vmaf" else ("ssim", _SSIM_RE)
        # Both streams restart at t=0 so frames are compared pairwise, whatever the seek did.
        graph = (f"{first}setpts=PTS-STARTPTS[d];{second}setpts=PTS-STARTPTS[r];"
                 f"[d][r]{name}")
        args += ["-lavfi", graph, "-f", "null", "-"]
        found = pattern.findall(self.tools.run(args).stderr)
        if not found:
            raise ToolError(f"{metric.upper()} measurement gave no result")
        return float(found[-1])

    def judge_quality(self, value: float) -> str | None:
        if value < self.threshold:
            return (f"rejected: quality too low ({self.metric.upper()} {value:.4f} "
                    f"< {self.threshold})")
        return None

    def copy_metadata(self, source: Path, output: Path) -> None:
        """Best effort: copy every tag (GPS, camera, dates) to MP4/MOV outputs with exiftool."""
        if not (self.config.video.copy_metadata and self.tools.has("exiftool")):
            return
        if extension(output) not in MP4_FAMILY:
            return
        with contextlib.suppress(ToolError):  # the video itself is fine; tags are a bonus
            self.tools.run(["exiftool", "-q", "-q", "-overwrite_original", "-tagsFromFile",
                            str(source), "-all:all", str(output)])

    def _encode_and_check(
        self, est: Estimate, info: VideoInfo, output: Path, *, full_quality: bool
    ) -> Estimate:
        """Full encode of ``est.path`` into ``output`` followed by every safety check."""
        est.staged = output
        try:
            self.run_with_progress(self.encode_args(est.path, info, output), info.duration)
            self.copy_metadata(est.path, output)
            est.estimated_size = output.stat().st_size
            est.exact = True
            reason = self.judge_saving(est)
            if reason is None:
                new_info = self.probe(output)
                tolerance = max(0.5, info.duration * 0.01)
                if new_info is None or abs(new_info.duration - info.duration) > tolerance:
                    reason = "rejected: encoded duration does not match the original"
                elif full_quality:
                    est.quality = self.measure(output, est.path, info)
                    reason = self.judge_quality(est.quality)
        except ToolError as exc:
            return est.skip(f"encoding failed ({exc})", remember=False)
        except BaseException:
            est.discard()
            raise
        if reason:
            return est.skip(reason)
        return est

    # -- workflow ---------------------------------------------------------------------------
    def estimate(self, path: Path, st: os.stat_result, workdir: Path) -> Estimate:
        est = self.new_estimate(path, st)
        est.metric = self.metric
        try:
            info = self.probe(path)
        except ToolError as exc:
            return est.skip(f"cannot analyse video ({exc})")
        reason = self.precheck(path, st.st_size, info)
        if reason:
            return est.skip(reason)
        assert info is not None
        target = self.target_path(path)
        est.target = target if target != path else None
        cfg = self.config.video
        if info.duration <= cfg.sample_count * cfg.sample_seconds * 2:
            # Short video: a complete trial encode is cheap and gives an exact estimate.
            output = self.staging_file(workdir, target.suffix)
            return self._encode_and_check(est, info, output, full_quality=True)
        return self._estimate_from_samples(est, info, workdir)

    def _estimate_from_samples(self, est: Estimate, info: VideoInfo, workdir: Path) -> Estimate:
        path = est.path
        encoded = source = 0
        qualities: list[float] = []
        try:
            video_bytes = self.stream_bytes(path, info.video_index)
            for index, (start, length) in enumerate(self.sample_windows(info.duration)):
                if self.progress:
                    self.progress(index / self.config.video.sample_count)
                sample = self.staging_file(workdir, ".mkv")
                try:
                    self.tools.run(self.encode_args(path, info, sample, (start, length)))
                    encoded += self.stream_bytes(sample, 0)
                    absolute = (info.start_time + start, length)
                    source += self.stream_bytes(path, info.video_index, absolute)
                    qualities.append(self.measure(sample, path, info, (start, length)))
                finally:
                    sample.unlink(missing_ok=True)
        except ToolError as exc:
            return est.skip(f"estimation failed ({exc})", remember=False)
        if source <= 0 or encoded <= 0:
            return est.skip("estimation failed (could not measure the video stream)")
        ratio = encoded / source
        est.estimated_size = round(est.original_size - video_bytes + video_bytes * ratio)
        est.exact = False
        est.quality = min(qualities)
        reason = self.judge_saving(est) or self.judge_quality(est.quality)
        return est.skip(reason) if reason else est

    def materialize(self, est: Estimate, workdir: Path) -> Estimate:
        """Full encode next to the original (same filesystem), with complete verification."""
        path = est.path
        try:
            info = self.probe(path)
        except ToolError as exc:
            return est.skip(f"cannot analyse video ({exc})", remember=False)
        if info is None:
            return est.skip("no video stream")
        target = est.target or path
        needed = int((est.estimated_size or est.original_size) * 1.2) + 64 * _MB
        if free_space(path.parent) < needed:
            return est.skip("not enough free disk space for a safe re-encode", remember=False)
        output = target.with_name(f".{target.stem}{TEMP_MARK}{target.suffix}")
        full = self.config.video.verify == "full" or est.quality is None
        return self._encode_and_check(est, info, output, full_quality=full)
