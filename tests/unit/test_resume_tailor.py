"""Testes para ResumeTailor (trava anti-mentiras), PDF ReportLab e rota de download."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from core.models import ExperienceItem, Job, Profile, ProjectItem
from core.pdf_text import extract_text
from core.resume_pdf import generate_tailored_pdf
from core.resume_tailor import ResumeTailor
from web.app import app


def _sample_profile() -> Profile:
    return Profile(
        full_name="Maria Silva",
        email="maria@example.com",
        phone="+55 11 99999-0000",
        location="São Paulo, SP",
        city="São Paulo, SP",
        headline="Engenheira de Software Python",
        summary="Desenvolvedora backend com experiência em APIs e dados.",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker", "React"],
        experiences=[
            ExperienceItem(
                company="Empresa Alpha",
                role="Dev Python Sênior",
                period="2023 - Atual",
                highlights=["Construiu APIs em FastAPI e filas assíncronas com PostgreSQL."],
                skills=["Python", "FastAPI", "PostgreSQL"],
            ),
            ExperienceItem(
                company="Agência Beta",
                role="Dev Frontend",
                period="2021 - 2023",
                highlights=["Criou interfaces web com React."],
                skills=["React"],
            ),
        ],
        projects=[
            ProjectItem(
                name="Pipeline Crawler",
                description="Coletor assíncrono em Python e Docker.",
                skills=["Python", "Docker"],
            )
        ],
        languages=["Português (Nativo)", "Inglês (Avançado)"],
        certifications=["AWS Cloud Practitioner"],
    )


def _sample_job() -> Job:
    return Job(
        id="job-py-99",
        company="Nubank",
        title="Backend Engineer Python",
        url="https://boards.greenhouse.io/nubank/jobs/99",
        description="Buscamos especialista em Python, FastAPI, PostgreSQL e Kubernetes.",
        stack=["Python", "FastAPI", "PostgreSQL"],
        language="pt",
    )


def _test_settings() -> Settings:
    return Settings(
        DRY_RUN=True,
        NO_LLM=True,
        OPENROUTER_API_KEY="sk-test",
        GOOGLE_SHEETS_ID="sheet-test",
        GOOGLE_SERVICE_ACCOUNT_JSON="./sa.json",
    )


@pytest.mark.asyncio
async def test_resume_tailor_deterministic_and_anti_hallucination():
    settings = _test_settings()
    tailor = ResumeTailor(settings=settings)
    profile = _sample_profile()
    job = _sample_job()

    spec = await tailor.build_spec(job, profile)
    assert "Python" in spec.matched_skills
    assert "FastAPI" in spec.matched_skills
    assert spec.experiences[0].company == "Empresa Alpha"  # ranqueada acima por overlap de skills

    # Simula LLM tentando injetar skill inventada ("Rust") e empresa inexistente ("Google Fake")
    malicious_llm = {
        "headline_adapted": "Backend Python / FastAPI",
        "summary": "Experiência sólida em Python e FastAPI.",
        "prioritized_skills": ["Rust", "Kubernetes", "FastAPI", "Python"],
        "selected_companies": ["Google Fake", "Empresa Alpha"],
        "selected_projects": ["Projeto Inventado", "Pipeline Crawler"],
    }
    sanitized = tailor._enforce_anti_hallucination(malicious_llm, spec, profile)

    assert "Rust" not in sanitized.prioritized_skills
    assert "Kubernetes" not in sanitized.prioritized_skills
    assert sanitized.prioritized_skills[:2] == ["FastAPI", "Python"]
    assert [e.company for e in sanitized.experiences] == ["Empresa Alpha", "Agência Beta"]
    assert [p.name for p in sanitized.projects] == ["Pipeline Crawler"]


@pytest.mark.asyncio
async def test_generate_tailored_pdf_produces_ats_readable_pdf(tmp_path: Path):
    settings = _test_settings()
    tailor = ResumeTailor(settings=settings)
    profile = _sample_profile()
    job = _sample_job()

    spec = await tailor.build_spec(job, profile)
    resume_info = generate_tailored_pdf(spec, profile, tmp_path)

    assert resume_info.path.exists()
    assert resume_info.path.stat().st_size > 1000

    extracted = extract_text(resume_info.path)
    assert "Maria Silva" in extracted
    assert "Empresa Alpha" in extracted
    assert "FastAPI" in extracted
    assert "Pipeline Crawler" in extracted


def test_generated_resume_download_route_security_and_success(tmp_path: Path, monkeypatch):
    pdf_file = tmp_path / "job_py_99_nubank.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake content")

    class _FakeSettings:
        GENERATED_RESUMES_DIR = tmp_path

    monkeypatch.setattr("web.app.load_settings", lambda: _FakeSettings())
    client = TestClient(app)

    bad_ext = client.get("/api/resumes/generated/secret.txt")
    assert bad_ext.status_code == 400

    traversal = client.get("/api/resumes/generated/..%2F..%2Fsecret.pdf")
    assert traversal.status_code in (400, 403, 404)

    missing = client.get("/api/resumes/generated/nao_existe.pdf")
    assert missing.status_code == 404

    ok = client.get("/api/resumes/generated/job_py_99_nubank.pdf")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/pdf"
