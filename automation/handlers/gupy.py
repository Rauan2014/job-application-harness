"""Handler de automação para vagas na Gupy (*.gupy.io) usando sessão conectada."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Page

from automation.base_handler import BaseATSHandler
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


@dataclass(frozen=True)
class GupySelectors:
    APPLY_BTN: str = (
        "button[data-testid='job-apply-button'], a[href*='/apply'], "
        "button:has-text('Candidatar-se'), a:has-text('Candidatar-se'), "
        "button:has-text('Apply for this job')"
    )
    LOGIN_GATE: str = (
        "input[name='identification'], input[name='cpf'], input[type='password'], "
        "form[action*='auth.gupy.io'], button:has-text('Entrar com CPF')"
    )
    NAME_INPUT: str = "input[name='name'], input[name='fullName'], input[id*='name' i]"
    EMAIL_INPUT: str = "input[name='email'], input[type='email']"
    PHONE_INPUT: str = "input[name='phone'], input[name='mobilePhone'], input[type='tel']"
    FILE_INPUT: str = "input[type='file']"
    SUBMIT_BTN: str = (
        "button:has-text('Confirmar candidatura'), button:has-text('Enviar candidatura'), "
        "button:has-text('Finalizar candidatura'), button[type='submit']"
    )
    CONFIRMATION: str = (
        "h1:has-text('Candidatura enviada'), h2:has-text('Candidatura enviada'), "
        "h1:has-text('Inscrição realizada'), [data-testid*='application-success']"
    )


class GupyHandler(BaseATSHandler):
    """Automação para candidaturas em portais Gupy aproveitando sessão autenticada."""

    name: str = "gupy"
    selectors: GupySelectors = GupySelectors()

    def can_handle(self, job: Job) -> bool:
        if not self.validate_job_url(job.url):
            return False
        if job.ats and job.ats.lower() == "gupy":
            return True
        return "gupy.io" in (job.url or "").lower()

    async def _detect_gupy_auth_gate(self, page: Page) -> bool:
        cur_url = (page.url or "").lower()
        if any(seg in cur_url for seg in ("auth.gupy.io", "login.gupy.io", "/signin")):
            return True
        loc = page.locator(self.selectors.LOGIN_GATE)
        cnt = await loc.count()
        for i in range(cnt):
            with contextlib.suppress(Exception):
                if await loc.nth(i).is_visible():
                    return True
        return False

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        assert job.url is not None
        await page.goto(job.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)

        # Clica em "Candidatar-se" se ainda estiver na descrição da vaga
        apply_btn = page.locator(self.selectors.APPLY_BTN).first
        with contextlib.suppress(Exception):
            if await apply_btn.count() > 0 and await apply_btn.is_visible():
                await apply_btn.click(timeout=5000)
                await page.wait_for_timeout(1200)

        if await self._detect_gupy_auth_gate(page):
            raise BlockerDetected("Sessão Gupy ausente ou expirada — clique em 'Conectar com Gupy' no painel.")

        await self._check_blockers(page)

        description = job.description or ""
        with contextlib.suppress(Exception):
            main_loc = page.locator("main, [data-testid='job-description'], article").first
            if await main_loc.count() > 0:
                txt = (await main_loc.inner_text()).strip()
                if txt:
                    description = txt[:3500]

        questions: list[FormQuestion] = []
        fields = page.locator("form input:not([type='hidden']):not([type='file']), form textarea, form select")
        cnt = await fields.count()
        for i in range(cnt):
            el = fields.nth(i)
            with contextlib.suppress(Exception):
                if not await el.is_visible():
                    continue
                tag = (await el.evaluate("e => e.tagName.toLowerCase()")).strip()
                input_type = (await el.get_attribute("type") or "text").lower()
                if input_type in ("submit", "button", "reset"):
                    continue
                q_id = (await el.get_attribute("id")) or (await el.get_attribute("name")) or f"gupy_q_{i}"
                norm_id = q_id.lower()
                if any(k in norm_id for k in ("name", "email", "phone", "mobile")):
                    continue

                label = (await el.get_attribute("aria-label")) or (await el.get_attribute("placeholder")) or q_id
                if await el.get_attribute("id"):
                    lbl_el = page.locator(f"label[for='{await el.get_attribute('id')}']").first
                    if await lbl_el.count() > 0:
                        lbl_txt = (await lbl_el.inner_text()).strip()
                        if lbl_txt:
                            label = lbl_txt

                required = (
                    (await el.get_attribute("required")) is not None
                    or (await el.get_attribute("aria-required")) == "true"
                )
                if tag == "textarea":
                    kind = "textarea"
                elif tag == "select":
                    kind = "select"
                elif input_type == "checkbox":
                    kind = "checkbox"
                else:
                    kind = "text"
                options: list[str] = []
                if tag == "select":
                    options = [opt.strip() for opt in await el.locator("option").all_inner_texts() if opt.strip()]

                questions.append(FormQuestion(id=q_id, label=label, kind=kind, required=required, options=options))

        signals = await self._collect_page_signals(page, description)
        return PageInfo(
            description=description,
            questions=questions,
            state_summary=f"Formulário Gupy com {len(questions)} perguntas extras",
            page_signals=signals,
        )

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
        if await self._detect_gupy_auth_gate(page):
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason="Login Gupy necessário — clique em 'Conectar com Gupy' no painel.",
                dry_run=dry_run,
                submitted=False,
                resume_used=resume.path.name,
            )

        await self._fill_first_visible(page, self.selectors.NAME_INPUT, profile.full_name)
        await self._fill_first_visible(page, self.selectors.EMAIL_INPUT, profile.email)
        if profile.phone:
            await self._fill_first_visible(page, self.selectors.PHONE_INPUT, profile.phone)

        # Upload do PDF sob medida
        file_loc = page.locator(self.selectors.FILE_INPUT).first
        with contextlib.suppress(Exception):
            if await file_loc.count() > 0:
                await file_loc.set_input_files(str(resume.path))

        # Respostas customizadas
        for q_id, val in answers.answers.items():
            if val is None:
                continue
            target = page.locator(f"#{q_id}, [name='{q_id}']").first
            with contextlib.suppress(Exception):
                if await target.count() == 0 or not await target.is_visible():
                    continue
                tag = (await target.evaluate("e => e.tagName.toLowerCase()")).strip()
                if tag == "select":
                    await target.select_option(label=str(val))
                else:
                    await target.fill(str(val))

        return await self._finalize(
            page=page,
            job=job,
            resume=resume,
            dry_run=dry_run,
            ready_check=ready_check,
            submit_selector=self.selectors.SUBMIT_BTN,
            confirmation_selector=self.selectors.CONFIRMATION,
        )
