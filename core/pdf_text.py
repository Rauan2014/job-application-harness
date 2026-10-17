"""Extração de texto de PDF e resumo determinístico (sem LLM)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from core.models import PdfTextError, ResumeInfo

log = logging.getLogger(__name__)


def extract_text(path: Path) -> str:
    """Extrai o texto de todas as páginas. PDF ilegível/sem texto → `PdfTextError`."""
    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            try:
                decrypted = reader.decrypt("")
                if decrypted == 0:
                    raise PdfTextError(f"PDF criptografado ou protegido por senha: {path.name}")
            except PdfTextError:
                raise
            except Exception as exc:
                raise PdfTextError(f"PDF criptografado ou protegido por senha: {path.name}: {exc}") from exc
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfTextError:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, TypeError, Exception) as exc:
        raise PdfTextError(f"PDF ilegível ou corrompido: {path.name}: {exc}") from exc
    text = re.sub(r"[ \t]+", " ", "\n".join(pages))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise PdfTextError(f"PDF sem texto extraível: {path.name}")
    return text


def summarize(text: str, max_chars: int = 600) -> str:
    """Resumo determinístico: primeiras linhas não vazias até `max_chars` (corta em palavra)."""
    if max_chars <= 0:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = " | ".join(lines)
    if len(joined) <= max_chars:
        return joined
    cut = joined[:max_chars]
    space = cut.rfind(" ")
    if space > max_chars // 2:
        cut = cut[:space]
    return cut.rstrip(" |")


def load_resumes(paths: dict[str, Path], summary_chars: int = 600) -> dict[str, ResumeInfo]:
    """Carrega vários currículos; os ilegíveis são excluídos com warning."""
    out: dict[str, ResumeInfo] = {}
    for name, path in paths.items():
        try:
            text = extract_text(path)
        except PdfTextError as exc:
            log.warning("currículo excluído: %s", exc)
            continue
        out[name] = ResumeInfo(path=path, summary=summarize(text, summary_chars), text=text)
    return out


# Aliases para compatibilidade
extract_pdf_text = extract_text
build_resume_summary = summarize

