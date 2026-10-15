"""Testes unitários para extração e sumarização de PDF (pdf_text)."""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from core.models import PdfTextError
from core.pdf_text import extract_text, load_resumes, summarize


def test_extract_text_valid_pdf(sample_resume_pdf: Path) -> None:
    text = extract_text(sample_resume_pdf)
    assert "John Doe" in text
    assert "Senior AI Engineer" in text


def test_corrupted_pdf_raises_pdf_text_error(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a valid pdf header")

    with pytest.raises(PdfTextError):
        extract_text(corrupt)


def test_empty_text_pdf_raises_pdf_text_error(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    blank = tmp_path / "blank.pdf"
    with blank.open("wb") as f:
        writer.write(f)

    with pytest.raises(PdfTextError, match="sem texto extraível"):
        extract_text(blank)


def test_summarize_truncation() -> None:
    text = (
        "Senior AI Engineer with 10 years of experience building autonomous agents and pipelines.\n"
        "Expert in Python, TypeScript, LangChain, Next.js, and browser automation.\n"
        "Delivered multi-agent architectures reducing operational costs by 40%."
    )
    res_short = summarize(text, max_chars=50)
    assert len(res_short) <= 50
    # Não corta no meio de uma palavra se houver espaço razoável
    assert not res_short.endswith(" ")
    assert "Senior AI" in res_short

    # Menor que o limite mantém o texto completo com quebras tratadas
    res_full = summarize(text, max_chars=1000)
    assert "Senior AI Engineer" in res_full
    assert "Next.js" in res_full


def test_load_resumes_skips_invalid(tmp_path: Path, sample_resume_pdf: Path) -> None:
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"garbage")

    paths = {
        "valid.pdf": sample_resume_pdf,
        "bad.pdf": bad_pdf,
    }
    loaded = load_resumes(paths)
    assert "valid.pdf" in loaded
    assert "bad.pdf" not in loaded
    assert "John Doe" in loaded["valid.pdf"].text
