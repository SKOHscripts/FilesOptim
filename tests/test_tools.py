from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from filesoptim.tools import ToolError, Tools, detect_package_manager, install_hint
from tests.conftest import FakeTools

PY = sys.executable


def test_which_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        calls.append(name)
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    tools = Tools()
    assert not tools.has("nothing")
    assert tools.which("nothing") is None
    assert calls == ["nothing"]


def test_run_success_and_capture() -> None:
    proc = Tools().run([PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert proc.stdout.strip() == "out"
    assert proc.stderr.strip() == "err"


def test_run_failure_reports_last_line() -> None:
    with pytest.raises(ToolError, match="failed: second"):
        Tools().run([PY, "-c", "import sys; print('first\\nsecond', file=sys.stderr); exit(3)"])
    with pytest.raises(ToolError, match="exit code 4"):
        Tools().run([PY, "-c", "exit(4)"])
    proc = Tools().run([PY, "-c", "exit(5)"], check=False)
    assert proc.returncode == 5


def test_run_missing_program_and_timeout() -> None:
    with pytest.raises(ToolError, match="not installed"):
        Tools(path="").run(["definitely-not-a-program"])
    with pytest.raises(ToolError, match="timed out"):
        Tools().run([PY, "-c", "import time; time.sleep(5)"], timeout=0.2)


def test_popen_streams_stdout(tmp_path: Path) -> None:
    code = "print('a'); print('b')"
    with (tmp_path / "err").open("w+") as err, Tools().popen([PY, "-c", code], stderr=err) as proc:
        assert proc.stdout is not None
        assert [line.strip() for line in proc.stdout] == ["a", "b"]
        assert proc.wait() == 0
        assert isinstance(proc, subprocess.Popen)


def test_package_manager_detection_and_hints() -> None:
    assert detect_package_manager(FakeTools()) is None
    assert install_hint(["qpdf", "ffmpeg"], FakeTools()) == "install: ffmpeg qpdf"
    apt = FakeTools(["apt-get"])
    assert detect_package_manager(apt) == "apt"
    assert install_hint(["exiftool", "ffmpeg", "ffprobe", "unknown"], apt) == (
        "sudo apt install ffmpeg libimage-exiftool-perl"
    )
    assert install_hint(["gs"], FakeTools(["pacman"])) == "sudo pacman -S ghostscript"
