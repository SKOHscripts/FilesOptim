"""Configuration: defaults, TOML loading and XDG locations."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):  # pragma: no cover - depends on the Python version
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


class ConfigError(ValueError):
    """Raised for invalid configuration files or values."""


# --------------------------------------------------------------------------------------------
# XDG base directories
# --------------------------------------------------------------------------------------------
def _xdg(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def cache_home() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def default_config_path() -> Path:
    return config_home() / "filesoptim" / "config.toml"


def state_dir() -> Path:
    return state_home() / "filesoptim"


def work_cache_dir() -> Path:
    return cache_home() / "filesoptim"


# --------------------------------------------------------------------------------------------
# Configuration sections
# --------------------------------------------------------------------------------------------
@dataclass
class GeneralConfig:
    exclude: list[str] = field(
        default_factory=lambda: [
            ".git", ".hg", ".svn", "node_modules", "__pycache__",
            ".venv", "venv", ".tox", "lost+found",
        ]
    )
    skip_hidden: bool = True
    one_file_system: bool = True
    min_age_seconds: int = 60


@dataclass
class OptimizeConfig:
    types: list[str] = field(default_factory=lambda: ["jpeg", "png", "gif", "pdf", "video"])
    min_saving_percent: float = 1.0
    min_saving_bytes: int = 2048
    jobs: int = 0
    keep_originals: str = "auto"
    backup_dir: str = ""
    staging_limit_mb: int = 1024
    min_free_mb: int = 2048
    jpeg_progressive: bool = False


@dataclass
class VideoConfig:
    codec: str = "hevc"
    crf: int = 0
    preset: str = ""
    metric: str = "ssim"
    min_ssim: float = 0.98
    min_vmaf: float = 95.0
    min_saving_percent: float = 20.0
    verify: str = "full"
    skip_codecs: list[str] = field(default_factory=lambda: ["hevc", "av1", "vp9", "vvc"])
    min_bits_per_pixel: float = 0.03
    min_size_mb: int = 5
    sample_count: int = 5
    sample_seconds: int = 6
    skip_hdr: bool = True
    data_streams: str = "drop"
    copy_metadata: bool = True


@dataclass
class PdfConfig:
    mode: str = "lossless"
    min_saving_percent_lossy: float = 10.0
    skip_signed: bool = True


@dataclass
class TagConfig:
    category: bool = True
    camera: bool = True
    folder: bool = True
    extra: list[str] = field(default_factory=list)
    generic_folders: list[str] = field(
        default_factory=lambda: [
            "dcim", "camera", "camera roll", "images", "pictures", "photos", "videos",
            "download", "downloads", "desktop", "tmp", "temp", "new folder", "nouveau dossier",
            "téléchargements", "bureau", "screenshots", "whatsapp images", "whatsapp video",
        ]
    )


@dataclass
class SortConfig:
    mode: str = "move"
    templates: dict[str, str] = field(
        default_factory=lambda: {
            "image": "Photos/{year}/{month}",
            "video": "Videos/{year}/{month}",
            "audio": "Music/{artist}/{album}",
            "document": "Documents/{ext}",
            "archive": "Archives",
            "other": "Other",
        }
    )
    rename: dict[str, str] = field(
        default_factory=lambda: {
            "image": "{date:%Y-%m-%d_%H-%M-%S}",
            "video": "{date:%Y-%m-%d_%H-%M-%S}",
            "audio": "{track:02d} - {title}",
        }
    )
    unknown: str = "Unknown"
    undated: str = "Undated"
    use_mtime: bool = True
    lowercase_extension: bool = True
    sidecar_extensions: list[str] = field(default_factory=lambda: ["xmp", "aae", "thm"])
    tags: TagConfig = field(default_factory=TagConfig)


@dataclass
class CleanConfig:
    cache_max_age_days: int = 90
    trash_max_age_days: int = 30
    thumbnail_max_age_days: int = 0
    journal_max_age_days: int = 30
    coredump_max_age_days: int = 7
    pacman_keep: int = 2


@dataclass
class ScanConfig:
    protected: list[str] = field(default_factory=list)


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    pdf: PdfConfig = field(default_factory=PdfConfig)
    sort: SortConfig = field(default_factory=SortConfig)
    clean: CleanConfig = field(default_factory=CleanConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)


CHOICES: dict[str, tuple[str, ...]] = {
    "optimize.keep_originals": ("auto", "never", "trash", "backup"),
    "video.codec": ("hevc", "av1"),
    "video.metric": ("ssim", "vmaf"),
    "video.verify": ("full", "sample"),
    "video.data_streams": ("drop", "keep"),
    "pdf.mode": ("lossless", "printer", "ebook", "prepress", "screen", "default"),
    "sort.mode": ("move", "copy"),
}
OPTIMIZE_TYPES = ("jpeg", "png", "gif", "pdf", "video")
SORT_CATEGORIES = ("image", "video", "audio", "document", "archive", "other")


# --------------------------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------------------------
def _coerce(value: Any, current: Any, name: str) -> Any:
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
    elif isinstance(current, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif isinstance(current, float):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif isinstance(current, str):
        if isinstance(value, str):
            return value
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"invalid value for '{name}': {value!r}")


def _merge(target: Any, data: dict[str, Any], prefix: str) -> None:
    known = {f.name for f in fields(target)}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if key not in known:
            raise ConfigError(f"unknown option '{name}'")
        current = getattr(target, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"'{name}' must be a table")
            _merge(current, value, f"{name}.")
        elif isinstance(current, dict):
            if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
                raise ConfigError(f"'{name}' must be a table of strings")
            current.update(value)
        else:
            setattr(target, key, _coerce(value, current, name))


def validate(config: Config) -> Config:
    """Check enumerations and ranges; returns the config for chaining."""
    for dotted, allowed in CHOICES.items():
        section, key = dotted.split(".")
        value = getattr(getattr(config, section), key)
        if value not in allowed:
            raise ConfigError(f"'{dotted}' must be one of {', '.join(allowed)} (got {value!r})")
    unknown_types = set(config.optimize.types) - set(OPTIMIZE_TYPES)
    if unknown_types:
        raise ConfigError(f"unknown optimisation type(s): {', '.join(sorted(unknown_types))}")
    unknown_cats = (set(config.sort.templates) | set(config.sort.rename)) - set(SORT_CATEGORIES)
    if unknown_cats:
        raise ConfigError(f"unknown sort category(ies): {', '.join(sorted(unknown_cats))}")
    if not 0 < config.video.min_ssim <= 1:
        raise ConfigError("'video.min_ssim' must be in ]0, 1]")
    if not 0 < config.video.min_vmaf <= 100:
        raise ConfigError("'video.min_vmaf' must be in ]0, 100]")
    if not 0 <= config.video.crf <= 63:
        raise ConfigError("'video.crf' must be between 0 (codec default) and 63")
    if config.video.sample_count < 1 or config.video.sample_seconds < 1:
        raise ConfigError("'video.sample_count' and 'video.sample_seconds' must be >= 1")
    for part in (config.optimize, config.video):
        if not 0 <= part.min_saving_percent < 100:
            raise ConfigError("'min_saving_percent' must be in [0, 100[")
    if config.optimize.min_free_mb < 0 or config.optimize.staging_limit_mb < 0:
        raise ConfigError("'min_free_mb' and 'staging_limit_mb' must be >= 0")
    if config.optimize.keep_originals == "backup" and not config.optimize.backup_dir:
        raise ConfigError("'optimize.keep_originals = \"backup\"' needs 'optimize.backup_dir'")
    return config


def load_config(path: Path | None = None) -> Config:
    """Load the configuration file (``path`` or the XDG default) over the defaults."""
    config = Config()
    explicit = path is not None
    path = path or default_config_path()
    if not path.exists():
        if explicit:
            raise ConfigError(f"configuration file not found: {path}")
        return config
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    _merge(config, data, "")
    return validate(config)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return "[" + ", ".join(_toml_value(v) for v in value) + "]"


def dump_config(config: Config) -> str:
    """Effective configuration as TOML (round-trips through :func:`load_config`)."""
    lines: list[str] = []

    def section(obj: Any, name: str) -> None:
        tables: list[tuple[str, Any]] = []
        lines.append(f"[{name}]")
        for item in fields(obj):
            value = getattr(obj, item.name)
            if is_dataclass(value) or isinstance(value, dict):
                tables.append((item.name, value))
            else:
                lines.append(f"{item.name} = {_toml_value(value)}")
        lines.append("")
        for key, value in tables:
            if isinstance(value, dict):
                lines.append(f"[{name}.{key}]")
                lines.extend(f"{k} = {_toml_value(v)}" for k, v in value.items())
                lines.append("")
            else:
                section(value, f"{name}.{key}")

    for item in fields(config):
        section(getattr(config, item.name), item.name)
    return "\n".join(lines)


DEFAULT_CONFIG_TOML = """\
# FilesOptim configuration (all values below are the defaults).
# Location: $XDG_CONFIG_HOME/filesoptim/config.toml (usually ~/.config/filesoptim/config.toml)

[general]
# Directory/file names (glob patterns) never visited.
exclude = [".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", "lost+found"]
skip_hidden = true          # ignore files and folders starting with "."
one_file_system = true      # do not cross into other mounted filesystems
min_age_seconds = 60        # ignore files modified very recently (may still be written)

[optimize]
types = ["jpeg", "png", "gif", "pdf", "video"]
min_saving_percent = 1.0    # lossless results smaller than this gain are ignored...
min_saving_bytes = 2048     # ...as are gains smaller than this many bytes
jobs = 0                    # parallel workers for images/PDF (0 = number of CPUs)
keep_originals = "auto"     # auto (trash for lossy types) | never | trash | backup
backup_dir = ""             # used when keep_originals = "backup"
staging_limit_mb = 1024     # disk budget to keep estimation results for instant apply
min_free_mb = 2048          # never let a disk used by FilesOptim go below this free space
jpeg_progressive = false    # lossless conversion to progressive JPEG (usually smaller)

[video]
codec = "hevc"              # hevc (libx265) | av1 (libsvtav1 / libaom-av1)
crf = 0                     # 0 = codec default (hevc 22, av1 30); lower = better quality
preset = ""                 # "" = codec default (hevc "medium", av1 "6")
metric = "ssim"             # ssim | vmaf (vmaf needs ffmpeg built with libvmaf)
min_ssim = 0.98             # quality floor: the encode is rejected below it
min_vmaf = 95.0
min_saving_percent = 20.0   # re-encoding must save at least this much
verify = "full"             # full: measure quality on the whole file | sample: trust samples
skip_codecs = ["hevc", "av1", "vp9", "vvc"]  # already efficient: never re-encoded
min_bits_per_pixel = 0.03   # below this the video is already heavily compressed
min_size_mb = 5
sample_count = 5            # estimation samples spread over the video
sample_seconds = 6
skip_hdr = true             # HDR videos are left untouched
data_streams = "drop"       # drop | keep (e.g. GoPro telemetry; may fail with some muxers)
copy_metadata = true        # copy all tags (GPS, camera...) with exiftool for MP4/MOV

[pdf]
mode = "lossless"           # lossless (qpdf) | printer | ebook | prepress | screen | default (ghostscript, lossy)
min_saving_percent_lossy = 10.0
skip_signed = true          # never touch digitally signed PDFs

[sort]
mode = "move"               # move | copy
unknown = "Unknown"         # value for a missing tag (artist, album...)
undated = "Undated"         # folder used when no reliable date is found
use_mtime = true            # fall back to the file modification time for the date
lowercase_extension = true
sidecar_extensions = ["xmp", "aae", "thm"]

[sort.templates]            # destination folder per category ("" = leave in place)
image = "Photos/{year}/{month}"
video = "Videos/{year}/{month}"
audio = "Music/{artist}/{album}"
document = "Documents/{ext}"
archive = "Archives"
other = "Other"

[sort.rename]               # new file name (without extension) when --rename is used
image = "{date:%Y-%m-%d_%H-%M-%S}"
video = "{date:%Y-%m-%d_%H-%M-%S}"
audio = "{track:02d} - {title}"

[sort.tags]                 # XMP keywords written with --tag
category = true
camera = true
folder = true
extra = []
generic_folders = ["dcim", "camera", "camera roll", "images", "pictures", "photos", "videos", "download", "downloads", "desktop", "tmp", "temp", "new folder", "nouveau dossier", "téléchargements", "bureau", "screenshots", "whatsapp images", "whatsapp video"]

[clean]
cache_max_age_days = 90     # ~/.cache files unused for this long
trash_max_age_days = 30     # trash items deleted more than this many days ago
thumbnail_max_age_days = 0
journal_max_age_days = 30   # archived systemd journal files
coredump_max_age_days = 7
pacman_keep = 2             # package versions kept in the pacman cache

[scan]
protected = []              # folders never reported/removed as empty
"""
