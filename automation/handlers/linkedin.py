"""Handler de automação para LinkedIn Jobs (Easy Apply / redirecionamento externo)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Page

from automation.base_handler import BaseATSHandler
from core.browser_job_reader import resolve_linkedin_external_url
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
class LinkedInSelectors:
    EASY_APPLY_BTN: str = (
        "button.jobs-apply-button:has-text('Candidatura simplificada'), "
        "button.jobs-apply-button:has-text('Easy Apply'), "
        "button[aria-label*='Candidatura simplificada' i], "
        "button[aria-label*='Easy Apply' i]"
    )
    EXTERNAL_APPLY_BTN: str = (
        "a.jobs-apply-button, a[data-tracking-control-name*='apply'], "
        "button.jobs-apply-button:has-text('Candidatar-se'), button.jobs-apply-button:has-text('Apply')"
    )
    MODAL: str = ".jobs-easy-apply-modal, [role='dialog'][aria-modal='true']"
    NEXT_BTN: str = (
        "button[aria-label*='Avançar' i], button[aria-label*='Continue' i], "
        "button[aria-label*='Next' i], button[aria-label*='Revisar' i], button[aria-label*='Review' i]"
    )
    SUBMIT_BTN: str = (
        "button[aria-label*='Enviar candidatura' i], button[aria-label*='Submit application' i], "
        ".jobs-easy-apply-modal button:has-text('Enviar candidatura'), "
        ".jobs-easy-apply-modal button:has-text('Submit application')"
    )
    CONFIRMATION: str = (
        "h2:has-text('Candidatura enviada'), h2:has-text('Application sent'), "
        "h3:has-text('Sua candidatura foi enviada'), [data-test-modal-id='post-apply-modal']"
    )


class LinkedInHandler(BaseATSHandler):
    """Automação para LinkedIn Easy Apply com sessão autenticada e proteção contra checkpoint."""

    name: str = "linkedin"
    selectors: LinkedInSelectors = LinkedInSelectors()

    def can_handle(self, job: Job) -> bool:
        if not self.validate_job_url(job.url):
            return False
        if job.ats and job.ats.lower() == "linkedin":
            return True
        return "linkedin.com/jobs" in (job.url or "").lower()

    async def _is_auth_wall(self, page: Page) -> bool:
        cur_url = (page.url or "").lower()
        return any(seg in cur_url for seg in ("/login", "/uas/login", "/checkpoint", "/authwall"))

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        assert job.url is not None
        await page.goto(job.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        if await self._is_auth_wall(page):
            raise BlockerDetected("Sessão LinkedIn ausente/expirada — clique em 'Conectar com LinkedIn' no painel.")

        await self._check_blockers(page)

        description = job.description or ""
        with contextlib.suppress(Exception):
            desc_el = page.locator(".jobs-description__content, .show-more-less-html__markup, #job-details").first
            if await desc_el.count() > 0:
                txt = (await desc_el.inner_text()).strip()
                if txt:
                    description = txt[:3500]

        easy_btn = page.locator(self.selectors.EASY_APPLY_BTN).first
        has_easy = False
        with contextlib.suppress(Exception):
            has_easy = await easy_btn.count() > 0 and await easy_btn.is_visible()

        if not has_easy:
            # Verifica se existe link externo direto de ATS
            ext_loc = page.locator("a.jobs-apply-button, a[data-tracking-control-name*='apply']").first
            ext_href = ""
            with contextlib.suppress(Exception):
                if await ext_loc.count() > 0:
                    ext_href = await ext_loc.get_attribute("href") or ""
            resolved = resolve_linkedin_external_url(ext_href, description)
            if resolved:
                raise BlockerDetected(f"Vaga externa no LinkedIn — aplique via URL ATS: {resolved}")
            raise BlockerDetected("Botão 'Candidatura simplificada / Easy Apply' não disponível nesta vaga.")

        with contextlib.suppress(Exception):
            await easy_btn.click(timeout=5000)
            await page.wait_for_timeout(1000)

        questions: list[FormQuestion] = []
        modal = page.locator(self.selectors.MODAL).first
        if await modal.count() > 0:
            fields = modal.locator("input:not([type='hidden']):not([type='file']), textarea, select")
            cnt = await fields.count()
            for i in range(cnt):
                el = fields.nth(i)
                with contextlib.suppress(Exception):
                    if not await el.is_visible():
                        continue
                    tag = (await el.evaluate("e => e.tagName.toLowerCase()")).strip()
                    q_id = (await el.get_attribute("id")) or (await el.get_attribute("name")) or f"li_q_{i}"
                    label = (await el.get_attribute("aria-label")) or (await el.get_attribute("placeholder")) or q_id
                    if await el.get_attribute("id"):
                        lbl_el = modal.locator(f"label[for='{await el.get_attribute('id')}']").first
                        if await lbl_el.count() > 0:
                            lbl_txt = (await lbl_el.inner_text()).strip()
                            if lbl_txt:
                                label = lbl_txt
                    required = (
                        (await el.get_attribute("required")) is not None
                        or (await el.get_attribute("aria-required")) == "true"
                    )
                    kind = "textarea" if tag == "textarea" else ("select" if tag == "select" else "text")
                    options: list[str] = []
                    if tag == "select":
                        options = [opt.strip() for opt in await el.locator("option").all_inner_texts() if opt.strip()]
                    questions.append(FormQuestion(id=q_id, label=label, kind=kind, required=required, options=options))

        signals = await self._collect_page_signals(page, description)
        return PageInfo(
            description=description,
            questions=questions,
            state_summary=f"Modal LinkedIn Easy Apply com {len(questions)} campos visíveis",
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
        if await self._is_auth_wall(page):
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason="Sessão LinkedIn expirada — reconecte pelo painel.",
                dry_run=dry_run,
                submitted=False,
                resume_used=resume.path.name,
            )

        # Preenche campos iniciais, e-mail/telefone e sobe currículo sob medida
        await self._fill_first_visible(page, "input[id*='email' i], input[type='email']", profile.email)
        if profile.phone:
            await self._fill_first_visible(page, "input[id*='phone' i], input[type='tel']", profile.phone)

        for step in range(5):
            file_loc = page.locator(".jobs-easy-apply-modal input[type='file'], input[type='file']").first
            with contextlib.suppress(Exception):
                if await file_loc.count() > 0:
                    await file_loc.set_input_files(str(resume.path))

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

            submit_loc = page.locator(self.selectors.SUBMIT_BTN)
            if await submit_loc.count() > 0 and await submit_loc.first.is_visible():
                break

            next_loc = page.locator(self.selectors.NEXT_BTN).first
            if await next_loc.count() > 0 and await next_loc.is_visible() and step < 4:
                await next_loc.click(timeout=5000)
                await page.wait_for_timeout(800)
            else:
                break

        return await self._finalize(
            page=page,
            job=job,
            resume=resume,
            dry_run=dry_run,
            ready_check=ready_check,
            submit_selector=self.selectors.SUBMIT_BTN,
            confirmation_selector=self.selectors.CONFIRMATION,
        )
