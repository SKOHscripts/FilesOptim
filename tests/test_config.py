from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from filesoptim import config as cfgmod
from filesoptim.config import (
    DEFAULT_CONFIG_TOML,
    Config,
    ConfigError,
    default_config_path,
    dump_config,
    load_config,
    validate,
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_xdg_directories(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert default_config_path() == isolated_env / ".config/filesoptim/config.toml"
    assert cfgmod.state_dir() == isolated_env / ".local/state/filesoptim"
    assert cfgmod.work_cache_dir() == isolated_env / ".cache/filesoptim"
    assert cfgmod.data_home() == isolated_env / ".local/share"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")  # ignored: must be absolute
    assert cfgmod.cache_home() == isolated_env / ".cache"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert cfgmod.state_home() == isolated_env / ".local/state"


def test_default_file_matches_defaults(tmp_path: Path) -> None:
    loaded = load_config(write(tmp_path, DEFAULT_CONFIG_TOML))
    assert asdict(loaded) == asdict(Config())


def test_dump_round_trip(tmp_path: Path) -> None:
    original = Config()
    original.video.codec = "av1"
    original.sort.templates["image"] = 'Pics/{year} "quoted"'
    original.sort.tags.extra = ["été", "famille"]
    loaded = load_config(write(tmp_path, dump_config(original)))
    assert asdict(loaded) == asdict(original)


def test_missing_default_file_gives_defaults() -> None:
    assert asdict(load_config()) == asdict(Config())


def test_missing_explicit_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_partial_override_and_coercion(tmp_path: Path) -> None:
    path = write(tmp_path, """
[optimize]
min_saving_percent = 5
jobs = 3
[video]
min_ssim = 0.99
[sort.templates]
image = "Images/{year}"
[sort.tags]
extra = ["a"]
""")
    loaded = load_config(path)
    assert loaded.optimize.min_saving_percent == 5.0
    assert isinstance(loaded.optimize.min_saving_percent, float)
    assert loaded.optimize.jobs == 3
    assert loaded.video.min_ssim == 0.99
    assert loaded.sort.templates["image"] == "Images/{year}"
    assert loaded.sort.templates["video"] == "Videos/{year}/{month}"
    assert loaded.sort.tags.extra == ["a"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[nope]\na = 1", "unknown option 'nope'"),
        ("[general]\nskip_hidden = 1", "invalid value for 'general.skip_hidden'"),
        ("[general]\nmin_age_seconds = true", "invalid value"),
        ("[general]\nmin_age_seconds = 1.5", "invalid value"),
        ("[video]\nmin_ssim = true", "invalid value"),
        ("[video]\ncodec = 3", "invalid value"),
        ("[general]\nexclude = [1]", "invalid value"),
        ("[general]\nexclude = 'x'", "invalid value"),
        ("sort = 1", "'sort' must be a table"),
        ("[sort]\ntemplates = 1", "must be a table of strings"),
        ("[sort.templates]\nimage = 1", "must be a table of strings"),
        ("[video]\ncodec = 'h264'", "'video.codec' must be one of"),
        ("[optimize]\ntypes = ['jpeg', 'bmp']", "unknown optimisation type"),
        ("[sort.templates]\nmovies = 'x'", "unknown sort category"),
        ("[video]\nmin_ssim = 1.5", "min_ssim"),
        ("[video]\nmin_vmaf = 0", "min_vmaf"),
        ("[video]\ncrf = 70", "crf"),
        ("[video]\nsample_count = 0", "sample_count"),
        ("[optimize]\nmin_saving_percent = 100", "min_saving_percent"),
        ("[video]\nmin_saving_percent = -1", "min_saving_percent"),
        ("[optimize]\nkeep_originals = 'backup'", "backup_dir"),
        ("not toml [", "config.toml"),
    ],
)
def test_invalid_configs(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text))


def test_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError):
        load_config(path)


def test_validate_returns_config() -> None:
    config = Config()
    assert validate(config) is config


def test_tomllib_is_used() -> None:
    assert cfgmod.tomllib is (sys.modules.get("tomllib") or sys.modules.get("tomli"))
