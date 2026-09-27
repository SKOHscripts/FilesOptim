from __future__ import annotations

from pathlib import Path

import pytest

from filesoptim.config import Config
from filesoptim.optimize.pdf import PdfOptimizer
from tests.conftest import FakeTools

BODY = b"%PDF-1.4\n" + b"1 0 obj << /Type /Page >> endobj\n" * 2000


def pdf(tmp_path: Path, extra: bytes = b"", name: str = "doc.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(BODY + extra)
    return path


class Qpdf:
    """Fake qpdf: optimisation halves the file, --check and --show-npages are scriptable."""

    def __init__(self, *, code: int = 0, check: int = 0, pages: tuple[str, str] = ("3", "3"),
                 output: bool = True) -> None:
        self.code, self.check, self.pages, self.output = code, check, list(pages), output

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        if argv[1] == "--check":
            return self.check, "", ""
        if argv[1] == "--show-npages":
            return 0, self.pages.pop(0) + "\n", ""
        if self.output:
            data = Path(argv[-2]).read_bytes()
            Path(argv[-1]).write_bytes(data[: len(data) // 2])
        return self.code, "", "qpdf: damaged file"


def estimate(opt: PdfOptimizer, path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    work = tmp_path / "w"
    work.mkdir(exist_ok=True)
    return opt.estimate(path, path.stat(), work)


def test_modes_and_tools(config: Config) -> None:
    opt = PdfOptimizer(config, FakeTools())
    assert opt.missing_tools() == ["qpdf"]
    assert not opt.lossy
    assert opt.signature() == "pdf:v1:lossless"
    assert opt.min_saving() == (1.0, 2048)
    config.pdf.mode = "ebook"
    assert opt.lossy
    assert opt.missing_tools() == ["qpdf", "gs"]
    assert PdfOptimizer(config, FakeTools(["qpdf", "gs"])).missing_tools() == []
    assert opt.min_saving() == (10.0, 2048)


@pytest.mark.parametrize("code", [0, 3])
def test_lossless_success(tmp_path: Path, config: Config, code: int) -> None:
    tools = FakeTools(["qpdf"], qpdf=Qpdf(code=code))
    est = estimate(PdfOptimizer(config, tools), pdf(tmp_path), tmp_path)
    assert est.candidate
    assert est.saving_percent == pytest.approx(50, abs=1)


def test_lossy_mode_uses_ghostscript(tmp_path: Path, config: Config) -> None:
    config.pdf.mode = "printer"

    def gs(argv: list[str]) -> tuple[int, str, str]:
        out = next(a for a in argv if a.startswith("-sOutputFile="))
        Path(out.split("=", 1)[1]).write_bytes(BODY[:1000])
        return 0, "", ""

    tools = FakeTools(["qpdf", "gs"], qpdf=Qpdf(), gs=gs)
    est = estimate(PdfOptimizer(config, tools), pdf(tmp_path), tmp_path)
    assert est.candidate and est.lossy
    assert "-dPDFSETTINGS=/printer" in tools.calls[0]


def test_prechecks(tmp_path: Path, config: Config) -> None:
    tools = FakeTools(["qpdf"], qpdf=Qpdf())
    opt = PdfOptimizer(config, tools)
    assert estimate(opt, pdf(tmp_path, b"/Encrypt 5 0 R", "e.pdf"), tmp_path).skip_reason == (
        "encrypted PDF (left untouched)"
    )
    signed = pdf(tmp_path, b"/ByteRange [0 1 2 3]", "s.pdf")
    assert "digitally signed" in str(estimate(opt, signed, tmp_path).skip_reason)
    config.pdf.skip_signed = False
    assert estimate(opt, signed, tmp_path).candidate
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"<html>")
    assert "not a real PDF" in str(estimate(opt, fake, tmp_path).skip_reason)


def test_rejections(tmp_path: Path, config: Config) -> None:
    source = pdf(tmp_path)
    failing = FakeTools(["qpdf"], qpdf=Qpdf(code=2))
    est = estimate(PdfOptimizer(config, failing), source, tmp_path)
    assert str(est.skip_reason).startswith("optimiser error (qpdf failed")
    bad_check = FakeTools(["qpdf"], qpdf=Qpdf(check=2))
    assert estimate(PdfOptimizer(config, bad_check), source, tmp_path).skip_reason == (
        "rejected: optimised PDF failed the structure check"
    )
    pages = FakeTools(["qpdf"], qpdf=Qpdf(pages=("3", "2")))
    assert estimate(PdfOptimizer(config, pages), source, tmp_path).skip_reason == (
        "rejected: page count changed"
    )
    garbage = FakeTools(["qpdf"], qpdf=Qpdf(pages=("oops", "3")))
    assert "cannot count pages" in str(
        estimate(PdfOptimizer(config, garbage), source, tmp_path).skip_reason
    )
