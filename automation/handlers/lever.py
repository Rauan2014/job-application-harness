"""Handler de automação para formulários da plataforma Lever (jobs.lever.co)."""

from __future__ import annotations

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
class LeverSelectors:
    FORM: str = "#application-form, form[action*='lever'], form"
    NAME: str = "input[name='name']"
    EMAIL: str = "input[name='email']"
    PHONE: str = "input[name='phone']"
    ORG: str = "input[name='org']"
    LINKEDIN: str = "input[name='urls[LinkedIn]']"
    GITHUB: str = "input[name='urls[GitHub]']"
    PORTFOLIO: str = "input[name='urls[Portfolio]']"
    OTHER_URL: str = "input[name='urls[Other]']"
    LOCATION: str = "input#location-input, input.location-input, input[name='location']"
    RESUME: str = "input[type='file'][name='resume'], input[type='file']"
    CUSTOM_CARDS: str = ".application-question"
    SUBMIT_BTN: str = (
        "button#btn-submit, button#create-application, .template-btn-submit, "
        "button[type='submit']:not(#hcaptchaSubmitBtn):not(.hidden)"
    )
    CONFIRMATION: str = ".application-success, [data-qa='success-message']"
    CAPTCHA: tuple[str, ...] = (
        ".g-recaptcha",
        "#recaptcha",
        "#recaptcha-mock",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='turnstile']",
        ".cf-turnstile",
    )
    LOGIN: tuple[str, ...] = (
        "form[action*='/login']",
        "form[action*='/signin']",
        "input[type='password']",
    )


class LeverHandler(BaseATSHandler):
    """Automação robusta para candidaturas na plataforma Lever."""

    name: str = "lever"
    selectors: LeverSelectors = LeverSelectors()

    def can_handle(self, job: Job) -> bool:
        if job.ats and job.ats.lower() == "lever":
            return True
        url = (job.url or "").lower()
        return "lever.co" in url or "jobs.lever.co" in url

    async def _check_blockers(self, page: Page) -> None:
        """Verifica se há CAPTCHA ou tela de login bloqueando o formulário."""
        await super()._check_blockers(page)

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        if not job.url:
            raise ValueError(f"Job {job.id} não possui URL para inspeção.")

        await page.goto(job.url, wait_until="domcontentloaded")
        await self._check_blockers(page)

        # Extração de descrição se disponível
        description_text = ""
        for desc_sel in [".posting-description", ".section.page-centered", ".posting-headline"]:
            loc = page.locator(desc_sel)
            if await loc.count() > 0:
                text = await loc.first.inner_text()
                if text.strip():
                    description_text = text.strip()
                    break

        # Se houver botão de ir para o formulário (/apply), navega até ele
        apply_btn = page.locator("a[href*='/apply'], a.postings-btn")
        if await apply_btn.count() > 0 and await page.locator(self.selectors.RESUME).count() == 0:
            apply_href = await apply_btn.first.get_attribute("href")
            if apply_href and apply_href.startswith("http"):
                await page.goto(apply_href, wait_until="domcontentloaded")
            else:
                await apply_btn.first.click()
                await page.wait_for_timeout(1500)
            await self._check_blockers(page)

        # Extração de perguntas adicionais
        questions: list[FormQuestion] = []
        cards = page.locator(self.selectors.CUSTOM_CARDS)
        card_count = await cards.count()

        for idx in range(card_count):
            card = cards.nth(idx)
            label_loc = card.locator("label, .application-label").first
            if await label_loc.count() == 0:
                continue

            label_text = (await label_loc.inner_text()).strip()
            norm_label = label_text.lower()
            skip_fields = [
                "full name", "email", "phone", "resume", "curriculo",
                "currículo", "linkedin", "github", "location",
            ]
            if any(k in norm_label for k in skip_fields):
                continue

            is_req = (
                "*" in label_text
                or "\u2731" in label_text
                or "✱" in label_text
                or "required" in (await label_loc.get_attribute("class") or "")
                or await card.locator("[required]").count() > 0
            )
            clean_label = re.sub(r"[\s*\u2731✱]+$", "", label_text).strip()

            # Tipo de campo
            field_id = f"custom_field_{idx}"
            kind: str = "text"
            options: list[str] = []

            select_loc = card.locator("select").first
            if await select_loc.count() > 0:
                kind = "select"
                field_name = await select_loc.get_attribute("name") or await select_loc.get_attribute("id")
                if field_name:
                    field_id = field_name
                opts = select_loc.locator("option")
                opt_count = await opts.count()
                for o_idx in range(opt_count):
                    opt_text = (await opts.nth(o_idx).inner_text()).strip()
                    if opt_text and not opt_text.lower().startswith("select"):
                        options.append(opt_text)
            elif await card.locator("textarea").count() > 0:
                kind = "textarea"
                txt_loc = card.locator("textarea").first
                field_name = await txt_loc.get_attribute("name") or await txt_loc.get_attribute("id")
                if field_name:
                    field_id = field_name
            elif await card.locator("input[type='radio']").count() > 0:
                kind = "radio"
                radios = card.locator("input[type='radio']")
                r_cnt = await radios.count()
                for r_idx in range(r_cnt):
                    r_elem = radios.nth(r_idx)
                    val = await r_elem.get_attribute("value")
                    parent_label = r_elem.locator("xpath=ancestor-or-self::label")
                    r_text = (await parent_label.first.inner_text()).strip() if await parent_label.count() > 0 else ""
                    opt_str = r_text or val or f"Option {r_idx+1}"
                    if opt_str not in options:
                        options.append(opt_str)
                    if r_idx == 0:
                        field_name = await r_elem.get_attribute("name") or await r_elem.get_attribute("id")
                        if field_name:
                            field_id = field_name
            elif await card.locator("input[type='checkbox']").count() > 0:
                kind = "checkbox"
                chk_loc = card.locator("input[type='checkbox']").first
                field_name = await chk_loc.get_attribute("name") or await chk_loc.get_attribute("id")
                if field_name:
                    field_id = field_name
            elif await card.locator("input[type='text']").count() > 0:
                txt_loc = card.locator("input[type='text']").first
                field_name = await txt_loc.get_attribute("name") or await txt_loc.get_attribute("id")
                if field_name:
                    field_id = field_name

            questions.append(
                FormQuestion(
                    id=field_id,
                    label=clean_label,
                    kind=kind,  # type: ignore[arg-type]
                    required=is_req,
                    options=options,
                )
            )

        summary = f"Lever job '{job.title}' em '{job.company}' com {len(questions)} perguntas adicionais."
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

        if page.url != job.url and not ("/apply" in page.url and job.url in page.url):
            await page.goto(job.url, wait_until="domcontentloaded")

        # Se houver botão de ir para o formulário (/apply) e ainda não estiver nele
        apply_btn = page.locator("a[href*='/apply'], a.postings-btn")
        if await apply_btn.count() > 0 and await page.locator(self.selectors.RESUME).count() == 0:
            apply_href = await apply_btn.first.get_attribute("href")
            if apply_href and apply_href.startswith("http"):
                await page.goto(apply_href, wait_until="domcontentloaded")
            else:
                await apply_btn.first.click()
                await page.wait_for_timeout(1500)

        await self._check_blockers(page)

        # 1. Dados básicos
        if await page.locator(self.selectors.NAME).count() > 0:
            await page.locator(self.selectors.NAME).first.fill(profile.full_name)

        if await page.locator(self.selectors.EMAIL).count() > 0:
            await page.locator(self.selectors.EMAIL).first.fill(profile.email)

        if profile.phone and await page.locator(self.selectors.PHONE).count() > 0:
            await page.locator(self.selectors.PHONE).first.fill(profile.phone)

        company = (
            profile.current_company
            or profile.base_answers.get("current company")
            or profile.base_answers.get("empresa atual")
            or profile.base_answers.get("empresa")
            or "Desenvolvedor de Software / Autônomo"
        )
        if await page.locator(self.selectors.ORG).count() > 0:
            await page.locator(self.selectors.ORG).first.fill(company)

        if profile.location and await page.locator(self.selectors.LOCATION).count() > 0:
            loc_input = page.locator(self.selectors.LOCATION).first
            city = profile.location.split(",")[0].strip()
            await loc_input.click()
            await loc_input.press_sequentially(city, delay=80)
            await page.wait_for_timeout(1500)
            suggestions = page.locator(".dropdown-location, #location-0, .dropdown-results > div")
            if await suggestions.count() > 0:
                await suggestions.first.click()
                await page.wait_for_timeout(500)
            else:
                await loc_input.fill(profile.location)

        # 2. Links
        for link_type, sel in [
            ("linkedin", self.selectors.LINKEDIN),
            ("github", self.selectors.GITHUB),
            ("portfolio", self.selectors.PORTFOLIO),
        ]:
            if link_type in profile.links and await page.locator(sel).count() > 0:
                await page.fill(sel, profile.links[link_type])

        # 3. Anexo do currículo
        file_loc = page.locator(self.selectors.RESUME).first
        if await file_loc.count() > 0:
            await file_loc.set_input_files(str(resume.path))
        else:
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason="Campo de upload de currículo não encontrado na Lever.",
                dry_run=dry_run,
            )



        # 5. Perguntas adicionais e EEO
        cards = page.locator(self.selectors.CUSTOM_CARDS)
        card_count = await cards.count()

        for idx in range(card_count):
            card = cards.nth(idx)
            label_loc = card.locator("label, .application-label").first
            if await label_loc.count() == 0:
                continue

            label_text = (await label_loc.inner_text()).strip()
            norm_label = label_text.lower()
            if any(k in norm_label for k in [
                "full name", "email", "phone", "resume", "curriculo", "currículo",
                "location", "current company", "empresa atual", "org"
            ]):
                continue

            # Se for pergunta sensível/EEO: NUNCA usar LLM
            if is_sensitive_question(label_text):
                select_loc = card.locator("select").first
                if await select_loc.count() > 0:
                    await self._choose_eeo_option(select_loc, label_text, profile.eeo)
                continue

            # Pergunta não sensível: buscar resposta nos facts/answers
            ans_val: str | None = None
            # Tentar achar pelo name/id do input
            inputs = card.locator("input, select, textarea")
            field_name = ""
            if await inputs.count() > 0:
                field_name = await inputs.first.get_attribute("name") or await inputs.first.get_attribute("id") or ""

            if field_name and field_name in answers.answers:
                ans_val = answers.answers[field_name]
            elif f"custom_field_{idx}" in answers.answers:
                ans_val = answers.answers[f"custom_field_{idx}"]
            else:
                # Busca por similaridade com a label
                clean_lbl = re.sub(r"\s*\*.*$", "", label_text).strip().lower()
                for qid, qans in answers.answers.items():
                    if qid.lower() in clean_lbl or clean_lbl in qid.lower():
                        ans_val = qans
                        break

            if ans_val is None:
                from core.decision.rules import resolve_profile_answer
                sel_elem = card.locator("select").first
                opts = []
                if await sel_elem.count() > 0:
                    opts = await sel_elem.locator("option").all_inner_texts()
                q_kind = "select" if opts else "text"
                card_q = FormQuestion(
                    id=field_name or f"custom_field_{idx}", label=label_text, kind=q_kind, options=opts
                )
                ans_val = resolve_profile_answer(card_q, profile)

            if ans_val is not None:
                # Preencher no tipo adequado
                if await card.locator("select").count() > 0:
                    sel_elem = card.locator("select").first
                    opts = await sel_elem.locator("option").all_inner_texts()
                    from core.decision.rules import match_option
                    chosen = match_option(str(ans_val), opts)
                    if chosen:
                        await sel_elem.select_option(label=chosen)
                    else:
                        selected = False
                        for o in opts:
                            if ans_val.lower() in o.lower() or o.lower() in ans_val.lower():
                                await sel_elem.select_option(label=o)
                                selected = True
                                break
                        if not selected:
                            for o in opts:
                                if any(ok in o.lower() for ok in ["linkedin", "other", "outro", "yes", "sim", "no", "não"]):
                                    await sel_elem.select_option(label=o)
                                    selected = True
                                    break
                            if not selected:
                                for o in opts:
                                    if o.strip() and not o.lower().startswith("select") and not o.lower().startswith("selecion"):
                                        await sel_elem.select_option(label=o)
                                        break

                elif await card.locator("input[type='radio']").count() > 0:
                    radios = card.locator("input[type='radio']")
                    r_cnt = await radios.count()
                    matched = False
                    for r_idx in range(r_cnt):
                        r_elem = radios.nth(r_idx)
                        val = (await r_elem.get_attribute("value") or "").strip().lower()
                        parent_label = r_elem.locator("xpath=ancestor-or-self::label")
                        lbl_text = ((await parent_label.first.inner_text()) if await parent_label.count() > 0 else "").strip().lower()
                        ans_lower = str(ans_val).strip().lower()
                        if ans_lower in (val, lbl_text) or val in ans_lower or (lbl_text and (ans_lower in lbl_text or lbl_text in ans_lower)):
                            await r_elem.check()
                            matched = True
                            break
                    if not matched and r_cnt > 0:
                        chosen_idx = 0
                        norm_lbl = label_text.lower()
                        if any(k in norm_lbl for k in ["relative", "parente", "sponsor", "visto", "crime"]):
                            for r_idx in range(r_cnt):
                                val = (await radios.nth(r_idx).get_attribute("value") or "").lower()
                                if val in ["no", "não", "nao", "false"]:
                                    chosen_idx = r_idx
                                    break
                        await radios.nth(chosen_idx).check()

                elif await card.locator("textarea").count() > 0:
                    await card.locator("textarea").first.fill(str(ans_val))
                elif await card.locator("input[type='checkbox']").count() > 0:
                    if str(ans_val).lower() in {"true", "yes", "sim", "1"}:
                        await card.locator("input[type='checkbox']").first.check()
                elif await card.locator("input[type='text'], input:not([type])").count() > 0:
                    await card.locator("input[type='text'], input:not([type])").first.fill(str(ans_val))

        # Garantir preenchimento de org / empresa atual
        org_loc = page.locator("input[name='org'], input#org, input[placeholder*='company' i], input[placeholder*='empresa' i]")
        for oi in range(await org_loc.count()):
            el = org_loc.nth(oi)
            if await el.is_visible():
                curr_val = (await el.input_value()).strip()
                if not curr_val:
                    await el.fill(company)

        # Aceite de consentimento de privacidade / LGPD / GDPR
        consent_loc = page.locator("input[type='checkbox'][name*='consent'], input[type='checkbox'][name*='privacy']")
        for c_i in range(await consent_loc.count()):
            chk = consent_loc.nth(c_i)
            if not await chk.is_checked():
                await chk.check()

        # Varredura autônoma de segurança: nenhum campo obrigatório visível pode ficar em branco
        req_loc = page.locator("input[required], select[required], textarea[required], [aria-required='true']")
        for ri in range(await req_loc.count()):
            el = req_loc.nth(ri)
            try:
                if not await el.is_visible():
                    continue
                tag = (await el.evaluate("e => e.tagName.toLowerCase()")).strip()
                t_type = (await el.get_attribute("type") or "").lower()
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
                            if "org" in name_attr or "company" in name_attr:
                                await el.fill(company)
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
