"""PDF optimiser: lossless restructuring with qpdf, optional (lossy) Ghostscript modes."""

from __future__ import annotations

import mmap
from pathlib import Path
from typing import ClassVar

from filesoptim.optimize.base import FileOptimizer
from filesoptim.tools import ToolError

# qpdf exit codes: 0 = success, 2 = errors, 3 = success with warnings.
_QPDF_OK = (0, 3)


class PdfOptimizer(FileOptimizer):
    name = "pdf"
    extensions = frozenset({"pdf"})
    magic: ClassVar[tuple[bytes, ...]] = (b"%PDF",)

    @property
    def mode(self) -> str:
        return self.config.pdf.mode

    @property
    def lossy(self) -> bool:
        return self.mode != "lossless"

    def missing_tools(self) -> list[str]:
        missing = [] if self.tools.has("qpdf") else ["qpdf"]
        if self.lossy and not self.tools.has("gs"):
            missing.append("gs")
        return missing

    def signature(self) -> str:
        return f"{super().signature()}:{self.mode}"

    def min_saving(self) -> tuple[float, int]:
        percent, size = super().min_saving()
        if self.lossy:
            percent = max(percent, self.config.pdf.min_saving_percent_lossy)
        return percent, size

    def precheck(self, path: Path) -> str | None:
        reason = super().precheck(path)
        if reason:
            return reason
        with path.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data.find(b"/Encrypt") != -1:
                return "encrypted PDF (left untouched)"
            if self.config.pdf.skip_signed and data.find(b"/ByteRange") != -1:
                return "digitally signed PDF (the signature would be broken)"
        return None

    def produce(self, source: Path, output: Path) -> None:
        if self.lossy:
            self.tools.run(
                [
                    "gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.5", f"-dPDFSETTINGS=/{self.mode}",
                    f"-sOutputFile={output}", str(source),
                ]
            )
            return
        proc = self.tools.run(
            [
                "qpdf", "--object-streams=generate", "--compress-streams=y",
                "--recompress-flate", "--compression-level=9", str(source), str(output),
            ],
            check=False,
        )
        if proc.returncode not in _QPDF_OK:
            raise ToolError(f"qpdf failed: {(proc.stderr or proc.stdout).strip()[-200:]}")

    def page_count(self, path: Path) -> int:
        proc = self.tools.run(["qpdf", "--show-npages", str(path)], check=False)
        text = proc.stdout.strip()
        if proc.returncode not in _QPDF_OK or not text.isdigit():
            raise ToolError(f"cannot count pages of {path.name}")
        return int(text)

    def verify(self, source: Path, output: Path) -> str | None:
        check = self.tools.run(["qpdf", "--check", str(output)], check=False)
        if check.returncode not in _QPDF_OK:
            return "rejected: optimised PDF failed the structure check"
        if self.page_count(source) != self.page_count(output):
            return "rejected: page count changed"
        return None
