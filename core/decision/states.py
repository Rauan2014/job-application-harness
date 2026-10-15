"""Montagem dos estados para cada ponto de decisão (DP1 a DP6).

Invariantes:
- Chaves em ordem de importância (mais importante primeiro).
- Sem PII (sem nome, email, telefone, endereço, valores digitados de campos).
- Respeita o orçamento LAYA_STATE_MAX_CHARS.
- Rótulos de opções curtos (<= 40 caracteres) e distintos.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from core.models import FormQuestion, Job, PageInfo, Profile


def _clean_str(text: str | None, max_len: int = 1500) -> str:
    if not text:
        return ""
    # Remove espaços excessivos
    cleaned = " ".join(text.split()).strip()
    return cleaned[:max_len]


def clean_option_label(label: str) -> str:
    """Garante rótulo curto (<= 40 chars), sem espaços duplicados."""
    norm = unicodedata.normalize("NFC", label)
    clean = " ".join(norm.split())
    return clean[:40].strip()


def build_dp1_job_fit_state(job: Job, profile: Profile, max_chars: int = 1500) -> dict[str, str]:
    """DP1: Compatibilidade do perfil com a vaga. Sem PII."""
    desc_budget = max(200, max_chars - 600)
    return {
        "job_title": _clean_str(job.title, 150),
        "job_company": _clean_str(job.company, 100),
        "job_stack": _clean_str(", ".join(job.stack), 250),
        "profile_skills": _clean_str(", ".join(profile.skills), 250),
        "profile_headline": _clean_str(profile.headline or "", 150),
        "job_description": _clean_str(job.description or "", desc_budget),
    }


def build_dp2_resume_fallback_state(
    job: Job, job_description: str, max_chars: int = 1500
) -> dict[str, str]:
    """DP2: Escolha de currículo base substituto."""
    desc_budget = max(200, max_chars - 400)
    return {
        "job_title": _clean_str(job.title, 150),
        "job_company": _clean_str(job.company, 100),
        "job_stack": _clean_str(", ".join(job.stack), 200),
        "job_description": _clean_str(job_description, desc_budget),
    }


def build_dp3_blocker_state(page_info: PageInfo, max_chars: int = 1500) -> dict[str, str]:
    """DP3: Verificação de bloqueios (CAPTCHA, login, etc.)."""
    return {
        "page_signals": _clean_str(page_info.page_signals, max_chars),
        "state_summary": _clean_str(page_info.state_summary, max_chars),
    }


def build_dp4_sensitivity_state(question: FormQuestion, max_chars: int = 1500) -> dict[str, str]:
    """DP4: Sensibilidade da pergunta (EEO)."""
    options_summary = ", ".join(question.options) if question.options else "none"
    return {
        "question_label": _clean_str(question.label, 300),
        "question_kind": _clean_str(question.kind, 50),
        "question_options": _clean_str(options_summary, max_chars - 400),
    }


def build_dp5_field_mapping_state(question: FormQuestion, max_chars: int = 1500) -> dict[str, str]:
    """DP5: Mapeamento de pergunta para atributo do perfil."""
    options_summary = ", ".join(question.options) if question.options else "none"
    return {
        "question_label": _clean_str(question.label, 300),
        "question_kind": _clean_str(question.kind, 50),
        "question_options": _clean_str(options_summary, max_chars - 400),
    }


def build_dp6_ready_to_submit_state(
    questions: list[FormQuestion],
    filled: dict[str, Any],
    visible_errors: list[str],
    max_chars: int = 1500,
) -> dict[str, str]:
    """DP6: Prontidão para envio (sem PII: apenas preenchido sim/não)."""
    field_lines: list[str] = []
    missing_req: list[str] = []
    for q in questions:
        is_filled = bool(filled.get(q.id) and str(filled.get(q.id)).strip())
        status_txt = "filled" if is_filled else "empty"
        req_txt = "required" if q.required else "optional"
        field_lines.append(f"{q.label} ({req_txt}): {status_txt}")
        if q.required and not is_filled:
            missing_req.append(q.label)

    summary_text = " | ".join(field_lines)
    errors_text = " | ".join(visible_errors) if visible_errors else "none"

    return {
        "missing_required": _clean_str(", ".join(missing_req) or "none", 300),
        "visible_errors": _clean_str(errors_text, 300),
        "fields_summary": _clean_str(summary_text, max_chars - 700),
    }
