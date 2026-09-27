"""Common optimiser interface: estimate first (always), then materialise and install."""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from filesoptim.categories import extension
from filesoptim.config import Config
from filesoptim.fsutils import stat_key
from filesoptim.tools import ToolError, Tools
from filesoptim.ui import percent


@dataclass
class Estimate:
    """What optimising one file would give, computed *before* anything is changed."""

    path: Path
    kind: str
    original_size: int
    stat_key: tuple[int, int]
    estimated_size: int | None = None
    exact: bool = True
    quality: float | None = None
    metric: str = ""
    staged: Path | None = None
    target: Path | None = None
    skip_reason: str | None = None
    remember: bool = False
    lossy: bool = False

    @property
    def candidate(self) -> bool:
        return self.skip_reason is None and self.estimated_size is not None

    @property
    def saving(self) -> int:
        return 0 if self.estimated_size is None else self.original_size - self.estimated_size

    @property
    def saving_percent(self) -> float:
        return percent(self.saving, self.original_size)

    def skip(self, reason: str, *, remember: bool = True) -> Estimate:
        self.skip_reason = reason
        self.remember = remember
        self.discard()
        return self

    def discard(self) -> None:
        """Delete the staged result (if any)."""
        if self.staged is not None:
            self.staged.unlink(missing_ok=True)
            self.staged = None


@dataclass
class Outcome:
    path: Path
    kind: str
    status: str  # "optimized" | "skipped" | "failed"
    before: int
    after: int
    message: str = ""
    final_path: Path | None = None

    @property
    def saving(self) -> int:
        return self.before - self.after if self.status == "optimized" else 0


class Optimizer(ABC):
    """Base class. Subclasses never touch the original file: they only produce candidates."""

    name: ClassVar[str]
    extensions: ClassVar[frozenset[str]]
    version: ClassVar[int] = 1

    def __init__(self, config: Config, tools: Tools) -> None:
        self.config = config
        self.tools = tools
        self.progress: Callable[[float], None] | None = None

    # -- capabilities -----------------------------------------------------------------------
    @property
    def lossy(self) -> bool:
        return False

    @abstractmethod
    def missing_tools(self) -> list[str]:
        """External programs to install for this optimiser to work (empty when usable)."""

    def unavailable_reason(self) -> str | None:
        missing = self.missing_tools()
        return f"missing {', '.join(missing)}" if missing else None

    def signature(self) -> str:
        """Fingerprint of the settings: results are remembered per signature."""
        return f"{self.name}:v{self.version}"

    def accepts(self, path: Path) -> bool:
        return extension(path) in self.extensions

    def min_saving(self) -> tuple[float, int]:
        return self.config.optimize.min_saving_percent, self.config.optimize.min_saving_bytes

    # -- helpers ----------------------------------------------------------------------------
    def new_estimate(self, path: Path, st: os.stat_result) -> Estimate:
        return Estimate(path, self.name, st.st_size, stat_key(st), lossy=self.lossy)

    def judge_saving(self, est: Estimate) -> str | None:
        min_percent, min_bytes = self.min_saving()
        if est.saving < max(min_bytes, 1) or est.saving_percent < min_percent:
            return f"no significant gain ({est.saving_percent:.1f} %)"
        return None

    @staticmethod
    def staging_file(workdir: Path, suffix: str) -> Path:
        return workdir / f"{uuid.uuid4().hex}{suffix.lower()}"

    # -- workflow ---------------------------------------------------------------------------
    @abstractmethod
    def estimate(self, path: Path, st: os.stat_result, workdir: Path) -> Estimate:
        """Precise estimate. May leave a verified result in ``Estimate.staged``."""

    def materialize(self, est: Estimate, workdir: Path) -> Estimate:
        """Produce the final verified file when the estimate did not keep one."""
        return self.estimate(est.path, est.path.stat(), workdir)


class FileOptimizer(Optimizer):
    """Optimisers producing a whole candidate file quickly (images, PDF): exact estimates."""

    magic: ClassVar[tuple[bytes, ...]] = ()

    def precheck(self, path: Path) -> str | None:
        with path.open("rb") as fh:
            head = fh.read(1024)
        if self.magic and not any(head.startswith(m) for m in self.magic):
            return f"not a real {self.name.upper()} file (content does not match the extension)"
        return None

    @abstractmethod
    def produce(self, source: Path, output: Path) -> None:
        """Write an optimised version of ``source`` to ``output``."""

    @abstractmethod
    def verify(self, source: Path, output: Path) -> str | None:
        """Return why ``output`` must be rejected, or ``None`` if it is a faithful copy."""

    def estimate(self, path: Path, st: os.stat_result, workdir: Path) -> Estimate:
        est = self.new_estimate(path, st)
        try:
            reason = self.precheck(path)
        except OSError as exc:
            return est.skip(f"unreadable file ({exc})", remember=False)
        if reason:
            return est.skip(reason)
        output = self.staging_file(workdir, path.suffix)
        est.staged = output
        try:
            self.produce(path, output)
            if not output.exists():
                return est.skip("already optimal (no smaller output)")
            est.estimated_size = output.stat().st_size
            reason = self.judge_saving(est) or self.verify(path, output)
        except (ToolError, OSError) as exc:
            return est.skip(f"optimiser error ({exc})", remember=False)
        if reason:
            return est.skip(reason)
        return est
