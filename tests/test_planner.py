from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from filesoptim.categories import category_of
from filesoptim.config import Config, ConfigError
from filesoptim.sorting.metadata import MediaInfo
from filesoptim.sorting.planner import (
    Planner,
    SortOp,
    SortOptions,
    SortPlan,
    describe_op,
    export_plan,
    preview,
)
from tests.conftest import FakeTools, Out

DATE = datetime(2019, 6, 12, 10, 15, 30)


class FakeReader:
    def __init__(self, **by_name: dict[str, Any]) -> None:
        self.by_name = by_name

    def read(self, paths: Sequence[Path]) -> dict[Path, MediaInfo]:
        infos = {}
        for path in paths:
            fields = {"date": DATE, "date_source": "metadata", **self.by_name.get(path.name, {})}
            infos[path] = MediaInfo(path, category_of(path), **fields)
        return infos


def touch(path: Path, content: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else path.name.encode())
    return path


def planner(config: Config, *, exiftool: bool = True, **infos: dict[str, Any]) -> Planner:
    return Planner(config, FakeTools(["exiftool"] if exiftool else []), FakeReader(**infos))


def dests(plan: SortPlan, root: Path) -> dict[str, str]:
    return {str(op.source.relative_to(root)): str(op.destination.relative_to(root))
            for op in plan.ops}


def test_default_reader_is_created(config: Config) -> None:
    assert Planner(config, FakeTools()).reader.use_mtime is True


def test_basic_plan(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "a" / "photo.JPG")
    touch(src / "clip.mp4")
    touch(src / "song.mp3")
    touch(src / "doc.PDF")
    touch(src / "archive.zip")
    touch(src / "thing.xyz")
    touch(src / ".hidden.jpg")
    plan = planner(config, **{"song.mp3": {"artist": "A", "album_artist": "", "album": "B"}}).plan(
        SortOptions(src, src))
    assert dests(plan, src) == {
        "a/photo.JPG": "Photos/2019/06/photo.jpg",
        "clip.mp4": "Videos/2019/06/clip.mp4",
        "song.mp3": "Music/A/B/song.mp3",
        "doc.PDF": "Documents/pdf/doc.pdf",
        "archive.zip": "Archives/archive.zip",
        "thing.xyz": "Other/thing.xyz",
    }
    assert {op.action for op in plan.ops} == {"move"}
    assert len(plan.moves) == 6


def test_destination_inside_source_and_flat_mode(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "new.jpg")
    touch(src / "sub" / "deep.jpg")
    touch(src / "sorted" / "Photos" / "old.jpg")
    (src / "link.jpg").symlink_to(src / "new.jpg")
    touch(src / ".secret.jpg")
    recursive = planner(config).plan(SortOptions(src, src / "sorted"))
    assert sorted(op.source.name for op in recursive.ops) == ["deep.jpg", "new.jpg"]
    flat = planner(config).plan(SortOptions(src, src / "sorted", recursive=False))
    assert [op.source.name for op in flat.ops] == ["new.jpg"]


def test_idempotent_and_duplicates(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "Photos" / "2019" / "06" / "done.jpg")
    touch(src / "x" / "same.jpg", b"identical")
    touch(src / "y" / "same.jpg", b"identical")
    touch(src / "z" / "same.jpg", b"different")
    touch(src / "Photos" / "2019" / "06" / "exists.jpg", b"on disk")
    touch(src / "w" / "exists.jpg", b"on disk")
    touch(src / "v" / "exists.jpg", b"other content")
    (src / "Photos" / "2019" / "06" / "folder.jpg").mkdir()
    touch(src / "u" / "folder.jpg")
    plan = planner(config).plan(SortOptions(src, src))
    skipped = {str(p.relative_to(src)): r for p, r in plan.skipped}
    assert skipped["Photos/2019/06/done.jpg"] == "already in place"
    assert skipped["Photos/2019/06/exists.jpg"] == "already in place"
    assert skipped["y/same.jpg"] == "duplicate of same.jpg (same content)"
    assert skipped["w/exists.jpg"].startswith("duplicate of ")
    moved = dests(plan, src)
    assert moved["x/same.jpg"] == "Photos/2019/06/same.jpg"
    assert moved["z/same.jpg"] == "Photos/2019/06/same_1.jpg"
    assert moved["v/exists.jpg"] == "Photos/2019/06/exists_1.jpg"
    assert moved["u/folder.jpg"] == "Photos/2019/06/folder_1.jpg"


def test_file_whose_suffix_name_is_itself(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "Photos" / "2019" / "06" / "a.jpg", b"one")
    touch(src / "Photos" / "2019" / "06" / "a_1.jpg", b"two")
    touch(src / "b" / "a.jpg", b"three")
    plan = planner(config).plan(SortOptions(src, src))
    assert dests(plan, src) == {"b/a.jpg": "Photos/2019/06/a_2.jpg"}


def test_sidecars(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "IMG_1.JPG")
    touch(src / "IMG_1.xmp")
    touch(src / "IMG_1.AAE")
    touch(src / "IMG_2.jpg")
    touch(src / "IMG_2.jpg.xmp")
    touch(src / "lonely.xmp")
    touch(src / "x.xmp")
    touch(src / "x.xmp.aae")
    config.sort.lowercase_extension = False
    plan = planner(config).plan(SortOptions(src, tmp_path / "out", rename=True))
    ops = {op.source.name: op for op in plan.ops}
    stamp = "2019-06-12_10-15-30"
    assert ops["IMG_1.JPG"].destination.name == f"{stamp}.JPG"
    assert ops["IMG_1.xmp"].destination.name == f"{stamp}.xmp"
    assert ops["IMG_1.AAE"].destination.name == f"{stamp}.AAE"
    assert ops["IMG_1.xmp"].sidecar_of == src / "IMG_1.JPG"
    # Extensions keep their case here, so ".JPG" and ".jpg" do not collide.
    assert ops["IMG_2.jpg"].destination.name == f"{stamp}.jpg"
    assert ops["IMG_2.jpg.xmp"].destination.name == f"{stamp}.jpg.xmp"
    assert ops["lonely.xmp"].sidecar_of is None
    assert ops["x.xmp.aae"].sidecar_of is None


def test_sidecar_duplicate_and_keep(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "a" / "pic.jpg", b"one")
    touch(src / "a" / "pic.xmp", b"sidecar")
    touch(tmp_path / "out" / "Photos" / "2019" / "06" / "pic.xmp", b"sidecar")
    plan = planner(config).plan(SortOptions(src, tmp_path / "out"))
    assert [p.name for p, _ in plan.skipped] == ["pic.xmp"]
    keep_src = tmp_path / "keep"
    touch(keep_src / "doc.txt")
    touch(keep_src / "doc.xmp")
    config.sort.templates["other"] = ""
    config.sort.templates["document"] = ""
    kept = planner(config).plan(SortOptions(keep_src, keep_src, set_mtime=True))
    assert [op.action for op in kept.ops] == ["keep"]  # the sidecar is not moved alone


def test_templates(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "undated.jpg")
    touch(src / "song.mp3")
    config.sort.templates["image"] = "{camera}/{year}/{month}/{date:%d}"
    config.sort.templates["audio"] = "{genre}/{artist:%Y}"  # a spec that does not fit: ignored
    plan = planner(config, **{"undated.jpg": {"date": None, "date_source": ""}}).plan(
        SortOptions(src, src))
    assert dests(plan, src) == {
        "undated.jpg": "Unknown/Undated/undated.jpg",  # date parts collapse into "Undated"
        "song.mp3": "Unknown/Unknown/song.mp3",
    }
    config.sort.templates["image"] = "{year}/{year}-{month}"
    flat = planner(config, **{"undated.jpg": {"date": None}}).plan(SortOptions(src, src))
    assert dests(flat, src)["undated.jpg"] == "Undated/undated.jpg"
    config.sort.templates["image"] = "{camera}"
    config.sort.unknown = ""  # renders to nothing: stays at the root
    flat = planner(config, **{"undated.jpg": {"date": None}}).plan(SortOptions(src, src))
    assert (src / "undated.jpg", "already in place") in flat.skipped


@pytest.mark.parametrize("template", ["{year", "{camera.model}", "{camera!z}"])
def test_invalid_templates(tmp_path: Path, config: Config, template: str) -> None:
    touch(tmp_path / "src" / "a.jpg")
    config.sort.templates["image"] = template
    with pytest.raises(ConfigError):
        planner(config, **{"a.jpg": {"date": None}}).plan(
            SortOptions(tmp_path / "src", tmp_path / "src"))


def test_invalid_rename_template(tmp_path: Path, config: Config) -> None:
    touch(tmp_path / "src" / "a.jpg")
    config.sort.rename["image"] = "{date"
    with pytest.raises(ConfigError):
        planner(config).plan(SortOptions(tmp_path / "src", tmp_path / "out", rename=True))


def test_rename_rules(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "IMG_1.JPG")
    touch(src / "guess.jpg")
    touch(src / "song.MP3")
    touch(src / "untitled.mp3")
    touch(src / "notes.TXT")
    plan = planner(config, **{
        "guess.jpg": {"date_source": "mtime"},
        "song.MP3": {"track": 3, "title": "Intro/Outro"},
        "untitled.mp3": {"track": None, "title": ""},
    }).plan(SortOptions(src, tmp_path / "out", rename=True))
    names = {op.source.name: op.destination.name for op in plan.ops}
    assert names == {
        "IMG_1.JPG": "2019-06-12_10-15-30.jpg",
        "guess.jpg": "guess.jpg",  # never renamed from a guessed date
        "song.MP3": "03 - Intro_Outro.mp3",
        "untitled.mp3": "untitled.mp3",
        "notes.TXT": "notes.txt",
    }


def test_generic_folders(config: Config) -> None:
    p = planner(config)
    for name in ("DCIM", "100CANON", "101_pana", "2019", "2019-06", "", "Camera Roll"):
        assert p.is_generic_folder(name), name
    assert not p.is_generic_folder("Vacances Bretagne")


def test_keywords_and_metadata(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "Vacances" / "IMG_20190612_101530.jpg")
    touch(src / "DCIM" / "b.jpg")
    touch(src / "root.jpg")
    touch(src / "clip.avi")
    touch(src / "doc.pdf")
    config.sort.tags.extra = ["Famille", " "]
    infos = {
        "IMG_20190612_101530.jpg": {"date_source": "filename", "camera": "Pixel 7",
                                    "keywords": ["photo"]},
        "b.jpg": {"keywords": []},
    }
    options = SortOptions(src, src, fix_dates=True, tag=True, extra_tags=["famille", "Été"])
    plan = planner(config, **infos).plan(options)
    ops = {op.source.name: op for op in plan.ops}
    first = ops["IMG_20190612_101530.jpg"]
    assert first.set_date == DATE
    assert first.add_keywords == ["Pixel 7", "Vacances", "Famille", "Été"]
    assert ops["b.jpg"].add_keywords == ["Photo", "Famille", "Été"]
    assert ops["root.jpg"].add_keywords == ["Photo", "Famille", "Été"]
    assert ops["root.jpg"].set_date is None  # its date came from the metadata
    assert not ops["clip.avi"].changes_metadata  # format exiftool cannot write
    assert not ops["doc.pdf"].changes_metadata
    config.sort.tags.category = config.sort.tags.camera = config.sort.tags.folder = False
    config.sort.tags.extra = []
    quiet = planner(config, **infos).plan(SortOptions(src, src, tag=True))
    assert all(not op.add_keywords for op in quiet.ops)
    no_tool = planner(config, exiftool=False, **infos).plan(options)
    assert all(not op.changes_metadata for op in no_tool.ops)


def test_in_place_metadata_and_mtime(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    photo = touch(src / "Photos" / "2019" / "06" / "IMG_20190612_101530.jpg")
    same = touch(src / "Photos" / "2019" / "06" / "same.jpg")
    os.utime(same, (DATE.timestamp(), DATE.timestamp()))
    infos = {"IMG_20190612_101530.jpg": {"date_source": "filename"},
             "same.jpg": {"date_source": "metadata"}}
    plan = planner(config, **infos).plan(SortOptions(src, src, fix_dates=True, set_mtime=True))
    assert len(plan.ops) == 1
    op = plan.ops[0]
    assert (op.action, op.source, op.set_date) == ("keep", photo, DATE)
    assert op.set_mtime == DATE.timestamp()
    assert [(p.name, r) for p, r in plan.skipped] == [("same.jpg", "already in place")]
    shown = Out()
    preview(plan, shown)
    assert "keep" in shown.text and "+mtime" in shown.text
    guessed = planner(config, **{"IMG_20190612_101530.jpg": {"date_source": "mtime"}})
    assert guessed.plan(SortOptions(src, src, set_mtime=True)).ops == []


def test_only_and_copy(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    touch(src / "a.jpg")
    touch(src / "b.pdf")
    plan = planner(config).plan(SortOptions(src, tmp_path / "out", mode="copy", only=["image"]))
    assert [(op.source.name, op.action) for op in plan.ops] == [("a.jpg", "copy")]
    assert plan.skipped == [(src / "b.pdf", "category not selected")]


def test_preview_and_export(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    for i in range(3):
        touch(src / f"IMG_2019061{i}_101530.jpg")
    touch(src / "guess.txt")
    touch(src / "Photos" / "2019" / "06" / "IMG_20190612_101530.jpg", b"other")
    infos = {"guess.txt": {"date_source": "mtime"},
             "IMG_20190610_101530.jpg": {"date_source": "filename"}}
    plan = planner(config, **infos).plan(SortOptions(src, src, fix_dates=True))
    out = Out(verbose=True)
    preview(plan, out, limit=2)
    text = out.text
    assert "4 file(s) to move, 1 renamed, 1 metadata update(s), 1 left untouched." in text
    assert "Photos/2019/06" in text and "… and 2 more (use --show-all)." in text
    assert "already in place" in text
    assert "dated from their modification time only" in text
    assert "Photos/2019/06/IMG_20190612_101530.jpg: already in place" in text
    full = Out()
    preview(plan, full, limit=None)
    assert "more (use --show-all)" not in full.text
    empty = Out()
    preview(SortPlan(SortOptions(src, src)), empty, limit=None)
    assert "0 file(s) to move" in empty.text and "action" not in empty.text
    target = tmp_path / "plan.json"
    export_plan(plan, target)
    data = json.loads(target.read_text())
    assert data["mode"] == "move" and len(data["operations"]) == 4
    assert data["operations"][0]["set_date"] == "2019-06-12T10:15:30"
    assert data["skipped"][0]["reason"] == "already in place"


def test_describe_and_serialise() -> None:
    op = SortOp(Path("/a/x.jpg"), Path("/b/x.jpg"), "move", "image", DATE, "filename",
                set_date=DATE, add_keywords=["k"], set_mtime=1.0, sidecar_of=Path("/a/y.jpg"))
    assert describe_op(op) == "+date 2019-06-12 10:15; +tags k; +mtime; sidecar of y.jpg"
    bare = SortOp(Path("/a"), Path("/b"), "keep", "other")
    assert describe_op(bare) == ""
    assert bare.to_dict()["date"] is None and bare.to_dict()["sidecar_of"] is None
    assert op.to_dict()["sidecar_of"] == "/a/y.jpg"


def test_folder_patterns() -> None:
    from pathlib import PurePosixPath as P

    from filesoptim.sorting.planner import compile_folder_pattern, sorted_by_hand

    assert compile_folder_pattern("{year}/*").fullmatch("2019/Vacances Bretagne")
    assert not compile_folder_pattern("{year}/*").fullmatch("2019")
    assert not compile_folder_pattern("{year}/*").fullmatch("Import/x")
    assert compile_folder_pattern("/Photos/**/").fullmatch("photos/a/b/c")
    assert compile_folder_pattern("{year}-{month}?").fullmatch("2019-06a")
    assert not compile_folder_pattern("{month}").fullmatch("13")
    assert compile_folder_pattern("a.b").fullmatch("a.b")
    assert not compile_folder_pattern("a.b").fullmatch("axb")
    patterns = ["", "  /  ", "{year}/*"]
    assert sorted_by_hand(P("2019/Mariage/Soirée"), patterns) == "{year}/*"  # sub-folders too
    assert sorted_by_hand(P("2019"), patterns) is None
    assert sorted_by_hand(P("."), patterns) is None


def test_leave_sorted_and_library_duplicates(tmp_path: Path, config: Config) -> None:
    src = tmp_path / "src"
    kept = touch(src / "2019" / "Vacances" / "a.jpg", b"holiday photo")
    touch(src / "2020" / "Mariage" / "Soirée" / "b.jpg", b"wedding")
    touch(src / "Import" / "copy.jpg", b"holiday photo")  # same content as a kept photo
    touch(src / "Import" / "same size.jpg", b"HOLIDAY PHOTO")  # same size, other content
    touch(src / "loose.jpg", b"loose")
    config.sort.templates["image"] = "{year}/{year}-{month}"
    plan = planner(config).plan(SortOptions(src, src, leave_sorted=["{year}/*"]))
    skipped = {str(p.relative_to(src)): r for p, r in plan.skipped}
    assert skipped["2019/Vacances/a.jpg"] == "already sorted by hand (matches '{year}/*')"
    assert "2020/Mariage/Soirée/b.jpg" in skipped
    assert skipped["Import/copy.jpg"] == (
        "duplicate of 2019/Vacances/a.jpg (already in your library)")
    assert dests(plan, src) == {"Import/same size.jpg": "2019/2019-06/same size.jpg",
                                "loose.jpg": "2019/2019-06/loose.jpg"}
    assert kept.exists()
