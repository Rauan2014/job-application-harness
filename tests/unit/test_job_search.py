"""Testes unitários para o módulo de busca de vagas (core.job_search).

Valida detecção de ATS, senioridade, formatação de data brasileira,
extração de links ATS embutidos e conversão para o modelo de domínio Job.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from core.job_search import (
    detect_ats,
    detect_seniority,
    extract_ats_link,
    fetch_brazil_tech_jobs,
    format_brazilian_date,
    jobs_to_domain_models,
    search_jobs,
)
from core.models import Job


def test_detect_ats_various_platforms() -> None:
    assert detect_ats("https://boards.greenhouse.io/corp/jobs/123") == "Greenhouse"
    assert detect_ats("https://jobs.lever.co/company/abc") == "Lever"
    assert detect_ats("https://empresa.gupy.io/job/10") == "Gupy"
    assert detect_ats("https://apply.workable.com/j/123") == "Workable"
    assert detect_ats("https://corp.myworkdayjobs.com/en-US/careers") == "Workday"
    assert detect_ats("https://jobs.ashbyhq.com/company/xyz") == "Ashby"
    assert detect_ats("https://jobs.smartrecruiters.com/company/1") == "SmartRecruiters"
    assert detect_ats("https://indeed.com/viewjob?jk=123") == "Indeed"
    assert detect_ats("https://minhaempresa.com.br/carreiras") == "Direct / Career Site"


def test_extract_ats_link_from_markdown_and_regex() -> None:
    body_greenhouse = "Venha trabalhar conosco! Link para aplicação: https://boards.greenhouse.io/acme/jobs/9981"
    assert extract_ats_link(body_greenhouse, "fallback") == "https://boards.greenhouse.io/acme/jobs/9981"

    body_markdown = "Para se candidatar acesse [Inscreva-se aqui](https://careers.google.com/jobs/results/123)"
    assert extract_ats_link(body_markdown, "fallback") == "https://careers.google.com/jobs/results/123"

    body_empty = "Sem link direto no texto"
    assert extract_ats_link(body_empty, "https://github.com/issue/1") == "https://github.com/issue/1"


def test_detect_seniority_levels() -> None:
    assert detect_seniority("Estagiário de Engenharia de Software") == "Estágio"
    assert detect_seniority("Intern Backend Engineer") == "Estágio"
    assert detect_seniority("Desenvolvedor Python Júnior") == "Júnior"
    assert detect_seniority("Entry-level Data Scientist") == "Júnior"
    assert detect_seniority("Senior Full Stack Engineer") == "Sênior"
    assert detect_seniority("Engenheiro de Software Sr.") == "Sênior"
    assert detect_seniority("Tech Lead / Staff Engineer") == "Staff / Lead"
    assert detect_seniority("Principal Architect") == "Staff / Lead"
    assert detect_seniority("Desenvolvedor Node.js Pleno") == "Pleno"
    assert detect_seniority("Software Engineer") == "Pleno"


def test_format_brazilian_date() -> None:
    d = datetime.datetime(2026, 10, 27, 14, 30, 0)
    assert format_brazilian_date(d) == "27/10/2026"


def test_jobs_to_domain_models_populates_spreadsheet_and_strategic_fields() -> None:
    raw = [
        {
            "title": "Backend Python Developer",
            "company": "Tech Inovação",
            "job_url": "https://boards.greenhouse.io/techinovacao/jobs/101",
            "ats": "Greenhouse",
            "modality": "100% Remoto",
            "seniority": "Sênior",
            "location": "Brasil / Remoto",
            "salary": "R$ 15.000 - R$ 20.000",
            "source_platform": "github_tech_br",
            "external_job_id": "gh-9981",
            "posted_at": "2026-10-27T10:00:00Z",
            "job_date": "27/10/2026",
            "resume_label": "Currículo Otimizado - Tech Inovação",
            "base_used": ["Base Português", "Cognition"],
            "stack": ["Python", "FastAPI", "PostgreSQL"],
            "description": "Desenvolvimento de APIs de alta escala.",
        }
    ]

    domain_jobs = jobs_to_domain_models(raw)
    assert len(domain_jobs) == 1
    job = domain_jobs[0]

    assert isinstance(job, Job)
    assert job.title == "Backend Python Developer"
    assert job.company == "Tech Inovação"
    assert job.url == "https://boards.greenhouse.io/techinovacao/jobs/101"
    assert job.ats == "Greenhouse"
    assert job.modality == "100% Remoto"
    assert job.seniority == "Sênior"
    assert job.salary == "R$ 15.000 - R$ 20.000"
    assert job.source_platform == "github_tech_br"
    assert job.external_job_id == "gh-9981"
    assert job.date == "27/10/2026"
    assert job.resume_label == "Currículo Otimizado - Tech Inovação"
    assert job.base_used == ["Base Português", "Cognition"]
    assert job.stack == ["Python", "FastAPI", "PostgreSQL"]
    assert job.sheet_status == "A Fazer"
    assert job.row_index == 1


@respx.mock
@pytest.mark.asyncio
async def test_fetch_brazil_tech_jobs_mocked() -> None:
    mock_issues = [
        {
            "id": 123456,
            "title": "[Remoto] Desenvolvedor Python @ Fintech Alpha",
            "body": "Vaga remota. Inscrições em https://jobs.lever.co/alpha/eng-1",
            "labels": [{"name": "Python"}, {"name": "Remoto"}],
            "html_url": "https://github.com/backend-br/vagas/issues/123",
            "created_at": "2026-10-27T08:00:00Z",
        }
    ]

    respx.get("https://api.github.com/repos/frontendbr/vagas/issues?state=open&per_page=20").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("https://api.github.com/repos/backend-br/vagas/issues?state=open&per_page=20").mock(
        return_value=httpx.Response(200, json=mock_issues)
    )

    jobs = await fetch_brazil_tech_jobs(limit=5, query="Python")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["company"] == "Fintech Alpha"
    assert job["job_url"] == "https://jobs.lever.co/alpha/eng-1"
    assert job["ats"] == "Lever"
    assert job["modality"] == "100% Remoto"
    assert "Python" in job["stack"]
    assert job["seniority"] == "Pleno"
    assert job["source_platform"] == "github_tech_br"
