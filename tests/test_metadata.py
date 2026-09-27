from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from filesoptim.fsutils import TEMP_MARK, FileChangedError
from filesoptim.sorting import metadata
from filesoptim.sorting.metadata import (
    MediaInfo,
    MetadataReader,
    _as_list,
    camera_name,
    can_write_metadata,
    date_from_filename,
    parse_iso_utc,
    parse_tag_date,
    plausible,
    revert_metadata,
    write_metadata,
)
from filesoptim.tools import ToolError
from tests.conftest import FakeTools, age, make_image


@pytest.fixture
def utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def exif_jpeg(path: Path, *, original: str | None = None, base: str | None = None,
              make: str = "Canon", model: str = "Canon EOS 80D") -> Path:
    exif = Image.Exif()
    exif[271], exif[272] = make, model
    if base:
        exif[306] = base
    if original:
        exif.get_ifd(0x8769)[36867] = original
    Image.new("RGB", (8, 8)).save(path, exif=exif)
    return path


# -- parsing helpers ----------------------------------------------------------------------
def test_parse_tag_date() -> None:
    assert parse_tag_date("2019:06:12 10:15:30") == datetime(2019, 6, 12, 10, 15, 30)
    assert parse_tag_date("2019-06-12T10:15:30+02:00") == datetime(2019, 6, 12, 10, 15, 30)
    assert parse_tag_date("2019:06:12") == datetime(2019, 6, 12)
    assert parse_tag_date("") is None
    assert parse_tag_date("0000:00:00 00:00:00") is None
    assert parse_tag_date("1900:01:01 00:00:00") is None
    assert parse_tag_date("not a date at all") is None
    assert plausible(datetime(2000, 1, 1)) and not plausible(datetime(1969, 12, 31))


def test_parse_iso_utc(utc: None) -> None:
    assert parse_iso_utc("2021-01-01T12:00:00.000000Z") == datetime(2021, 1, 1, 12)
    assert parse_iso_utc("2021-01-01 12:00:00") == datetime(2021, 1, 1, 12)
    assert parse_iso_utc("garbage") is None
    assert parse_iso_utc("1800-01-01T00:00:00Z") is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("IMG_20190612_101530.jpg", datetime(2019, 6, 12, 10, 15, 30)),
        ("2019-06-12 10.15.30.jpg", datetime(2019, 6, 12, 10, 15, 30)),
        ("Screenshot 2023-05-14 at 15.30.00.png", datetime(2023, 5, 14, 15, 30)),
        ("PXL_20230514_153000123.jpg", datetime(2023, 5, 14, 15, 30)),
        ("VID20230514153000.mp4", datetime(2023, 5, 14, 15, 30)),
        ("IMG-20230514-WA0001.jpg", datetime(2023, 5, 14)),
        ("scan 2001.02.03.pdf", datetime(2001, 2, 3)),
        ("20230230_101010.jpg", None),
        ("19000101_101010.jpg", None),
        ("random-name.jpg", None),
        ("0100000000.jpg", None),
        ("9999999999999.jpg", None),
    ],
)
def test_date_from_filename(name: str, expected: datetime | None) -> None:
    assert date_from_filename(name) == expected


def test_epoch_names() -> None:
    assert date_from_filename("1684071000.jpg") == datetime.fromtimestamp(1684071000)
    assert date_from_filename("1684071000000.jpg") == datetime.fromtimestamp(1684071000)


@pytest.mark.parametrize(
    ("make", "model", "name"),
    [("Canon", "Canon EOS 80D", "Canon EOS 80D"), ("Apple", "iPhone 12", "Apple iPhone 12"),
     ("NIKON CORPORATION", "NIKON D750", "NIKON D750"), ("", "", ""), ("Sony", None, "Sony"),
     (None, "X100", "X100")],
)
def test_camera_name(make: Any, model: Any, name: str) -> None:
    assert camera_name(make, model) == name


def test_as_list() -> None:
    assert _as_list(None) == [] and _as_list("") == []
    assert _as_list("a") == ["a"] and _as_list(["a", 1]) == ["a", "1"]


# -- exiftool reader ----------------------------------------------------------------------
def test_read_with_exiftool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = make_image(tmp_path / "photo.jpg", fmt="JPEG")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v")
    plain = tmp_path / "IMG_20200101_120000.jpg"
    plain.write_bytes(b"x")
    records = {
        str(photo): {"SourceFile": str(photo), "DateTimeOriginal": "0000:00:00 00:00:00",
                     "CreateDate": "2018:05:06 07:08:09", "Make": "Apple",
                     "Model": "iPhone 12", "Subject": ["Family", "Trip"]},
        str(clip): {"SourceFile": str(clip), "MediaCreateDate": "2017:01:02 03:04:05",
                    "Subject": "Holidays"},
        str(plain): {"SourceFile": str(plain)},
    }

    def exiftool(argv: list[str]) -> tuple[int, str, str]:
        files = [a for a in argv if a.startswith("/")]
        return 0, json.dumps([records[f] for f in files] + [{"SourceFile": "/other"}]), ""

    monkeypatch.setattr(metadata, "EXIFTOOL_BATCH", 2)
    tools = FakeTools(["exiftool"], exiftool=exiftool)
    infos = MetadataReader(tools).read([photo, clip, plain])
    assert len(tools.calls) == 2  # two batches
    assert infos[photo].date == datetime(2018, 5, 6, 7, 8, 9)
    assert infos[photo].date_source == "metadata"
    assert infos[photo].camera == "Apple iPhone 12"
    assert infos[photo].keywords == ["Family", "Trip"]
    assert infos[clip].date == datetime(2017, 1, 2, 3, 4, 5)
    assert infos[clip].keywords == ["Holidays"]
    assert infos[plain].date == datetime(2020, 1, 1, 12) and infos[plain].date_source == "filename"


def test_exiftool_garbage_output(tmp_path: Path) -> None:
    photo = age(make_image(tmp_path / "p.jpg", fmt="JPEG"), 86400)
    tools = FakeTools(["exiftool"], exiftool=lambda argv: (1, "Error: boom", ""))
    info = MetadataReader(tools).read([photo])[photo]
    assert info.date_source == "mtime"
    assert info.date == datetime.fromtimestamp(photo.stat().st_mtime)


# -- fallbacks ----------------------------------------------------------------------------
def test_pillow_fallback(tmp_path: Path) -> None:
    original = exif_jpeg(tmp_path / "a.jpg", original="2019:06:12 10:15:30",
                         base="2020:01:01 00:00:00")
    base_only = exif_jpeg(tmp_path / "b.jpg", base="2020:01:02 03:04:05", make="",
                          model="X100")
    no_exif = age(make_image(tmp_path / "c.jpg", fmt="JPEG"))
    broken = tmp_path / "IMG_20210101_000000.jpg"
    broken.write_bytes(b"not an image")
    infos = MetadataReader(FakeTools(), use_mtime=False).read([original, base_only, no_exif,
                                                               broken])
    assert infos[original].date == datetime(2019, 6, 12, 10, 15, 30)
    assert infos[original].camera == "Canon EOS 80D"
    assert infos[base_only].date == datetime(2020, 1, 2, 3, 4, 5)
    assert infos[base_only].camera == "X100"
    assert infos[no_exif].date is None and infos[no_exif].date_source == ""
    assert infos[broken].date == datetime(2021, 1, 1) and infos[broken].date_source == "filename"


def test_ffprobe_fallback(tmp_path: Path, utc: None) -> None:
    clips = {}
    outputs = {
        "ok.mp4": (0, json.dumps({"format": {"tags": {"creation_time": "2016-02-03T04:05:06Z"}}})),
        "notags.mp4": (0, json.dumps({"format": {}})),
        "list.mp4": (0, "[]"),
        "bad.mp4": (0, "{"),
        "fail.mp4": (1, ""),
    }
    for name in outputs:
        clips[name] = tmp_path / name
        clips[name].write_bytes(b"v")

    def ffprobe(argv: list[str]) -> tuple[int, str, str]:
        code, text = outputs[Path(argv[-1]).name]
        return code, text, "error"

    reader = MetadataReader(FakeTools(["ffprobe"], ffprobe=ffprobe), use_mtime=False)
    infos = reader.read(list(clips.values()))
    assert infos[clips["ok.mp4"]].date == datetime(2016, 2, 3, 4, 5, 6)
    assert all(infos[clips[n]].date is None for n in outputs if n != "ok.mp4")
    no_tool = MetadataReader(FakeTools(), use_mtime=False).read([clips["ok.mp4"]])
    assert no_tool[clips["ok.mp4"]].date is None


# -- audio --------------------------------------------------------------------------------
class FakeTags(dict[str, list[str]]):
    def get(self, key: str, default: Any = None) -> Any:
        if key == "genre":
            raise ValueError("unsupported")
        return super().get(key, default)


class FakeAudio:
    def __init__(self, tags: Any) -> None:
        self.tags = tags


def test_audio_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    songs = {name: tmp_path / name for name in
             ("full.mp3", "error.mp3", "none.mp3", "empty.mp3", "odd.mp3", "nodate.mp3")}
    for path in songs.values():
        path.write_bytes(b"a")
    tags = {
        "full.mp3": FakeTags(artist=["Daft Punk"], albumartist=["Daft Punk"],
                             album=["Discovery"], title=["One More Time"],
                             tracknumber=["1/14"], date=["2001-03-07"]),
        "odd.mp3": FakeTags(tracknumber=["A1"], date=["1800"]),
        "empty.mp3": FakeTags(),
        "nodate.mp3": FakeTags(artist=["Solo"]),
    }

    def fake_file(path: Path, easy: bool = False) -> Any:
        name = Path(path).name
        if name == "error.mp3":
            raise RuntimeError("damaged")
        if name == "none.mp3":
            return None
        return FakeAudio(tags[name])

    monkeypatch.setattr(metadata.mutagen, "File", fake_file)
    infos = MetadataReader(FakeTools(), use_mtime=False).read(list(songs.values()))
    full = infos[songs["full.mp3"]]
    assert (full.artist, full.album_artist, full.album, full.title, full.track) == (
        "Daft Punk", "Daft Punk", "Discovery", "One More Time", 1)
    assert full.genre == "" and full.date == datetime(2001, 1, 1)
    odd = infos[songs["odd.mp3"]]
    assert odd.track is None and odd.date is None
    assert infos[songs["nodate.mp3"]].artist == "Solo"
    assert infos[songs["nodate.mp3"]].date is None
    for name in ("error.mp3", "none.mp3", "empty.mp3"):
        assert infos[songs[name]].artist == ""


def test_complete_date_edge_cases(tmp_path: Path) -> None:
    reader = MetadataReader(FakeTools())
    ghost = MediaInfo(tmp_path / "ghost.txt", "other")
    reader._complete_date(ghost)  # stat fails: stays undated
    assert ghost.date is None
    dated = MediaInfo(tmp_path / "x", "other", date=datetime(2000, 1, 1), date_source="metadata")
    reader._complete_date(dated)
    assert dated.date_source == "metadata"


# -- writing ------------------------------------------------------------------------------
def test_write_and_revert_arguments(tmp_path: Path) -> None:
    tmp_path = tmp_path / "work"
    tmp_path.mkdir()
    tools = FakeTools(["exiftool"])
    photo, clip = tmp_path / "a.jpg", tmp_path / "b.mp4"
    photo.write_bytes(b"photo")
    clip.write_bytes(b"clip")
    os.utime(photo, (1000, 1000))
    write_metadata(tools, photo, "image", datetime(2019, 6, 12, 10, 15, 30), ["Trip"])
    out = str(tmp_path / f".a{TEMP_MARK}.jpg")
    assert tools.calls[-1] == [
        "exiftool", "-q", "-q", "-api", "QuickTimeUTC",
        "-EXIF:DateTimeOriginal=2019:06:12 10:15:30", "-EXIF:CreateDate=2019:06:12 10:15:30",
        "-XMP-dc:Subject-=Trip", "-XMP-dc:Subject+=Trip", "-o", out, str(photo),
    ]
    assert photo.stat().st_mtime == 1000  # timestamps kept
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "b.mp4"]  # no temp left
    write_metadata(tools, clip, "video", datetime(2019, 6, 12), [])
    assert "-QuickTime:MediaCreateDate=2019:06:12 00:00:00" in tools.calls[-1]
    write_metadata(tools, photo, "image", None, ["Only"])
    assert tools.calls[-1][5:7] == ["-XMP-dc:Subject-=Only", "-XMP-dc:Subject+=Only"]
    revert_metadata(tools, clip, "video", True, ["Trip"])
    assert "-QuickTime:CreateDate=" in tools.calls[-1]
    assert "-XMP-dc:Subject-=Trip" in tools.calls[-1]
    revert_metadata(tools, photo, "image", False, [])
    assert tools.calls[-1][5:] == ["-o", out, str(photo)]


def test_metadata_writes_never_damage_the_original(tmp_path: Path) -> None:
    tmp_path = tmp_path / "work"
    tmp_path.mkdir()
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"precious")
    # exiftool fails: the original is untouched and no temporary file remains
    failing = FakeTools(["exiftool"], exiftool=lambda argv: (1, "", "Error: bad file"))
    with pytest.raises(ToolError):
        write_metadata(failing, photo, "image", None, ["x"])
    # exiftool "succeeds" without writing anything
    silent = FakeTools(["exiftool"], exiftool=lambda argv: (0, "", ""))
    with pytest.raises(ToolError, match="did not write"):
        write_metadata(silent, photo, "image", None, ["x"])

    # the photo is edited by someone else while exiftool works: their edit wins
    def concurrent_edit(argv: list[str]) -> tuple[int, str, str]:
        Path(argv[argv.index("-o") + 1]).write_bytes(b"tagged")
        photo.write_bytes(b"edited by the user meanwhile")
        return 0, "", ""

    with pytest.raises(FileChangedError):
        write_metadata(FakeTools(["exiftool"], exiftool=concurrent_edit), photo, "image", None,
                       ["x"])
    assert photo.read_bytes() == b"edited by the user meanwhile"
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]
    (tmp_path / f".a{TEMP_MARK}.jpg").write_bytes(b"leftover")  # from a killed run
    write_metadata(FakeTools(["exiftool"]), photo, "image", None, ["x"])
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]
    assert can_write_metadata(Path("x.HEIC")) and not can_write_metadata(Path("x.avi"))
