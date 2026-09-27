"""Lossless JPEG, PNG and GIF optimisers, verified pixel by pixel."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from PIL import Image

from filesoptim.optimize.base import FileOptimizer

# Local, trusted files: very large photos/panoramas must not be refused.
Image.MAX_IMAGE_PIXELS = None

_PALETTE_MODES = {"P", "PA"}


def _frames_equal(first: Image.Image, second: Image.Image) -> bool:
    if first.mode == second.mode and first.mode not in _PALETTE_MODES:
        return first.tobytes() == second.tobytes()
    return first.convert("RGBA").tobytes() == second.convert("RGBA").tobytes()


def images_identical(first: Path, second: Path) -> bool:
    """True when both files decode to exactly the same pixels (every frame)."""
    try:
        with Image.open(first) as a, Image.open(second) as b:
            if a.format != b.format or a.size != b.size:
                return False
            frames = getattr(a, "n_frames", 1)
            if frames != getattr(b, "n_frames", 1):
                return False
            for index in range(frames):
                a.seek(index)
                b.seek(index)
                if not _frames_equal(a, b):
                    return False
    except (OSError, ValueError, SyntaxError):
        return False
    return True


class ImageOptimizer(FileOptimizer):
    """Shared verification: the optimised image must be pixel-identical."""

    def verify(self, source: Path, output: Path) -> str | None:
        if images_identical(source, output):
            return None
        return "rejected: result is not pixel-identical to the original"


class JpegOptimizer(ImageOptimizer):
    name = "jpeg"
    extensions = frozenset({"jpg", "jpeg", "jpe", "jfif"})
    magic: ClassVar[tuple[bytes, ...]] = (b"\xff\xd8\xff",)

    def missing_tools(self) -> list[str]:
        return [] if self.tools.has("jpegoptim") or self.tools.has("jpegtran") else ["jpegoptim"]

    def signature(self) -> str:
        return f"{super().signature()}:progressive={self.config.optimize.jpeg_progressive}"

    def produce(self, source: Path, output: Path) -> None:
        progressive = self.config.optimize.jpeg_progressive
        if self.tools.has("jpegoptim"):
            shutil.copyfile(source, output)
            args = ["jpegoptim", "--quiet", "--strip-none", "--preserve"]
            if progressive:
                args.append("--all-progressive")
            self.tools.run([*args, str(output)])
        else:
            args = ["jpegtran", "-copy", "all", "-optimize"]
            if progressive:
                args.append("-progressive")
            self.tools.run([*args, "-outfile", str(output), str(source)])


class PngOptimizer(ImageOptimizer):
    name = "png"
    extensions = frozenset({"png"})
    magic: ClassVar[tuple[bytes, ...]] = (b"\x89PNG\r\n\x1a\n",)

    def missing_tools(self) -> list[str]:
        return [] if self.tools.has("oxipng") or self.tools.has("optipng") else ["optipng"]

    def precheck(self, path: Path) -> str | None:
        reason = super().precheck(path)
        if reason:
            return reason
        with path.open("rb") as fh:
            head = fh.read(1 << 16)
        idat = head.find(b"IDAT")
        actl = head.find(b"acTL")
        if actl != -1 and (idat == -1 or actl < idat):
            return "animated PNG (left untouched)"
        return None

    def produce(self, source: Path, output: Path) -> None:
        if self.tools.has("oxipng"):
            self.tools.run(
                ["oxipng", "--quiet", "--opt", "4", "--preserve", "--out", str(output), str(source)]
            )
        else:
            self.tools.run(
                ["optipng", "-quiet", "-o5", "-preserve", "-out", str(output), str(source)]
            )


class GifOptimizer(ImageOptimizer):
    name = "gif"
    extensions = frozenset({"gif"})
    magic: ClassVar[tuple[bytes, ...]] = (b"GIF87a", b"GIF89a")

    def missing_tools(self) -> list[str]:
        return [] if self.tools.has("gifsicle") else ["gifsicle"]

    def produce(self, source: Path, output: Path) -> None:
        self.tools.run(["gifsicle", "--no-warnings", "-O3", str(source), "-o", str(output)])
