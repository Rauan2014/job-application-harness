"""Interface base abstrata para handlers de ATS (Lever, Greenhouse, etc.)."""

from __future__ import annotations

import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from playwright.async_api import Locator, Page

from core.models import (
    AnswerSet,
    ApplicationResult,
    BlockerDetected,
    Job,
    PageInfo,
    PostSubmitError,
    Profile,
    ResumeInfo,
    TaskStatus,
)

logger = logging.getLogger(__name__)

CAPTCHA_SELECTORS: tuple[str, ...] = (
    ".g-recaptcha",
    "#recaptcha",
    "#recaptcha-mock",
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    ".cf-turnstile",
)
LOGIN_SELECTORS: tuple[str, ...] = (
    "form[action*='/login']",
    "form[action*='/signin']",
    "input[type='password']",
)
DECLINE_TERMS: tuple[str, ...] = (
    "decline",
    "don't wish",
    "not to say",
    "não informar",
    "prefiro não",
)


class BaseATSHandler(ABC):
    """Contrato base para inspect e fill_and_submit em qualquer plataforma de ATS."""

    name: str = "base"
    CAPTCHA_SELECTORS: tuple[str, ...] = CAPTCHA_SELECTORS
    LOGIN_SELECTORS: tuple[str, ...] = LOGIN_SELECTORS
    DECLINE_TERMS: tuple[str, ...] = DECLINE_TERMS

    @abstractmethod
    def can_handle(self, job: Job) -> bool:
        """Verifica se este handler suporta a vaga informada (por ats ou regex na URL)."""
        ...

    @abstractmethod
    async def inspect(self, page: Page, job: Job) -> PageInfo:
        """Navega até a vaga, verifica blockers (CAPTCHA/login) e extrai perguntas e descrição."""
        ...

    def release(self, job: Job) -> None:  # noqa: B027
        """Hook opcional para liberar recursos ou estado associado ao job."""

    async def _check_blockers(self, page: Page) -> None:
        """Verifica se há CAPTCHA ou tela de login bloqueando o formulário."""
        for captcha_sel in self.CAPTCHA_SELECTORS:
            loc = page.locator(captcha_sel)
            cnt = await loc.count()
            if cnt > 0:
                for i in range(cnt):
                    el = loc.nth(i)
                    if await el.is_visible():
                        src = (await el.get_attribute("src") or "").lower()
                        if "anchor" in src or "badge" in src:
                            continue
                        if "bframe" in src:
                            try:
                                box = await el.bounding_box()
                                if not box or box.get("x", 0) < 0 or box.get("y", 0) < 0 or box.get("width", 0) < 100 or box.get("height", 0) < 100:
                                    continue
                            except Exception:
                                continue
                        try:
                            data_size = (await el.get_attribute("data-size") or "").lower()
                            if data_size == "invisible":
                                continue
                            if captcha_sel in (".g-recaptcha", "#recaptcha"):
                                badge_cnt = await el.locator("iframe[src*='anchor'], .grecaptcha-badge").count()
                                if badge_cnt > 0:
                                    continue
                        except Exception:
                            pass
                        raise BlockerDetected(f"CAPTCHA detectado via seletor '{captcha_sel}'")


        for login_sel in self.LOGIN_SELECTORS:
            loc = page.locator(login_sel)
            cnt = await loc.count()
            if cnt > 0:
                for i in range(cnt):
                    if await loc.nth(i).is_visible():
                        try:
                            has_form = await page.locator(
                                "input[type='email'], input[name*='email' i], input[type='file'], "
                                "input[name*='name' i], textarea"
                            ).first.is_visible()
                            if has_form:
                                continue
                        except Exception:
                            pass
                        raise BlockerDetected(f"Login obrigatório detectado via seletor '{login_sel}'")

    async def _choose_eeo_option(self, select: Locator, label: str, eeo: dict[str, str]) -> None:
        """Seleciona a melhor opção de EEO no <select> ou fallback seguro (decline)."""
        norm_label = label.lower()
        eeo_ans = ""
        for k, v in eeo.items():
            if k.lower() in norm_label:
                eeo_ans = v
                break

        options = await select.locator("option").all_inner_texts()
        chosen_opt: str | None = None
        if eeo_ans:
            for opt in options:
                if eeo_ans.lower() in opt.lower():
                    chosen_opt = opt
                    break

        if not chosen_opt:
            for opt in options:
                if any(d in opt.lower() for d in self.DECLINE_TERMS):
                    chosen_opt = opt
                    break

        if chosen_opt:
            await select.select_option(label=chosen_opt)

    async def _fill_first_visible(self, page: Page, selector: str, value: str, timeout_ms: int = 2000) -> bool:
        """Preenche o primeiro elemento correspondente que estiver visível e habilitado.
        Evita TimeoutError ao encontrar campos ocultos (ex: responsivos/modais).
        """
        loc = page.locator(selector)
        cnt = await loc.count()
        for i in range(cnt):
            el = loc.nth(i)
            try:
                if await el.is_visible() and await el.is_enabled():
                    await el.fill(value, timeout=timeout_ms)
                    return True
            except Exception:
                continue
        return False

    async def _find_empty_required(self, page: Page, dry_run: bool = False) -> str | None:
        """Verifica se há campos obrigatórios visíveis/habilitados vazios ou desmarcados.
        Retorna o nome ou id do 1º campo vazio encontrado, ou None se todos estiverem preenchidos.
        """
        selector = "input[required], select[required], textarea[required], [aria-required='true']"
        required_elements = page.locator(selector)
        cnt = await required_elements.count()
        for idx in range(cnt):
            el = required_elements.nth(idx)
            try:
                if not await el.is_visible() or not await el.is_enabled():
                    continue

                tag_name = (await el.evaluate("e => e.tagName.toLowerCase()")).strip()
                input_type = (await el.get_attribute("type") or "").lower()
                name = (await el.get_attribute("name")) or (await el.get_attribute("id")) or f"required_field_{idx}"

                if tag_name == "input":
                    if input_type == "checkbox":
                        tab_idx = await el.get_attribute("tabindex")
                        if tab_idx == "-1":
                            continue
                        if not await el.is_checked():
                            return name
                    elif input_type == "radio":
                        radio_name = await el.get_attribute("name")
                        if radio_name:
                            group = page.locator(f"input[type='radio'][name='{radio_name}']")
                            g_cnt = await group.count()
                            any_checked = False
                            for g_idx in range(g_cnt):
                                if await group.nth(g_idx).is_checked():
                                    any_checked = True
                                    break
                            if not any_checked:
                                return name
                        else:
                            if not await el.is_checked():
                                return name
                    elif input_type == "file":
                        has_files = await el.evaluate("e => Boolean(e.files && e.files.length > 0)")
                        if not has_files:
                            if dry_run:
                                continue
                            if any(k in name.lower() for k in ("resume", "curriculo", "cv")):
                                continue
                            return name
                    else:
                        val = (await el.input_value()).strip()
                        if not val:
                            return name
                elif tag_name in ("select", "textarea"):
                    val = (await el.input_value()).strip()
                    if not val:
                        return name
            except Exception as exc:
                logger.debug("Erro ao verificar campo obrigatório: %s", exc)
                continue
        return None

    async def _finalize(
        self,
        page: Page,
        job: Job,
        resume: ResumeInfo,
        dry_run: bool,
        ready_check: Callable[[], Awaitable[bool]] | None,
        submit_selector: str,
        confirmation_selector: str,
        timeout_ms: int = 10000,
    ) -> ApplicationResult:
        """Finaliza a candidatura: valida obrigatórios, ready_check, dry-run, submit e confirmação."""
        # 1. Campos obrigatórios vazios (SAFE-5, SAFE-6)
        empty_field = await self._find_empty_required(page, dry_run=dry_run)
        if empty_field:
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason=f"Campo obrigatório não preenchido: {empty_field}",
                dry_run=dry_run,
                submitted=False,
                resume_used=resume.path.name,
            )

        # 2. Verificação prévia de segurança (ready_check / DP6)
        if ready_check is not None:
            is_ready = await ready_check()
            if not is_ready:
                return ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.NEEDS_REVIEW,
                    reason="Bloqueado por ready_check de segurança pré-submissão.",
                    dry_run=dry_run,
                    submitted=False,
                    resume_used=resume.path.name,
                )

        # 3. Dry-run (submissão simulada)
        if dry_run:
            logger.info("Dry-run ativado para %s (job %s). Submissão não realizada.", self.name, job.id)
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.COMPLETED,
                dry_run=True,
                submitted=False,
                resume_used=resume.path.name,
                reason=f"Dry run concluído com sucesso: formulário preenchido sem submissão ({self.name}).",
            )

        # 4. Localização do botão de submissão (SAFE-3)
        submit_loc = page.locator(submit_selector)
        btn_count = await submit_loc.count()
        if btn_count == 0:
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason=f"Botão de submit não encontrado no formulário ({self.name}).",
                dry_run=False,
                submitted=False,
                resume_used=resume.path.name,
            )

        submit_btn = None
        for b_i in range(btn_count - 1, -1, -1):
            cand = submit_loc.nth(b_i)
            with contextlib.suppress(Exception):
                if await cand.is_visible():
                    submit_btn = cand
                    break
        if submit_btn is None:
            submit_btn = submit_loc.last if hasattr(submit_loc, "last") else submit_loc.first

        # 5. Clique de submissão
        with contextlib.suppress(Exception):
            await submit_btn.scroll_into_view_if_needed(timeout=3000)
        try:
            await submit_btn.click(timeout=timeout_ms)
        except Exception:
            try:
                await submit_btn.click(force=True, timeout=timeout_ms)
            except Exception:
                try:
                    await submit_btn.evaluate("el => el.click()")
                except Exception as exc:
                    raise PostSubmitError(f"Erro ao disparar clique de submissão em {self.name}: {exc}") from exc

        # 6. Espera confirmação após clique (SAFE-4)
        try:
            confirmed = False
            try:
                await page.wait_for_selector(confirmation_selector, timeout=min(timeout_ms, 5000))
                confirmed = True
            except Exception:
                await page.wait_for_timeout(3000)
                with contextlib.suppress(Exception):
                    conf_loc = page.locator(confirmation_selector)
                    conf_cnt = await conf_loc.count()
                    for c_i in range(conf_cnt):
                        if await conf_loc.nth(c_i).is_visible():
                            confirmed = True
                            break

                curr_url = (page.url or "").lower()
                body_text = ""
                with contextlib.suppress(Exception):
                    body_text = (await page.inner_text("body")).lower()

                success_keywords = (
                    "thank you",
                    "obrigado",
                    "sucesso",
                    "recebida",
                    "recebido",
                    "submitted",
                    "candidatura enviada",
                    "application received",
                    "application submitted",
                    "thanks for applying",
                    "your application has been sent",
                    "enviada com sucesso",
                    "inscrição realizada",
                    "inscrição enviada",
                    "entraremos em contato",
                    "cadastro realizado",
                )
                success_urls = (
                    "/thanks",
                    "/thank-you",
                    "/submitted",
                    "/success",
                    "/confirmation",
                    "form-app.inhire.app",
                    "status=success",
                    "applied=true",
                )

                if any(u in curr_url for u in success_urls) or any(k in body_text for k in success_keywords):
                    confirmed = True
                elif not confirmed:
                    has_error = False
                    for err_sel in (".field-error", ".invalid-feedback", ".form-error", ".alert-danger"):
                        with contextlib.suppress(Exception):
                            err_loc = page.locator(err_sel)
                            if await err_loc.count() > 0 and await err_loc.first.is_visible():
                                has_error = True
                                break
                    if not has_error:
                        logger.info(
                            "Submit acionado e nenhum erro de validação visível na tela (%s). Confirmado.",
                            self.name,
                        )
                        confirmed = True

            if not confirmed:
                raise TimeoutError(f"Confirmação não detectada via '{confirmation_selector}' nem via página de sucesso.")

            logger.info("Candidatura enviada com sucesso para job %s (%s).", job.id, self.name)
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.COMPLETED,
                dry_run=False,
                submitted=True,
                resume_used=resume.path.name,
                reason=f"Candidatura enviada e confirmada ({self.name}).",
            )
        except Exception as exc:
            # Pós-submit com falha: NUNCA deve ser re-tentado automaticamente
            raise PostSubmitError(
                f"Submit disparado em {self.name} para job {job.id}, mas confirmação não foi detectada a tempo: {exc}"
            ) from exc

    async def _collect_page_signals(self, page: Page, description: str = "") -> str:
        """Coleta sinais técnicos da página sem PII para o DP3."""
        try:
            from urllib.parse import urlparse

            title = ""
            with contextlib.suppress(Exception):
                title = await page.title()

            host = ""
            try:
                raw_url = page.url
                if raw_url:
                    host = urlparse(raw_url).netloc
            except Exception:
                pass

            body_snippet = ""
            try:
                container_sel = "main, [role='main'], form, #main-content, .ia-BasePage, .application-container"
                main_el = page.locator(container_sel).first
                target_el = main_el if await main_el.count() > 0 else page.locator("body")
                if await target_el.count() > 0:
                    text = await target_el.inner_text()
                    if description and description in text:
                        text = text.replace(description, "")
                    clean_text = " ".join(text.split())
                    import re
                    clean_text = re.sub(
                        r"(?:(?:este\s+site|este\s+s[ií]tio|this\s+site)\s+(?:[eé]|es|is)\s+)?(?:protect\w+|proteg\w+)\s+(?:pelo|por|by)\s+recaptcha[\s\S]*?(?:google|\.|$)",
                        "",
                        clean_text,
                        flags=re.IGNORECASE,
                    )
                    clean_text = re.sub(
                        r"(?:ir para o conte[uú]do principal|p[aá]gina inicial|"
                        r"avalia[cç][oõ]es de empresas|cargos e sal[aá]rios|"
                        r"mensagens n[uú]mero de mensagens[\s\S]*?anunciar vagas)",
                        "",
                        clean_text,
                        flags=re.IGNORECASE,
                    )
                    body_snippet = " ".join(clean_text[:500].split())
            except Exception:
                pass

            parts = []
            if title:
                parts.append(f"title: {title}")
            if host:
                parts.append(f"host: {host}")
            if body_snippet:
                parts.append(f"body: {body_snippet}")
            return " | ".join(parts)
        except Exception as exc:
            logger.debug("Erro ao coletar sinais da página: %s", exc)
            return ""

    @abstractmethod
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
        """Preenche o formulário e executa submit (ou para antes se dry_run=True)."""
        ...
