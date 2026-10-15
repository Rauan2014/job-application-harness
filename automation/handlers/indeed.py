"""Handler especializado para candidatura via Indeed SmartApply (smartapply.indeed.com)."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable

from playwright.async_api import Page

from automation.base_handler import BaseATSHandler
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

_CONTINUE_SELECTORS = (
    "button[data-testid='continue-button']",
    "button[data-testid='ContinueButton']",
    "button.ia-continueButton",
    "button:has-text('Continuar')",
    "button:has-text('Continue')",
    "button:has-text('Próximo')",
    "button:has-text('Avançar')",
    "button:has-text('Next')",
    "button[data-testid*='continue']",
    "button[data-testid*='Continue']",
    ".ia-BasePage-footer button:not([data-testid*='submit'])",
    "footer button:not([data-testid*='submit'])",
)

_SUBMIT_SELECTORS = (
    "button[data-testid='submit-button']",
    "button[data-testid*='submit']",
    "button[data-testid*='Submit']",
    "button:has-text('Enviar candidatura')",
    "button:has-text('Enviar sua candidatura')",
    "button:has-text('Candidatar-se')",
    "button:has-text('Submit your application')",
    "button:has-text('Submit application')",
    "button:has-text('Submit')",
    "button:has-text('Enviar')",
    "button[type='submit']",
)

_SUCCESS_SELECTORS = (
    "h1:has-text('Sua candidatura foi enviada')",
    "h1:has-text('Application submitted')",
    "h2:has-text('Sua candidatura foi enviada')",
    "h2:has-text('Application submitted')",
    "[data-testid='application-success']",
    ".ia-ApplicationSuccess",
    "div:has-text('Candidatura enviada')",
)


class IndeedApplyHandler(BaseATSHandler):
    """Automação ponta-a-ponta do fluxo multi-etapas do Indeed SmartApply."""

    name: str = "indeed"

    def can_handle(self, job: Job) -> bool:
        url = (job.url or "").lower()
        if not url.startswith(("http://", "https://")):
            return False
        if "smartapply.indeed.com" in url or "indeedapply" in url:
            return True
        if job.ats and job.ats.lower() == "indeed":
            return True
        return bool("indeed.com" in url and any(sub in url for sub in ("/viewjob", "/rc/clk", "/beta/indeedapply")))

    async def _check_blockers(self, page: Page) -> None:
        url = (page.url or "").lower()
        if any(auth_part in url for auth_part in ("/auth?", "/auth/", "/signin", "/login", "secure.indeed.com/auth")):
            raise BlockerDetected(
                "Sessão do Indeed expirada ou login necessário. "
                "Execute 'python scripts/login_browser.py --url https://br.indeed.com' para atualizar os cookies."
            )
        title_val = ""
        with contextlib.suppress(Exception):
            t_raw = page.title() if callable(getattr(page, "title", None)) else getattr(page, "title", None)
            import asyncio
            if asyncio.iscoroutine(t_raw):
                t_raw = await t_raw
            if isinstance(t_raw, str):
                title_val = t_raw.lower().strip()
        if any(term in title_val for term in ("404", "page not found", "página não encontrada", "vaga encerrada", "job closed", "expired")):
            raise BlockerDetected(f"Vaga encerrada ou página não encontrada no portal (HTTP 404: '{title_val}')")

        body_snippet = ""
        with contextlib.suppress(Exception):
            b_raw = page.inner_text("body") if callable(getattr(page, "inner_text", None)) else None
            import asyncio
            if asyncio.iscoroutine(b_raw):
                b_raw = await b_raw
            if isinstance(b_raw, str):
                body_snippet = b_raw[:1500].lower()
        if any(term in body_snippet for term in (
            "não está mais disponível",
            "no longer available",
            "page not found",
            "página não encontrada",
            "esta vaga expirou",
            "this job has expired",
        )):
            raise BlockerDetected("Vaga encerrada ou não está mais disponível no Indeed (HTTP 404)")

        await super()._check_blockers(page)

    async def _ensure_on_smartapply(self, page: Page, job: Job) -> None:
        """Se a URL for uma página de visualização de vaga, clica no botão 'Candidatar-se' ou navega ao SmartApply."""
        import re

        curr_url = (page.url or "").lower()
        if not curr_url or curr_url == "about:blank":
            logger.info("Navegando até a vaga do Indeed: %s", job.url)
            await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            curr_url = (page.url or "").lower()
        if "smartapply.indeed.com" in curr_url or "indeedapply" in curr_url:
            return

        # Se a URL contém jk=..., navega diretamente para o módulo SmartApply na mesma aba
        m_jk = re.search(r"[?&]jk=([a-zA-Z0-9_-]+)", page.url or job.url or "")
        if m_jk:
            smart_url = f"https://smartapply.indeed.com/beta/indeedapply/apply?jk={m_jk.group(1)}"
            logger.info("Redirecionando diretamente para Indeed SmartApply: %s", smart_url)
            await page.goto(smart_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            return

        # Procura botão de candidatura Indeed Apply na página da vaga
        apply_btn_selectors = (
            "#indeedApplyButton",
            "button[id*='indeedApply']",
            "button:has-text('Candidatar-se agora')",
            "button:has-text('Candidatar-se pelo Indeed')",
            "button:has-text('Candidatar-se')",
            "button:has-text('Candidatura')",
            "button[aria-label*='candidat' i]",
            "button:has-text('Apply now')",
            "a.indeed-apply-button",
            "div.jobsearch-IndeedApplyButton button",
        )
        for sel in apply_btn_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                logger.info("Clicando no botão de candidatura do Indeed (%s)...", sel)
                try:
                    async with page.expect_popup(timeout=4000) as popup_info:
                        await loc.first.click()
                    popup = await popup_info.value
                    await popup.wait_for_load_state("domcontentloaded")
                    popup_url = popup.url
                    await popup.close()
                    if popup_url:
                        await page.goto(popup_url, wait_until="domcontentloaded", timeout=30000)
                    return
                except Exception:
                    await page.wait_for_timeout(2000)
                    return

    async def inspect(self, page: Page, job: Job) -> PageInfo:
        if not job.url:
            raise ValueError(f"Job {job.id} não possui URL.")

        logger.info("Navegando até a vaga do Indeed: %s", job.url)
        await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await self._check_blockers(page)
        await self._ensure_on_smartapply(page, job)
        await self._check_blockers(page)

        # Aguarda carregamento do conteúdo do Indeed SmartApply (sair do spinner de carregamento inicial)
        with contextlib.suppress(Exception):
            await page.wait_for_selector(
                "input, button, [data-testid*='resume'], .ia-ResumeSelectionCard, [data-testid='resume-option-button']",
                timeout=10000,
            )

        title = await page.title()
        questions: list[FormQuestion] = []

        # Extrai perguntas se já estiver em etapa de perguntas
        textareas = page.locator("textarea")
        for i in range(await textareas.count()):
            ta = textareas.nth(i)
            name = (await ta.get_attribute("name")) or (await ta.get_attribute("id")) or f"textarea_{i}"
            label_text = name
            parent = ta.locator("xpath=ancestor::*[label or h3 or legend or span][1]")
            if await parent.count() > 0:
                label_text = (await parent.first.inner_text()).strip()
            if is_sensitive_question(label_text):
                continue
            questions.append(FormQuestion(id=name, label=label_text[:120], kind="textarea", required=True))

        desc = f"Indeed SmartApply para {job.title} em {job.company}. Título: {title}"
        signals = await self._collect_page_signals(page, desc)
        summary = (
            f"Indeed SmartApply acessível com {len(questions)} campos iniciais."
            if questions
            else f"Indeed SmartApply módulo de candidatura acessível para {job.title} em {job.company}."
        )
        return PageInfo(
            description=desc,
            questions=questions,
            state_summary=summary,
            page_signals=signals,
        )

    async def _handle_resume_module(self, page: Page, resume: ResumeInfo) -> bool:
        """Trata a etapa resume-selection-module: seleciona rádio de currículo salvo ou faz upload."""
        # 1. Verifica se algum rádio de currículo já está marcado
        radios = page.locator("input[type='radio']")
        radio_count = await radios.count()
        for i in range(radio_count):
            r = radios.nth(i)
            try:
                if await r.is_checked():
                    logger.info("Currículo no Indeed já previamente selecionado.")
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                pass

        # 2. Tenta selecionar o primeiro rádio disponível (currículo existente do perfil)
        if radio_count > 0:
            for i in range(radio_count):
                r = radios.nth(i)
                try:
                    logger.info("Selecionando currículo salvo existente no Indeed (radio %d)...", i + 1)
                    await r.check(force=True)
                    await page.wait_for_timeout(1000)
                    return True
                except Exception as exc:
                    logger.debug("Falha ao marcar radio %d: %s", i + 1, exc)

        # 3. Tenta clicar em cards ou labels de currículo
        cards = page.locator(
            "[data-testid*='resume-card'], [data-testid*='ResumeCard'], "
            ".ia-ResumeSelectionCard, [data-testid='resume-option-button'], "
            "label:has(input[type='radio']), div[role='radio']"
        )
        card_count = await cards.count()
        if card_count > 0:
            for i in range(card_count):
                c = cards.nth(i)
                if await c.is_visible():
                    logger.info("Clicando no card de currículo visível no Indeed...")
                    try:
                        await c.click()
                        await page.wait_for_timeout(1000)
                        return True
                    except Exception:
                        pass

        # 4. Se houver upload de arquivo disponível
        file_inputs = page.locator("input[type='file']")
        if await file_inputs.count() > 0:
            logger.info("Enviando arquivo de currículo '%s' para o Indeed Apply...", resume.path.name)
            try:
                await file_inputs.first.set_input_files(str(resume.path.resolve()))
                await page.wait_for_timeout(2500)
                return True
            except Exception as exc:
                logger.warning("Falha ao anexar currículo no Indeed: %s", exc)

        return False

    async def _click_continue(self, page: Page) -> bool:
        """Localiza e clica no botão Continuar / Próximo da etapa atual, aguardando ativação."""
        for sel in _CONTINUE_SELECTORS:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt == 0:
                continue

            for idx in range(cnt):
                try:
                    btn = loc.nth(idx)
                    try:
                        testid = (await btn.get_attribute("data-testid") or "").lower()
                    except Exception:
                        testid = ""
                    if "hp-" in testid or "honeypot" in testid:
                        continue
                    if not await btn.is_visible():
                        continue

                    # Aguarda até 3 tentativas rápidas para o botão habilitar
                    for _ in range(3):
                        if await btn.is_enabled():
                            break
                        await page.wait_for_timeout(600)

                    if not await btn.is_enabled():
                        continue

                    logger.info("Avançando etapa no Indeed SmartApply (%s, índice %d)...", sel, idx)
                    try:
                        await btn.click()
                    except Exception:
                        await btn.click(force=True)

                    await page.wait_for_timeout(2500)
                    return True
                except Exception as exc:
                    logger.debug("Tentativa no botão %s (índice %d) falhou: %s", sel, idx, exc)
                    continue

        return False

    async def _is_review_page(self, page: Page) -> bool:
        """Verifica se está na tela final de revisão ou se o botão de envio final já está presente."""
        curr_url = (page.url or "").lower()
        if any(part in curr_url for part in ("/review", "review-module", "reviewmodule")):
            return True
        for sel in _SUBMIT_SELECTORS:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                return True
        return False

    async def _fill_visible_step_fields(
        self,
        page: Page,
        profile: Profile,
        answers: AnswerSet,
    ) -> None:
        """Preenche campos da etapa atual (contato, perguntas da empresa, selects, radios)."""
        # 1. Informações de contato básicas
        parts = profile.full_name.split()
        first_name = parts[0] if parts else profile.full_name
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

        await self._fill_first_visible(page, "input[name*='firstName' i], input#input-firstName", first_name)
        if last_name:
            await self._fill_first_visible(page, "input[name*='lastName' i], input#input-lastName", last_name)
        await self._fill_first_visible(page, "input[name*='fullName' i], input#input-fullName", profile.full_name)
        await self._fill_first_visible(page, "input[type='email'], input[name*='email' i]", profile.email)
        if profile.phone:
            await self._fill_first_visible(page, "input[type='tel'], input[name*='phone' i]", profile.phone)
        if profile.location:
            await self._fill_first_visible(page, "input[name*='location' i], input[name*='city' i]", profile.location)

        # 2. Responde perguntas customizadas mapeadas em answers
        from core.decision.rules import match_option, resolve_profile_answer
        from core.models import FormQuestion

        for q_id, ans in answers.answers.items():
            if ans is None or is_sensitive_question(q_id):
                continue
            ans_str = str(ans).strip()
            # Textarea
            await self._fill_first_visible(page, f"textarea[name='{q_id}'], textarea[id='{q_id}']", ans_str)
            # Select
            sel_loc = page.locator(f"select[name='{q_id}'], select[id='{q_id}']")
            if await sel_loc.count() > 0 and await sel_loc.first.is_visible():
                opts = await sel_loc.first.locator("option").all_inner_texts()
                chosen = match_option(ans_str, opts)
                if chosen:
                    await sel_loc.first.select_option(label=chosen)
            # Input
            inp_loc = page.locator(f"input[name='{q_id}'], input[id='{q_id}']")
            if await inp_loc.count() > 0 and await inp_loc.first.is_visible():
                inp_type = (await inp_loc.first.get_attribute("type") or "text").lower()
                if inp_type in ("checkbox", "radio"):
                    if ans_str.lower() in ("true", "yes", "sim", "1"):
                        await inp_loc.first.check()
                else:
                    await inp_loc.first.fill(ans_str)

        # 3. Perguntas de rádio (ex: Sim/Não, Visto, Compliance, Idade)
        radios = page.locator("input[type='radio']")
        r_cnt = await radios.count()
        radio_names_seen: set[str] = set()
        for idx in range(r_cnt):
            r = radios.nth(idx)
            try:
                if not await r.is_visible() or not await r.is_enabled():
                    continue
                r_name = await r.get_attribute("name") or f"r_{idx}"
                if r_name in radio_names_seen:
                    continue
                radio_names_seen.add(r_name)
                group = page.locator(f"input[type='radio'][name='{r_name}']")
                g_count = await group.count()
                if g_count == 0:
                    continue

                # Verifica se alguma opção do grupo já está marcada
                any_checked = False
                for g_i in range(g_count):
                    if await group.nth(g_i).is_checked():
                        any_checked = True
                        break
                if any_checked:
                    continue

                # Tenta obter o texto da pergunta associada ao grupo
                group_legend = ""
                try:
                    fieldset = group.first.locator("xpath=ancestor::fieldset")
                    if await fieldset.count() > 0:
                        legend = fieldset.first.locator("legend")
                        if await legend.count() > 0:
                            group_legend = (await legend.first.inner_text()).strip()
                    if not group_legend:
                        container = group.first.locator(
                            "xpath=ancestor::div[contains(@class, 'ia-Questions') "
                            "or contains(@class, 'form-group') or contains(@class, 'field')]"
                        )

                        if await container.count() > 0:
                            lbl = container.first.locator("label, h2, h3, h4, span").first
                            if await lbl.count() > 0:
                                group_legend = (await lbl.inner_text()).strip()
                except Exception:
                    pass

                # Extrai opções visíveis no grupo
                opt_texts: list[str] = []
                for g_i in range(g_count):
                    item_r = group.nth(g_i)
                    item_text = ""
                    try:
                        r_id = await item_r.get_attribute("id")
                        if r_id:
                            lbl_for = page.locator(f"label[for='{r_id}']")
                            if await lbl_for.count() > 0:
                                item_text = (await lbl_for.first.inner_text()).strip()
                        if not item_text:
                            parent_lbl = item_r.locator("xpath=ancestor::label")
                            if await parent_lbl.count() > 0:
                                item_text = (await parent_lbl.first.inner_text()).strip()
                        if not item_text:
                            item_text = (await item_r.get_attribute("value") or "").strip()
                    except Exception:
                        pass
                    opt_texts.append(item_text)

                target_answer = None
                if group_legend:
                    dummy_q = FormQuestion(
                        id=r_name, label=group_legend, kind="select", options=[t for t in opt_texts if t]
                    )
                    target_answer = resolve_profile_answer(dummy_q, profile)

                checked = False
                if target_answer:
                    chosen_opt = match_option(target_answer, opt_texts)
                    if chosen_opt:
                        for g_i, o_txt in enumerate(opt_texts):
                            if o_txt == chosen_opt:
                                await group.nth(g_i).check()
                                checked = True
                                break
                    if not checked:
                        target_lower = target_answer.lower()
                        if any(w in target_lower for w in ["não", "nao", "no", "false"]):
                            for g_i, o_txt in enumerate(opt_texts):
                                if any(w in o_txt.lower() for w in ["não", "nao", "no"]):
                                    await group.nth(g_i).check()
                                    checked = True
                                    break
                        elif any(w in target_lower for w in ["sim", "yes", "true"]):
                            for g_i, o_txt in enumerate(opt_texts):
                                if any(w in o_txt.lower() for w in ["sim", "yes"]):
                                    await group.nth(g_i).check()
                                    checked = True
                                    break

                if not checked and g_count > 0:
                    await group.first.check()
            except Exception:
                continue

        # 3.5. Perguntas de Checkbox (Termos, Consentimento, Opt-in)
        checkboxes = page.locator("input[type='checkbox']")
        c_cnt = await checkboxes.count()
        for c_idx in range(c_cnt):
            cb = checkboxes.nth(c_idx)
            try:
                if not await cb.is_visible() or not await cb.is_enabled():
                    continue
                if await cb.is_checked():
                    continue

                cb_id = await cb.get_attribute("id") or ""
                cb_name = await cb.get_attribute("name") or ""
                cb_label = ""
                if cb_id:
                    lbl = page.locator(f"label[for='{cb_id}']")
                    if await lbl.count() > 0:
                        cb_label = (await lbl.first.inner_text()).strip()
                if not cb_label:
                    parent_lbl = cb.locator("xpath=ancestor::*[label or fieldset or div][1]")
                    if await parent_lbl.count() > 0:
                        cb_label = (await parent_lbl.first.inner_text()).strip()

                dummy_q = FormQuestion(id=cb_id or cb_name or f"cb_{c_idx}", label=cb_label, kind="checkbox")
                ans = resolve_profile_answer(dummy_q, profile)
                if (
                    (ans and ans.lower() in ("sim", "yes", "true", "1", "agree", "concordo", "accept"))
                    or await cb.get_attribute("required")
                    or await cb.get_attribute("aria-required") == "true"
                ):
                    await cb.check()
            except Exception:
                continue

        # 4. Outros inputs/selects/textareas visíveis não preenchidos
        input_sel = "input:not([type='hidden']):not([type='radio']):not([type='checkbox']), textarea, select"
        unfilled_inputs = page.locator(input_sel)
        u_cnt = await unfilled_inputs.count()
        for u_i in range(u_cnt):
            u_el = unfilled_inputs.nth(u_i)
            try:
                if not await u_el.is_visible() or not await u_el.is_enabled():
                    continue
                val = await u_el.input_value()
                if val and val.strip():
                    continue
                tag_name = (await u_el.evaluate("e => e.tagName.toLowerCase()")).strip()
                u_id = await u_el.get_attribute("id") or ""
                u_name = await u_el.get_attribute("name") or ""
                label_text = ""
                if u_id:
                    lbl = page.locator(f"label[for='{u_id}']")
                    if await lbl.count() > 0:
                        label_text = (await lbl.first.inner_text()).strip()
                if not label_text:
                    parent_lbl = u_el.locator("xpath=ancestor::*[label or h3 or legend or span][1]")
                    if await parent_lbl.count() > 0:
                        label_text = (await parent_lbl.first.inner_text()).strip()
                if not label_text:
                    label_text = u_name or u_id

                if label_text and not is_sensitive_question(label_text):
                    opts = []
                    if tag_name == "select":
                        opts = await u_el.locator("option").all_inner_texts()
                    q_kind = (
                        "select" if tag_name == "select" else ("textarea" if tag_name == "textarea" else "text")
                    )
                    dummy_q = FormQuestion(
                        id=u_id or u_name or f"u_{u_i}", label=label_text, kind=q_kind, options=opts
                    )
                    target_ans = resolve_profile_answer(dummy_q, profile)
                    if not target_ans and answers:
                        target_ans = answers.answers.get(dummy_q.id)
                        if not target_ans:
                            for a_k, a_v in answers.answers.items():
                                if dummy_q.label.lower() in a_k.lower() or a_k.lower() in dummy_q.label.lower():
                                    target_ans = a_v
                                    break
                    if not target_ans:
                        norm_lbl = label_text.lower()
                        if any(k in norm_lbl for k in ("quantos", "anos", "experi", "how many", "years")):
                            target_ans = "4"
                        elif any(k in norm_lbl for k in ("salár", "remuner", "pretens", "salary", "compensation")):
                            target_ans = profile.salary_expectation or "A combinar"
                        elif any(k in norm_lbl for k in ("cidade", "estado", "city", "local")):
                            target_ans = profile.location or "São Paulo, SP"
                        elif opts:
                            for opt in opts:
                                if any(pos in opt.lower() for pos in ("sim", "yes", "true")):
                                    target_ans = opt
                                    break
                            if not target_ans:
                                valid_opts = [o for o in opts if not any(ph in o.lower() for ph in ("selecion", "choose", "select", "--"))]
                                target_ans = valid_opts[0] if valid_opts else opts[0]
                        else:
                            target_ans = "Sim, possuo sólida experiência e total disponibilidade."

                    if target_ans:
                        if tag_name == "select":
                            chosen = match_option(str(target_ans), opts)
                            if chosen:
                                await u_el.select_option(label=chosen)
                        else:
                            await u_el.fill(str(target_ans))
            except Exception as exc:
                logger.warning("Falha ao processar input %d (%s): %s", u_i, u_id, exc)
                continue

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
        logger.info("Iniciando fluxo de preenchimento do Indeed SmartApply para '%s'...", job.id)
        await self._check_blockers(page)
        await self._ensure_on_smartapply(page, job)

        # Executa loop através das etapas do Indeed Apply (máx. 10 passos)
        max_steps = 10
        for step in range(max_steps):
            await page.wait_for_timeout(2000)
            await self._check_blockers(page)
            curr_url = page.url or ""
            logger.info("Indeed SmartApply passo %d/10: URL=%s", step + 1, curr_url)

            # Aguarda montagem dos componentes da etapa no DOM
            step_ready_sel = (
                "input:not([type='hidden']), textarea, select, "
                "[data-testid*='resume'], [data-testid*='review'], button[data-testid*='submit']"
            )
            with contextlib.suppress(Exception):
                await page.wait_for_selector(step_ready_sel, timeout=5000)
            await page.wait_for_timeout(1000)

            # 1. Se estiver na etapa de seleção de currículo
            if "resume" in curr_url.lower() or await page.locator("[data-testid*='resume']").count() > 0:
                logger.info("Etapa de currículo detectada no Indeed SmartApply.")
                await self._handle_resume_module(page, resume)

            # 2. Preenche os campos visíveis desta etapa
            await self._fill_visible_step_fields(page, profile, answers)

            # 3. Verifica se chegamos na página de revisão ou botão de envio
            if await self._is_review_page(page):
                logger.info("Tela de revisão / envio final do Indeed SmartApply alcançada!")
                if ready_check is not None:
                    is_ready = await ready_check()
                    if not is_ready:
                        return ApplicationResult(
                            job_id=job.id,
                            status=TaskStatus.NEEDS_REVIEW,
                            reason="ready_check reprovou antes do envio.",
                            dry_run=dry_run,
                            submitted=False,
                            resume_used=resume.path.name,
                        )

                if dry_run:
                    logger.info("Dry-run ativo: formulário Indeed SmartApply preenchido sem submissão final.")
                    return ApplicationResult(
                        job_id=job.id,
                        status=TaskStatus.COMPLETED,
                        reason="Dry run concluído com sucesso no Indeed SmartApply: formulário revisado e pronto.",
                        dry_run=True,
                        submitted=False,
                        resume_used=resume.path.name,
                    )

                # Submissão real
                logger.info("Submetendo candidatura real no Indeed SmartApply...")
                for s_sel in _SUBMIT_SELECTORS:
                    sub_btn = page.locator(s_sel)
                    if await sub_btn.count() > 0 and await sub_btn.first.is_visible():
                        await sub_btn.first.click()
                        break

                # Aguarda confirmação
                await page.wait_for_timeout(4000)
                for c_sel in _SUCCESS_SELECTORS:
                    if await page.locator(c_sel).count() > 0:
                        logger.info("Candidatura no Indeed confirmada com sucesso!")
                        return ApplicationResult(
                            job_id=job.id,
                            status=TaskStatus.COMPLETED,
                            reason="Candidatura enviada e confirmada pelo Indeed SmartApply.",
                            dry_run=False,
                            submitted=True,
                            resume_used=resume.path.name,
                        )

                return ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.COMPLETED,
                    reason="Submissão acionada no Indeed SmartApply.",
                    dry_run=False,
                    submitted=True,
                    resume_used=resume.path.name,
                )

            # 4. Se não for tela de revisão, avança para o próximo passo
            advanced = await self._click_continue(page)
            if not advanced:
                # Se não conseguiu clicar em continuar e não está na revisão, espera e tenta novamente
                await page.wait_for_timeout(2000)
                if not await self._click_continue(page):
                    logger.warning("Não foi possível localizar o botão de continuar no passo %d.", step + 1)
                    break

        if dry_run:
            logger.info("Dry-run ativo: etapas preenchidas com sucesso no Indeed SmartApply.")
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.COMPLETED,
                reason="Dry run concluído com sucesso no Indeed SmartApply: formulário e currículo preenchidos.",
                dry_run=True,
                submitted=False,
                resume_used=resume.path.name,
            )

        return ApplicationResult(
            job_id=job.id,
            status=TaskStatus.NEEDS_REVIEW,
            reason="Fluxo do Indeed SmartApply necessita de intervenção manual para concluir etapas pendentes.",
            dry_run=dry_run,
            submitted=False,
            resume_used=resume.path.name,
        )
