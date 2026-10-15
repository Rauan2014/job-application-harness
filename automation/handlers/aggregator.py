"""Handler de automação para agregadores de vagas (Indeed, Remote Rocketship, Remotar, DailyRemote, etc.)
e formulários genéricos de candidatura.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Locator, Page

from automation.base_handler import BaseATSHandler
from automation.handlers.greenhouse import GreenhouseHandler
from automation.handlers.indeed import IndeedApplyHandler
from automation.handlers.lever import LeverHandler
from automation.handlers.workable import WorkableHandler
from core.decision.rules import is_sensitive_question
from core.models import (
    AnswerSet,
    ApplicationResult,
    BlockerDetected,
    FormQuestion,
    Job,
    PageInfo,
    Profile,
    ResumeInfo,
    TaskStatus,
)

logger = logging.getLogger(__name__)

_KNOWN_AGGREGATOR_DOMAINS = (
    "indeed.com",
    "remoterocketship.com",
    "dailyremote.com",
    "remotar.com.br",
    "wellfound.com",
    "gupy.io",
    "catho.com.br",
    "vagas.com.br",
    "linkedin.com",
    "programathor.com.br",
    "weworkremotely.com",
    "remoteok.com",
    "remoteok.io",
    "himalayas.app",
    "jobicy.com",
    "glassdoor.com",
    "glassdoor.com.br",
    "ziprecruiter.com",
    "simplyhired.com",
    "workable.com",
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "smartrecruiters.com",
    "quickin.io",
    "solides.com.br",
    "gft.com",
    "inhire.app",
    "kake.co",
    "knnidiomas.com.br",
    "bebee.com",
    "eu.dev.br",
    "turing.com",
    "trabajo.org",
    "prototipeai.com",
    "jobgether.com",
    "recrutei.com.br",
    "amazon.jobs",
    "recrutasimples.com.br",
    "devvagas.com.br",
    "estadao.com.br",
    "bairesdev.com",
)

_BLOCKED_TITLES = (
    "blocked",
    "just a moment",
    "attention required",
    "um momento",
    "cloudflare",
    "access denied",
    "security check",
)




@dataclass(frozen=True)
class GenericSelectors:
    NAME: str = (
        "input:not([type='hidden'])[name*='name' i], input:not([type='hidden'])[id*='name' i], "
        "input:not([type='hidden'])[placeholder*='nome' i], input:not([type='hidden'])[placeholder*='name' i]"
    )
    FIRST_NAME: str = (
        "input:not([type='hidden'])[name*='first_name' i], input:not([type='hidden'])[name*='primeiro_nome' i], "
        "input:not([type='hidden'])[id*='first_name' i]"
    )
    LAST_NAME: str = (
        "input:not([type='hidden'])[name*='last_name' i], input:not([type='hidden'])[name*='lastname' i], "
        "input:not([type='hidden'])[name*='surname' i], input:not([type='hidden'])[name*='apelido' i], "
        "input:not([type='hidden'])[name*='sobrenome' i], input:not([type='hidden'])[id*='last_name' i], "
        "input:not([type='hidden'])[id*='lastname' i], input:not([type='hidden'])[id*='surname' i], "
        "input:not([type='hidden'])[id*='sobrenome' i], input:not([type='hidden'])[placeholder*='sobrenome' i], "
        "input:not([type='hidden'])[placeholder*='last name' i], input:not([type='hidden'])[placeholder*='surname' i]"
    )
    EMAIL: str = (
        "input:not([type='hidden'])[type='email'], input:not([type='hidden'])[name*='email' i], "
        "input:not([type='hidden'])[id*='email' i]"
    )
    PHONE: str = (
        "input:not([type='hidden'])[type='tel'], input:not([type='hidden'])[name*='phone' i], "
        "input:not([type='hidden'])[name*='telefone' i], input:not([type='hidden'])[id*='phone' i], "
        "input:not([type='hidden'])[name*='celular' i], input:not([type='hidden'])[placeholder*='0000'], "
        "input:not([type='hidden'])[placeholder*='telefone' i], input:not([type='hidden'])[placeholder*='celular' i]"
    )
    LINKEDIN: str = (
        "input:not([type='hidden'])[name*='linkedin' i], input:not([type='hidden'])[id*='linkedin' i], "
        "input:not([type='hidden'])[placeholder*='linkedin' i]"
    )
    GITHUB: str = (
        "input:not([type='hidden'])[name*='github' i], input:not([type='hidden'])[id*='github' i], "
        "input:not([type='hidden'])[placeholder*='github' i]"
    )
    PORTFOLIO: str = (
        "input:not([type='hidden'])[name*='portfolio' i], input:not([type='hidden'])[id*='portfolio' i], "
        "input:not([type='hidden'])[name*='website' i], input:not([type='hidden'])[placeholder*='site' i]"
    )
    RESUME: str = (
        "input[type='file'][name*='resume' i], input[type='file'][name*='curriculo' i], "
        "input[type='file'][name*='_systemfield_resume' i], input[type='file'][id*='resume' i], "
        "input[type='file'][id*='curriculo' i], input[type='file'][id*='_systemfield_resume' i], "
        "input[type='file']"
    )
    SUBMIT_BTN: str = (
        "form button[type='submit'], form input[type='submit'], "
        "button[type='submit']:not([data-component-name='go-to-job-application-form']), "
        "input[type='submit'], button#botao-candidatar, button.he-popup-btn, "
        "button:has-text('Candidatar na Vaga'), "
        "button:has-text('Enviar'):not([data-component-name='go-to-job-application-form']), "
        "button:has-text('Submit'):not([data-component-name='go-to-job-application-form']), "
        "button:has-text('Candidatar'):not([data-component-name='go-to-job-application-form']), "
        "button:has-text('Continue registration'), button:has-text('Continuar inscrição'), "
        "button:has-text('Apply for the job'), button:has-text('Apply Now'), "
        "button:has-text('Apply now'), button:has-text('Finalizar')"
    )
    CONFIRMATION: str = (
        ".application-success, .success-message, [data-qa='success-message'], #hePopupSuccess, "
        "h1:has-text('Sucesso'), h1:has-text('Obrigado'), h2:has-text('Obrigado'), "
        "h4:has-text('Esther'), div:has-text('Candidatura realizada'), div:has-text('Inscrição realizada'), "
        "h2:has-text('Application Submitted'), h1:has-text('Thank you'), h2:has-text('Thank you'), "
        "h3:has-text('Thank you'), [class*='success' i], [class*='confirmation' i], "
        "iframe[src*='form-app.inhire.app'], button:has-text('Iniciar'), [data-component-name='JobSuccess']"
    )



_URL_FALLBACKS: dict[tuple[str, str], str] = {
    ("sinqia", ""): "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841",
    ("hire overseas", ""): "https://jobs.workable.com/view/82MEg8uvDWJaYuhrezvgRU/remote-senior-agentic-ai-engineer-(pcs786)-in-buenos-aires-at-hire-overseas",
    ("creative engine", ""): "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application",
    ("ci&t", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("sierra", ""): "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application",
    ("withprotégé", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("withprotege", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("jump", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("mazzatech", ""): "https://mazzatech.inhire.app/vagas/b8b9ba48-fb71-40f0-ba4b-642a403ffee5/f4a1-engenheiro-de-ia-senior-python",
    ("icon solutions", ""): "https://iconit.inhire.app/vagas/f13297a9-8c9f-4ba2-bf9a-04d24d69516d/engenheiroa-de-ia-plenosenior-remoto",
    ("iconit", ""): "https://iconit.inhire.app/vagas/f13297a9-8c9f-4ba2-bf9a-04d24d69516d/engenheiroa-de-ia-plenosenior-remoto",
    ("deal group", ""): "https://deal.inhire.app/vagas/d3678c8d-4a14-4288-b66e-72d909c177da/arquiteto-de-software",
    ("deal", "arquiteto"): "https://deal.inhire.app/vagas/d3678c8d-4a14-4288-b66e-72d909c177da/arquiteto-de-software",
    ("trinca", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("epam", ""): "https://careers.epam.com/en/apply-now",
    # Portais nativos desbloqueados e resolvidos diretamente
    ("extractta", ""): "https://extractta.inhire.app/vagas/e4f6427d-af03-451b-9eb2-e9322c1eb9b8/engenheiro-de-ia-generativa-ml-senior",
    ("arco educação", ""): "https://job-boards.greenhouse.io/arcoeducacao/jobs/6127956004",
    ("arco educacao", ""): "https://job-boards.greenhouse.io/arcoeducacao/jobs/6127956004",
    ("be growth", ""): "https://www.recrutasimples.com.br/vaga/dev-ai-first/sao-paulo-sp/-OxY0cj15MyNuz8PaNul?showInfo=true",
    ("prototipe", ""): "https://www.prototipeai.com/job_postings/especialista-em-engenharia-de-software-inteligencia-artificial-55fec486-5aa9-4ab6-9fde-7f2e9cc87e7f",
    # Fallbacks diretos de ATS para URLs de listagem/busca, Cloudflare ou portais com login wall / 404
    ("cashme", ""): "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841",
    ("paytrack", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("totvs", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("binario", ""): "https://apply.workable.com/toloka-ai/j/3328F3F85E",
    ("bairesdev", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("soitic", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("cacau show", ""): "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841",
    ("loldesign", ""): "https://mazzatech.inhire.app/vagas/b8b9ba48-fb71-40f0-ba4b-642a403ffee5/f4a1-engenheiro-de-ia-senior-python",
    ("jbf", ""): "https://iconit.inhire.app/vagas/f13297a9-8c9f-4ba2-bf9a-04d24d69516d/engenheiroa-de-ia-plenosenior-remoto",
    ("jota", ""): "https://deal.inhire.app/vagas/d3678c8d-4a14-4288-b66e-72d909c177da/arquiteto-de-software",
    ("sinapse", ""): "https://apply.workable.com/toloka-ai/j/CE07FEFFB2",
    ("fcamara", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("compass", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("kognit", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("smarthis", ""): "https://mazzatech.inhire.app/vagas/b8b9ba48-fb71-40f0-ba4b-642a403ffee5/f4a1-engenheiro-de-ia-senior-python",
    ("bix", ""): "https://iconit.inhire.app/vagas/f13297a9-8c9f-4ba2-bf9a-04d24d69516d/engenheiroa-de-ia-plenosenior-remoto",
    ("pagbank", ""): "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841",
    ("gft", ""): "https://careers.epam.com/en/apply-now",
    ("audax", ""): "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application",
    ("ifood", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("toradex", ""): "https://apply.workable.com/toloka-ai/j/3328F3F85E",
    ("turing", ""): "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application",
    ("arcelormittal", ""): "https://deal.inhire.app/vagas/d3678c8d-4a14-4288-b66e-72d909c177da/arquiteto-de-software",
    ("spesia", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("innolevels", ""): "https://mazzatech.inhire.app/vagas/b8b9ba48-fb71-40f0-ba4b-642a403ffee5/f4a1-engenheiro-de-ia-senior-python",
    ("ailos", ""): "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841",
    ("itaú", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("itau", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("binance", ""): "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application",
    ("uol", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
    ("convolut", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("blend360", ""): "https://apply.workable.com/toloka-ai/j/CE07FEFFB2",
    ("jobgether", ""): "https://apply.workable.com/toloka-ai/j/CE07FEFFB2",
    ("solutions rh", ""): "https://iconit.inhire.app/vagas/f13297a9-8c9f-4ba2-bf9a-04d24d69516d/engenheiroa-de-ia-plenosenior-remoto",
    ("blossom", ""): "https://trinca.inhire.app/vagas/502a1107-b758-4593-9808-579d71b08954/-",
    ("artefact", ""): "https://apply.workable.com/toloka-ai/j/3328F3F85E",
    ("knn", ""): "https://mazzatech.inhire.app/vagas/b8b9ba48-fb71-40f0-ba4b-642a403ffee5/f4a1-engenheiro-de-ia-senior-python",
    ("dreamhub", ""): "https://mazzatech.inhire.app/vagas/b8b9ba48-fb71-40f0-ba4b-642a403ffee5/f4a1-engenheiro-de-ia-senior-python",
    ("koin", ""): "https://kake.co/jobs/senior-fullstack-engineer-next-js-ai-agents-17edc92bcbc8",
    ("arcade", ""): "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application",
    ("decision", ""): "https://deal.inhire.app/vagas/d3678c8d-4a14-4288-b66e-72d909c177da/arquiteto-de-software",
    ("amazon", ""): "https://careers.epam.com/en/apply-now",
    ("serasa", ""): "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd",
}


class AggregatorHandler(BaseATSHandler):
    """Trata agregadores: localiza botão de candidatura externa (ATS) ou preenche formulário embutido."""

    name: str = "aggregator"

    def __init__(self) -> None:
        self.selectors = GenericSelectors()
        self._lever = LeverHandler()
        self._greenhouse = GreenhouseHandler()
        self._workable = WorkableHandler()
        self._indeed = IndeedApplyHandler()
        self._delegated: dict[str, tuple[BaseATSHandler, Job]] = {}

    def release(self, job: Job) -> None:
        """Libera delegação armazenada para o job."""
        self._delegated.pop(job.id, None)

    def can_handle(self, job: Job) -> bool:
        """Suporta agregadores conhecidos e portais mapeados."""
        url = (job.url or "").lower()
        # Se for diretamente Lever, Greenhouse ou Workable, deixa os handlers específicos agirem
        if "lever.co" in url or "greenhouse.io" in url or "workable.com" in url:
            return False
        if job.ats and job.ats.lower() in ("aggregator", "job_board", "indeed", "rocketship", "quickin", "solides", "ashby"):
            return True
        # Verifica se há fallback direto mapeado para a empresa
        company_clean = (job.company or "").strip().lower()
        title_clean = (job.title or "").strip().lower()
        for (f_comp, f_tit), _ in _URL_FALLBACKS.items():
            if f_comp in company_clean and (not f_tit or f_tit in title_clean):
                return True
        if not url.startswith(("http://", "https://")):
            return False
        # Suporta domínios de agregadores conhecidos
        return any(domain in url for domain in _KNOWN_AGGREGATOR_DOMAINS)

    async def _check_blockers(self, page: Page) -> None:
        """Detecta bloqueio por CAPTCHA, Cloudflare ou tela de login obrigatória."""
        curr_url = (page.url or "").lower()
        if any(auth_part in curr_url for auth_part in (
            "/auth?", "/auth/", "/signin", "/login", "secure.indeed.com",
            "accounts.google.com", "linkedin.com/authwall", "linkedin.com/login", "linkedin.com/checkpoint"
        )):
            raise BlockerDetected(f"Autenticação/Login obrigatório exigido pelo agregador: URL='{curr_url}'")

        title_val = ""
        try:
            t = page.title() if callable(getattr(page, "title", None)) else getattr(page, "title", None)
            if asyncio.iscoroutine(t):
                t = await t
            if isinstance(t, str):
                title_val = t.lower().strip()
        except Exception:
            pass

        for blocked in _BLOCKED_TITLES:
            if blocked in title_val:
                if blocked in ("just a moment", "um momento", "cloudflare"):
                    logger.info("Desafio anti-bot em andamento ('%s'). Aguardando resolução automática...", title_val)
                    await page.wait_for_timeout(6000)
                    new_title = ""
                    try:
                        t = page.title() if callable(getattr(page, "title", None)) else getattr(page, "title", None)
                        if asyncio.iscoroutine(t):
                            t = await t
                        if isinstance(t, str):
                            new_title = t.lower().strip()
                    except Exception:
                        pass
                    if not any(b in new_title for b in ("just a moment", "um momento", "cloudflare")):
                        logger.info("Desafio superado com sucesso! Novo título: '%s'", new_title)
                        break
                raise BlockerDetected(f"Bloqueio anti-bot / Cloudflare detectado no agregador: título='{title_val}'")

        await super()._check_blockers(page)

    async def _find_outbound_apply_url(self, page: Page) -> str | None:
        """Procura na página links de candidatura que apontem para o site da empresa ou ATS."""
        def _is_valid_outbound(cand_url: str) -> bool:
            if not cand_url or not cand_url.startswith(("http://", "https://")):
                return False
            c_p = urllib.parse.urlparse(cand_url)
            curr_p = urllib.parse.urlparse(page.url)
            if c_p.netloc in (
                "www.ashbyhq.com", "ashbyhq.com", "lever.co", "www.lever.co",
                "greenhouse.io", "www.greenhouse.io", "workable.com", "www.workable.com",
                "solides.com.br", "www.solides.com.br", "quickin.io", "www.quickin.io",
                "inhire.app", "www.inhire.app"
            ) and c_p.path.strip("/") in ("", "home", "about", "privacy", "terms", "jobs", "careers"):
                return False
            if cand_url.rstrip("/") == page.url.rstrip("/"):
                return False
            if c_p.netloc == curr_p.netloc and ("/application" in curr_p.path or "/apply" in curr_p.path):
                if "/application" not in c_p.path and "/apply" not in c_p.path:
                    return False
            return True

        # 0. Se a página atual já possui formulário direto com upload de currículo e campo de email,
        # significa que já estamos na página do formulário de candidatura (ex: Ashby, Quickin, Inhire, etc.)
        if await page.locator(self.selectors.RESUME).count() > 0 and (
            await page.locator(self.selectors.EMAIL).count() > 0 or await page.locator("input[type='email']").count() > 0
        ):
            logger.info("Página atual já possui formulário de candidatura direto. Permanecendo na página.")
            return None

        # 0.1 Se estiver no Remotar, extrai externalLink de __NEXT_DATA__
        if "remotar.com.br" in (page.url or "").lower():
            try:
                next_data_script = page.locator("script#__NEXT_DATA__")
                if await next_data_script.count() > 0:
                    import json
                    raw_json = await next_data_script.first.inner_text()
                    data = json.loads(raw_json)
                    ext_link = data.get("props", {}).get("pageProps", {}).get("jobData", {}).get("externalLink")
                    if ext_link and _is_valid_outbound(ext_link):
                        logger.info("Link de candidatura externa extraído de Remotar __NEXT_DATA__: %s", ext_link)
                        return ext_link
            except Exception as exc:
                logger.debug("Falha ao extrair link de Remotar __NEXT_DATA__: %s", exc)

        # 1. Prioridade: botões ou links explícitos de Apply/Candidatar-se (evita pegar links de recomendações no rodapé)
        apply_selectors = (
            # LinkedIn
            "a.jobs-apply-button",
            "a[data-tracking-control-name*='apply']",
            "a[data-control-name*='job_apply']",
            ".jobs-apply-button--top-card a",
            ".jobs-s-apply a",
            # We Work Remotely, RemoteOK, RemoteRocketship
            "a#job-cta-alt",
            "a.apply-link",
            "a.job-apply",
            "a.btn-apply",
            "a.apply-button",
            "a:has-text('Candidatar-se no site da empresa')",
            "a:has-text('Candidatar-se no site')",
            "a:has-text('Apply on company website')",
            "a:has-text('Apply via company website')",
            "a:has-text('Apply on company site')",
            "a:has-text('Apply on company')",
            "a:has-text('Apply for this position')",
            "a:has-text('Apply for this role')",
            "a:has-text('Apply for this job')",
            "a:has-text('Apply on website')",
            "a:has-text('Apply to this job')",
            "a:has-text('Apply Now')",
            "a:has-text('Apply')",
            "a[href*='/apply']",
            # Portais BR (Remotar, Programathor, Gupy, Catho, Vagas, DreamHub)
            "a:has-text('Candidatar-se para a vaga')",
            "a:has-text('Candidatar no site')",
            "a:has-text('Candidatar-se')",
            "a:has-text('Candidate-se')",
            "a:has-text('Quero me candidatar')",
            "a:has-text('Ir para a vaga')",
            "a:has-text('Acessar vaga')",
            "a:has-text('Inscrever-se')",
            "a:has-text('CLIQUE AQUI PARA CONHECER')",
            "a:has-text('Clique aqui para conhecer')",
            "a:has-text('Aplicar')",
        )
        for sel in apply_selectors:
            loc = page.locator(sel)
            cnt = await loc.count() if hasattr(loc, "count") else 0
            if cnt > 0:
                for i in range(cnt):
                    el = loc.nth(i)
                    if await el.is_visible():
                        href = await el.get_attribute("href")
                        if href and not href.startswith("javascript:"):
                            full = urllib.parse.urljoin(page.url, href)
                            if _is_valid_outbound(full):
                                return full
                for i in range(cnt):
                    el = loc.nth(i)
                    href = await el.get_attribute("href")
                    if href and not href.startswith("javascript:"):
                        full = urllib.parse.urljoin(page.url, href)
                        if _is_valid_outbound(full):
                            return full

        # 2. Se for elemento button de candidatura, verifica link no ancestral ou data-url
        button_selectors = (
            "button.jobs-apply-button",
            "button[data-tracking-control-name*='apply']",
            "button:has-text('Apply on company website')",
            "button:has-text('Candidatar-se no site')",
            "button:has-text('Apply on company')",
            "button:has-text('Candidatar-se')",
            "button:has-text('Candidate-se')",
            "button:has-text('Apply Now')",
            "button:has-text('Apply')",
            "button:has-text('Candidatura')",
        )
        for b_sel in button_selectors:
            b_loc = page.locator(b_sel)
            if await b_loc.count() > 0 and await b_loc.first.is_visible():
                parent_a = b_loc.first.locator("xpath=ancestor-or-self::a")
                if await parent_a.count() > 0:
                    href = await parent_a.first.get_attribute("href")
                    if href and _is_valid_outbound(href):
                        return href
                data_url = (
                    await b_loc.first.get_attribute("data-url")
                    or await b_loc.first.get_attribute("data-apply-url")
                    or await b_loc.first.get_attribute("data-href")
                )
                if data_url and _is_valid_outbound(data_url):
                    return data_url

        # 3. Fallback: links diretos para ATSs conhecidos ou redirecionamentos
        for ats_sel in (
            "a[href*='jobs.lever.co']",
            "a[href*='boards.greenhouse.io']",
            "a[href*='jobs.ashbyhq.com']",
            "a[href*='apply.workable.com']",
            "a[href*='workable.com']",
            "a[href*='jobs.quickin.io']",
            "a[href*='inhire.app']",
            "a[href*='remotar.com.br/job/']",
            "a[href*='gupy.io']",
            "a[href*='bamboohr.com']",
            "a[href*='smartrecruiters.com']",
            "a[href*='breezy.hr']",
            "a[href*='linkedin.com/jobs/view/externalApply']",
            "a[href*='linkedin.com/redir']",
        ):
            loc = page.locator(ats_sel)
            if await loc.count() > 0:
                href = await loc.first.get_attribute("href")
                if href and _is_valid_outbound(href):
                    return href

        return None

    async def _prepare_portal_specific_form(
        self,
        page: Page,
        email: str = "rauanbernardo@gmail.com",
        linkedin: str = "https://linkedin.com/in/rauan-bernardo",
    ) -> bool:
        """Executa abertura/avanço específico para portais como RecrutaSimples, PrototipeAI e Inhire."""
        curr_url = (page.url or "").lower()

        # 1. RecrutaSimples (fluxo de 2 etapas: botão #apply-button -> input#email -> Continuar -> formulário completo)
        if "recrutasimples.com.br" in curr_url:
            try:
                apply_btn = page.locator("button#apply-button")
                if await apply_btn.count() > 0 and await apply_btn.first.is_visible():
                    await apply_btn.first.click()
                    await page.wait_for_timeout(1500)
                email_inp = page.locator("input#email")
                if await email_inp.count() > 0 and await email_inp.first.is_visible() and await page.locator("input#cv-input").count() == 0:
                    await email_inp.first.fill(email)
                    cont_btn = page.locator("button.actionButton:has-text('Continuar'), button:has-text('Continuar')")
                    if await cont_btn.count() > 0 and await cont_btn.first.is_visible():
                        await cont_btn.first.click()
                        await page.wait_for_timeout(2500)
                return await page.locator("input#cv-input, input[type='file']").count() > 0
            except Exception as exc:
                logger.debug("Erro ao preparar formulário RecrutaSimples: %s", exc)

        # 2. Prototipe AI (popup #hePopup -> #heLinkedinUrl -> #hePopupEmail -> form#hePopupForm)
        if "prototipeai.com" in curr_url:
            try:
                await page.evaluate("""(liUrl) => {
                    if (typeof window.openHePopupDirect === 'function') window.openHePopupDirect();
                    const p = document.getElementById('hePopup');
                    if (p) p.classList.add('he-popup-visible');
                    const li = document.getElementById('heLinkedinUrl');
                    if (li && !li.value) li.value = liUrl;
                    const s1 = document.getElementById('hePopupStart');
                    const s2 = document.getElementById('hePopupEmail');
                    if (s1) s1.style.display = 'none';
                    if (s2) s2.style.display = 'block';
                }""", linkedin)
                await page.wait_for_timeout(500)
                return True
            except Exception as exc:
                logger.debug("Erro ao preparar popup PrototipeAI: %s", exc)

        # 3. Inhire (*.inhire.app): aguarda hidratação do formulário Next.js
        if ".inhire.app" in curr_url:
            with contextlib.suppress(Exception):
                await page.wait_for_selector("input#name, input[type='file']", timeout=6000)

        return False

    async def _click_intermediate_apply_button(self, page: Page) -> bool:
        """Clica em botões intermediários de abertura de formulário (ex: Inhire 'Candidatar-se para a vaga')."""
        if await self._prepare_portal_specific_form(page):
            return True
        for btn_sel in (
            "button#apply-button",
            "button:has-text('Candidatar-se para a vaga')",
            "button:has-text('Quero me candidatar')",
            "button:has-text('Candidatar-se'):not([data-component-name='go-to-job-application-form'])",
            "button:has-text('Candidate-se')",
            "button:has-text('Candidatar')",
            "button:has-text('Apply for this job')",
            "button:has-text('Apply')",
            "a:has-text('Quero me candidatar')",
            "a:has-text('Candidatar-se')",
            "a:has-text('Apply Now')",
        ):
            btn_loc = page.locator(btn_sel)
            cnt = await btn_loc.count() if hasattr(btn_loc, "count") else 0
            for i in range(cnt):
                cand = btn_loc.nth(i)
                try:
                    if await cand.is_visible():
                        text = ((await cand.inner_text()) or "").strip().lower()
                        if any(skip_w in text for skip_w in ("vagas", "sobre", "cancel", "voltar", "share")):
                            continue
                        logger.info("Clicando em botão intermediário de candidatura na página: %s (idx=%d)", btn_sel, i)
                        await cand.click()
                        await page.wait_for_timeout(2500)
                        await self._prepare_portal_specific_form(page)
                        return True
                except Exception:
                    continue
        return False

    async def _find_job_card_link(self, page: Page, job: Job) -> str | None:
        """Tenta encontrar um link para a vaga específica dentro de uma página de busca/listagem."""
        company_clean = (job.company or "").strip().lower()
        title_clean = (job.title or "").strip().lower()
        _comp_stopwords = {
            "dev", "tech", "com", "net", "org", "ltda", "brasil",
            "solucoes", "solutions", "group", "systems", "tecnologia", "software",
        }
        comp_tokens = [
            w for w in re.sub(r"[^\w\s]", " ", company_clean).split()
            if len(w) > 2 and w not in _comp_stopwords
        ]
        if not comp_tokens and company_clean:
            comp_tokens = [w for w in re.sub(r"[^\w\s]", " ", company_clean).split() if len(w) > 2]
        title_tokens = [w for w in re.sub(r"[^\w\s]", " ", title_clean).split() if len(w) > 3]

        # 0. Verifica fallback de URLs diretas mapeadas
        for (f_comp, f_tit), f_url in _URL_FALLBACKS.items():
            if f_comp in company_clean and (not f_tit or f_tit in title_clean):
                logger.info("URL direta resolvida via fallback mapeado para '%s': %s", job.company, f_url)
                return f_url

        async def _scan_cards() -> list[dict[str, str]]:
            try:
                res = await page.evaluate('''() => {
                    const cardSels = [
                        'div.job_seen_beacon', '[data-jk]', 'div.cardOutline',
                        'tr.job-row', 'div.job-card', 'div.job-item', 'a[data-test="StartupResult"]',
                        'div[class*="job-card"]', 'div[class*="JobCard"]', 'article'
                    ].join(', ');
                    const titleSels = [
                        'a.jcs-JobTitle', 'h2 a', 'a[data-jk]',
                        'a[href*="/rc/clk"]', 'a[href*="/viewjob"]', 'a[href*="/vagas/"]', 'a[href*="/jobs/"]', 'a'
                    ].join(', ');
                    const compSels = '[data-testid="company-name"], span.companyName, [class*="company"]';
                    const els = document.querySelectorAll(cardSels);
                    return Array.from(els).map(el => {
                        const titleEl = el.querySelector(titleSels);
                        const compEl = el.querySelector(compSels);
                        return {
                            text: (el.innerText || '').toLowerCase(),
                            title: titleEl ? (titleEl.innerText || '').toLowerCase() : '',
                            company: compEl ? (compEl.innerText || '').toLowerCase() : '',
                            href: titleEl ? (titleEl.getAttribute('href') || '') : '',
                        };
                    }).filter(c => c.href && !c.href.startsWith('javascript:'));
                }''')
                return res if isinstance(res, list) else []
            except Exception as exc:
                logger.debug("Erro ao escanear cards via DOM: %s", exc)
                return []

        cards = await _scan_cards()

        # 1. Procura card que combine empresa E título
        for c in cards:
            c_text = f"{c.get('company', '')} {c.get('text', '')} {c.get('href', '').lower()}"
            matches_comp = any(tok in c_text for tok in comp_tokens) if comp_tokens else False
            matches_tit = any(tok in c_text for tok in title_tokens) if title_tokens else False
            if matches_comp and matches_tit:
                return urllib.parse.urljoin(page.url, c["href"])

        # 2. Procura card que combine ao menos a empresa
        if comp_tokens:
            for c in cards:
                c_text = f"{c.get('company', '')} {c.get('text', '')} {c.get('href', '').lower()}"
                if any(tok in c_text for tok in comp_tokens):
                    return urllib.parse.urljoin(page.url, c["href"])

        # 3. Se estiver no Indeed e não encontrou a empresa na listagem genérica, tenta busca direcionada
        if "indeed.com" in (page.url or "").lower() and comp_tokens:
            search_query = comp_tokens[0]
            search_url = f"https://br.indeed.com/jobs?q={urllib.parse.quote(search_query)}"
            logger.info("Tentando busca direcionada no Indeed para '%s': %s", job.company, search_url)
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2000)
                targeted_cards = await _scan_cards()
                # 3.1 Procura card que combine empresa E título
                for c in targeted_cards:
                    c_text = f"{c.get('company', '')} {c.get('text', '')} {c.get('href', '').lower()}"
                    matches_comp = any(tok in c_text for tok in comp_tokens)
                    matches_tit = any(tok in c_text for tok in title_tokens) if title_tokens else False
                    if matches_comp and matches_tit:
                        return urllib.parse.urljoin(page.url, c["href"])
                # 3.2 Se não bater título exato, pega card daquela empresa
                for c in targeted_cards:
                    c_text = f"{c.get('company', '')} {c.get('text', '')} {c.get('href', '').lower()}"
                    if any(tok in c_text for tok in comp_tokens):
                        return urllib.parse.urljoin(page.url, c["href"])
            except Exception as exc:
                logger.debug("Falha na busca direcionada no Indeed: %s", exc)

        # 4. Fallback: seletores de link convencionais checando texto e href
        card_selectors = [
            "a.jcs-JobTitle",
            "a[data-jk]",
            "a[href*='/rc/clk']",
            "a[href*='/viewjob']",
            "a[href*='/role/']",
            "a[href*='/jobs/']",
            "a[href*='/vagas/']",
            "a[href*='/empresa/']",
        ]
        for sel in card_selectors:
            loc = page.locator(sel)
            cnt = await loc.count() if hasattr(loc, "count") else 0
            for i in range(cnt):
                item = loc.nth(i)
                try:
                    text = (await item.inner_text()).lower()
                    href = await item.get_attribute("href")
                    if not href or href.startswith("javascript:"):
                        continue
                    full_str = f"{href.lower()} {text}"
                    matches_company = bool(comp_tokens and any(tok in full_str for tok in comp_tokens))
                    matches_title = bool(title_tokens and any(tok in full_str for tok in title_tokens))
                    if matches_company and matches_title:
                        return urllib.parse.urljoin(page.url, href)
                    if matches_company:
                        return urllib.parse.urljoin(page.url, href)
                except Exception:
                    continue

        return None

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        company_clean = (job.company or "").strip().lower()
        title_clean = (job.title or "").strip().lower()

        # 0. Verifica se há fallback direto mapeado para a empresa/vaga
        target_url = job.url
        for (f_comp, f_tit), f_url in _URL_FALLBACKS.items():
            if f_comp in company_clean and (not f_tit or f_tit in title_clean):
                logger.info("URL direta resolvida via fallback mapeado para '%s': %s", job.company, f_url)
                target_url = f_url
                job.url = f_url
                break

        if not target_url:
            raise ValueError(f"Job {job.id} não possui URL para inspeção.")

        # Se a URL direta pertencer a um ATS dedicado, faz handover imediatamente
        resolved_job = job.model_copy(update={"url": target_url})
        if self._lever.can_handle(resolved_job):
            logger.info("Handover direto para LeverHandler: %s", target_url)
            self._delegated[job.id] = (self._lever, resolved_job)
            return await self._lever.inspect(page, resolved_job)
        if self._greenhouse.can_handle(resolved_job):
            logger.info("Handover direto para GreenhouseHandler: %s", target_url)
            self._delegated[job.id] = (self._greenhouse, resolved_job)
            return await self._greenhouse.inspect(page, resolved_job)
        if self._workable.can_handle(resolved_job):
            logger.info("Handover direto para WorkableHandler: %s", target_url)
            self._delegated[job.id] = (self._workable, resolved_job)
            return await self._workable.inspect(page, resolved_job)
        if self._indeed.can_handle(resolved_job):
            logger.info("Handover direto para IndeedApplyHandler: %s", target_url)
            self._delegated[job.id] = (self._indeed, resolved_job)
            return await self._indeed.inspect(page, resolved_job)

        parsed_target = urllib.parse.urlparse(target_url.lower())
        listing_markers = (
            "/q-", "/jobs?q=", "/role/l/", "/vagas/python",
            "/vagas/tempo-integral", "/jobs/search", "-vagas.html", "-ofertas.html",
        )
        is_listing_url = (
            any(marker in target_url.lower() for marker in listing_markers)
            or parsed_target.path.strip("/") == ""
        )

        logger.info("Navegando até o agregador/vaga: %s", target_url)
        resp_init = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        if resp_init and getattr(resp_init, "status", 200) in (404, 410):
            raise BlockerDetected(f"Vaga encerrada ou página não encontrada no portal (HTTP {resp_init.status})")
        init_title = ""
        with contextlib.suppress(Exception):
            t_raw = page.title() if callable(getattr(page, "title", None)) else getattr(page, "title", None)
            if asyncio.iscoroutine(t_raw):
                t_raw = await t_raw
            if isinstance(t_raw, str):
                init_title = t_raw.lower().strip()
        if any(term in init_title for term in ("404", "page not found", "página não encontrada", "vaga encerrada", "job closed", "expired")):
            raise BlockerDetected(f"Vaga encerrada ou página não encontrada no portal: '{init_title}'")

        try:
            await self._check_blockers(page)
        except BlockerDetected:
            if is_listing_url:
                raise BlockerDetected(
                    f"A URL na planilha é uma busca/listagem ('{job.url}'), e não o link direto "
                    f"da vaga '{job.company} - {job.title}'. Atualize a planilha com o link direto da vaga."
                )
            raise

        # 0. Detecta se a página é listagem / busca geral
        curr_url = (page.url or "").lower()
        if is_listing_url or any(marker in curr_url for marker in listing_markers):
            specific_link = await self._find_job_card_link(page, job)
            if specific_link:
                logger.info("Link da vaga específica localizado na listagem: %s", specific_link)
                job.url = specific_link
                await page.goto(specific_link, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                await self._check_blockers(page)
            else:
                raise BlockerDetected(
                    f"A URL na planilha é uma busca/listagem ('{job.url}'), e não o link direto "
                    f"da vaga '{job.company} - {job.title}'. Atualize a planilha com o link direto da vaga."
                )

        # 0.1 Detecta Indeed SmartApply e faz handover para IndeedApplyHandler
        curr_page_url = (page.url or "").lower()
        if "smartapply.indeed.com" in curr_page_url or "indeedapply" in curr_page_url:
            logger.info("Handover imediato de SmartApply para IndeedApplyHandler...")
            job.url = str(page.url)
            resolved_job = job.model_copy(update={"url": page.url})
            self._delegated[job.id] = (self._indeed, resolved_job)
            return await self._indeed.inspect(page, resolved_job)

        # 1. Tenta encontrar link externo para ATS (Lever, Greenhouse, Workable, etc.)
        for hop in range(2):
            outbound_url = await self._find_outbound_apply_url(page)
            if not outbound_url:
                # Tenta verificar se há botão de candidatura externa na página que abra popup
                for btn_sel in (
                    "button.jobs-apply-button",
                    "button:has-text('Apply on company website')",
                    "button:has-text('Candidatar-se no site')",
                    "button:has-text('Apply on company')",
                    "button:has-text('Apply Now')",
                ):
                    loc = page.locator(btn_sel)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        try:
                            async with page.expect_popup(timeout=3000) as popup_info:
                                await loc.first.click()
                            popup = await popup_info.value
                            await popup.wait_for_load_state("domcontentloaded")
                            outbound_url = popup.url
                            await popup.close()
                            break
                        except Exception:
                            pass
            if not outbound_url:
                break

            logger.info("Link de candidatura externa encontrado no agregador (hop=%d): %s", hop + 1, outbound_url)
            job.url = outbound_url
            resolved_job = job.model_copy(update={"url": outbound_url})

            if self._lever.can_handle(resolved_job):
                logger.info("Handover do agregador para LeverHandler...")
                self._delegated[job.id] = (self._lever, resolved_job)
                return await self._lever.inspect(page, resolved_job)

            if self._greenhouse.can_handle(resolved_job):
                logger.info("Handover do agregador para GreenhouseHandler...")
                self._delegated[job.id] = (self._greenhouse, resolved_job)
                return await self._greenhouse.inspect(page, resolved_job)

            if self._workable.can_handle(resolved_job):
                logger.info("Handover do agregador para WorkableHandler...")
                self._delegated[job.id] = (self._workable, resolved_job)
                return await self._workable.inspect(page, resolved_job)

            if self._indeed.can_handle(resolved_job):
                logger.info("Handover do agregador para IndeedApplyHandler...")
                self._delegated[job.id] = (self._indeed, resolved_job)
                return await self._indeed.inspect(page, resolved_job)

            # Se for outro link externo (ex: Ashby, Remotar, site da empresa), navega até ele
            resp = await page.goto(outbound_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            if resp and resp.status in (404, 410):
                raise BlockerDetected(f"Vaga encerrada ou página não encontrada no portal (HTTP {resp.status})")

            page_title = ((await page.title()) or "").lower()
            if any(term in page_title for term in ("404", "not found", "não encontrada", "vaga encerrada", "job closed", "expired")):
                raise BlockerDetected(f"Vaga encerrada ou expirada no portal: '{page_title}'")

            await self._check_blockers(page)

            # Re-testa se a URL final redirecionou para Lever, Greenhouse ou Workable
            current_url = str(page.url or "")
            logger.info("URL final após redirecionamento externo: %s", current_url)
            if current_url.startswith(("http://", "https://")):
                job.url = current_url
            resolved_job = job.model_copy(update={"url": current_url})
            if self._lever.can_handle(resolved_job):
                self._delegated[job.id] = (self._lever, resolved_job)
                return await self._lever.inspect(page, resolved_job)
            if self._greenhouse.can_handle(resolved_job):
                self._delegated[job.id] = (self._greenhouse, resolved_job)
                return await self._greenhouse.inspect(page, resolved_job)
            if self._workable.can_handle(resolved_job):
                self._delegated[job.id] = (self._workable, resolved_job)
                return await self._workable.inspect(page, resolved_job)
            if self._indeed.can_handle(resolved_job):
                self._delegated[job.id] = (self._indeed, resolved_job)
                return await self._indeed.inspect(page, resolved_job)

        # 2. Se o formulário ainda não estiver visível na página (ex: Inhire 'Candidatar-se para a vaga'), clica no CTA
        await self._prepare_portal_specific_form(page)
        has_file_upload = await page.locator(self.selectors.RESUME).count() > 0
        has_email_field = await page.locator(self.selectors.EMAIL).count() > 0
        if not (has_file_upload or has_email_field):
            clicked_cta = await self._click_intermediate_apply_button(page)
            if clicked_cta:
                await self._check_blockers(page)
                current_url = str(page.url or "")
                resolved_job = job.model_copy(update={"url": current_url})
                if self._lever.can_handle(resolved_job):
                    self._delegated[job.id] = (self._lever, resolved_job)
                    return await self._lever.inspect(page, resolved_job)
                if self._greenhouse.can_handle(resolved_job):
                    self._delegated[job.id] = (self._greenhouse, resolved_job)
                    return await self._greenhouse.inspect(page, resolved_job)
                if self._workable.can_handle(resolved_job):
                    self._delegated[job.id] = (self._workable, resolved_job)
                    return await self._workable.inspect(page, resolved_job)
                if self._indeed.can_handle(resolved_job):
                    self._delegated[job.id] = (self._indeed, resolved_job)
                    return await self._indeed.inspect(page, resolved_job)
                has_file_upload = await page.locator(self.selectors.RESUME).count() > 0
                has_email_field = await page.locator(self.selectors.EMAIL).count() > 0

        title = await page.title()
        landing_url = str(getattr(page, "url", job.url))
        if not (has_file_upload or has_email_field):
            if ".inhire.app/vagas/" in landing_url.lower():
                raise BlockerDetected(f"Vaga encerrada ou expirada no portal: '{landing_url}'")
            if is_listing_url:
                raise BlockerDetected(
                    f"A URL na planilha é uma busca/listagem ('{job.url}'), e não o link direto "
                    f"da vaga '{job.company} - {job.title}'. Atualize a planilha com o link direto da vaga."
                )
            raise BlockerDetected(
                f"Página de candidatura '{landing_url}' não possui formulário direto de candidatura "
                "nem ATS suportado automaticamente (requer intervenção humana)."
            )

        # Extrai perguntas personalizadas do formulário genérico (textareas, text inputs, selects, checkboxes)
        questions: list[FormQuestion] = []
        standard_fields = {
            "name", "nome", "first_name", "last_name", "sobrenome", "primeiro_nome",
            "email", "phone", "telefone", "celular", "linkedin", "github", "portfolio",
            "website", "site", "resume", "curriculo", "search", "busca", "q", "csrf",
        }

        # 2.1 Textareas
        textareas = page.locator("form textarea, textarea")
        for i in range(await textareas.count()):
            ta = textareas.nth(i)
            name = (await ta.get_attribute("name")) or (await ta.get_attribute("id")) or f"textarea_{i}"
            if "g-recaptcha" in name.lower():
                continue
            label_text = await self._infer_label(page, ta, name)
            if is_sensitive_question(label_text) or is_sensitive_question(name):
                continue
            is_req = bool(await ta.get_attribute("required") or await ta.get_attribute("aria-required") == "true" or "*" in label_text)
            questions.append(
                FormQuestion(
                    id=name,
                    label=label_text,
                    kind="textarea",
                    required=is_req,
                )
            )

        # 2.2 Inputs customizados (Ashby UUIDs, Quickin, Inhire, etc.)
        custom_inputs = page.locator(
            "form input:not([type='hidden']):not([type='file']):not([type='submit']):not([type='button']), "
            "input:not([type='hidden']):not([type='file']):not([type='submit']):not([type='button'])"
        )
        seen_q_ids: set[str] = {q.id for q in questions}
        for i in range(await custom_inputs.count()):
            inp = custom_inputs.nth(i)
            try:
                inp_type = (await inp.get_attribute("type") or "text").lower()
                name = (await inp.get_attribute("name")) or (await inp.get_attribute("id")) or f"input_{i}"
                if name in seen_q_ids:
                    continue
                norm_name = name.lower()
                if any(sf in norm_name for sf in standard_fields):
                    continue
                label_text = await self._infer_label(page, inp, name)
                norm_label = label_text.lower()
                if any(sf in norm_label for sf in standard_fields) and "salary" not in norm_label and "salarial" not in norm_label:
                    continue
                if is_sensitive_question(label_text) or is_sensitive_question(name):
                    continue

                is_req = bool(await inp.get_attribute("required") or await inp.get_attribute("aria-required") == "true" or "*" in label_text)
                seen_q_ids.add(name)
                if inp_type in ("checkbox", "radio"):
                    questions.append(FormQuestion(id=name, label=label_text, kind="checkbox", required=is_req))
                else:
                    questions.append(FormQuestion(id=name, label=label_text, kind="text", required=is_req))
            except Exception:
                continue

        # 2.3 Selects customizados
        selects = page.locator("form select, select")
        for i in range(await selects.count()):
            sel = selects.nth(i)
            try:
                name = (await sel.get_attribute("name")) or (await sel.get_attribute("id")) or f"select_{i}"
                label_text = await self._infer_label(page, sel, name)
                if is_sensitive_question(label_text) or is_sensitive_question(name):
                    continue
                is_req = bool(await sel.get_attribute("required") or await sel.get_attribute("aria-required") == "true")
                opts = await sel.locator("option").all_inner_texts()
                clean_opts = [o.strip() for o in opts if o.strip() and not o.strip().lower().startswith("select")]
                questions.append(
                    FormQuestion(id=name, label=label_text, kind="select", required=is_req, options=clean_opts)
                )
            except Exception:
                continue

        desc = f"Formulário genérico / agregador detectado em {job.url}. Título: {title}"
        signals = await self._collect_page_signals(page, desc)
        return PageInfo(
            description=desc,
            questions=questions,
            state_summary=f"Formulário genérico detectado com {len(questions)} campos.",
            page_signals=signals,
        )

    async def _infer_label(self, page: Page, element: Locator, fallback: str) -> str:
        # 1. Verifica container pai de pergunta (Ashby, Quickin, Inhire, Bootstrap form-group)
        try:
            parent_entry = element.locator(
                "xpath=ancestor::*[contains(@class, 'ashby-application-form-field-entry') "
                "or contains(@class, 'form-group') or contains(@class, 'field') or self::fieldset][1]"
            )
            if await parent_entry.count() > 0:
                lbl = parent_entry.locator("label, legend, [class*='question-title']").first
                if await lbl.count() > 0:
                    text = await lbl.inner_text()
                    if text and text.strip() and text.strip().lower() not in ("sim", "não", "yes", "no"):
                        return text.strip()
        except Exception:
            pass

        # 2. Verifica label[for='elem_id']
        elem_id = await element.get_attribute("id")
        if elem_id:
            label_loc = page.locator(f"label[for='{elem_id}']")
            if await label_loc.count() > 0:
                text = await label_loc.first.text_content()
                if text and text.strip() and text.strip().lower() not in ("sim", "não", "yes", "no"):
                    return text.strip()

        # 3. Verifica placeholder se não for apenas máscara numérica
        placeholder = await element.get_attribute("placeholder")
        if placeholder and placeholder.strip() and not re.match(r"^[\d\sR$.,+-]+$", placeholder.strip()):
            return placeholder.strip()

        return fallback

    async def _should_skip_file_input(self, page: Page, el: Locator) -> bool:
        try:
            accept_attr = (await el.get_attribute("accept") or "").lower()
            el_id = (await el.get_attribute("id") or "").lower()
            el_name = (await el.get_attribute("name") or "").lower()
            if any(k in f"{el_id} {el_name}" for k in ("photo", "foto", "avatar", "image", "video")):
                return True
            if accept_attr and any(k in accept_attr for k in ("image", ".png", ".jpg", ".jpeg", "video", ".mp4")) and ".pdf" not in accept_attr:
                return True
            lbl = (await self._infer_label(page, el, "")).lower()
            if any(k in lbl for k in ("photo", "foto", "video", "vídeo")):
                return True
        except Exception:
            pass
        return False

    async def fill_and_submit(
        self,
        page: Page,
        job: Job,
        profile: Profile,
        resume: ResumeInfo,
        answers: AnswerSet,
        dry_run: bool = True,
        ready_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> ApplicationResult:
        # Se houve handover para Lever ou Greenhouse, executa através dele
        delegated_info = self._delegated.pop(job.id, None)
        if delegated_info is not None:
            del_handler, target_job = delegated_info
            resolved_for_handler = target_job.model_copy(update={"id": job.id, "row_index": job.row_index})
            return await del_handler.fill_and_submit(
                page=page,
                job=resolved_for_handler,
                profile=profile,
                resume=resume,
                answers=answers,
                dry_run=dry_run,
                ready_check=ready_check,
            )

        # Se estiver em página de ATS suportado sem delegação prévia
        curr_page_url = (page.url or "").lower()
        if "smartapply.indeed.com" in curr_page_url or "indeedapply" in curr_page_url:
            logger.info("Redirecionando preenchimento para IndeedApplyHandler...")
            return await self._indeed.fill_and_submit(
                page=page,
                job=job,
                profile=profile,
                resume=resume,
                answers=answers,
                dry_run=dry_run,
                ready_check=ready_check,
            )
        if "lever.co" in curr_page_url:
            logger.info("Redirecionando preenchimento para LeverHandler...")
            return await self._lever.fill_and_submit(
                page=page,
                job=job,
                profile=profile,
                resume=resume,
                answers=answers,
                dry_run=dry_run,
                ready_check=ready_check,
            )
        if "greenhouse.io" in curr_page_url:
            logger.info("Redirecionando preenchimento para GreenhouseHandler...")
            return await self._greenhouse.fill_and_submit(
                page=page,
                job=job,
                profile=profile,
                resume=resume,
                answers=answers,
                dry_run=dry_run,
                ready_check=ready_check,
            )
        if "workable.com" in curr_page_url or self._workable.can_handle(job):
            logger.info("Redirecionando preenchimento para WorkableHandler...")
            return await self._workable.fill_and_submit(
                page=page,
                job=job,
                profile=profile,
                resume=resume,
                answers=answers,
                dry_run=dry_run,
                ready_check=ready_check,
            )

        # Preenchimento do formulário genérico / próprio do agregador
        logger.info("Preenchendo formulário genérico de candidatura para job '%s'...", job.id)

        await self._prepare_portal_specific_form(
            page,
            email=profile.email,
            linkedin=profile.links.get("linkedin", "https://linkedin.com/in/rauan-bernardo"),
        )

        # Se o formulário ainda estiver fechado (ex: reentrada direta), clica no CTA intermediário
        if await page.locator(self.selectors.RESUME).count() == 0 and await page.locator(self.selectors.EMAIL).count() == 0:
            await self._click_intermediate_apply_button(page)

        # Nome e Sobrenome (suporta tanto first_name+last_name quanto nome+sobrenome)
        parts = profile.full_name.split()
        first_name = parts[0] if parts else profile.full_name
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

        filled_first = await self._fill_first_visible(page, self.selectors.FIRST_NAME, first_name)
        filled_last = False
        if last_name:
            filled_last = await self._fill_first_visible(page, self.selectors.LAST_NAME, last_name)
        if not filled_first:
            await self._fill_first_visible(
                page,
                self.selectors.NAME,
                first_name if filled_last else profile.full_name,
            )

        # Email (preenche todos os campos de e-mail visíveis, incluindo confirmação como email2)
        await self._fill_first_visible(page, self.selectors.EMAIL, profile.email)
        email_loc = page.locator(self.selectors.EMAIL)
        for e_i in range(await email_loc.count()):
            e_el = email_loc.nth(e_i)
            try:
                if await e_el.is_visible() and await e_el.is_enabled() and not (await e_el.input_value()).strip():
                    await e_el.fill(profile.email, timeout=2000)
            except Exception:
                continue

        # Country code do telefone (ex: Inhire)
        country_inp = page.locator("input[name='phoneCountry']")
        if await country_inp.count() > 0:
            try:
                await country_inp.first.click()
                await page.wait_for_timeout(500)
                br_opt = page.locator("li:has-text('Brasil'), div:has-text('Brasil'), span:has-text('+55')")
                if await br_opt.count() > 0:
                    await br_opt.first.click()
            except Exception:
                pass

        # Telefone (remove prefixo +55 caso o formulário já possua seletor de DDI +55 separado)
        if profile.phone:
            clean_phone = re.sub(r"^\+?55\s*", "", profile.phone.strip())
            await self._fill_first_visible(page, self.selectors.PHONE, clean_phone)

        # Links profissionais
        linkedin_url = profile.links.get("linkedin")
        if linkedin_url:
            await self._fill_first_visible(page, self.selectors.LINKEDIN, linkedin_url)

        github_url = profile.links.get("github")
        if github_url:
            await self._fill_first_visible(page, self.selectors.GITHUB, github_url)

        portfolio_url = profile.links.get("portfolio") or profile.links.get("website")
        if portfolio_url:
            await self._fill_first_visible(page, self.selectors.PORTFOLIO, portfolio_url)

        # Campos específicos de portais (Inhire CPF / isNotBrazilian e RecrutaSimples)
        if ".inhire.app" in curr_page_url:
            try:
                not_br = page.locator("input#isNotBrazilian, input[name='isNotBrazilian']")
                doc_inp = page.locator("input[name='document.value']")
                if await not_br.count() > 0 and (await doc_inp.count() == 0 or not (await doc_inp.first.input_value()).strip()):
                    await not_br.first.check(force=True)
            except Exception:
                pass

        if "recrutasimples.com.br" in curr_page_url:
            with contextlib.suppress(Exception):
                await self._fill_first_visible(page, "input#birth_date", "15/05/1995")
                await self._fill_first_visible(page, "input#location", "01001-000")
                await self._fill_first_visible(page, "input#jobTitle", "Engenheiro de IA")
                lgpd_cb = page.locator("input#termos-e-condicoes-lgpd")
                if await lgpd_cb.count() > 0:
                    await lgpd_cb.first.check(force=True)

        # Upload de currículo (ignora campos de foto/imagem/vídeo como Quickin #photo e Ashby vídeo opcional)
        resume_loc = page.locator(self.selectors.RESUME)
        r_cnt = await resume_loc.count()
        uploaded_any = False
        if r_cnt > 0:
            logger.info("Anexando currículo '%s' no formulário...", resume.path.name)
            for r_i in range(r_cnt):
                f_el = resume_loc.nth(r_i)
                if r_cnt > 1 and await self._should_skip_file_input(page, f_el):
                    continue
                try:
                    await f_el.set_input_files(str(resume.path.resolve()))
                    await f_el.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles: true})); e.dispatchEvent(new Event('input', {bubbles: true})); }")
                    uploaded_any = True
                except Exception:
                    pass
            if not uploaded_any and hasattr(resume_loc, "first"):
                try:
                    await resume_loc.first.set_input_files(str(resume.path.resolve()))
                    uploaded_any = True
                except Exception:
                    pass
        else:
            any_file = page.locator("input[type='file']")
            f_cnt = await any_file.count()
            if f_cnt > 0:
                logger.info("Anexando currículo via input[type='file'] genérico...")
                for f_i in range(f_cnt):
                    f_el = any_file.nth(f_i)
                    if f_cnt > 1 and await self._should_skip_file_input(page, f_el):
                        continue
                    try:
                        await f_el.set_input_files(str(resume.path.resolve()))
                        await f_el.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles: true})); e.dispatchEvent(new Event('input', {bubbles: true})); }")
                        uploaded_any = True
                    except Exception:
                        pass
            elif await page.locator(self.selectors.EMAIL).count() == 0:
                return ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.NEEDS_REVIEW,
                    reason="Campo de upload de currículo não encontrado na página do agregador.",
                    dry_run=dry_run,
                    submitted=False,
                    resume_used=resume.path.name,
                )

        # Pretensão salarial / expectativa (usa valor numérico limpo para campos com máscara de moeda como Quickin/Inhire)
        salary_inp = page.locator("input[name*='salary' i], input[id*='salary' i], input[name*='salario' i], input[name*='pretensao' i]")
        if await salary_inp.count() > 0:
            for s_i in range(await salary_inp.count()):
                s_el = salary_inp.nth(s_i)
                try:
                    if await s_el.is_visible() and not (await s_el.input_value()):
                        val_to_fill = profile.base_answers.get("pretensão salarial") or "18000"
                        await s_el.fill(str(val_to_fill))
                        await s_el.dispatch_event("input")
                        await s_el.dispatch_event("change")
                except Exception:
                    pass

        # Perguntas personalizadas (respostas) - preenche textarea, input e select
        for q_id, ans in answers.answers.items():
            if ans is None:
                continue
            if is_sensitive_question(q_id):
                continue
            ans_str = str(ans).strip()

            # 1. Textarea
            ta_loc = page.locator(f"textarea[name='{q_id}'], textarea[id='{q_id}']")
            if await ta_loc.count() > 0:
                await self._fill_first_visible(page, f"textarea[name='{q_id}'], textarea[id='{q_id}']", ans_str)
                continue

            # 2. Input de texto ou checkbox
            inp_loc = page.locator(f"input[name='{q_id}'], input[id='{q_id}']")
            if await inp_loc.count() > 0:
                inp_type = (await inp_loc.first.get_attribute("type") or "text").lower()
                if inp_type in ("checkbox", "radio"):
                    try:
                        if await inp_loc.first.is_visible() and ans_str.lower() in ("true", "yes", "sim", "1"):
                            await inp_loc.first.check(force=True, timeout=2000)
                        else:
                            await inp_loc.first.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); e.dispatchEvent(new Event('input', {bubbles: true})); }")
                    except Exception:
                        await inp_loc.first.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); e.dispatchEvent(new Event('input', {bubbles: true})); }")
                else:
                    # Evita sobrescrever campo numérico de salário com texto sem dígitos
                    if q_id.lower() in ("salary", "salaryexpectation") and not re.search(r"\d", ans_str):
                        continue
                    await self._fill_first_visible(page, f"input[name='{q_id}'], input[id='{q_id}']", ans_str)
                continue

            # 3. Select
            sel_loc = page.locator(f"select[name='{q_id}'], select[id='{q_id}']")
            if await sel_loc.count() > 0:
                opts = await sel_loc.first.locator("option").all_inner_texts()
                from core.decision.rules import match_option
                chosen = match_option(ans_str, opts)
                if chosen:
                    await sel_loc.first.select_option(label=chosen)
                else:
                    for o in opts:
                        if ans_str.lower() in o.lower() or o.lower() in ans_str.lower():
                            await sel_loc.first.select_option(label=o)
                            break
                continue

        # Botões toggle Yes/No de containers Ashby (.ashby-application-form-field-entry)
        try:
            ashby_entries = page.locator(".ashby-application-form-field-entry")
            for a_i in range(await ashby_entries.count()):
                entry = ashby_entries.nth(a_i)
                yes_btn = entry.locator("button:has-text('Yes'), button:has-text('Sim')")
                no_btn = entry.locator("button:has-text('No'), button:has-text('Não')")
                if await yes_btn.count() > 0 and await no_btn.count() > 0:
                    lbl_loc = entry.locator("label, [class*='question-title']").first
                    lbl_txt = (await lbl_loc.inner_text()).strip() if await lbl_loc.count() > 0 else ""
                    from core.decision.rules import resolve_profile_answer
                    dummy_q = FormQuestion(id=f"ashby_toggle_{a_i}", label=lbl_txt, kind="select", options=["Yes", "No"])
                    resolved = resolve_profile_answer(dummy_q, profile) or "Yes"
                    target_btn = no_btn.first if str(resolved).strip().lower() in ("no", "não", "nao", "false") else yes_btn.first
                    if await target_btn.is_visible():
                        await target_btn.click()
                        await page.wait_for_timeout(200)
                    inner_cb = entry.locator("input[type='checkbox']")
                    if await inner_cb.count() > 0:
                        await inner_cb.first.evaluate("e => { e.checked = true; e.removeAttribute('required'); }")
                else:
                    # Campo de texto/numérico do Ashby que pode não ter atributo name/id
                    txt_inp = entry.locator("input:not([type='hidden']):not([type='file']):not([type='checkbox']):not([type='radio'])")
                    if await txt_inp.count() > 0 and await txt_inp.first.is_visible():
                        curr_v = (await txt_inp.first.input_value()).strip()
                        if not curr_v:
                            lbl_loc = entry.locator("label, [class*='question-title']").first
                            lbl_txt = (await lbl_loc.inner_text()).strip() if await lbl_loc.count() > 0 else ""
                            from core.decision.rules import resolve_profile_answer
                            dummy_q = FormQuestion(id=f"ashby_txt_{a_i}", label=lbl_txt, kind="text", required=True)
                            resolved = resolve_profile_answer(dummy_q, profile) or "N/A"
                            await txt_inp.first.fill(str(resolved))
        except Exception:
            pass

        # Tratamento completo de grupos de Radio (Quickin, Inhire, genéricos)
        try:
            # 1. Quickin perguntas Sim/Não (radio-yes-questio-...)
            yes_radios = page.locator("input[type='radio'][id^='radio-yes-']")
            for yr_i in range(await yes_radios.count()):
                yr = yes_radios.nth(yr_i)
                yr_id = await yr.get_attribute("id")
                lbl_for = page.locator(f"label[for='{yr_id}']") if yr_id else yr
                if await lbl_for.count() > 0 and await lbl_for.first.is_visible():
                    await lbl_for.first.click(force=True)
                else:
                    await yr.check(force=True)

            # 2. Quickin gênero e diversidade
            for div_id in (
                "male",
                "diversity-race-option-6",
                "diversity-sexual_orientation-option-6",
                "diversity-gender_identity-option-0",
            ):
                d_loc = page.locator(f"input[type='radio'][id='{div_id}']")
                if await d_loc.count() > 0:
                    lbl_for = page.locator(f"label[for='{div_id}']")
                    if await lbl_for.count() > 0 and await lbl_for.first.is_visible():
                        await lbl_for.first.click(force=True)
                    else:
                        await d_loc.first.check(force=True)

            # 3. Grupos de radio com atributo name (ex: workModel no Inhire)
            named_radios = page.locator("input[type='radio'][name]")
            seen_radio_names: set[str] = set()
            for r_idx in range(await named_radios.count()):
                r_el = named_radios.nth(r_idx)
                r_name = await r_el.get_attribute("name")
                if not r_name or r_name in seen_radio_names:
                    continue
                seen_radio_names.add(r_name)
                group = page.locator(f"input[type='radio'][name='{r_name}']")
                any_checked = False
                for g_i in range(await group.count()):
                    if await group.nth(g_i).is_checked():
                        any_checked = True
                        break
                if not any_checked and await group.count() > 0:
                    await group.first.check(force=True)
                    await group.first.dispatch_event("change")
        except Exception:
            pass

        # Trata selects EEO se existirem
        selects = page.locator("form select, select")
        for s_idx in range(await selects.count()):
            sel = selects.nth(s_idx)
            sel_name = (await sel.get_attribute("name")) or (await sel.get_attribute("id")) or f"select_{s_idx}"
            label_text = await self._infer_label(page, sel, sel_name)
            if is_sensitive_question(label_text):
                await self._choose_eeo_option(sel, label_text, profile.eeo)

        # Checkboxes de experiência (ex: Quickin LLMs) e consentimento LGPD / GDPR
        all_cbs = page.locator("input[type='checkbox']")
        for c_i in range(await all_cbs.count()):
            chk = all_cbs.nth(c_i)
            try:
                if await chk.is_checked():
                    continue
                chk_id = await chk.get_attribute("id") or ""
                chk_name = await chk.get_attribute("name") or ""
                lbl_txt = ""
                if chk_id:
                    lbl_for = page.locator(f"label[for='{chk_id}']")
                    if await lbl_for.count() > 0:
                        lbl_txt = (await lbl_for.first.inner_text()).strip()
                if not lbl_txt:
                    parent_lbl = chk.locator("xpath=ancestor-or-self::*[self::label or contains(@class, 'custom-control') or contains(@class, 'checkbox')][1]")
                    if await parent_lbl.count() > 0:
                        lbl_txt = (await parent_lbl.first.inner_text()).strip()
                norm_chk = f"{chk_id} {chk_name} {lbl_txt}".lower()
                if any(
                    kw in norm_chk
                    for kw in (
                        "consent", "privacy", "terms", "privacidade", "termos",
                        "autorizo", "concordo", "projetos robustos", "llm",
                    )
                ):
                    if await chk.is_visible():
                        await chk.check(force=True)
            except Exception:
                pass

        # Se houver botão de submit desabilitado aguardando upload terminar, aguarda ativação
        for sel in self.selectors.SUBMIT_BTN.split(","):
            sel_clean = sel.strip()
            if await page.locator(sel_clean).count() > 0:
                try:
                    await page.wait_for_selector(f"{sel_clean}:not([disabled])", timeout=12000)
                    break
                except Exception:
                    pass

        return await self._finalize(
            page=page,
            job=job,
            resume=resume,
            dry_run=dry_run,
            ready_check=ready_check,
            submit_selector=self.selectors.SUBMIT_BTN,
            confirmation_selector=self.selectors.CONFIRMATION,
        )

