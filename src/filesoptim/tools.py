"""Discovery and execution of the external programs FilesOptim drives."""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """An external program failed or is missing."""


@dataclass(frozen=True)
class ToolInfo:
    purpose: str
    packages: dict[str, str]


# Package names per package manager (apt, dnf, pacman, zypper).
TOOL_INFO: dict[str, ToolInfo] = {
    "jpegoptim": ToolInfo(
        "JPEG lossless optimisation",
        {"apt": "jpegoptim", "dnf": "jpegoptim", "pacman": "jpegoptim", "zypper": "jpegoptim"},
    ),
    "jpegtran": ToolInfo(
        "JPEG lossless optimisation (fallback)",
        {
            "apt": "libjpeg-turbo-progs",
            "dnf": "libjpeg-turbo-utils",
            "pacman": "libjpeg-turbo",
            "zypper": "libjpeg-turbo",
        },
    ),
    "oxipng": ToolInfo(
        "PNG lossless optimisation (preferred)",
        {"apt": "oxipng", "dnf": "oxipng", "pacman": "oxipng", "zypper": "oxipng"},
    ),
    "optipng": ToolInfo(
        "PNG lossless optimisation",
        {"apt": "optipng", "dnf": "optipng", "pacman": "optipng", "zypper": "optipng"},
    ),
    "gifsicle": ToolInfo(
        "GIF lossless optimisation",
        {"apt": "gifsicle", "dnf": "gifsicle", "pacman": "gifsicle", "zypper": "gifsicle"},
    ),
    "qpdf": ToolInfo(
        "PDF lossless optimisation and checks",
        {"apt": "qpdf", "dnf": "qpdf", "pacman": "qpdf", "zypper": "qpdf"},
    ),
    "gs": ToolInfo(
        "PDF lossy optimisation (optional modes)",
        {
            "apt": "ghostscript",
            "dnf": "ghostscript",
            "pacman": "ghostscript",
            "zypper": "ghostscript",
        },
    ),
    "ffmpeg": ToolInfo(
        "video re-encoding and quality measurement",
        {"apt": "ffmpeg", "dnf": "ffmpeg-free", "pacman": "ffmpeg", "zypper": "ffmpeg"},
    ),
    "ffprobe": ToolInfo(
        "video analysis",
        {"apt": "ffmpeg", "dnf": "ffmpeg-free", "pacman": "ffmpeg", "zypper": "ffmpeg"},
    ),
    "exiftool": ToolInfo(
        "metadata reading/writing (dates, XMP keywords)",
        {
            "apt": "libimage-exiftool-perl",
            "dnf": "perl-Image-ExifTool",
            "pacman": "perl-image-exiftool",
            "zypper": "exiftool",
        },
    ),
}

PACKAGE_MANAGERS = {
    "apt": ("apt-get", "sudo apt install"),
    "dnf": ("dnf", "sudo dnf install"),
    "pacman": ("pacman", "sudo pacman -S"),
    "zypper": ("zypper", "sudo zypper install"),
}


class Tools:
    """Locate and run external programs. Tests substitute a fake with the same interface."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._cache: dict[str, str | None] = {}

    def which(self, name: str) -> str | None:
        if name not in self._cache:
            self._cache[name] = shutil.which(name, path=self.path)
        return self._cache[name]

    def has(self, name: str) -> bool:
        return self.which(name) is not None

    def _resolve(self, args: Sequence[str | Path]) -> list[str]:
        argv = [str(a) for a in args]
        exe = self.which(argv[0])
        if exe is None:
            raise ToolError(f"'{argv[0]}' is not installed")
        argv[0] = exe
        return argv

    def run(
        self,
        args: Sequence[str | Path],
        *,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a program and capture its text output."""
        argv = self._resolve(args)
        log.debug("run: %s", " ".join(argv))
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"{Path(argv[0]).name} timed out") from exc
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            last = detail[-1] if detail else f"exit code {proc.returncode}"
            raise ToolError(f"{Path(argv[0]).name} failed: {last}")
        return proc

    def popen(self, args: Sequence[str | Path], *, stderr: IO[Any]) -> subprocess.Popen[str]:
        """Start a program whose text stdout is streamed (used for ffmpeg progress)."""
        argv = self._resolve(args)
        log.debug("popen: %s", " ".join(argv))
        return subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )


def detect_package_manager(tools: Tools) -> str | None:
    for key, (binary, _) in PACKAGE_MANAGERS.items():
        if tools.has(binary):
            return key
    return None


def install_hint(names: Sequence[str], tools: Tools) -> str:
    """Command line suggesting how to install the given programs on this distribution."""
    manager = detect_package_manager(tools)
    if manager is None:
        return "install: " + " ".join(sorted(set(names)))
    packages = sorted({TOOL_INFO[n].packages[manager] for n in names if n in TOOL_INFO})
    return f"{PACKAGE_MANAGERS[manager][1]} {' '.join(packages)}"
