"""Handler de automação para formulários da plataforma Greenhouse (boards.greenhouse.io)."""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Page

from automation.base_handler import BaseATSHandler
from core.decision.rules import is_sensitive_question
from core.models import (
    AnswerSet,
    ApplicationResult,
    FormQuestion,
    Job,
    PageInfo,
    Profile,
    ResumeInfo,
    TaskStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GreenhouseSelectors:
    FORM: str = "#application_form, form[action*='greenhouse'], form"
    FIRST_NAME: str = "input#first_name, input[name*='first_name']"
    LAST_NAME: str = "input#last_name, input[name*='last_name']"
    EMAIL: str = "input#email, input[name*='email']"
    PHONE: str = "input#phone, input[name*='phone']"
    RESUME: str = "input[type='file'][id*='resume'], input#resume_file, input[type='file']"
    LINKEDIN: str = (
        "input[id*='job_application_answers_attributes'][autocomplete*='url'], "
        "input[name*='linkedin' i], input#job_application_answers_attributes_0_text_value"
    )
    WEBSITE: str = "input[name*='website' i], input#job_application_answers_attributes_1_text_value"
    CUSTOM_CONTAINER: str = "#custom_fields, .custom-fields"
    EEO_CONTAINER: str = "#eeo_questions, #demographic_questions"
    SUBMIT_BTN: str = "input#submit_app, button#submit_app, input[type='submit'], button[type='submit']"
    CONFIRMATION: str = (
        "#application_confirmation, .application-confirmed, #application-submitted, "
        "h1:has-text('Obrigado'), h1:has-text('Thank you'), h2:has-text('Obrigado'), "
        "h2:has-text('Thank you'), [data-testid*='confirmation']"
    )
    CAPTCHA: tuple[str, ...] = (
        ".g-recaptcha",
        "#recaptcha",
        "#recaptcha-mock",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='turnstile']",
    )
    LOGIN: tuple[str, ...] = (
        "form[action*='/login']",
        "form[action*='/signin']",
        "input[type='password']",
    )


class GreenhouseHandler(BaseATSHandler):
    """Automação robusta para candidaturas na plataforma Greenhouse."""

    name: str = "greenhouse"
    selectors: GreenhouseSelectors = GreenhouseSelectors()

    def can_handle(self, job: Job) -> bool:
        if not self.validate_job_url(job.url):
            return False
        if job.ats and job.ats.lower() == "greenhouse":
            return True
        url = (job.url or "").lower()
        return "greenhouse.io" in url or "boards.greenhouse.io" in url or "job-boards.greenhouse.io" in url

    async def _check_blockers(self, page: Page) -> None:
        """Verifica se há CAPTCHA ou tela de login bloqueando o formulário."""
        await super()._check_blockers(page)

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        if not job.url:
            raise ValueError(f"Job {job.id} não possui URL para inspeção.")

        await page.goto(job.url, wait_until="domcontentloaded")
        await self._check_blockers(page)

        # Extração de descrição completa (título + corpo da vaga)
        header_text = ""
        header_loc = page.locator("#header, h1.app-title")
        if await header_loc.count() > 0:
            header_text = (await header_loc.first.inner_text()).strip()

        body_text = ""
        for desc_sel in ["#content", ".content", "#job-description"]:
            loc = page.locator(desc_sel)
            if await loc.count() > 0:
                text = await loc.first.inner_text()
                if text.strip():
                    body_text = text.strip()
                    break

        description_text = f"{header_text}\n\n{body_text}" if header_text and body_text else header_text or body_text

        # Extração de campos personalizados (#custom_fields .field)
        questions: list[FormQuestion] = []
        fields = page.locator(f"{self.selectors.CUSTOM_CONTAINER} .field, #application_form .field")
        count = await fields.count()

        for idx in range(count):
            field = fields.nth(idx)
            label_loc = field.locator("label").first
            if await label_loc.count() == 0:
                continue

            label_text = (await label_loc.inner_text()).strip()
            norm_label = label_text.lower()
            if any(k in norm_label for k in ["first name", "last name", "email", "phone", "resume", "curriculo"]):
                continue

            is_req = "*" in label_text or await field.locator("[required]").count() > 0
            clean_label = re.sub(r"\s*\*.*$", "", label_text).strip()

            field_id = f"gh_field_{idx}"
            kind: str = "text"
            options: list[str] = []

            select_loc = field.locator("select").first
            if await select_loc.count() > 0:
                kind = "select"
                field_id = await select_loc.get_attribute("id") or await select_loc.get_attribute("name") or field_id
                opts = select_loc.locator("option")
                opt_count = await opts.count()
                for o_idx in range(opt_count):
                    opt_text = (await opts.nth(o_idx).inner_text()).strip()
                    if opt_text and not opt_text.lower().startswith("select"):
                        options.append(opt_text)
            elif await field.locator("textarea").count() > 0:
                kind = "textarea"
                txt_loc = field.locator("textarea").first
                field_id = await txt_loc.get_attribute("id") or await txt_loc.get_attribute("name") or field_id
            elif await field.locator("input[type='checkbox']").count() > 0:
                kind = "checkbox"
                chk_loc = field.locator("input[type='checkbox']").first
                field_id = await chk_loc.get_attribute("id") or await chk_loc.get_attribute("name") or field_id
            elif await field.locator("input[type='text'], input:not([type])").count() > 0:
                txt_loc = field.locator("input[type='text'], input:not([type])").first
                field_id = await txt_loc.get_attribute("id") or await txt_loc.get_attribute("name") or field_id

            questions.append(
                FormQuestion(
                    id=field_id,
                    label=clean_label,
                    kind=kind,  # type: ignore[arg-type]
                    required=is_req,
                    options=options,
                )
            )

        summary = f"Greenhouse job '{job.title}' em '{job.company}' com {len(questions)} campos customizados."
        signals = await self._collect_page_signals(page, description_text)
        return PageInfo(description=description_text, questions=questions, state_summary=summary, page_signals=signals)

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
        if not job.url:
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.FAILED,
                reason="URL da vaga vazia.",
                dry_run=dry_run,
            )

        if page.url != job.url:
            await page.goto(job.url, wait_until="domcontentloaded")

        await self._check_blockers(page)

        name_parts = profile.full_name.strip().split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        if await page.locator(self.selectors.FIRST_NAME).count() > 0:
            await page.fill(self.selectors.FIRST_NAME, first_name)
        if last_name and await page.locator(self.selectors.LAST_NAME).count() > 0:
            await page.fill(self.selectors.LAST_NAME, last_name)

        # 2. Email e Telefone
        if await page.locator(self.selectors.EMAIL).count() > 0:
            await page.fill(self.selectors.EMAIL, profile.email)

        if profile.phone and await page.locator(self.selectors.PHONE).count() > 0:
            await page.fill(self.selectors.PHONE, profile.phone)

        # 3. Anexo do currículo
        file_loc = page.locator(self.selectors.RESUME).first
        if await file_loc.count() > 0:
            await file_loc.set_input_files(str(resume.path))
        else:
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason="Campo de upload de currículo não encontrado no Greenhouse.",
                dry_run=dry_run,
            )

        # 4. Links (LinkedIn e Website)
        if "linkedin" in profile.links and await page.locator(self.selectors.LINKEDIN).count() > 0:
            await page.fill(self.selectors.LINKEDIN, profile.links["linkedin"])

        website_val = profile.links.get("portfolio") or profile.links.get("github")
        if website_val and await page.locator(self.selectors.WEBSITE).count() > 0:
            await page.fill(self.selectors.WEBSITE, website_val)

        # 5. Campos customizados
        custom_fields = page.locator(f"{self.selectors.CUSTOM_CONTAINER} .field")
        c_count = await custom_fields.count()
        for idx in range(c_count):
            field = custom_fields.nth(idx)
            label_loc = field.locator("label").first
            if await label_loc.count() == 0:
                continue
            label_text = (await label_loc.inner_text()).strip()
            if is_sensitive_question(label_text):
                continue

            input_elem = field.locator("input, select, textarea").first
            if await input_elem.count() == 0:
                continue

            field_id = (
                await input_elem.get_attribute("id")
                or await input_elem.get_attribute("name")
                or f"gh_field_{idx}"
            )
            ans_val: str | None = answers.answers.get(field_id)

            if ans_val is None:
                clean_lbl = re.sub(r"\s*\*.*$", "", label_text).strip().lower()
                for qid, qans in answers.answers.items():
                    if qid.lower() in clean_lbl or clean_lbl in qid.lower():
                        ans_val = qans
                        break

            if ans_val is None:
                from core.decision.rules import resolve_profile_answer
                sel_elem = field.locator("select").first
                opts = []
                if await sel_elem.count() > 0:
                    opts = await sel_elem.locator("option").all_inner_texts()
                q_kind = "select" if opts else "text"
                gh_q = FormQuestion(id=field_id, label=label_text, kind=q_kind, options=opts)
                ans_val = resolve_profile_answer(gh_q, profile)

            if ans_val is not None:
                if await field.locator("select").count() > 0:
                    sel = field.locator("select").first
                    opts = await sel.locator("option").all_inner_texts()
                    from core.decision.rules import match_option
                    chosen = match_option(str(ans_val), opts)
                    if chosen:
                        await sel.select_option(label=chosen)
                    else:
                        for o in opts:
                            if ans_val.lower() in o.lower():
                                await sel.select_option(label=o)
                                break

                elif await field.locator("textarea").count() > 0:
                    await field.locator("textarea").first.fill(str(ans_val))
                elif await field.locator("input[type='checkbox']").count() > 0:
                    if str(ans_val).lower() in {"true", "yes", "sim", "1"}:
                        await field.locator("input[type='checkbox']").first.check()
                elif await field.locator("input[type='text'], input:not([type])").count() > 0:
                    await field.locator("input[type='text'], input:not([type])").first.fill(str(ans_val))

        # 5.1 Suporte ao novo Greenhouse React (job-boards.greenhouse.io: comboboxes e inputs question_*)
        try:
            comboboxes = page.locator("input[role='combobox']")
            for cb_i in range(await comboboxes.count()):
                cb = comboboxes.nth(cb_i)
                cb_id = (await cb.get_attribute("id") or "").lower()
                if "iti-" in cb_id or not await cb.is_visible():
                    continue
                await cb.click()
                if "country" in cb_id:
                    await cb.fill("Brazil")
                elif "school" in cb_id:
                    await cb.fill("Universidade")
                elif "degree" in cb_id:
                    await cb.fill("Bachelor")
                elif "discipline" in cb_id:
                    await cb.fill("Computer")
                opt_loc = page.locator("[role='option'], div[class*='option']").first
                with contextlib.suppress(Exception):
                    await opt_loc.wait_for(state="visible", timeout=1500)
                if await opt_loc.count() > 0 and await opt_loc.is_visible():
                    await opt_loc.click()
                else:
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")

            q_inputs = page.locator("input[id^='question_']:not([role='combobox'])")
            for qi in range(await q_inputs.count()):
                q_el = q_inputs.nth(qi)
                if not await q_el.is_visible() or (await q_el.input_value()).strip():
                    continue
                q_id_attr = await q_el.get_attribute("id") or ""
                lbl_loc = page.locator(f"label[for='{q_id_attr}']")
                lbl_t = ((await lbl_loc.first.inner_text()) if await lbl_loc.count() > 0 else "").lower()
                if "linkedin" in lbl_t:
                    await q_el.fill(profile.links.get("linkedin", ""))
                elif "pretens" in lbl_t or "salar" in lbl_t:
                    await q_el.fill(str(profile.base_answers.get("pretensão salarial") or "R$ 15.000,00"))
                elif "acessibilidade" in lbl_t or "adapta" in lbl_t:
                    await q_el.fill("Não")
                else:
                    await q_el.fill("Sim")
        except Exception:
            pass

        # 6. Campos EEO / Demográficos (nunca enviados ao LLM, estritamente profile.eeo ou decline)
        eeo_fields = page.locator(f"{self.selectors.EEO_CONTAINER} .field")
        e_count = await eeo_fields.count()
        for idx in range(e_count):
            field = eeo_fields.nth(idx)
            label_loc = field.locator("label").first
            if await label_loc.count() == 0:
                continue
            label_text = (await label_loc.inner_text()).strip().lower()
            select_loc = field.locator("select").first
            if await select_loc.count() > 0:
                await self._choose_eeo_option(select_loc, label_text, profile.eeo)

        # Varredura autônoma de segurança: nenhum campo obrigatório visível pode ficar em branco
        req_loc = page.locator("input[required], select[required], textarea[required], [aria-required='true']")
        for ri in range(await req_loc.count()):
            el = req_loc.nth(ri)
            try:
                if not await el.is_visible():
                    continue
                tag = (await el.evaluate("e => e.tagName.toLowerCase()")).strip()
                t_type = (await el.get_attribute("type") or "").lower()
                role_attr = (await el.get_attribute("role") or "").lower()
                if role_attr == "combobox":
                    continue
                if tag == "select":
                    val = (await el.input_value()).strip()
                    if not val:
                        opts = await el.locator("option").all_inner_texts()
                        for o in opts:
                            if o.strip() and not o.lower().startswith("select") and not o.lower().startswith("selecion"):
                                await el.select_option(label=o)
                                break
                elif tag == "input" and t_type == "radio":
                    r_name = await el.get_attribute("name")
                    if r_name:
                        group = page.locator(f"input[type='radio'][name='{r_name}']")
                        any_ch = False
                        for gi in range(await group.count()):
                            if await group.nth(gi).is_checked():
                                any_ch = True
                                break
                        if not any_ch and await group.count() > 0:
                            await group.first.check()
                elif tag == "input" and t_type == "checkbox":
                    if not await el.is_checked():
                        await el.check()
                elif tag in ("input", "textarea"):
                    if t_type not in ("file", "hidden", "submit", "button"):
                        val = (await el.input_value()).strip()
                        if not val:
                            name_attr = (await el.get_attribute("name") or "").lower()
                            id_attr = (await el.get_attribute("id") or "").lower()
                            combined_attr = f"{name_attr} {id_attr}"
                            if any(k in combined_attr for k in ("first_name", "last_name", "email", "phone", "first", "last")):
                                continue
                            if "org" in name_attr or "company" in name_attr:
                                await el.fill(profile.current_company or "Desenvolvedor de Software / Autônomo")
                            elif "salary" in name_attr or "salario" in name_attr:
                                await el.fill("18000")
                            else:
                                await el.fill("Sim / Yes")
            except Exception:
                continue

        return await self._finalize(
            page=page,
            job=job,
            resume=resume,
            dry_run=dry_run,
            ready_check=ready_check,
            submit_selector=self.selectors.SUBMIT_BTN,
            confirmation_selector=self.selectors.CONFIRMATION,
        )
