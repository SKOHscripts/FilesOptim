"""Reading dates, cameras, keywords and music tags; writing dates and XMP keywords."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mutagen
from PIL import Image

from filesoptim.categories import category_of, extension
from filesoptim.fsutils import TEMP_MARK, install_file, stat_key
from filesoptim.tools import ToolError, Tools

MIN_YEAR = 1970
EXIFTOOL_BATCH = 200
# Formats exiftool can safely write dates/XMP into.
WRITABLE_EXTENSIONS = frozenset(
    "jpg jpeg jpe png tif tiff heic heif webp dng cr2 cr3 nef arw orf rw2 raf pef "
    "mp4 m4v mov 3gp 3g2".split()
)
IMAGE_DATE_TAGS = ("EXIF:DateTimeOriginal", "EXIF:CreateDate")
VIDEO_DATE_TAGS = (
    "QuickTime:CreateDate", "QuickTime:ModifyDate",
    "QuickTime:TrackCreateDate", "QuickTime:MediaCreateDate",
)

_DATETIME_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_. T]{0,3}|[-_ ]?at[-_ ]?)([01]\d|2[0-3])[-_.:h]?([0-5]\d)[-_.:m]?([0-5]\d)"
    r"\d{0,3}(?!\d)"
)
_DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"
)
_EPOCH_RE = re.compile(r"^(\d{10}|\d{13})$")


@dataclass
class MediaInfo:
    path: Path
    category: str
    date: datetime | None = None
    date_source: str = ""  # "metadata" | "filename" | "mtime" | ""
    camera: str = ""
    keywords: list[str] = field(default_factory=list)
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    title: str = ""
    genre: str = ""
    track: int | None = None


def plausible(value: datetime) -> bool:
    return MIN_YEAR <= value.year <= datetime.now().year + 1


def parse_tag_date(text: object) -> datetime | None:
    """Parse ``2019:06:12 10:15:30`` / ISO dates from metadata; zero dates give ``None``."""
    raw = str(text).strip()
    if len(raw) < 10:
        return None
    normalised = f"{raw[:4]}-{raw[5:7]}-{raw[8:10]}"
    if len(raw) >= 19:
        normalised += f" {raw[11:19]}"
    try:
        value = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    return value if plausible(value) else None


def parse_iso_utc(text: str) -> datetime | None:
    """ffprobe ``creation_time`` (UTC) -> naive local time."""
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone().replace(tzinfo=None)
    return local if plausible(local) else None


def date_from_filename(name: str) -> datetime | None:
    """Dates embedded by phones/cameras/apps: ``IMG_20190612_101530``, ``2019-06-12 10.15.30``,
    ``Screenshot 2023-05-14 at 15.30.00``, ``IMG-20230514-WA0001``, epoch stamps..."""
    stem = Path(name).stem
    for pattern in (_DATETIME_RE, _DATE_RE):
        for match in pattern.finditer(stem):
            parts = [int(g) for g in match.groups()]
            with contextlib.suppress(ValueError):
                hour, minute, second = [*parts[3:], 0, 0, 0][:3]
                value = datetime(parts[0], parts[1], parts[2], hour, minute, second)
                if plausible(value):
                    return value
    epoch = _EPOCH_RE.match(stem)
    if epoch:
        seconds = int(stem) / (1000 if len(stem) == 13 else 1)
        with contextlib.suppress(OverflowError, OSError, ValueError):
            value = datetime.fromtimestamp(seconds)
            if value.year >= 2000 and plausible(value):
                return value
    return None


def camera_name(make: object, model: object) -> str:
    make_text, model_text = str(make or "").strip(), str(model or "").strip()
    if not model_text:
        return make_text
    if make_text and not model_text.lower().startswith(make_text.split()[0].lower()):
        return f"{make_text} {model_text}"
    return model_text


def _as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


class MetadataReader:
    """Collects :class:`MediaInfo` using exiftool when present, Pillow/ffprobe otherwise."""

    def __init__(self, tools: Tools, *, use_mtime: bool = True) -> None:
        self.tools = tools
        self.use_mtime = use_mtime

    def read(self, paths: Sequence[Path]) -> dict[Path, MediaInfo]:
        infos = {p: MediaInfo(p, category_of(p)) for p in paths}
        media = [p for p in paths if infos[p].category in ("image", "video")]
        if self.tools.has("exiftool"):
            for start in range(0, len(media), EXIFTOOL_BATCH):
                self._read_exiftool(media[start:start + EXIFTOOL_BATCH], infos)
        else:
            for path in media:
                if infos[path].category == "image":
                    self._read_pillow(infos[path])
                elif self.tools.has("ffprobe"):
                    self._read_ffprobe(infos[path])
        for path in paths:
            info = infos[path]
            if info.category == "audio":
                self._read_audio(info)
            self._complete_date(info)
        return infos

    def _read_exiftool(self, paths: Sequence[Path], infos: dict[Path, MediaInfo]) -> None:
        args = ["exiftool", "-json", "-q", "-q", "-fast", "-charset", "filename=utf8",
                "-d", "%Y:%m:%d %H:%M:%S", "-api", "QuickTimeUTC",
                "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate", "-Make", "-Model",
                "-XMP-dc:Subject", *[str(p.absolute()) for p in paths]]
        try:
            records: list[dict[str, Any]] = json.loads(self.tools.run(args, check=False).stdout)
        except (ToolError, json.JSONDecodeError):
            return
        by_name = {str(p.absolute()): p for p in paths}
        for record in records:
            path = by_name.get(str(record.get("SourceFile", "")))
            if path is None:
                continue
            info = infos[path]
            for key in ("DateTimeOriginal", "CreateDate", "MediaCreateDate"):
                value = parse_tag_date(record.get(key, ""))
                if value is not None:
                    info.date, info.date_source = value, "metadata"
                    break
            info.camera = camera_name(record.get("Make"), record.get("Model"))
            info.keywords = _as_list(record.get("Subject"))

    def _read_pillow(self, info: MediaInfo) -> None:
        try:
            with Image.open(info.path) as image:
                exif = image.getexif()
        except (OSError, ValueError, SyntaxError):
            return
        value = parse_tag_date(exif.get_ifd(0x8769).get(36867) or exif.get(306) or "")
        if value is not None:
            info.date, info.date_source = value, "metadata"
        info.camera = camera_name(exif.get(271), exif.get(272))

    def _read_ffprobe(self, info: MediaInfo) -> None:
        try:
            proc = self.tools.run(["ffprobe", "-v", "error", "-show_entries",
                                   "format_tags=creation_time", "-of", "json", str(info.path)])
            tags = json.loads(proc.stdout).get("format", {}).get("tags", {})
        except (ToolError, json.JSONDecodeError, AttributeError):
            return
        value = parse_iso_utc(str(tags.get("creation_time", "")))
        if value is not None:
            info.date, info.date_source = value, "metadata"

    @staticmethod
    def _read_audio(info: MediaInfo) -> None:
        try:
            audio = mutagen.File(info.path, easy=True)
        except Exception:  # mutagen raises many different errors on damaged files
            return
        tags = getattr(audio, "tags", None)
        if not tags:
            return

        def first(key: str) -> str:
            try:
                values = tags.get(key) or []
            except (KeyError, ValueError):
                values = []
            return str(values[0]).strip() if values else ""

        info.artist = first("artist")
        info.album_artist = first("albumartist")
        info.album = first("album")
        info.title = first("title")
        info.genre = first("genre")
        track = first("tracknumber").split("/")[0].strip()
        info.track = int(track) if track.isdigit() else None
        year = first("date")[:4]
        if year.isdigit() and info.date is None:
            value = datetime(int(year), 1, 1)
            if plausible(value):
                info.date, info.date_source = value, "metadata"

    def _complete_date(self, info: MediaInfo) -> None:
        if info.date is not None:
            return
        value = date_from_filename(info.path.name)
        if value is not None:
            info.date, info.date_source = value, "filename"
        elif self.use_mtime:
            with contextlib.suppress(OSError):
                info.date = datetime.fromtimestamp(info.path.stat().st_mtime)
                info.date_source = "mtime"


def can_write_metadata(path: Path) -> bool:
    return extension(path) in WRITABLE_EXTENSIONS


def _date_args(category: str, stamp: str) -> list[str]:
    tags = VIDEO_DATE_TAGS if category == "video" else IMAGE_DATE_TAGS
    return [f"-{tag}={stamp}" for tag in tags]


def _rewrite(tools: Tools, path: Path, edits: list[str]) -> None:
    """Apply exiftool ``edits`` without ever rewriting ``path`` in place.

    exiftool writes a complete new file next to it (``-o``); that file is flushed to the disk
    and swapped in atomically, and only if the original did not change in the meantime.
    """
    before = stat_key(path.stat())
    output = path.with_name(f".{path.stem}{TEMP_MARK}{path.suffix}")
    output.unlink(missing_ok=True)  # leftover of an interrupted run
    try:
        tools.run(["exiftool", "-q", "-q", "-api", "QuickTimeUTC", *edits,
                   "-o", str(output), str(path)])
        if not output.is_file():
            raise ToolError(f"exiftool did not write {path.name}")
        install_file(output, path, expected=before)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def write_metadata(
    tools: Tools, path: Path, category: str, set_date: datetime | None, keywords: Sequence[str]
) -> None:
    """Write missing dates and XMP keywords (lossless: pixels are not touched)."""
    edits: list[str] = []
    if set_date is not None:
        edits += _date_args(category, set_date.strftime("%Y:%m:%d %H:%M:%S"))
    for keyword in keywords:  # remove-then-add never duplicates a keyword
        edits += [f"-XMP-dc:Subject-={keyword}", f"-XMP-dc:Subject+={keyword}"]
    _rewrite(tools, path, edits)


def revert_metadata(
    tools: Tools, path: Path, category: str, had_date: bool, keywords: Sequence[str]
) -> None:
    """Undo :func:`write_metadata`: clear the dates we added and remove our keywords."""
    edits: list[str] = []
    if had_date:
        edits += _date_args(category, "")
    for keyword in keywords:
        edits.append(f"-XMP-dc:Subject-={keyword}")
    _rewrite(tools, path, edits)
