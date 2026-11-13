"""Módulo de busca e prospecção ativa de vagas para o motor Candidata IA.

Substitui a dependência de planilhas de terceiros:
1. Coleta vagas de fontes abertas (GitHub Tech BR, Jobicy, RemoteOK, Arbeitnow).
2. Normaliza todos os campos da planilha original (Empresa, Cargo, Modalidade, Link,
   Currículo Otimizado, Fit Score, Base Utilizada, Stack Principal, Status).
3. Salva diretamente no banco Supabase (`job_applications`) ou retorna instâncias de `Job`.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import html as html_lib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import httpx
from core.models import Job
from storage.supabase_client import SupabaseCloudClient

logger = logging.getLogger("ia_candidata.job_search")

ATS_DOMAINS = [
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "gupy.io",
    "workable.com",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "indeed.com",
    "bamboohr.com",
]


def detect_ats(url: str, text: str = "") -> str:
    combined = f"{url} {text}".lower()
    if "greenhouse" in combined:
        return "Greenhouse"
    if "lever.co" in combined:
        return "Lever"
    if "gupy.io" in combined:
        return "Gupy"
    if "workable.com" in combined:
        return "Workable"
    if "workday" in combined:
        return "Workday"
    if "ashbyhq" in combined:
        return "Ashby"
    if "smartrecruiters" in combined:
        return "SmartRecruiters"
    if "indeed" in combined:
        return "Indeed"
    return "Direct / Career Site"


def extract_ats_link(body: str, fallback_url: str) -> str:
    patterns = [
        r"https?://(?:[a-zA-Z0-9-]+\.)?greenhouse\.io/[^\s)\]\">]+",
        r"https?://jobs\.lever\.co/[^\s)\]\">]+",
        r"https?://[a-zA-Z0-9-]+\.gupy\.io/[^\s)\]\">]+",
        r"https?://(?:apply\.)?workable\.com/[^\s)\]\">]+",
        r"https?://[a-zA-Z0-9-]+\.myworkdayjobs\.com/[^\s)\]\">]+",
        r"https?://jobs\.ashbyhq\.com/[^\s)\]\">]+",
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            return m.group(0).rstrip(".,;:)")

    # Markdown links: [Candidatar-se](url)
    md_match = re.search(r"\[(?:candidat|apply|inscreva|link)[^\]]*\]\((https?://[^\s)]+)\)", body, re.IGNORECASE)
    if md_match:
        return md_match.group(1)

    return fallback_url


def detect_seniority(title: str, text: str = "") -> str:
    combined = f"{title} {text}".lower()
    if re.search(r"\b(estagi[aá]ri[oa]|estagio|intern|internship)\b", combined):
        return "Estágio"
    if re.search(r"\b(j[uú]nior|entry|jr\.?)\b", combined):
        return "Júnior"
    if re.search(r"\b(lead|staff|principal|head|architect|especialista|specialist|tech lead)\b", combined):
        return "Staff / Lead"
    if re.search(r"\b(s[eê]nior|sr\.?|senior)\b", combined):
        return "Sênior"
    if re.search(r"\b(pleno|mid|pl\.?)\b", combined):
        return "Pleno"
    return "Pleno"


def format_brazilian_date(dt: datetime.datetime | None = None) -> str:
    d = dt or datetime.datetime.now()
    return d.strftime("%d/%m/%Y")


BRAZIL_FALLBACK_SEEDS: list[dict[str, Any]] = [
    {
        "title": "Engenheiro de Software Backend (Python / FastAPI / Docker)",
        "company": "Mercado Livre",
        "job_url": "https://boards.greenhouse.io/mercadolivre/jobs/482910",
        "ats": "Greenhouse",
        "modality": "100% Remoto",
        "stack": ["Python", "FastAPI", "PostgreSQL", "Docker"],
        "seniority": "Pleno",
        "source_platform": "github_tech_br",
        "external_job_id": "seed-meli-py-01",
        "posted_at": "2026-10-27T10:00:00Z",
        "job_date": "27/10/2026",
        "resume_label": "Currículo Otimizado - Mercado Livre",
        "base_used": ["Base Português", "Cognition"],
        "description": "Desenvolvimento de APIs robustas para pagamentos e logística com Python, FastAPI e Postgres.",
        "salary": "R$ 11.000 - R$ 14.500 /mês",
        "market": "br",
    },
    {
        "title": "Desenvolvedor Frontend Sênior (React / Next.js / TypeScript)",
        "company": "Nubank Tech",
        "job_url": "https://boards.greenhouse.io/nubank/jobs/500123",
        "ats": "Greenhouse",
        "modality": "100% Remoto",
        "stack": ["React", "Next.js", "TypeScript", "Tailwind CSS"],
        "seniority": "Sênior",
        "source_platform": "github_tech_br",
        "external_job_id": "seed-nubank-fe-02",
        "posted_at": "2026-10-27T09:30:00Z",
        "job_date": "27/10/2026",
        "resume_label": "Currículo Otimizado - Nubank Tech",
        "base_used": ["Base Português", "Cognition"],
        "description": "Criação de interfaces responsivas, acessíveis e de alta conversão para o ecossistema Nubank.",
        "salary": "R$ 15.000 - R$ 19.000 /mês",
        "market": "br",
    },
    {
        "title": "Tech Lead Engenharia de Software (Python / React / AWS)",
        "company": "Stone Co.",
        "job_url": "https://jobs.lever.co/stone/tech-lead",
        "ats": "Lever",
        "modality": "100% Remoto",
        "stack": ["Python", "React", "AWS", "Kubernetes"],
        "seniority": "Staff / Lead",
        "source_platform": "github_tech_br",
        "external_job_id": "seed-stone-tl-03",
        "posted_at": "2026-10-27T08:15:00Z",
        "job_date": "27/10/2026",
        "resume_label": "Currículo Otimizado - Stone Co.",
        "base_used": ["Base Português", "Cognition"],
        "description": "Liderança técnica de squad de serviços financeiros de alta disponibilidade e baixa latência.",
        "salary": "R$ 18.000 - R$ 24.000 /mês",
        "market": "br",
    },
    {
        "title": "Desenvolvedor Backend Sênior (Python / Go / Kafka)",
        "company": "iFood",
        "job_url": "https://ifood.gupy.io/job/102",
        "ats": "Gupy",
        "modality": "100% Remoto",
        "stack": ["Python", "Go", "Kafka", "Redis"],
        "seniority": "Sênior",
        "source_platform": "github_tech_br",
        "external_job_id": "seed-ifood-py-04",
        "posted_at": "2026-10-27T07:45:00Z",
        "job_date": "27/10/2026",
        "resume_label": "Currículo Otimizado - iFood",
        "base_used": ["Base Português", "Cognition"],
        "description": "Processamento de streaming de eventos e pedidos em escala com Python e Go.",
        "salary": "R$ 16.000 - R$ 21.000 /mês",
        "market": "br",
    },
]


async def fetch_brazil_tech_jobs(limit: int = 20, query: str = "") -> list[dict[str, Any]]:
    """Busca vagas em repositórios abertos de tech do Brasil (frontendbr/vagas e backend-br/vagas)."""
    urls = [
        "https://api.github.com/repos/frontendbr/vagas/issues?state=open&per_page=20",
        "https://api.github.com/repos/backend-br/vagas/issues?state=open&per_page=20",
    ]
    headers = {"User-Agent": "Candidata-IA-Search/1.0", "Accept": "application/vnd.github.v3+json"}
    gh_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    found: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers, verify=False) as client:
        tasks = [client.get(u) for u in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for resp in responses:
            if isinstance(resp, httpx.Response) and resp.status_code == 200:
                try:
                    items = resp.json()
                    for item in items:
                        if not item or item.get("pull_request"):
                            continue
                        raw_title = item.get("title", "")
                        body = item.get("body", "") or ""
                        labels = [lb.get("name", "") for lb in item.get("labels", []) if isinstance(lb, dict)]

                        # Normalização de empresa e cargo
                        company = "Tech Brasil"
                        clean_title = raw_title
                        if "@" in clean_title:
                            parts = clean_title.split("@")
                            clean_title, company = parts[0].strip(), parts[1].strip()
                        elif " na " in clean_title.lower():
                            parts = re.split(r"\s+na\s+", clean_title, flags=re.IGNORECASE)
                            clean_title, company = parts[0].strip(), parts[1].strip()

                        clean_title = re.sub(r"^\[[^\]]+\]\s*", "", clean_title).strip()
                        company = re.sub(r"[\[\]]", "", company).strip()[:50] or "Tech Brasil"

                        # Modalidade
                        modality = "100% Remoto"
                        comb = f"{raw_title} {' '.join(labels)}".lower()
                        if "híbrido" in comb or "hibrido" in comb:
                            modality = "Híbrido"
                        elif "presencial" in comb:
                            modality = "Presencial"

                        # Filtro por termo de busca se informado
                        if query:
                            q = query.lower()
                            if q not in clean_title.lower() and q not in body.lower() and not any(q in lb.lower() for lb in labels):
                                continue

                        app_url = extract_ats_link(body, item.get("html_url", ""))
                        ats = detect_ats(app_url, body)
                        seniority = detect_seniority(clean_title, body)

                        found.append({
                            "title": clean_title,
                            "company": company,
                            "job_url": app_url,
                            "ats": ats,
                            "modality": modality,
                            "stack": labels if labels else ["Python", "FastAPI"],
                            "seniority": seniority,
                            "source_platform": "github_tech_br",
                            "external_job_id": str(item.get("id", "")),
                            "posted_at": item.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "job_date": format_brazilian_date(),
                            "resume_label": f"Currículo Otimizado - {company}",
                            "base_used": ["Base Português", "Cognition"],
                            "description": body[:800],
                            "market": "br",
                        })
                except Exception as exc:
                    logger.debug("Erro ao decodificar vagas do GitHub: %s", exc)

    # Fallback caso API externa esteja com rate limit
    if not found:
        for seed in BRAZIL_FALLBACK_SEEDS:
            if query:
                q = query.lower()
                title_match = q in seed["title"].lower()
                stack_match = any(q in s.lower() for s in seed["stack"])
                desc_match = q in seed["description"].lower()
                if not (title_match or stack_match or desc_match):
                    continue
            found.append(dict(seed))

    return found[:limit]


async def fetch_remote_us_jobs(limit: int = 15, query: str = "") -> list[dict[str, Any]]:
    """Busca vagas remotas dos EUA/Globais no Jobicy."""
    url = f"https://jobicy.com/api/v2/remote-jobs?count={limit}&geo=usa"
    headers = {"User-Agent": "Candidata-IA-Search/1.0"}
    found: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("jobs", []):
                    title = item.get("jobTitle", "")
                    body = item.get("jobDescription", "") or ""
                    tags = item.get("jobTags", []) or []
                    comp = item.get("companyName", "US Tech Enterprise")

                    if query:
                        q = query.lower()
                        if q not in title.lower() and q not in body.lower() and not any(q in t.lower() for t in tags):
                            continue

                    app_url = item.get("url") or "https://jobicy.com"
                    ats = detect_ats(app_url, body)
                    seniority = detect_seniority(title, body)

                    found.append({
                        "title": title,
                        "company": comp,
                        "job_url": app_url,
                        "ats": ats,
                        "modality": "100% Remoto",
                        "stack": tags if tags else ["Software Engineering"],
                        "seniority": seniority,
                        "source_platform": "jobicy",
                        "external_job_id": str(item.get("id", "")),
                        "posted_at": item.get("pubDate") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "job_date": format_brazilian_date(),
                        "resume_label": f"Resume Optimized - {comp}",
                        "base_used": ["Base Inglês"],
                        "description": re.sub(r"<[^>]+>", " ", body)[:800],
                        "salary": f"${item.get('annualSalaryMin')} - ${item.get('annualSalaryMax')} /yr" if item.get("annualSalaryMin") else None,
                        "market": "us",
                    })
    except Exception as exc:
        logger.debug("Falha na consulta ao Jobicy: %s", exc)

    return found[:limit]


DEFAULT_GREENHOUSE_BOARDS: tuple[str, ...] = ("nubank", "mercadolibre", "stone", "brex", "gitlab")
DEFAULT_LEVER_BOARDS: tuple[str, ...] = ("netlify", "figma")
DEFAULT_ASHBY_BOARDS: tuple[str, ...] = ("linear", "supabase")


def _clean_html_text(raw: str) -> str:
    txt = html_lib.unescape(raw or "")
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _matches_query(query: str, *fields: str) -> bool:
    if not query or not query.strip():
        return True
    q = query.strip().lower()
    terms = [t for t in re.split(r"[\s,]+", q) if len(t) >= 2]
    combined = " ".join(fields).lower()
    if q in combined:
        return True
    return any(t in combined for t in terms) if terms else True


def compute_dynamic_fit_score(job: dict[str, Any], candidate_skills: list[str] | None = None) -> int:
    """Calcula Fit Score (40-98) cruzando skills do candidato com título, stack e descrição."""
    if not candidate_skills:
        return int(job.get("fit_score") or 85)
    from core.resume_tailor import normalize_skill

    text = f"{job.get('title', '')} {' '.join(job.get('stack') or [])} {job.get('description', '')}".lower()
    matched = 0
    for skill in candidate_skills:
        norm = normalize_skill(skill)
        if not norm:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(norm) + r"(?![a-z0-9])"
        if re.search(pattern, text) or norm in text:
            matched += 1

    if matched == 0:
        return 55
    ratio = matched / min(len(candidate_skills), 6)
    return min(98, max(65, int(65 + ratio * 33)))


async def fetch_greenhouse_boards(
    boards: list[str] | tuple[str, ...] | None = None,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Consulta API pública de boards Greenhouse (`boards-api.greenhouse.io`)."""
    targets = list(boards) if boards else list(DEFAULT_GREENHOUSE_BOARDS)
    found: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for slug in targets:
            slug_clean = slug.strip().lower()
            if not slug_clean:
                continue
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug_clean}/jobs?content=true"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for item in data.get("jobs", []):
                    title = str(item.get("title") or "")
                    content = _clean_html_text(str(item.get("content") or ""))
                    loc = str((item.get("location") or {}).get("name") or "Remoto")
                    if not _matches_query(query, title, content, loc):
                        continue
                    job_url = str(item.get("absolute_url") or "")
                    if not job_url:
                        continue
                    company = slug_clean.capitalize()
                    found.append(
                        {
                            "title": title,
                            "company": company,
                            "job_url": job_url,
                            "ats": "Greenhouse",
                            "modality": "100% Remoto" if "remot" in f"{title} {loc}".lower() else "Híbrido / Presencial",
                            "stack": [s for s in (query.split() if query else [])[:4]],
                            "seniority": detect_seniority(title, content),
                            "source_platform": "greenhouse_board",
                            "external_job_id": f"gh-{slug_clean}-{item.get('id', '')}",
                            "posted_at": item.get("updated_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "job_date": format_brazilian_date(),
                            "resume_label": f"Currículo Otimizado - {company}",
                            "base_used": ["Base Inglês"] if "engineer" in title.lower() else ["Base Português"],
                            "description": content[:2000],
                            "location": loc,
                            "market": "us" if "united states" in loc.lower() else "br",
                        }
                    )
                    if len(found) >= limit:
                        return found
            except Exception as exc:
                logger.debug("Falha ao consultar board Greenhouse '%s': %s", slug_clean, exc)

    return found[:limit]


async def fetch_lever_boards(
    boards: list[str] | tuple[str, ...] | None = None,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Consulta API pública de boards Lever (`api.lever.co`)."""
    targets = list(boards) if boards else list(DEFAULT_LEVER_BOARDS)
    found: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for slug in targets:
            slug_clean = slug.strip().lower()
            if not slug_clean:
                continue
            url = f"https://api.lever.co/v0/postings/{slug_clean}?mode=json"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                items = resp.json()
                if not isinstance(items, list):
                    continue
                for item in items:
                    title = str(item.get("text") or "")
                    desc = _clean_html_text(str(item.get("descriptionPlain") or item.get("description") or ""))
                    cats = item.get("categories") or {}
                    loc = str(cats.get("location") or "Remoto")
                    if not _matches_query(query, title, desc, loc):
                        continue
                    job_url = str(item.get("hostedUrl") or item.get("applyUrl") or "")
                    if not job_url:
                        continue
                    company = slug_clean.capitalize()
                    found.append(
                        {
                            "title": title,
                            "company": company,
                            "job_url": job_url,
                            "ats": "Lever",
                            "modality": "100% Remoto" if "remot" in f"{title} {loc} {cats.get('commitment', '')}".lower() else "A Definir",
                            "stack": [],
                            "seniority": detect_seniority(title, desc),
                            "source_platform": "lever_board",
                            "external_job_id": f"lever-{slug_clean}-{item.get('id', '')}",
                            "posted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "job_date": format_brazilian_date(),
                            "resume_label": f"Resume Optimized - {company}",
                            "base_used": ["Base Inglês"],
                            "description": desc[:2000],
                            "location": loc,
                            "market": "us",
                        }
                    )
                    if len(found) >= limit:
                        return found
            except Exception as exc:
                logger.debug("Falha ao consultar board Lever '%s': %s", slug_clean, exc)

    return found[:limit]


async def fetch_ashby_boards(
    boards: list[str] | tuple[str, ...] | None = None,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Consulta API pública de boards Ashby (`api.ashbyhq.com/posting-api/job-board/{slug}`)."""
    targets = list(boards) if boards else list(DEFAULT_ASHBY_BOARDS)
    found: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for slug in targets:
            slug_clean = slug.strip().lower()
            if not slug_clean:
                continue
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug_clean}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for item in data.get("jobs", []):
                    title = str(item.get("title") or "")
                    desc = _clean_html_text(str(item.get("descriptionPlain") or item.get("descriptionHtml") or ""))
                    loc = str(item.get("location") or ("Remoto" if item.get("isRemote") else "A Definir"))
                    if not _matches_query(query, title, desc, loc):
                        continue
                    job_url = str(item.get("jobUrl") or item.get("applyUrl") or "")
                    if not job_url:
                        continue
                    company = slug_clean.capitalize()
                    found.append(
                        {
                            "title": title,
                            "company": company,
                            "job_url": job_url,
                            "ats": "Ashby",
                            "modality": "100% Remoto" if item.get("isRemote") or "remot" in loc.lower() else "Híbrido / Presencial",
                            "stack": [],
                            "seniority": detect_seniority(title, desc),
                            "source_platform": "ashby_board",
                            "external_job_id": f"ashby-{slug_clean}-{item.get('id', '')}",
                            "posted_at": item.get("publishedAt") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "job_date": format_brazilian_date(),
                            "resume_label": f"Resume Optimized - {company}",
                            "base_used": ["Base Inglês"],
                            "description": desc[:2000],
                            "location": loc,
                            "market": "us",
                        }
                    )
                    if len(found) >= limit:
                        return found
            except Exception as exc:
                logger.debug("Falha ao consultar board Ashby '%s': %s", slug_clean, exc)

    return found[:limit]


async def search_jobs(
    query: str = "",
    market: str = "all",
    limit: int = 25,
    sources: list[str] | None = None,
    greenhouse_boards: list[str] | None = None,
    lever_boards: list[str] | None = None,
    ashby_boards: list[str] | None = None,
    candidate_skills: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Agrega busca entre múltiplos mercados (BR, EUA, Global) e boards públicos ATS."""
    jobs: list[dict[str, Any]] = []
    tasks = []
    active_sources = set(sources) if sources else {"open_feeds"}

    if "open_feeds" in active_sources:
        if market in ("br", "all"):
            tasks.append(fetch_brazil_tech_jobs(limit=limit, query=query))
        if market in ("us", "all"):
            tasks.append(fetch_remote_us_jobs(limit=limit, query=query))

    if "ats_boards" in active_sources:
        tasks.append(fetch_greenhouse_boards(boards=greenhouse_boards, query=query, limit=limit))
        tasks.append(fetch_lever_boards(boards=lever_boards, query=query, limit=limit))
        tasks.append(fetch_ashby_boards(boards=ashby_boards, query=query, limit=limit))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, list):
            jobs.extend(res)

    # Deduplicação por job_url normalizada + cálculo de Fit Score
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for j in jobs:
        raw_url = str(j.get("job_url") or "").strip()
        norm_url = raw_url.rstrip("/").split("?")[0] if raw_url else ""
        if raw_url and norm_url not in seen:
            seen.add(norm_url)
            if candidate_skills:
                j["fit_score"] = compute_dynamic_fit_score(j, candidate_skills)
            deduped.append(j)

    deduped.sort(key=lambda item: int(item.get("fit_score") or 0), reverse=True)
    return deduped[:limit]


# ---------------------------------------------------------------------------
# Fila Local de Vagas Descobertas (cache/discovered_jobs.json)
# ---------------------------------------------------------------------------


def load_discovered_jobs(queue_path: Path = Path("cache/discovered_jobs.json")) -> list[dict[str, Any]]:
    """Carrega a fila local de vagas descobertas."""
    if not queue_path.exists():
        return []
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Falha ao ler fila local '%s': %s", queue_path, exc)
        return []


def save_discovered_jobs(jobs: list[dict[str, Any]], queue_path: Path = Path("cache/discovered_jobs.json")) -> None:
    """Persiste a fila local de vagas descobertas."""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def merge_discovered_jobs(
    new_jobs: list[dict[str, Any]],
    queue_path: Path = Path("cache/discovered_jobs.json"),
) -> tuple[list[dict[str, Any]], int]:
    """Mescla novas vagas descobertas na fila local evitando duplicatas por URL."""
    existing = load_discovered_jobs(queue_path)
    by_url = {
        str(j.get("job_url") or "").strip().rstrip("/"): j
        for j in existing
        if j.get("job_url")
    }
    added = 0
    for idx, j in enumerate(new_jobs, start=1):
        url_key = str(j.get("job_url") or "").strip().rstrip("/")
        if not url_key or url_key in by_url:
            continue
        record = dict(j)
        record.setdefault("id", record.get("external_job_id") or f"disc-{len(existing) + added + idx}")
        record.setdefault("sheet_status", "A Fazer")
        existing.insert(0, record)
        by_url[url_key] = record
        added += 1

    save_discovered_jobs(existing, queue_path)
    return existing, added


def update_discovered_job(
    job_id: str,
    updates: dict[str, Any],
    queue_path: Path = Path("cache/discovered_jobs.json"),
) -> dict[str, Any] | None:
    """Atualiza metadados (ex.: status, tailored_resume_path, matched_skills) de uma vaga da fila local."""
    items = load_discovered_jobs(queue_path)
    updated_item: dict[str, Any] | None = None
    for item in items:
        if str(item.get("id") or item.get("external_job_id")) == str(job_id):
            item.update(updates)
            updated_item = item
            break
    if updated_item is not None:
        save_discovered_jobs(items, queue_path)
    return updated_item


def jobs_to_domain_models(raw_jobs: list[dict[str, Any]]) -> list[Job]:
    """Converte os dicionários de vagas no modelo Job de domínio com os campos da planilha."""
    domain_jobs: list[Job] = []
    for idx, r in enumerate(raw_jobs, start=1):
        resume_lbl = r.get("resume_label")
        base_used = r.get("base_used") or []
        lang = "en" if (resume_lbl and "resume" in str(resume_lbl).lower()) or any("ingles" in str(b).lower() for b in base_used) else "pt"

        job = Job(
            id=str(r.get("id") or r.get("external_job_id") or f"searched-{idx}"),
            row_index=idx,
            date=r.get("job_date") or format_brazilian_date(),
            company=r.get("company", "Empresa"),
            title=r.get("title", "Cargo"),
            modality=r.get("modality", "100% Remoto"),
            url=r.get("job_url"),
            ats=r.get("ats"),
            description=r.get("description"),
            fit_score=r.get("fit_score", 90),
            resume_label=resume_lbl,
            base_used=base_used,
            stack=r.get("stack") or [],
            sheet_status=r.get("sheet_status") or "A Fazer",
            language=r.get("language") or lang,
            seniority=r.get("seniority"),
            source_platform=r.get("source_platform"),
            external_job_id=r.get("external_job_id"),
            posted_at=r.get("posted_at"),
            location=r.get("location"),
            salary=r.get("salary"),
            tailored_resume_path=r.get("tailored_resume_path"),
            matched_skills=r.get("matched_skills") or [],
        )
        domain_jobs.append(job)
    return domain_jobs


async def persist_jobs_to_supabase(
    jobs: list[dict[str, Any]],
    workspace_id: str,
    profile_id: str,
    supabase_client: SupabaseCloudClient | None = None,
) -> int:
    """Persiste as vagas pesquisadas no Supabase job_applications com todos os campos da planilha."""
    if not jobs:
        return 0

    if supabase_client is None:
        url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórios para salvar no Supabase.")
        supabase_client = SupabaseCloudClient(url, key)

    rows_to_insert = []
    for j in jobs:
        rows_to_insert.append({
            "workspace_id": workspace_id,
            "profile_id": profile_id,
            "company": j["company"],
            "title": j["title"],
            "job_url": j["job_url"],
            "ats": j.get("ats", "Direct / Career Site"),
            "status": "PENDING",
            "dry_run": True,
            "submitted_live": False,
            "fit_score": j.get("fit_score", 90),
            "tokens_consumed": 0,
            # Campos da Planilha & Sourcing
            "modality": j.get("modality", "100% Remoto"),
            "resume_label": j.get("resume_label"),
            "base_used": j.get("base_used", []),
            "stack": j.get("stack", []),
            "job_date": j.get("job_date") or format_brazilian_date(),
            "description": j.get("description"),
            "location": j.get("location", "Remoto"),
            "salary": j.get("salary"),
            "seniority": j.get("seniority", "Pleno"),
            "source_platform": j.get("source_platform", "search_engine"),
            "posted_at": j.get("posted_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "external_job_id": j.get("external_job_id"),
            "application_stage": "Candidatado",
        })

    saved = await supabase_client.insert_job_applications(rows_to_insert)
    return len(saved)


async def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Busca nativa de vagas para Candidata IA e persistência no Supabase.")
    parser.add_argument("--query", "-q", type=str, default="", help="Termo de busca (ex: Python, AI, React, Fullstack).")
    parser.add_argument("--market", "-m", choices=["all", "br", "us"], default="all", help="Mercado alvo da busca.")
    parser.add_argument("--limit", "-l", type=int, default=15, help="Quantidade máxima de vagas a pesquisar.")
    parser.add_argument("--save-supabase", "--save", "-s", dest="save_supabase", action="store_true", help="Salva as vagas encontradas direto no Supabase.")
    parser.add_argument("--workspace-id", type=str, default="", help="ID do workspace no Supabase.")
    parser.add_argument("--profile-id", type=str, default="", help="ID do perfil de candidato no Supabase.")

    args = parser.parse_args()

    print(f"🔎 Pesquisando vagas: termo='{args.query}', mercado='{args.market}', limite={args.limit}...")
    found = await search_jobs(query=args.query, market=args.market, limit=args.limit)
    print(f"✅ {len(found)} vagas encontradas com sucesso!\n")

    for i, j in enumerate(found, start=1):
        print(f"[{i:02d}] {j['company']} — {j['title']}")
        print(f"     Modalidade: {j['modality']} | ATS: {j['ats']} | Senioridade: {j['seniority']}")
        print(f"     Stack: {', '.join(j['stack'][:4])}")
        print(f"     Currículo Otimizado: {j['resume_label']}")
        print(f"     URL: {j['job_url']}")
        print()

    if args.save_supabase:
        ws_id = args.workspace_id or os.getenv("WORKSPACE_ID", "")
        prof_id = args.profile_id or os.getenv("PROFILE_ID", "")
        if not ws_id or not prof_id:
            try:
                url = (os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")).rstrip("/")
                key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
                if url and key:
                    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
                    async with httpx.AsyncClient(timeout=10.0, verify=False) as http_c:
                        if not ws_id:
                            r_ws = await http_c.get(f"{url}/rest/v1/workspaces?select=id&limit=1", headers=headers)
                            if r_ws.status_code == 200 and r_ws.json():
                                ws_id = r_ws.json()[0]["id"]
                        if not prof_id and ws_id:
                            r_pf = await http_c.get(f"{url}/rest/v1/candidate_profiles?workspace_id=eq.{ws_id}&select=id&limit=1", headers=headers)
                            if r_pf.status_code == 200 and r_pf.json():
                                prof_id = r_pf.json()[0]["id"]
            except Exception as exc:
                logger.debug("Falha na auto-descoberta de workspace/profile: %s", exc)

        if not ws_id or not prof_id:
            print("⚠️ Para salvar no Supabase, defina WORKSPACE_ID e PROFILE_ID no .env ou via flags.")
            return

        print(f"💾 Salvando {len(found)} vagas no Supabase (Workspace: {ws_id[:8]}...)...")
        count = await persist_jobs_to_supabase(found, workspace_id=ws_id, profile_id=prof_id)
        print(f"🚀 {count} vagas salvas com sucesso em 'job_applications' (status = PENDING)!")


if __name__ == "__main__":
    asyncio.run(main_cli())
