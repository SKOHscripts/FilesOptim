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


def test_run_interrupted_kills_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = Tools()
    real = subprocess.Popen.communicate
    calls: list[int] = []

    def communicate(self: subprocess.Popen[str], *args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
    with pytest.raises(KeyboardInterrupt):
        tools.run([PY, "-c", "import time; time.sleep(30)"])
    assert tools._running == set()


def test_terminate_all_stops_running_tools() -> None:
    import threading
    import time

    tools = Tools()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            tools.run([PY, "-c", "import time; time.sleep(30)"])
        except ToolError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    started = time.time()
    thread.start()
    while not tools._running:
        time.sleep(0.01)
    tools.terminate_all()
    thread.join(10)
    assert time.time() - started < 10 and errors  # killed: non-zero exit code

    class Gone:
        def terminate(self) -> None:
            raise ProcessLookupError

    tools._running.add(Gone())  # type: ignore[arg-type]
    tools.terminate_all()  # already finished children are ignored
