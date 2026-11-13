"""Gerador de PDF ATS-friendly em ReportLab a partir de TailoredResumeSpec."""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from core.models import Profile, ResumeInfo
from core.pdf_text import extract_text, summarize
from core.resume_tailor import TailoredResumeSpec

_LABELS = {
    "pt": {
        "summary": "RESUMO PROFISSIONAL",
        "skills": "HABILIDADES TÉCNICAS",
        "experience": "EXPERIÊNCIA PROFISSIONAL",
        "projects": "PROJETOS RELEVANTES",
        "education": "FORMAÇÃO ACADÊMICA",
        "languages": "IDIOMAS & CERTIFICAÇÕES",
    },
    "en": {
        "summary": "PROFESSIONAL SUMMARY",
        "skills": "TECHNICAL SKILLS",
        "experience": "EXPERIENCE",
        "projects": "RELEVANT PROJECTS",
        "education": "EDUCATION",
        "languages": "LANGUAGES & CERTIFICATIONS",
    },
}


def _safe_pdf_text(text: str) -> str:
    """Escapa XML e substitui caracteres fora do WinAnsi/cp1252 para evitar falhas no ReportLab."""
    cleaned = (text or "").replace("\r\n", "\n").strip()
    cleaned = cleaned.replace("—", "-").replace("–", "-").replace("•", "-")
    encoded = cleaned.encode("cp1252", errors="ignore").decode("cp1252")
    return html.escape(encoded)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "vaga").lower()).strip("_")
    return slug[:40] or "vaga"


def generate_tailored_pdf(
    spec: TailoredResumeSpec,
    profile: Profile,
    output_dir: Path,
) -> ResumeInfo:
    """Renderiza currículo em PDF ATS-friendly (1 coluna) via ReportLab e retorna `ResumeInfo`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _slugify(spec.job_id)
    company_slug = _slugify(spec.target_company)
    out_path = output_dir / f"{safe_id}_{company_slug}.pdf"

    lang_key = "en" if spec.language == "en" else "pt"
    labels = _LABELS[lang_key]

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "CandidateName",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=2,
        alignment=TA_LEFT,
    )
    headline_style = ParagraphStyle(
        "CandidateHeadline",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#1E3A8A"),
        spaceAfter=3,
    )
    contact_style = ParagraphStyle(
        "CandidateContact",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#475569"),
        spaceAfter=6,
    )
    section_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=8,
        spaceAfter=3,
    )
    body_style = ParagraphStyle(
        "BodyTextATS",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.2,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=3,
    )
    role_style = ParagraphStyle(
        "RoleHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=4,
        spaceAfter=1,
    )
    meta_style = ParagraphStyle(
        "RoleMeta",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#475569"),
        spaceAfter=2,
    )
    bullet_style = ParagraphStyle(
        "BulletATS",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.0,
        leading=12.5,
        leftIndent=10,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=2,
    )

    story = []

    # Cabeçalho
    story.append(Paragraph(_safe_pdf_text(profile.full_name), name_style))
    story.append(Paragraph(_safe_pdf_text(spec.headline_adapted), headline_style))

    contact_parts = [p for p in (profile.location, profile.email, profile.phone) if p]
    for k in ("linkedin", "github", "portfolio", "website"):
        link_val = (profile.links or {}).get(k)
        if link_val:
            contact_parts.append(link_val)
    story.append(Paragraph(_safe_pdf_text(" | ".join(contact_parts)), contact_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#CBD5E1"), spaceAfter=6))

    # Resumo
    if spec.summary:
        story.append(Paragraph(labels["summary"], section_style))
        story.append(Paragraph(_safe_pdf_text(spec.summary), body_style))

    # Habilidades Técnicas (matched em negrito primeiro)
    if spec.prioritized_skills:
        story.append(Paragraph(labels["skills"], section_style))
        matched_set = {s.lower() for s in spec.matched_skills}
        formatted_skills = [
            f"<b>{_safe_pdf_text(s)}</b>" if s.lower() in matched_set else _safe_pdf_text(s)
            for s in spec.prioritized_skills[:20]
        ]
        story.append(Paragraph(" | ".join(formatted_skills), body_style))

    # Experiências
    if spec.experiences:
        story.append(Paragraph(labels["experience"], section_style))
        for exp in spec.experiences[:5]:
            story.append(Paragraph(_safe_pdf_text(f"{exp.role} - {exp.company}"), role_style))
            meta_parts = [p for p in (exp.period, exp.location) if p]
            if meta_parts:
                story.append(Paragraph(_safe_pdf_text(" | ".join(meta_parts)), meta_style))
            for hl in exp.highlights[:5]:
                story.append(Paragraph(f"- {_safe_pdf_text(hl)}", bullet_style))
            if exp.skills:
                story.append(
                    Paragraph(
                        _safe_pdf_text(f"Stack: {', '.join(exp.skills[:10])}"),
                        meta_style,
                    )
                )
            story.append(Spacer(1, 2))

    # Projetos
    if spec.projects:
        story.append(Paragraph(labels["projects"], section_style))
        for proj in spec.projects[:4]:
            title_line = f"{proj.name} ({proj.url})" if proj.url else proj.name
            story.append(Paragraph(_safe_pdf_text(title_line), role_style))
            story.append(Paragraph(_safe_pdf_text(proj.description), body_style))
            if proj.skills:
                story.append(Paragraph(_safe_pdf_text(f"Stack: {', '.join(proj.skills[:8])}"), meta_style))

    # Formação
    if spec.education_line:
        story.append(Paragraph(labels["education"], section_style))
        story.append(Paragraph(_safe_pdf_text(spec.education_line), body_style))

    # Idiomas e Certificações
    extra_lines: list[str] = []
    if spec.languages:
        extra_lines.append(" | ".join(spec.languages))
    if spec.certifications:
        extra_lines.append(" | ".join(spec.certifications))
    if extra_lines:
        story.append(Paragraph(labels["languages"], section_style))
        for line in extra_lines:
            story.append(Paragraph(_safe_pdf_text(line), body_style))

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{profile.full_name} - {spec.target_title}",
        author=profile.full_name,
    )
    doc.build(story)

    extracted = extract_text(out_path)
    return ResumeInfo(
        path=out_path,
        summary=summarize(extracted),
        text=extracted,
    )
