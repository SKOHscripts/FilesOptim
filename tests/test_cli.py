from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from filesoptim import __version__, cli
from filesoptim.config import Config, default_config_path
from filesoptim.optimize.base import Outcome
from filesoptim.optimize.engine import OptimizeSummary
from filesoptim.sorting.executor import list_journals
from tests.conftest import FakeTools, Out, age, make_image

JUNK = b"\0" * 20_000


def jpegoptim(argv: list[str]) -> tuple[int, str, str]:
    target = Path(argv[-1])
    data = target.read_bytes()
    target.write_bytes(data[: data.rindex(b"\xff\xd9") + 2])
    return 0, "", ""


def run(argv: list[str], *, tools: FakeTools | None = None, answers: tuple[str, ...] = (),
        interactive: bool = True) -> tuple[int, Out]:
    out = Out(answers=answers, interactive=interactive)
    code = cli.main(argv, tools=tools or FakeTools(), console=out)
    return code, out


def photo_with_junk(path: Path) -> Path:
    make_image(path, fmt="JPEG")
    with path.open("ab") as fh:
        fh.write(JUNK)
    return age(path)


# -- generic behaviour --------------------------------------------------------------------
def test_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "typical workflow" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_default_console_and_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config", "--path", "-v", "--no-color"]) == 0
    assert capsys.readouterr().out.strip() == str(default_config_path())


def test_errors_map_to_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = run(["doctor", "--config", str(tmp_path / "missing.toml")])
    assert code == 2 and "configuration file not found" in out.text
    code, out = run(["bigfiles", str(tmp_path / "nope")])
    assert code == 2 and "not found" in out.text
    code, out = run(["bigfiles", str(tmp_path), "--min-size", "huge"])
    assert code == 2 and "invalid size" in out.text

    def interrupted(*args: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_doctor", interrupted)
    code, out = run(["doctor"])
    assert code == 130 and "Interrupted" in out.text

    def broken_pipe(*args: Any) -> int:
        raise BrokenPipeError

    class Stdout(io.StringIO):
        def fileno(self) -> int:
            return 99

    dup2_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(cli, "cmd_doctor", broken_pipe)
    monkeypatch.setattr(cli.sys, "stdout", Stdout())
    monkeypatch.setattr(cli.os, "dup2", lambda a, b: dup2_calls.append((a, b)))
    monkeypatch.setattr(cli.os, "open", lambda *a: 42)
    assert run(["doctor"])[0] == 1
    assert dup2_calls == [(42, 99)]


def test_main_module_imports() -> None:
    module = importlib.import_module("filesoptim.__main__")
    assert module.main is cli.main


# -- optimize -----------------------------------------------------------------------------
def test_optimize_estimate_then_apply(tmp_path: Path) -> None:
    photo = photo_with_junk(tmp_path / "p.jpg")
    size = photo.stat().st_size
    tools = FakeTools(["jpegoptim"], jpegoptim=jpegoptim)
    code, out = run(["optimize", str(tmp_path), "--estimate-only", "-t", "jpeg"], tools=tools)
    assert code == 0 and photo.stat().st_size == size
    assert "Estimate (before any change)" in out.text
    code, out = run(["optimize", str(tmp_path), "-n"], tools=tools)
    assert code == 0 and photo.stat().st_size == size
    code, out = run(["optimize", str(tmp_path), "-y", "--no-color"], tools=tools)
    assert code == 0 and photo.stat().st_size < size - 19_000
    code, out = run(["optimize", str(tmp_path), "-y"], tools=tools)
    assert "already processed" in out.text  # remembered in the state database


def test_optimize_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeEngine:
        def __init__(self, config: Config, tools: Any, console: Any, state: Any,
                     **kwargs: Any) -> None:
            captured["config"], captured["kwargs"] = config, kwargs

        def run(self, paths: list[Path], **kwargs: Any) -> OptimizeSummary:
            captured["run"] = kwargs
            return OptimizeSummary(outcomes=[Outcome(paths[0], "jpeg", "failed", 1, 1)])

    monkeypatch.setattr(cli, "Engine", FakeEngine)
    code, _ = run(["optimize", str(tmp_path), "-t", "jpeg,video", "--min-saving", "2",
                   "--keep-originals", "backup", "--backup-dir", str(tmp_path / "b"),
                   "-j", "2", "--jpeg-progressive", "--codec", "av1", "--crf", "30",
                   "--preset", "4", "--metric", "vmaf", "--min-ssim", "0.99",
                   "--min-vmaf", "90", "--video-min-saving", "25", "--pdf-mode", "ebook",
                   "--min-age", "0", "--force", "--include-hidden", "--exclude", "*.tmp",
                   "--cross-filesystems", "-y"])
    assert code == 1  # a failure was reported
    config = captured["config"]
    assert config.optimize.types == ["jpeg", "video"]
    assert (config.optimize.min_saving_percent, config.optimize.jobs) == (2.0, 2)
    assert config.optimize.jpeg_progressive is True
    assert (config.video.codec, config.video.crf, config.video.preset) == ("av1", 30, "4")
    assert (config.video.metric, config.video.min_ssim, config.video.min_vmaf) == (
        "vmaf", 0.99, 90.0)
    assert config.video.min_saving_percent == 25.0 and config.pdf.mode == "ebook"
    assert config.general.min_age_seconds == 0
    walk = captured["kwargs"]["walk"]
    assert captured["kwargs"]["force"] and not walk.skip_hidden
    assert "*.tmp" in walk.exclude and not walk.one_file_system
    assert captured["run"] == {"estimate_only": False, "assume_yes": True}
    code, out = run(["optimize", str(tmp_path), "--min-ssim", "2"])
    assert code == 2 and "min_ssim" in out.text


# -- sort / undo --------------------------------------------------------------------------
def test_sort_flow_and_undo(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "trip").mkdir(parents=True)
    photo = make_image(src / "trip" / "IMG_20190612_101530.jpg", fmt="JPEG")
    tools = FakeTools(["exiftool"])
    code, out = run(["sort", str(src), "--dest", str(tmp_path / "out"), "-n", "--rename",
                     "--tags", "family", "--export-plan", str(tmp_path / "plan.json"),
                     "--show-all"], tools=tools)
    assert code == 0 and photo.exists()
    assert json.loads((tmp_path / "plan.json").read_text())["operations"][0]["add_keywords"]
    code, out = run(["sort", str(src), "--dest", str(tmp_path / "out")], answers=("n",))
    assert code == 0 and photo.exists()
    code, out = run(["sort", str(src), "--dest", str(tmp_path / "out"), "--fix-dates",
                     "--tag", "--prune-empty", "--no-mtime", "-y"], tools=tools)
    moved = tmp_path / "out" / "Photos" / "2019" / "06" / photo.name
    assert code == 0 and moved.exists() and not (src / "trip").exists()
    assert "To revert: filesoptim undo" in out.text
    code, out = run(["undo", "--list"])
    assert code == 0 and "sort-" in out.text
    code, out = run(["undo", "-n"])
    assert code == 0 and moved.exists() and "move back" in out.text
    code, out = run(["undo"], answers=("n",))
    assert moved.exists()
    code, out = run(["undo", "-y"], tools=tools)
    assert code == 0 and photo.exists() and not moved.exists()
    assert list_journals() == []
    code, out = run(["undo"])
    assert code == 0 and "No sorting to undo." in out.text


def test_sort_errors_and_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    code, out = run(["sort", str(src / "nope")])
    assert code == 2
    (src / "file.txt").write_text("x")
    code, out = run(["sort", str(src / "file.txt")])
    assert code == 2 and "is not a folder" in out.text
    code, out = run(["sort", str(src), "--only", "image,cars"])
    assert code == 2 and "unknown categories: cars" in out.text
    code, out = run(["sort", str(src), "--tag", "--flat", "--copy", "-y"],
                    tools=FakeTools(["apt-get"]))
    assert "exiftool is missing" in out.text and "sudo apt install" in out.text
    code, out = run(["sort", str(tmp_path / "src" / "Other"), "-y"])
    assert code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    code, out = run(["sort", str(empty)])
    assert code == 0 and "Nothing to do." in out.text
    (src / "b.txt").write_text("y")

    class Failing:
        def __init__(self, *args: Any) -> None:
            pass

        def execute(self, plan: Any) -> Any:
            class Result:
                done, journal, failures = 0, Path("j"), [(Path("x"), "boom")]

            return Result()

    monkeypatch.setattr(cli, "SortExecutor", Failing)
    assert run(["sort", str(src), "-y"])[0] == 1


def test_undo_explicit_journal_with_errors(tmp_path: Path) -> None:
    journal = tmp_path / "manual.jsonl"
    target = tmp_path / "moved.txt"
    target.write_text("x")
    journal.write_text(json.dumps({"type": "header", "created": "now"}) + "\n" + json.dumps(
        {"type": "meta", "path": str(tmp_path / "absent.jpg"), "category": "image",
         "set_date": True, "keywords": []}) + "\n" + json.dumps(
        {"type": "move", "src": str(tmp_path / "gone" / "x.txt"),
         "dst": str(tmp_path / "missing.txt")}) + "\n")
    code, out = run(["undo", str(journal), "-y"], tools=FakeTools(["exiftool"]))
    assert code == 1 and "not found" in out.text
    assert "file no longer at its sorted location" in out.text
    empty = tmp_path / "empty.jsonl"
    empty.write_text(json.dumps({"type": "header"}) + "\n")
    assert run(["undo", str(empty), "-y"])[0] == 0
    assert run(["undo", str(tmp_path / "nope.jsonl")])[0] == 2


# -- clean / dupes / scans ----------------------------------------------------------------
def test_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    code, out = run(["clean", "--list"])
    assert code == 0 and "thumbnails" in out.text
    code, out = run(["clean", "--dry-run", "--only", "thumbnails"])
    assert code == 0 and "Nothing to clean." in out.text
    monkeypatch.setattr(cli.Cleaner, "run", lambda self, *a, **k: (0, ["error"]))
    assert run(["clean", "--system"])[0] == 1


def test_dupes(tmp_path: Path, capsys: pytest.CaptureFixture[str],
               monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a.txt", "b.txt", "sub/c.txt"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("duplicate content")
    code, out = run(["dupes", str(tmp_path)])
    assert code == 0 and "2 redundant copy(ies)" in out.text
    code, _ = run(["dupes", str(tmp_path), "--json", "--prefer", str(tmp_path / "sub")])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["keep"] == str(tmp_path / "sub" / "c.txt") and len(data[0]["remove"]) == 2
    assert run(["dupes", str(tmp_path), "--action", "delete", "-n"])[0] == 0
    assert run(["dupes", str(tmp_path), "--action", "delete"], answers=("n",))[0] == 0
    assert len(list(tmp_path.rglob("*.txt"))) == 3
    code, out = run(["dupes", str(tmp_path), "--action", "delete", "--keep", "shortest", "-y",
                     "--show-all"])
    assert code == 0 and sorted(p.name for p in tmp_path.rglob("*.txt")) == ["a.txt"]
    assert "freed." in out.text
    code, out = run(["dupes", str(tmp_path), "--action", "delete", "-y"])
    assert code == 0 and "No duplicate found." in out.text
    (tmp_path / "z.txt").write_text("duplicate content")
    monkeypatch.setattr(cli, "apply_actions", lambda *a: (0, ["failed"]))
    assert run(["dupes", str(tmp_path), "--action", "trash", "-y"])[0] == 1


def test_bigfiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "big").write_bytes(b"1" * 5000)
    (tmp_path / "small").write_bytes(b"1")
    code, out = run(["bigfiles", str(tmp_path), "--top", "1"])
    assert code == 0 and "big" in out.text and "small" not in out.text
    run(["bigfiles", str(tmp_path), "--json", "--min-size", "2"])
    assert [Path(d["path"]).name for d in json.loads(capsys.readouterr().out)] == ["big"]


def test_emptydirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "c").mkdir()
    code, out = run(["emptydirs", str(tmp_path)])
    assert code == 0 and "Empty folders: 2 (with sub-folders: 3)" in out.text
    assert run(["emptydirs", str(tmp_path), "--delete", "-n"])[0] == 0
    assert run(["emptydirs", str(tmp_path), "--delete"], answers=("n",))[0] == 0
    assert (tmp_path / "a" / "b").exists()
    real = Path.rmdir

    def rmdir(self: Path) -> None:
        if self.name == "c":
            raise OSError("busy")
        real(self)

    monkeypatch.setattr(Path, "rmdir", rmdir)
    code, out = run(["emptydirs", str(tmp_path), "--delete", "-y"])
    assert code == 1 and "busy" in out.text and not (tmp_path / "a").exists()
    monkeypatch.setattr(Path, "rmdir", real)
    code, out = run(["emptydirs", str(tmp_path), "--delete", "-y"])
    assert code == 0 and "1 folder(s) removed." in out.text


def test_brokenlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "dead").symlink_to(tmp_path / "nothing")
    (tmp_path / "revived").symlink_to(tmp_path / "later")
    code, out = run(["brokenlinks", str(tmp_path)])
    assert code == 0 and "Broken symbolic links: 2" in out.text
    assert run(["brokenlinks", str(tmp_path), "--delete", "-n"])[0] == 0
    assert run(["brokenlinks", str(tmp_path), "--delete"], answers=("n",))[0] == 0
    real_confirm = Out.confirm

    def confirm(self: Out, question: str, *, assume_yes: bool = False) -> bool:
        (tmp_path / "later").write_text("now it exists")  # fixed before deletion
        return real_confirm(self, question, assume_yes=True)

    monkeypatch.setattr(Out, "confirm", confirm)
    code, out = run(["brokenlinks", str(tmp_path), "--delete"])
    assert code == 0 and "1 link(s) deleted." in out.text
    assert (tmp_path / "revived").is_symlink() and not (tmp_path / "dead").is_symlink()


# -- report / doctor / config -------------------------------------------------------------
def test_report(tmp_path: Path, isolated_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (isolated_env / "doc.txt").write_text("hello")
    code, out = run(["report"])
    assert code == 0 and "Content: " in out.text
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("same")
    photo_with_junk(tmp_path / "p.jpg")
    tools = FakeTools(["jpegoptim"], jpegoptim=jpegoptim)
    code, out = run(["report", str(tmp_path), "--estimate", "--dupes"], tools=tools)
    assert "Estimate (before any change)" in out.text and "Duplicates" in out.text
    code, out = run(["report", str(tmp_path), "--json", "--estimate", "--dupes"], tools=tools)
    data = json.loads(capsys.readouterr().out)
    assert data["duplicates"]["groups"] == 1
    assert data["estimate"]["by_type"]["jpeg"]["saving"] >= 20_000
    assert out.text == ""  # nothing but JSON on stdout


def test_doctor_and_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["doctor"])
    assert code == 1 and "External tools" in out.text
    code, out = run(["config"])
    assert code == 0 and "[video]" in capsys.readouterr().out
    code, out = run(["config", "--init"])
    assert code == 0 and default_config_path().exists()
    code, out = run(["config", "--init"])
    assert code == 2 and "already exists" in out.text
    code, out = run(["config", "--init", "--force"])
    assert code == 0
    custom = tmp_path / "c.toml"
    assert run(["config", "--init", "--config", str(custom)])[0] == 2  # must exist to be used
    custom.write_text("[video]\ncodec = 'av1'\n")
    code, _ = run(["config", "--config", str(custom)])
    assert 'codec = "av1"' in capsys.readouterr().out


def test_signal_handler_interrupts_and_is_restored() -> None:
    import signal

    with pytest.raises(KeyboardInterrupt):
        cli._interrupt(signal.SIGTERM, None)
    before = signal.getsignal(signal.SIGTERM)
    run(["config", "--path"])
    assert signal.getsignal(signal.SIGTERM) is before


def test_sort_templates_and_leave_sorted(tmp_path: Path) -> None:
    src = tmp_path / "photos"
    (src / "2019" / "Vacances").mkdir(parents=True)
    (src / "2019" / "Vacances" / "IMG_20190712_101010.jpg").write_bytes(b"kept")
    (src / "IMG_20210301_120000.jpg").write_bytes(b"new")
    code, out = run(["sort", str(src), "--leave-sorted", "{year}/*", "--template",
                     "image={year}/{year}-{month}", "--no-mtime", "-y"])
    assert code == 0 and "already sorted by hand" in out.text
    assert (src / "2021" / "2021-03" / "IMG_20210301_120000.jpg").exists()
    assert (src / "2019" / "Vacances" / "IMG_20190712_101010.jpg").exists()
    for bad in ("image", "cars={year}"):
        code, out = run(["sort", str(src), "--template", bad])
        assert code == 2 and "--template expects CATEGORY=TEMPLATE" in out.text


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_every_command_has_a_detailed_help(lang: str, capsys: pytest.CaptureFixture[str],
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILESOPTIM_LANG", lang)
    commands = ["optimize", "sort", "tag", "undo", "clean", "dupes", "bigfiles", "emptydirs",
                "brokenlinks", "report", "doctor", "config"]
    for command in commands:
        with pytest.raises(SystemExit):
            cli.main([command, "--help"])
        text = capsys.readouterr().out
        assert f"filesoptim {command}" in text
        assert ("exemple" if lang == "fr" else "example") in text
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out
    assert all(command in text for command in commands)
    assert ("utilisation :" if lang == "fr" else "usage:") in text


def test_tag_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "Photos"
    event = src / "2019" / "Vacances Bretagne"
    event.mkdir(parents=True)
    photo = make_image(event / "IMG_20190712_101010.jpg", fmt="JPEG")
    before = photo.read_bytes()
    tools = FakeTools(["exiftool"])
    code, out = run(["tag", str(src), "--tags", "famille", "--fix-dates", "-n"], tools=tools)
    assert code == 0 and "Metadata plan" in out.text and "+tags" in out.text
    assert "Vacances Bretagne" in out.text and "famille" in out.text
    assert photo.read_bytes() == before  # preview only
    code, out = run(["tag", str(src), "--tags", "famille", "--fix-dates", "--set-mtime", "-y"],
                    tools=tools)
    assert code == 0 and photo.exists()  # nothing moved nor renamed
    assert [p.name for p in event.iterdir()] == ["IMG_20190712_101010.jpg"]
    assert any("-XMP-dc:Subject+=famille" in call for call in tools.calls)
    code, out = run(["tag", str(src), "--only-my-tags", "--tags", "x", "--no-keywords",
                     "--set-mtime", "-y"], tools=tools)
    assert code == 0
    assert run(["undo", "-y"], tools=tools)[0] == 0


def test_tag_errors(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    assert run(["tag", str(tmp_path / "f.txt")], tools=FakeTools(["exiftool"]))[0] == 2
    code, out = run(["tag", str(tmp_path), "--only", "audio"], tools=FakeTools(["exiftool"]))
    assert code == 2 and "image and video only" in out.text
    code, out = run(["tag", str(tmp_path)], tools=FakeTools(["apt-get"]))
    assert code == 2 and "sudo apt install libimage-exiftool-perl" in out.text
    code, out = run(["tag", str(tmp_path), "--no-keywords"], tools=FakeTools(["exiftool"]))
    assert code == 2 and "nothing to do" in out.text
