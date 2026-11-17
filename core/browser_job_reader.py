"""Leitor autenticado de vagas via navegador (LinkedIn e Gupy) usando storage_state.json."""

from __future__ import annotations

import asyncio
import datetime
import html as html_lib
import logging
import random
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from config.settings import Settings
from core.job_search import detect_ats, detect_seniority, extract_ats_link, format_brazilian_date
from core.models import HarnessError

logger = logging.getLogger(__name__)


class SessionExpiredError(HarnessError):
    """Sessão do portal ausente, expirada ou redirecionada para login/checkpoint."""

    def __init__(self, portal: str, message: str = "") -> None:
        self.portal = portal
        super().__init__(message or f"Sessão do portal '{portal}' expirada ou não conectada.")


def _strip_tags(raw_html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", raw_html or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_linkedin_external_url(raw_href: str, description_text: str = "") -> str | None:
    """Resolve links de redirecionamento do LinkedIn (/redir/redirect?url=...) ou links ATS no texto."""
    if raw_href:
        parsed = urlparse(raw_href)
        if "linkedin.com" in parsed.netloc.lower() and "/redir" in parsed.path.lower():
            qs = parse_qs(parsed.query)
            for key in ("url", "target", "redirect"):
                if qs.get(key):
                    candidate = unquote(qs[key][0]).strip()
                    if candidate.startswith(("http://", "https://")):
                        return candidate
        if raw_href.startswith(("http://", "https://")) and "linkedin.com" not in parsed.netloc.lower():
            return raw_href

    if description_text:
        extracted = extract_ats_link(description_text, "")
        if extracted:
            return extracted

    return None


def parse_linkedin_search_html(html_content: str, limit: int = 15) -> list[dict[str, Any]]:
    """Extrai cards de vagas do HTML de busca do LinkedIn Jobs (testável offline)."""
    if not html_content:
        return []

    jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # Padrão principal: divide por início de cada card data-occludable-job-id (evita corte em <li> aninhados)
    start_pattern = re.compile(r"<li[^>]*data-occludable-job-id=[\"'](\d+)[\"'][^>]*>", flags=re.IGNORECASE)
    starts = list(start_pattern.finditer(html_content))
    matches: list[tuple[str, str]] = []
    for i, m in enumerate(starts):
        end_pos = starts[i + 1].start() if i + 1 < len(starts) else len(html_content)
        matches.append((m.group(1), html_content[m.end() : end_pos]))

    if not matches:
        # Fallback genérico para cards com /jobs/view/
        link_pattern = re.compile(
            r"<a[^>]+href=[\"']([^\"']*/jobs/view/(\d+)[^\"']*)[\"'][^>]*>([\s\S]*?)</a>",
            flags=re.IGNORECASE,
        )
        for m in link_pattern.finditer(html_content):
            _, job_id, anchor_body = m.group(1), m.group(2), m.group(3)
            full_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
            if full_url in seen_urls:
                continue
            title = _strip_tags(anchor_body) or "Vaga LinkedIn"
            seen_urls.add(full_url)
            jobs.append(
                {
                    "title": title,
                    "company": "LinkedIn",
                    "job_url": full_url,
                    "ats": "LinkedIn",
                    "modality": "100% Remoto" if "remot" in title.lower() else "A Definir",
                    "stack": [],
                    "seniority": detect_seniority(title),
                    "source_platform": "linkedin_browser",
                    "external_job_id": f"li-{job_id}",
                    "posted_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "job_date": format_brazilian_date(),
                    "resume_label": f"Currículo Otimizado - LinkedIn {job_id}",
                    "base_used": ["Base Português"],
                    "description": "",
                    "location": "Brasil",
                    "market": "br",
                    "easy_apply": False,
                }
            )
            if len(jobs) >= limit:
                break
        return jobs

    for job_id, block in matches:
        full_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        if full_url in seen_urls:
            continue

        title_m = re.search(
            r"<a[^>]*class=[\"'][^\"']*job-card-list__title[^\"']*[\"'][^>]*>([\s\S]*?)</a>",
            block,
            flags=re.IGNORECASE,
        ) or re.search(
            r"<a[^>]*href=[\"'][^\"']*/jobs/view/[^\"']*[\"'][^>]*>([\s\S]*?)</a>",
            block,
            flags=re.IGNORECASE,
        )

        comp_m = re.search(
            r"<(?:span|div|a)[^>]*class=[\"'][^\"']*(?:company-name|primary-description)[^\"']*[\"'][^>]*>([\s\S]*?)</(?:span|div|a)>",
            block,
            flags=re.IGNORECASE,
        )
        loc_m = re.search(
            r"<(?:li|span)[^>]*class=[\"'][^\"']*metadata-item[^\"']*[\"'][^>]*>([\s\S]*?)</(?:li|span)>",
            block,
            flags=re.IGNORECASE,
        )

        title = _strip_tags(title_m.group(1)) if title_m else f"Vaga {job_id}"
        company = _strip_tags(comp_m.group(1)) if comp_m else "Empresa LinkedIn"
        location = _strip_tags(loc_m.group(1)) if loc_m else "Brasil"
        easy_apply = bool(re.search(r"candidatura simplificada|easy apply", block, flags=re.IGNORECASE))

        lower_block = block.lower()
        if "remot" in lower_block:
            modality = "100% Remoto"
        elif "híbrido" in lower_block or "hybrid" in lower_block:
            modality = "Híbrido"
        else:
            modality = "Presencial"

        seen_urls.add(full_url)
        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": full_url,
                "ats": "LinkedIn",
                "modality": modality,
                "stack": [],
                "seniority": detect_seniority(title, location),
                "source_platform": "linkedin_browser",
                "external_job_id": f"li-{job_id}",
                "posted_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "job_date": format_brazilian_date(),
                "resume_label": f"Currículo Otimizado - {company}",
                "base_used": ["Base Português"],
                "description": "",
                "location": location,
                "market": "br",
                "easy_apply": easy_apply,
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def parse_gupy_search_html(html_content: str, limit: int = 15) -> list[dict[str, Any]]:
    """Extrai cards de vagas do HTML de busca do Portal Gupy (portal.gupy.io)."""
    if not html_content:
        return []

    jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    link_pattern = re.compile(
        r"<a[^>]+href=[\"'](https?://[a-zA-Z0-9-]+\.gupy\.io/(?:jobs|job)/\d+[^\"']*|/job/[^\"']+)[\"'][^>]*>([\s\S]*?)</a>",
        flags=re.IGNORECASE,
    )

    for m in link_pattern.finditer(html_content):
        raw_url = m.group(1).strip()
        anchor_html = m.group(2)
        job_url = urljoin("https://portal.gupy.io", raw_url).split("?")[0]
        if job_url in seen_urls:
            continue

        # Tenta extrair título/empresa de headings internos do card
        h_m = re.search(r"<h[23][^>]*>([\s\S]*?)</h[23]>", anchor_html, flags=re.IGNORECASE)
        p_m = re.search(r"<p[^>]*>([\s\S]*?)</p>", anchor_html, flags=re.IGNORECASE)
        clean_all = _strip_tags(anchor_html)

        title = _strip_tags(h_m.group(1)) if h_m else (clean_all[:80] or "Vaga Gupy")
        company = _strip_tags(p_m.group(1)) if p_m else ""
        if not company:
            host = urlparse(job_url).netloc.split(".")[0]
            company = host.capitalize() if host and host != "portal" else "Empresa Gupy"

        lower_text = clean_all.lower()
        modality = (
            "100% Remoto"
            if "remot" in lower_text
            else ("Híbrido" if "híbrido" in lower_text or "hibrido" in lower_text else "A Definir")
        )

        ext_id_m = re.search(r"/(\d+)(?:$|[/?#])", job_url)
        ext_id = f"gupy-{ext_id_m.group(1)}" if ext_id_m else f"gupy-{len(jobs) + 1}"

        seen_urls.add(job_url)
        jobs.append(
            {
                "title": title,
                "company": company,
                "job_url": job_url,
                "ats": "Gupy",
                "modality": modality,
                "stack": [],
                "seniority": detect_seniority(title, clean_all),
                "source_platform": "gupy_browser",
                "external_job_id": ext_id,
                "posted_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "job_date": format_brazilian_date(),
                "resume_label": f"Currículo Otimizado - {company}",
                "base_used": ["Base Português"],
                "description": clean_all[:800],
                "location": "Brasil",
                "market": "br",
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


async def _open_browser_context(settings: Settings):
    """Abre contexto isolado com storage_state carregado para leitura de vagas."""
    if not settings.STORAGE_STATE_PATH or not settings.STORAGE_STATE_PATH.exists():
        raise SessionExpiredError("browser", "Arquivo storage_state.json não encontrado. Conecte sua conta primeiro.")

    if settings.USE_CAMOUFOX:
        try:
            from camoufox.async_api import AsyncCamoufox

            cm = AsyncCamoufox(headless=settings.HEADLESS, humanize=True)
            browser: Any = await cm.__aenter__()
            if hasattr(browser, "new_context"):
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 840},
                    storage_state=str(settings.STORAGE_STATE_PATH.resolve()),
                )
            else:
                ctx = browser
            return cm, None, browser, ctx
        except Exception as exc:
            logger.debug("Fallback para Chromium na leitura de vagas (%s)", exc)

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=settings.HEADLESS, args=["--no-sandbox"])
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 840},
        storage_state=str(settings.STORAGE_STATE_PATH.resolve()),
    )
    return None, pw, browser, ctx


async def _close_browser_context(cm: Any, pw: Any, browser: Any, ctx: Any) -> None:
    try:
        if ctx is not None:
            await ctx.close()
    except Exception:
        pass
    try:
        if cm is not None:
            await cm.__aexit__(None, None, None)
        elif browser is not None:
            await browser.close()
    except Exception:
        pass
    try:
        if pw is not None:
            await pw.stop()
    except Exception:
        pass


async def read_linkedin_jobs(
    query: str,
    settings: Settings,
    location: str = "Brasil",
    limit: int = 12,
    resolve_external_links: bool = True,
) -> list[dict[str, Any]]:
    """Lê vagas reais do LinkedIn Jobs usando a sessão autenticada do usuário."""
    capped_limit = min(max(1, limit), getattr(settings, "LINKEDIN_DAILY_LIMIT", 15))
    cm, pw, browser, ctx = await _open_browser_context(settings)
    try:
        page = await ctx.new_page()
        page.set_default_timeout(settings.ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)

        search_url = (
            f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(query or 'Developer')}"
            f"&location={quote_plus(location or 'Brasil')}"
        )
        await page.goto(search_url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.8, 3.0))

        cur_url = (page.url or "").lower()
        if any(seg in cur_url for seg in ("/login", "/uas/login", "/checkpoint", "/authwall")):
            raise SessionExpiredError("linkedin", "Sessão do LinkedIn expirou. Clique em 'Conectar com LinkedIn'.")

        html_content = await page.content()
        jobs = parse_linkedin_search_html(html_content, limit=capped_limit)

        # Enriquece descrição e resolve link externo de ATS quando disponível
        for job in jobs:
            try:
                await asyncio.sleep(random.uniform(1.2, 2.4))
                await page.goto(job["job_url"], wait_until="domcontentloaded")
                await asyncio.sleep(1.0)

                desc_loc = page.locator(".jobs-description__content, .show-more-less-html__markup, #job-details").first
                if await desc_loc.count() > 0:
                    desc_txt = (await desc_loc.inner_text()).strip()
                    if desc_txt:
                        job["description"] = desc_txt[:2500]

                easy_btn = page.locator(
                    "button.jobs-apply-button:has-text('Candidatura simplificada'), "
                    "button.jobs-apply-button:has-text('Easy Apply')"
                ).first
                if await easy_btn.count() > 0 and await easy_btn.is_visible():
                    job["easy_apply"] = True
                    job["ats"] = "LinkedIn"
                elif resolve_external_links:
                    ext_link = page.locator("a.jobs-apply-button, a[data-tracking-control-name*='apply']").first
                    if await ext_link.count() > 0:
                        href = await ext_link.get_attribute("href") or ""
                        resolved = resolve_linkedin_external_url(href, job.get("description", ""))
                        if resolved:
                            job["job_url"] = resolved
                            job["ats"] = detect_ats(resolved, job.get("description", ""))
            except Exception as exc:
                logger.debug("Falha ao detalhar vaga LinkedIn %s: %s", job.get("job_url"), exc)

        return jobs
    finally:
        await _close_browser_context(cm, pw, browser, ctx)


async def read_gupy_jobs(
    query: str,
    settings: Settings,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Lê vagas reais do Portal Gupy usando a sessão autenticada do usuário."""
    cm, pw, browser, ctx = await _open_browser_context(settings)
    try:
        page = await ctx.new_page()
        page.set_default_timeout(settings.ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)

        search_url = f"https://portal.gupy.io/job-search/term={quote_plus(query or 'Desenvolvedor')}"
        await page.goto(search_url, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2.0, 3.2))

        cur_url = (page.url or "").lower()
        if any(seg in cur_url for seg in ("auth.gupy.io", "login.gupy.io", "/signin")):
            raise SessionExpiredError("gupy", "Sessão da Gupy expirou. Clique em 'Conectar com Gupy'.")

        html_content = await page.content()
        jobs = parse_gupy_search_html(html_content, limit=limit)

        for job in jobs[:limit]:
            try:
                await asyncio.sleep(random.uniform(1.0, 2.0))
                await page.goto(job["job_url"], wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
                main_loc = page.locator("main, [data-testid='job-description'], article").first
                if await main_loc.count() > 0:
                    txt = (await main_loc.inner_text()).strip()
                    if txt:
                        job["description"] = txt[:2500]
            except Exception as exc:
                logger.debug("Falha ao detalhar vaga Gupy %s: %s", job.get("job_url"), exc)

        return jobs
    finally:
        await _close_browser_context(cm, pw, browser, ctx)
