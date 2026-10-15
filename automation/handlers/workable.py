"""Handler de automação para formulários da plataforma Workable (apply.workable.com)."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from playwright.async_api import Page

from automation.base_handler import BaseATSHandler
from core.decision.rules import is_sensitive_question, match_option
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
class WorkableSelectors:
    FORM: str = "[data-ui='application-form'], dialog, form"
    FIRST_NAME: str = "input[name='firstname'], input#firstname, input[name*='firstname' i]"
    LAST_NAME: str = "input[name='lastname'], input#lastname, input[name*='lastname' i]"
    FULL_NAME: str = "input[name='name'], input#name"
    EMAIL: str = "input[name='email'], input#email, input[type='email']"
    PHONE: str = "input[name='phone'], input#phone, input[type='tel']"
    ADDRESS: str = "input[name='address'], input[name='city']"
    HEADLINE: str = "input[name='headline']"
    SUMMARY: str = "textarea[name='summary'], textarea[name='cover_letter']"
    RESUME: str = "input[type='file'][data-ui='resume'], input[type='file'][name*='resume' i], input[type='file']"
    SUBMIT_BTN: str = (
        "button[data-ui='apply-button'], button[data-ui='application-form-submit'], "
        "button[data-ui='application-submit-btn'], button[type='submit'], "
        "button:has-text('Submit application'), button:has-text('Enviar candidatura'), "
        "button:has-text('Submit')"
    )
    CONFIRMATION: str = (
        "[data-ui='application-success'], .application-submitted, "
        "h1:has-text('Application received'), h1:has-text('Thank you'), "
        "h2:has-text('Thank you'), h1:has-text('Candidatura enviada')"
    )


class WorkableHandler(BaseATSHandler):
    """Automação robusta para candidaturas na plataforma Workable."""

    name: str = "workable"
    selectors: WorkableSelectors = WorkableSelectors()

    def can_handle(self, job: Job) -> bool:
        if job.ats and job.ats.lower() == "workable":
            return True
        url = (job.url or "").lower()
        return "workable.com" in url or "apply.workable.com" in url

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        """Descarta banner de cookies do Workable para não bloquear cliques."""
        cookie_selectors = (
            "[data-ui='cookie-consent'] button:has-text('Accept all')",
            "[data-ui='cookie-consent'] button:has-text('Accept')",
            "button[data-ui='cookie-consent-accept-all']",
            "button:has-text('Accept all')",
            "button:has-text('Accept All')",
            "button:has-text('Aceitar todos')",
            "button:has-text('Aceitar')",
        )
        for sel in cookie_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                try:
                    await loc.first.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    break
                except Exception:
                    pass
        try:
            await page.evaluate("""() => {
                const modal = document.querySelector("[data-ui='cookie-consent']");
                if (modal) modal.remove();
            }""")
        except Exception:
            pass

    async def _navigate_to_form(self, page: Page, job: Job) -> None:
        """Navega até o formulário de candidatura se estiver na página descritiva da vaga."""
        await self._dismiss_cookie_banner(page)
        # Se o formulário ou dialog já estiver aberto e visível, não clica novamente
        try:
            already_open = page.locator("dialog[open], [data-ui='application-form'], input[name='firstname']")
            if await already_open.count() > 0 and await already_open.first.is_visible():
                return
        except Exception:
            pass

        # Se houver botão "Apply for this job" / "Apply now" / "Candidatar-se", clica para abrir formulário
        apply_btn_selectors = (
            "button[data-ui='overview-apply-now']",
            "button[data-ui='sticky-apply-now']",
            "a[data-ui='overview-apply-btn']",
            "button[data-ui='overview-apply-btn']",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply for this job')",
            "a:has-text('Candidate-se')",
            "button:has-text('Candidate-se')",
            "a:has-text('Apply now')",
            "button:has-text('Apply now')",
            "a[href*='/apply']",
        )
        for sel in apply_btn_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                logger.info("Clicando no botão de candidatura do Workable (%s)...", sel)
                try:
                    await loc.first.click()
                    await page.wait_for_timeout(2500)
                    break
                except Exception:
                    try:
                        await loc.first.click(force=True)
                        await page.wait_for_timeout(2500)
                        break
                    except Exception:
                        pass
        await self._dismiss_cookie_banner(page)


    async def inspect(self, page: Page, job: Job) -> PageInfo:
        if not job.url:
            raise ValueError(f"Job {job.id} não possui URL para inspeção.")

        await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await self._check_blockers(page)
        await self._dismiss_cookie_banner(page)
        await self._navigate_to_form(page, job)
        await self._check_blockers(page)

        # Extração de descrição da vaga
        description_text = ""
        for desc_sel in ["[data-ui='job-description']", ".job-description", "main", "article"]:
            loc = page.locator(desc_sel)
            if await loc.count() > 0:
                text = await loc.first.inner_text()
                if text.strip():
                    description_text = text.strip()
                    break

        questions: list[FormQuestion] = []
        standard_fields = {
            "firstname", "lastname", "name", "email", "phone", "address",
            "city", "resume", "headline", "summary", "cover_letter",
        }

        # Extrai campos adicionais / customizados escaneando controles interativos do formulário ou dialog
        elements = page.locator(
            "form input:not([type='hidden']):not([type='file']), dialog input:not([type='hidden']):not([type='file']), "
            "form textarea, dialog textarea, form select, dialog select"
        )
        elem_count = await elements.count()
        seen_names: set[str] = set()

        for idx in range(elem_count):
            el = elements.nth(idx)
            el_name = (await el.get_attribute("name") or "").strip()
            el_id = (await el.get_attribute("id") or "").strip()
            field_id = el_name or el_id or f"workable_field_{idx}"

            if field_id in seen_names:
                continue

            # Ignora campos padrão já cobertos pelo preenchimento base
            lower_id = f"{el_name} {el_id}".lower()
            if any(sf in lower_id for sf in ["firstname", "lastname", "first_name", "last_name", "email", "phone", "address", "city", "postcode", "country", "resume", "headline", "summary"]):
                continue

            # Resolução robusta de label (aria-labelledby, label[for], ancestral label, container pai)
            label_text = ""
            aria_lbl = await el.get_attribute("aria-labelledby")
            if aria_lbl:
                lbl_elem = page.locator(f"[id='{aria_lbl}']")
                if await lbl_elem.count() > 0:
                    label_text = (await lbl_elem.first.inner_text()).strip()

            if not label_text and el_id:
                lbl_elem = page.locator(f"label[for='{el_id}']")
                if await lbl_elem.count() > 0:
                    label_text = (await lbl_elem.first.inner_text()).strip()

            if not label_text:
                parent_lbl = el.locator("xpath=ancestor::label[1]")
                if await parent_lbl.count() > 0:
                    label_text = (await parent_lbl.first.inner_text()).strip()

            if not label_text:
                parent_container = el.locator("xpath=ancestor::*[contains(@class, 'field') or contains(@class, 'group') or contains(@class, 'styles__')][1]")
                if await parent_container.count() > 0:
                    lbl_elem = parent_container.locator("label, [id*='_label'], [data-ui*='_label']").first
                    if await lbl_elem.count() > 0:
                        label_text = (await lbl_elem.inner_text()).strip()

            norm_label = label_text.lower()
            if not field_id.startswith(("CA_", "QA_")):
                if any(sf in norm_label for sf in ["first name", "last name", "email", "phone", "resume", "currículo"]):
                    continue

            tag_name = await el.evaluate("e => e.tagName.toLowerCase()")
            inp_type = (await el.get_attribute("type") or "").lower()
            is_req = "*" in label_text or (await el.get_attribute("required") is not None) or (await el.get_attribute("aria-required") == "true")
            clean_label = re.sub(r"[\s*]+$", "", label_text).strip() or field_id

            kind: str = "text"
            options: list[str] = []

            if tag_name == "select":
                kind = "select"
                opts = el.locator("option")
                for o_idx in range(await opts.count()):
                    opt_text = (await opts.nth(o_idx).inner_text()).strip()
                    if opt_text and not opt_text.lower().startswith("select"):
                        options.append(opt_text)
            elif inp_type == "radio":
                kind = "select"
                radios = page.locator(f"input[type='radio'][name='{el_name}']") if el_name else el
                for r_idx in range(await radios.count()):
                    r_elem = radios.nth(r_idx)
                    val = await r_elem.get_attribute("value") or ""
                    parent_label = r_elem.locator("xpath=ancestor-or-self::label")
                    r_text = (await parent_label.first.inner_text()).strip() if await parent_label.count() > 0 else ""
                    opt_str = r_text or val or f"Option {r_idx+1}"
                    if opt_str not in options:
                        options.append(opt_str)
            elif tag_name == "textarea":
                kind = "textarea"
            elif inp_type == "checkbox":
                kind = "checkbox"

            seen_names.add(field_id)
            questions.append(
                FormQuestion(
                    id=field_id,
                    label=clean_label,
                    kind=kind,  # type: ignore[arg-type]
                    required=is_req,
                    options=options,
                )
            )

        summary = f"Workable job '{job.title}' em '{job.company}' com {len(questions)} campos."
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
            return ApplicationResult(job_id=job.id, status=TaskStatus.FAILED, reason="URL da vaga vazia.", dry_run=dry_run)

        await self._check_blockers(page)
        await self._navigate_to_form(page, job)
        await self._check_blockers(page)

        name_parts = profile.full_name.strip().split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # 1. Nome completo ou First/Last Name
        await self._fill_first_visible(page, self.selectors.FIRST_NAME, first_name)
        if last_name:
            await self._fill_first_visible(page, self.selectors.LAST_NAME, last_name)
        await self._fill_first_visible(page, self.selectors.FULL_NAME, profile.full_name)

        # 2. Email e Telefone
        await self._fill_first_visible(page, self.selectors.EMAIL, profile.email)
        if profile.phone:
            clean_phone = re.sub(r"^\+?55\s*", "", profile.phone.strip())
            await self._fill_first_visible(page, self.selectors.PHONE, clean_phone)

        # 3. Localização / Endereço / País / CEP / Headline
        if profile.location:
            city_only = profile.location.split(",")[0].strip()
            await self._fill_first_visible(page, "input[name='address']", profile.location)
            await self._fill_first_visible(page, "input[name='city']", city_only)
            await self._fill_first_visible(page, "input[name='country']", "Brazil")
            await self._fill_first_visible(page, "input[name='postcode']", "01000-000")
        if profile.headline:
            await self._fill_first_visible(page, self.selectors.HEADLINE, profile.headline)

        # 4. Currículo e arquivos obrigatórios
        file_locs = page.locator("input[type='file']")
        for f_idx in range(await file_locs.count()):
            f_el = file_locs.nth(f_idx)
            try:
                accept_attr = (await f_el.get_attribute("accept") or "").lower()
                if any(img_ext in accept_attr for img_ext in ("image", ".jpg", ".jpeg", ".png", "video", ".mp4")) and "pdf" not in accept_attr:
                    continue
                await f_el.set_input_files(str(resume.path))
                await page.wait_for_timeout(800)
            except Exception:
                pass

        # 5. Resumo / Carta de apresentação
        summary_loc = page.locator(self.selectors.SUMMARY).first
        if await summary_loc.count() > 0:
            cover = profile.cover_letter_en if getattr(job, "language", "pt") == "en" else profile.cover_letter_pt
            await summary_loc.fill(cover)

        logger.info("Workable: Preenchendo campos adicionais (%d respostas disponíveis)...", len(answers.answers))
        # 6. Campos adicionais
        for qid, qans in answers.answers.items():
            if qans is None:
                continue
            inp_loc = page.locator(f"[name='{qid}'], [id='{qid}']").first
            if await inp_loc.count() == 0:
                logger.debug("Workable: campo adicional %s não encontrado pelo seletor [name/id]", qid)
                continue
            tag = await inp_loc.evaluate("e => e.tagName.toLowerCase()")
            inp_type = (await inp_loc.get_attribute("type") or "").lower()
            logger.info("Workable: Preenchendo campo %s (tag=%s, typ=%s) com '%s'", qid, tag, inp_type, str(qans)[:50])

            if inp_type == "radio":
                radios = page.locator(f"input[type='radio'][name='{qid}'], input[type='radio'][id='{qid}']")
                matched = False
                for r_i in range(await radios.count()):
                    r_elem = radios.nth(r_i)
                    val = (await r_elem.get_attribute("value") or "").lower()
                    parent_label = r_elem.locator("xpath=ancestor-or-self::*[self::label or contains(@class, 'labeled-input') or contains(@class, 'wrapper') or contains(@data-ui, 'option')][1]")
                    r_text = (await parent_label.first.inner_text()).strip().lower() if await parent_label.count() > 0 else ""
                    ans_lower = str(qans).lower()
                    if ans_lower in val or val in ans_lower or ans_lower in r_text or r_text in ans_lower:
                        if await parent_label.count() > 0:
                            await parent_label.first.click(force=True)
                        else:
                            await r_elem.check(force=True)
                        await r_elem.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); }")
                        matched = True
                        logger.info("Workable: Radio %s selecionado via match '%s' (checked=%s)", qid, r_text, await r_elem.is_checked())
                        break
                if not matched and await radios.count() > 0:
                    first_r = radios.first
                    parent_label = first_r.locator("xpath=ancestor-or-self::*[self::label or contains(@class, 'labeled-input') or contains(@class, 'wrapper') or contains(@data-ui, 'option')][1]")
                    if await parent_label.count() > 0:
                        await parent_label.first.click(force=True)
                    else:
                        await first_r.check(force=True)
                    await first_r.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); }")
                    logger.info("Workable: Radio %s fallback para primeira opção (checked=%s)", qid, await first_r.is_checked())
            elif tag == "select":
                opts = await inp_loc.locator("option").all_inner_texts()
                chosen = match_option(str(qans), opts)
                if chosen:
                    await inp_loc.select_option(label=chosen)
                else:
                    matched = False
                    for o in opts:
                        if str(qans).lower() in o.lower():
                            await inp_loc.select_option(label=o)
                            matched = True
                            break
                    if not matched and opts:
                        for o in opts:
                            if o.strip() and not any(p in o.lower() for p in ("select", "selecion", "--")):
                                await inp_loc.select_option(label=o)
                                break
            elif inp_type == "checkbox":
                if str(qans).lower() not in {"false", "no", "não", "0"}:
                    try:
                        await inp_loc.check(force=True)
                    except Exception:
                        parent_lbl = inp_loc.locator("xpath=ancestor-or-self::*[self::label or contains(@class, 'labeled-input') or contains(@class, 'wrapper')][1]")
                        if await parent_lbl.count() > 0:
                            await parent_lbl.first.click(force=True)
                    await inp_loc.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); }")
            else:
                await inp_loc.fill(str(qans))

        await self._dismiss_cookie_banner(page)
        return await self._finalize(
            page=page,
            job=job,
            resume=resume,
            dry_run=dry_run,
            ready_check=ready_check,
            submit_selector=self.selectors.SUBMIT_BTN,
            confirmation_selector=self.selectors.CONFIRMATION,
        )

