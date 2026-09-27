"""Check the environment: external tools, ffmpeg features, Python libraries, paths."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

from filesoptim.config import Config, state_dir
from filesoptim.optimize.engine import build_optimizers
from filesoptim.optimize.video import VideoOptimizer
from filesoptim.tools import TOOL_INFO, Tools, install_hint
from filesoptim.ui import Console


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "missing"


def run_doctor(config: Config, tools: Tools, console: Console, config_path: Path) -> int:
    console.heading("External tools")
    rows = []
    missing = []
    for name, info in TOOL_INFO.items():
        location = tools.which(name)
        if location is None:
            missing.append(name)
        rows.append([name, "✔" if location else "✘ missing", info.purpose])
    console.table(["tool", "status", "used for"], rows)
    if missing:
        console.info(f"To install the missing tools: {install_hint(missing, tools)}")

    console.heading("Features")
    status = 0
    for optimizer in build_optimizers(config, tools, config.optimize.types):
        reason = optimizer.unavailable_reason()
        if reason:
            status = 1
            console.warn(f"optimize {optimizer.name}: unavailable ({reason})")
        else:
            detail = ""
            if isinstance(optimizer, VideoOptimizer):
                detail = f" ({optimizer.encoder()}, quality metric: {optimizer.metric.upper()})"
            console.success(f"optimize {optimizer.name}{detail}")
    if tools.has("exiftool"):
        console.success("sort: dates and XMP keywords can be written")
    else:
        console.warn("sort: exiftool missing, metadata will be read with Pillow/ffprobe and "
                     "dates/keywords cannot be written")

    console.heading("Python libraries")
    console.info(f"Pillow {_version('Pillow')}, mutagen {_version('mutagen')}")

    console.heading("Files")
    exists = "" if config_path.exists() else " (not created: defaults are used)"
    console.info(f"configuration: {config_path}{exists}")
    console.info(f"state & undo journals: {state_dir()}")
    return status
