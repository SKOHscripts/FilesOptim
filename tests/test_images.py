from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from filesoptim.config import Config
from filesoptim.optimize.images import (
    GifOptimizer,
    JpegOptimizer,
    PngOptimizer,
    images_identical,
)
from filesoptim.tools import ToolError
from tests.conftest import FakeTools, make_image

JUNK = b"\x00" * 50_000


def jpeg_with_junk(path: Path) -> Path:
    """A JPEG followed by garbage after the end marker: dropping it is lossless."""
    make_image(path, fmt="JPEG", quality=90)
    with path.open("ab") as fh:
        fh.write(JUNK)
    return path


def strip_junk(data: bytes) -> bytes:
    return data[: data.rindex(b"\xff\xd9") + 2]


def jpegoptim_handler(argv: list[str]) -> tuple[int, str, str]:
    target = Path(argv[-1])
    target.write_bytes(strip_junk(target.read_bytes()))
    return 0, "", ""


def jpegtran_handler(argv: list[str]) -> tuple[int, str, str]:
    out = Path(argv[argv.index("-outfile") + 1])
    out.write_bytes(strip_junk(Path(argv[-1]).read_bytes()))
    return 0, "", ""


def resave_png(source: Path, output: Path) -> None:
    with Image.open(source) as image:
        image.save(output, optimize=True, compress_level=9)


def estimate(optimizer: JpegOptimizer | PngOptimizer | GifOptimizer, path: Path,
             workdir: Path):  # type: ignore[no-untyped-def]
    workdir.mkdir(exist_ok=True)
    return optimizer.estimate(path, path.stat(), workdir)


# -- pixel comparison ----------------------------------------------------------------------
def test_images_identical(tmp_path: Path) -> None:
    png = make_image(tmp_path / "a.png")
    same = tmp_path / "b.png"
    resave_png(png, same)
    assert images_identical(png, same)
    other = make_image(tmp_path / "c.png", size=(120, 81))
    assert not images_identical(png, other)
    changed = tmp_path / "d.png"
    with Image.open(png) as image:
        image.putpixel((0, 0), (255, 0, 0))
        image.save(changed)
    assert not images_identical(png, changed)
    as_jpeg = make_image(tmp_path / "e.jpg", fmt="JPEG")
    assert not images_identical(png, as_jpeg)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert not images_identical(png, broken)


def test_images_identical_modes_and_frames(tmp_path: Path) -> None:
    rgb = make_image(tmp_path / "rgb.png", mode="RGB")
    with Image.open(rgb) as image:
        image.convert("RGBA").save(tmp_path / "rgba.png")
    assert images_identical(rgb, tmp_path / "rgba.png")  # mode differs, pixels identical
    palette = make_image(tmp_path / "p.gif", mode="P")
    shutil.copy(palette, tmp_path / "p2.gif")
    assert images_identical(palette, tmp_path / "p2.gif")
    frames = [Image.new("RGB", (10, 10), c) for c in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(tmp_path / "anim.gif", save_all=True, append_images=frames[1:])
    frames[0].save(tmp_path / "still.gif")
    assert not images_identical(tmp_path / "anim.gif", tmp_path / "still.gif")
    other = [Image.new("RGB", (10, 10), c) for c in ((255, 0, 0), (0, 255, 0))]
    other[0].save(tmp_path / "anim2.gif", save_all=True, append_images=other[1:])
    assert not images_identical(tmp_path / "anim.gif", tmp_path / "anim2.gif")
    assert images_identical(tmp_path / "anim.gif", tmp_path / "anim.gif")


# -- JPEG ---------------------------------------------------------------------------------
def test_jpeg_tools_and_signature(config: Config) -> None:
    assert JpegOptimizer(config, FakeTools()).missing_tools() == ["jpegoptim"]
    assert JpegOptimizer(config, FakeTools(["jpegtran"])).missing_tools() == []
    assert JpegOptimizer(config, FakeTools(["jpegoptim"])).unavailable_reason() is None
    assert JpegOptimizer(config, FakeTools()).unavailable_reason() == "missing jpegoptim"
    config.optimize.jpeg_progressive = True
    assert JpegOptimizer(config, FakeTools()).signature() == "jpeg:v1:progressive=True"
    assert JpegOptimizer(config, FakeTools()).accepts(Path("x.JPEG"))
    assert not JpegOptimizer(config, FakeTools()).lossy


@pytest.mark.parametrize("progressive", [False, True])
def test_jpeg_with_jpegoptim(tmp_path: Path, config: Config, progressive: bool) -> None:
    config.optimize.jpeg_progressive = progressive
    tools = FakeTools(["jpegoptim"], jpegoptim=jpegoptim_handler)
    photo = jpeg_with_junk(tmp_path / "photo.jpg")
    est = estimate(JpegOptimizer(config, tools), photo, tmp_path / "work")
    assert est.candidate and est.exact
    assert est.staged is not None and est.staged.parent == tmp_path / "work"
    assert est.saving == len(JUNK)
    assert ("--all-progressive" in tools.calls[0]) is progressive
    assert photo.stat().st_size == est.original_size  # original untouched


@pytest.mark.parametrize("progressive", [False, True])
def test_jpeg_with_jpegtran(tmp_path: Path, config: Config, progressive: bool) -> None:
    config.optimize.jpeg_progressive = progressive
    tools = FakeTools(["jpegtran"], jpegtran=jpegtran_handler)
    est = estimate(JpegOptimizer(config, tools), jpeg_with_junk(tmp_path / "p.jpg"),
                   tmp_path / "w")
    assert est.candidate
    assert ("-progressive" in tools.calls[0]) is progressive


def test_jpeg_rejections(tmp_path: Path, config: Config) -> None:
    work = tmp_path / "w"
    plain = make_image(tmp_path / "plain.jpg", fmt="JPEG")
    no_gain = estimate(JpegOptimizer(config, FakeTools(["jpegoptim"])), plain, work)
    assert no_gain.skip_reason == "no significant gain (0.0 %)"
    assert no_gain.remember and no_gain.staged is None

    def lossy(argv: list[str]) -> tuple[int, str, str]:
        with Image.open(argv[-1]) as image:
            image.load()
            image.save(argv[-1], "JPEG", quality=5)
        return 0, "", ""

    est = estimate(JpegOptimizer(config, FakeTools(["jpegoptim"], jpegoptim=lossy)),
                   jpeg_with_junk(tmp_path / "x.jpg"), work)
    assert est.skip_reason == "rejected: result is not pixel-identical to the original"
    assert not list(work.iterdir())

    def failing(argv: list[str]) -> tuple[int, str, str]:
        return 1, "", "corrupt"

    est = estimate(JpegOptimizer(config, FakeTools(["jpegoptim"], jpegoptim=failing)),
                   plain, work)
    assert est.skip_reason is not None and est.skip_reason.startswith("optimiser error")
    assert not est.remember

    est = estimate(JpegOptimizer(config, FakeTools(["jpegtran"])), plain, work)
    assert est.skip_reason == "already optimal (no smaller output)"


def test_jpeg_prechecks(tmp_path: Path, config: Config) -> None:
    fake = make_image(tmp_path / "fake.jpg", fmt="PNG")
    optimizer = JpegOptimizer(config, FakeTools(["jpegoptim"]))
    est = estimate(optimizer, fake, tmp_path / "w")
    assert est.skip_reason is not None and "not a real JPEG" in est.skip_reason
    folder = tmp_path / "dir.jpg"
    folder.mkdir()
    est = optimizer.estimate(folder, folder.stat(), tmp_path / "w")
    assert est.skip_reason is not None and est.skip_reason.startswith("unreadable file")
    assert not est.remember


# -- PNG ----------------------------------------------------------------------------------
def test_png_tools(config: Config) -> None:
    assert PngOptimizer(config, FakeTools()).missing_tools() == ["optipng"]
    assert PngOptimizer(config, FakeTools(["oxipng"])).missing_tools() == []


@pytest.mark.parametrize("tool", ["oxipng", "optipng"])
def test_png_optimisation(tmp_path: Path, config: Config, tool: str) -> None:
    def handler(argv: list[str]) -> tuple[int, str, str]:
        out = argv.index("--out" if tool == "oxipng" else "-out") + 1
        resave_png(Path(argv[-1]), Path(argv[out]))
        return 0, "", ""

    image = make_image(tmp_path / "pic.png", size=(300, 200), compress_level=0)
    tools = FakeTools([tool], **{tool: handler})
    est = estimate(PngOptimizer(config, tools), image, tmp_path / "w")
    assert est.candidate and est.saving > 0
    assert tools.calls[0][0] == tool


def test_png_prechecks(tmp_path: Path, config: Config) -> None:
    optimizer = PngOptimizer(config, FakeTools(["optipng"]))
    frames = [Image.new("RGB", (8, 8), c) for c in ((0, 0, 0), (255, 255, 255))]
    frames[0].save(tmp_path / "anim.png", save_all=True, append_images=frames[1:])
    assert optimizer.precheck(tmp_path / "anim.png") == "animated PNG (left untouched)"
    magic = b"\x89PNG\r\n\x1a\n"
    (tmp_path / "actl_only.png").write_bytes(magic + b"acTL")
    assert optimizer.precheck(tmp_path / "actl_only.png") == "animated PNG (left untouched)"
    (tmp_path / "late.png").write_bytes(magic + b"IDAT....acTL")
    assert optimizer.precheck(tmp_path / "late.png") is None
    (tmp_path / "bad.png").write_bytes(b"GIF89a")
    assert "not a real PNG" in str(optimizer.precheck(tmp_path / "bad.png"))


# -- GIF ----------------------------------------------------------------------------------
def test_gif(tmp_path: Path, config: Config) -> None:
    assert GifOptimizer(config, FakeTools()).missing_tools() == ["gifsicle"]
    image = make_image(tmp_path / "a.gif", mode="P")
    with image.open("ab") as fh:
        fh.write(b"\x00" * 10_000)  # trailing garbage after the trailer

    def gifsicle(argv: list[str]) -> tuple[int, str, str]:
        data = Path(argv[-3]).read_bytes()
        Path(argv[-1]).write_bytes(data[:-10_000])
        return 0, "", ""

    tools = FakeTools(["gifsicle"], gifsicle=gifsicle)
    est = estimate(GifOptimizer(config, tools), image, tmp_path / "w")
    assert est.candidate and est.saving == 10_000


def test_estimate_properties(tmp_path: Path, config: Config) -> None:
    optimizer = GifOptimizer(config, FakeTools())
    est = optimizer.new_estimate(tmp_path, tmp_path.stat())
    assert not est.candidate
    assert est.saving == 0 and est.saving_percent == 0.0
    est.estimated_size = est.original_size
    assert est.candidate


def test_tool_error_type() -> None:
    assert issubclass(ToolError, RuntimeError)
