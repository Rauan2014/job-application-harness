"""Schemas Pydantic v2, enums e exceções de domínio do harness."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    """Estados de uma vaga na fila."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SKIPPED = "SKIPPED"


class Job(BaseModel):
    """Uma vaga lida da planilha."""

    id: str
    row_index: int
    date: str = ""
    company: str
    title: str
    modality: str = ""
    url: str | None = None
    ats: str | None = None
    description: str | None = None
    fit_score: int | None = None
    resume_label: str | None = None
    base_used: list[str] = Field(default_factory=list)
    stack: list[str] = Field(default_factory=list)
    sheet_status: str = ""
    language: Literal["pt", "en"] = "pt"


class Profile(BaseModel):
    """Dados do candidato (`data/profile.json`). Única fonte de fatos para o LLM."""

    full_name: str
    email: str
    phone: str
    location: str
    links: dict[str, str] = Field(default_factory=dict)
    work_authorization: str = ""
    salary_expectation: str | None = None
    skills: list[str] = Field(default_factory=list)
    headline: str | None = None
    current_company: str | None = None
    education_school: str | None = None
    education_degree: str | None = None
    education_level: str | None = None
    graduation_year: str | None = None
    cover_letter_pt: str | None = None
    cover_letter_en: str | None = None
    base_answers: dict[str, str] = Field(default_factory=dict)
    eeo: dict[str, str] = Field(default_factory=dict)


class FormQuestion(BaseModel):
    """Uma pergunta de formulário de ATS."""

    id: str
    label: str
    kind: Literal["text", "textarea", "select", "checkbox"] = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)


class PageInfo(BaseModel):
    """Resultado da fase `inspect` de um handler."""

    description: str = ""
    questions: list[FormQuestion] = Field(default_factory=list)
    state_summary: str = ""
    page_signals: str = ""


class ResumeInfo(BaseModel):
    """Currículo local com texto extraído e resumo determinístico."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    summary: str
    text: str


class ResumeChoice(BaseModel):
    """Escolha de currículo feita pelo LLM (fallback do DP2)."""

    filename: str
    reason: str = ""


class AnswerSet(BaseModel):
    """Respostas do LLM; chave = `FormQuestion.id`, `None` = sem base nos fatos."""

    answers: dict[str, str | None] = Field(default_factory=dict)


class ApplicationResult(BaseModel):
    """Resultado final do processamento de uma vaga."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: str
    status: TaskStatus
    reason: str | None = None
    dry_run: bool = True
    submitted: bool = False
    resume_used: str | None = None
    screenshot_path: Path | None = None
    attempts: int = 1
    duration_s: float | None = None
    tokens_used: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    started_at: str | None = None
    completed_at: str | None = None


# ---------------------------------------------------------------- exceções


class HarnessError(Exception):
    """Base das exceções de domínio."""


class DriveAccessError(HarnessError):
    """Pasta/arquivo do Drive inacessível (403/404) ou falha de autenticação."""


class LLMOutputError(HarnessError):
    """Saída do LLM inválida após esgotar as tentativas."""


class BlockerDetected(HarnessError):
    """CAPTCHA, login obrigatório ou verificação detectados na página."""


class TransientError(HarnessError):
    """Erro transitório (rede, timeout) antes do submit: pode ser re-tentado."""


class PostSubmitError(HarnessError):
    """Falha depois do clique em submit: nunca re-tentar."""


class DecisionUnavailable(HarnessError):
    """O modelo de decisão (Laya) não pôde responder; aplicar o fallback do DP."""


class SheetFormatError(HarnessError):
    """Planilha sem cabeçalho reconhecível."""


class PdfTextError(HarnessError):
    """PDF ilegível ou sem texto extraível."""
