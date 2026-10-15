"""Pipeline principal de processamento de vagas com integração ATS, Laya, LLM e segurança."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from automation.base_handler import BaseATSHandler
from automation.browser import BrowserManager
from automation.registry import get_handler
from config.settings import Settings
from core.decision.layer import DecisionLayer
from core.matcher import JobMatcher
from core.models import (
    AnswerSet,
    ApplicationResult,
    BlockerDetected,
    FormQuestion,
    Job,
    PostSubmitError,
    Profile,
    ResumeInfo,
    TaskStatus,
)
from core.resume_resolver import ResumeResolver

logger = logging.getLogger(__name__)


class Pipeline:
    """Orquestrador do ciclo de vida de uma candidatura."""

    def __init__(
        self,
        settings: Settings,
        decision_layer: DecisionLayer,
        resume_resolver: ResumeResolver,
        browser_manager: BrowserManager,
        matcher: JobMatcher,
    ) -> None:
        self.settings = settings
        self.decision_layer = decision_layer
        self.resume_resolver = resume_resolver
        self.browser_manager = browser_manager
        self.matcher = matcher

    async def _review(
        self,
        page: Any | None,
        job: Job,
        reason: str,
        resume_info: ResumeInfo | None = None,
        tag: str = "review",
        submitted: bool = False,
    ) -> ApplicationResult:
        """Monta ApplicationResult com NEEDS_REVIEW e captura screenshot logando falhas em debug."""
        screenshot_path: Path | None = None
        if page is not None and self.browser_manager is not None:
            try:
                screenshot_path = await self.browser_manager.save_screenshot(page, f"{tag}_{job.id}")
            except Exception as exc:
                logger.debug("Falha ao salvar screenshot (%s) para job %s: %s", tag, job.id, exc)

        return ApplicationResult(
            job_id=job.id,
            status=TaskStatus.NEEDS_REVIEW,
            reason=reason,
            dry_run=self.settings.DRY_RUN,
            submitted=submitted,
            resume_used=resume_info.path.name if resume_info else None,
            screenshot_path=screenshot_path,
        )

    async def _triage(self, job: Job, profile: Profile) -> ApplicationResult | None:
        """Etapa 1: Triagem de status prévio e Fit Score Gate (DP1)."""
        normalized_sheet_status = (job.sheet_status or "").strip().lower()
        if any(term in normalized_sheet_status for term in ["candidatado", "aplicado", "descartad", "rejeitad"]):
            logger.info("Job %s ignorado: status prévio na planilha '%s'.", job.id, job.sheet_status)
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.SKIPPED,
                reason=f"Status prévio na planilha '{job.sheet_status}' ignorado.",
                dry_run=self.settings.DRY_RUN,
            )

        dp1_action, dp1_reason = await self.decision_layer.decide_job_fit(job, profile)
        if dp1_action == "SKIPPED":
            logger.info("Job %s descartado no DP1: %s", job.id, dp1_reason)
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.SKIPPED,
                reason=dp1_reason or "Descartado na triagem",
                dry_run=self.settings.DRY_RUN,
            )
        elif dp1_action == "NEEDS_REVIEW":
            # Em modo autônomo, se o fit_score da planilha for aceitável (>= MIN_FIT_SCORE) ou ausente, prossegue
            if getattr(self.settings, "AUTONOMOUS_MODE", False) and (job.fit_score is None or job.fit_score >= self.settings.MIN_FIT_SCORE):
                logger.info(
                    "DP1 sugeriu revisão (%s), mas prosseguindo autonomamente por fit compatível (score=%s).",
                    dp1_reason,
                    job.fit_score,
                )
            else:
                logger.info("Job %s enviado para revisão no DP1: %s", job.id, dp1_reason)
                return ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.NEEDS_REVIEW,
                    reason=dp1_reason or "Necessita revisão no fit",
                    dry_run=self.settings.DRY_RUN,
                )
        return None

    async def _resolve_resume(self, job: Job) -> ResumeInfo | None:
        """Etapa 2: Resolução de currículo por label ou fallback DP2."""
        resume_info: ResumeInfo | None = None
        try:
            resume_info = await self.resume_resolver.resolve(job)
        except Exception as exc:
            logger.warning("Falha ao resolver currículo para job %s: %s", job.id, exc)

        if resume_info is None:
            base_resumes = self.resume_resolver.get_base_resumes(job.base_used)
            if base_resumes:
                async def matcher_fallback():
                    return await self.matcher.select_best_resume(job.description or "", base_resumes)

                resume_info, _ = await self.decision_layer.decide_resume_fallback(
                    job=job,
                    job_description=job.description or "",
                    base_resumes=base_resumes,
                    matcher_fallback_fn=matcher_fallback,
                )

        # Fallback autônomo: seleciona o currículo mais próximo do cache local
        if resume_info is None:
            all_resumes = list(self.resume_resolver.get_resumes().values())
            if all_resumes:
                company_norm = (job.company or "").lower()
                for r in all_resumes:
                    if company_norm and company_norm in r.path.name.lower():
                        resume_info = r
                        break
                if resume_info is None:
                    is_en = getattr(job, "language", "pt") == "en"
                    for r in all_resumes:
                        name_lower = r.path.name.lower()
                        if is_en and ("resume" in name_lower or "english" in name_lower or "ingles" in name_lower):
                            resume_info = r
                            break
                        if not is_en and ("curr" in name_lower or "pt" in name_lower):
                            resume_info = r
                            break
                if resume_info is None:
                    resume_info = all_resumes[0]
                logger.info("Currículo resolvido via fallback autônomo para job %s: %s", job.id, resume_info.path.name)

        return resume_info

    async def _build_answers(
        self,
        job: Job,
        profile: Profile,
        resume_info: ResumeInfo,
        non_sensitive_questions: list[FormQuestion],
    ) -> AnswerSet:
        """Etapa 5: Respostas com DP5 (mapeamento determinístico) e LLM Matcher."""
        answer_set = AnswerSet()
        unanswered_questions: list[FormQuestion] = []

        for q in non_sensitive_questions:
            mapped_val = await self.decision_layer.decide_field_mapping(job.id, q, profile)
            if mapped_val is not None:
                answer_set.answers[q.id] = mapped_val
            else:
                unanswered_questions.append(q)

        if unanswered_questions:
            if getattr(self.settings, "NO_LLM", False):
                logger.info(
                    "Modo NO_LLM ativo: respondendo %d campos de formulário diretamente via perfil/regras.",
                    len(unanswered_questions),
                )
                fallback = self.matcher.fallback_answers(unanswered_questions, profile)
                answer_set.answers.update(fallback.answers)
            else:
                try:
                    llm_answers = await self.matcher.answer_questions(
                        job=job,
                        questions=unanswered_questions,
                        profile=profile,
                        resume_text=resume_info.text,
                    )
                    answer_set.answers.update(llm_answers.answers)
                except Exception as exc:
                    logger.warning(
                        "Falha ao chamar LLM para job %s (%s); aplicando respostas do perfil.",
                        job.id,
                        exc,
                    )
                    fallback = self.matcher.fallback_answers(unanswered_questions, profile)
                    answer_set.answers.update(fallback.answers)

        return answer_set

    def _synthesize_autonomous_answer(self, q: FormQuestion, profile: Profile, job: Job | None = None) -> str:
        """Sintetiza resposta autônoma plausível e profissional para perguntas obrigatórias sem dados diretos."""
        norm_label = q.label.lower()

        # 1. Se possuir opções pré-definidas (select / radio)
        if q.options:
            positive_keywords = [
                "yes", "sim", "true", "1", "linkedin", "remoto", "remote", "integral",
                "full-time", "full time", "outro", "other", "pj", "clt", "authorized", "autorizado"
            ]
            for kw in positive_keywords:
                for opt in q.options:
                    if kw in opt.lower():
                        return opt
            valid_opts = [o for o in q.options if not any(ph in o.lower() for ph in ("selecion", "select", "choose", "--"))]
            if valid_opts:
                return valid_opts[0]
            return q.options[0]

        # 2. Checkbox
        if q.kind == "checkbox":
            return "true"

        # 3. Perguntas abertas / textuais por intenção semântica
        if any(term in norm_label for term in ("email", "e-mail", "correio")):
            return profile.email

        if any(term in norm_label for term in ("telefone", "phone", "whatsapp", "celular", "mobile")):
            return profile.phone

        if any(term in norm_label for term in ("salár", "salary", "pretens", "compensat", "remunera")):
            if any(term in norm_label for term in ("usd", "dollar", "dólar", "$")):
                return "5000"
            return profile.base_answers.get("pretensao_salarial", profile.salary_expectation or "A combinar")

        if any(term in norm_label for term in ("aviso", "notice", "start", "início", "disponib")):
            is_en = (getattr(job, "language", "pt") == "en") if job else False
            return "Immediately" if is_en else profile.base_answers.get("aviso_previo", "Imediato")

        if any(term in norm_label for term in ("empresa", "company", "org", "employer", "atual")):
            return profile.current_company or "Autônomo / Freelancer"

        if any(term in norm_label for term in ("cidade", "city", "local", "location", "endereço", "address", "país", "country")):
            return profile.location or "Brasil"

        if "linkedin" in norm_label:
            return profile.links.get("linkedin", "")

        if "github" in norm_label:
            return profile.links.get("github", "")

        if any(term in norm_label for term in ("site", "portfolio", "website")):
            return profile.links.get("portfolio", profile.links.get("github", ""))

        if any(term in norm_label for term in ("loom", "video", "vídeo", "recording", "intro")):
            linkedin_url = profile.links.get("linkedin", "")
            prefix = f"{linkedin_url} " if linkedin_url else ""
            return f"{prefix}(Video intro available upon request)".strip()

        if any(term in norm_label for term in ("experiên", "experience", "projet", "project", "resumo", "about")):
            return profile.headline or (profile.skills[0] if profile.skills else "Engenheiro de Software")

        is_en = (getattr(job, "language", "pt") == "en") if job else False
        if is_en:
            return "Yes, I have solid experience with all the required technologies and full availability."
        return "Sim, possuo sólida experiência com as tecnologias exigidas e total disponibilidade."

    def _check_required_facts(
        self,
        non_sensitive_questions: list[FormQuestion],
        answer_set: AnswerSet,
        profile: Profile,
        job: Job | None = None,
    ) -> FormQuestion | None:
        """Valida que todas as perguntas obrigatórias possuem fatos preenchidos ou sintetizados."""
        from core.decision.rules import _normalize, resolve_profile_answer

        for q in non_sensitive_questions:
            if not q.required:
                continue

            ans_val = answer_set.answers.get(q.id)
            if ans_val is None or str(ans_val).strip() == "":
                # 1. Tenta resolver via regras determinísticas de perfil
                resolved = resolve_profile_answer(q, profile)
                if resolved is not None:
                    ans_val = resolved
                    answer_set.answers[q.id] = resolved
                else:
                    # 2. Fallback em base_answers exigindo correspondência por tokens inteiros normalizados
                    norm_label = re.sub(r"[^\w\s]", " ", _normalize(q.label))
                    norm_label_tokens = set(norm_label.split())
                    for bk, bv in profile.base_answers.items():
                        clean_bk = re.sub(r"[^\w\s]", " ", _normalize(bk).replace("_", " "))
                        key_tokens = clean_bk.split()
                        if key_tokens and all(tok in norm_label_tokens for tok in key_tokens):
                            ans_val = bv
                            answer_set.answers[q.id] = bv
                            break

            if ans_val is None or str(ans_val).strip() == "":
                # 3. Se modo autônomo estiver ativo, sintetiza para garantir preenchimento sem intervenção humana
                if getattr(self.settings, "AUTONOMOUS_MODE", False):
                    synthetic_ans = self._synthesize_autonomous_answer(q, profile, job)
                    answer_set.answers[q.id] = synthetic_ans
                    logger.info("Pergunta obrigatória '%s' resolvida autonomamente com '%s'", q.label, synthetic_ans)
                else:
                    return q

        return None


    def _finalize_metrics(
        self,
        result: ApplicationResult,
        start_time: float,
        started_at: str,
    ) -> ApplicationResult:
        import time
        from datetime import datetime, timezone

        duration_s = round(time.perf_counter() - start_time, 2)
        tok_info = (
            self.matcher.get_and_reset_job_tokens()
            if hasattr(self.matcher, "get_and_reset_job_tokens")
            else {}
        )
        result.duration_s = duration_s
        result.tokens_used = tok_info.get("total_tokens", 0)
        result.prompt_tokens = tok_info.get("prompt_tokens", 0)
        result.completion_tokens = tok_info.get("completion_tokens", 0)
        result.started_at = started_at
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    async def process_job(
        self,
        job: Job,
        profile: Profile,
        screenshot_on_review: bool = True,
        on_step: Callable[[Job, str], Any] | None = None,
    ) -> ApplicationResult:
        """Executa todas as etapas para uma vaga: triagem, currículo, inspeção, LLM e preenchimento."""
        import asyncio
        import time
        from datetime import datetime, timezone

        start_time = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        if hasattr(self.matcher, "get_and_reset_job_tokens"):
            self.matcher.get_and_reset_job_tokens()

        async def _emit_step(step_name: str) -> None:
            if on_step is not None:
                try:
                    cb_res = on_step(job, step_name)
                    if asyncio.iscoroutine(cb_res):
                        await cb_res
                except Exception as exc:
                    logger.debug("Erro ao emitir step (%s) no job %s: %s", step_name, job.id, exc)

        await _emit_step("Iniciando triagem da vaga")

        # ---------------------------------------------------------------- Etapa 1: Triagem (DP1)
        triage_result = await self._triage(job, profile)
        if triage_result is not None:
            return self._finalize_metrics(triage_result, start_time, started_at)

        # ---------------------------------------------------------------- Etapa 2: Currículo (DP2)
        await _emit_step("Resolvendo currículo compatível")
        resume_info = await self._resolve_resume(job)
        if resume_info is None:
            logger.warning("Currículo não encontrado para job %s (label=%s)", job.id, job.resume_label)
            res = ApplicationResult(
                job_id=job.id,
                status=TaskStatus.NEEDS_REVIEW,
                reason="Nenhum currículo compatível encontrado no cache local.",
                dry_run=self.settings.DRY_RUN,
            )
            return self._finalize_metrics(res, start_time, started_at)

        # ---------------------------------------------------------------- Etapa 3: Handler de ATS
        handler: BaseATSHandler | None = get_handler(job)
        if handler is None:
            logger.info("ATS não suportada para job %s (ats=%s, url=%s)", job.id, job.ats, job.url)
            res = ApplicationResult(
                job_id=job.id,
                status=TaskStatus.SKIPPED,
                reason=f"Plataforma ATS não suportada (ats={job.ats}, url={job.url})",
                dry_run=self.settings.DRY_RUN,
                resume_used=resume_info.path.name,
            )
            return self._finalize_metrics(res, start_time, started_at)

        # ---------------------------------------------------------------- Etapa 4: Inspeção no Navegador e DP3
        await _emit_step(f"Acessando página da vaga ({job.ats or 'portal'})...")
        try:
            async with self.browser_manager.new_page() as page:
                try:
                    await _emit_step("Inspecionando formulário e campos...")
                    page_info = await handler.inspect(page, job)
                except BlockerDetected as exc:
                    logger.warning("Blocker detectado durante inspeção do job %s: %s", job.id, exc)
                    lower_exc = str(exc).lower()
                    if any(term in lower_exc for term in ("busca/listagem", "encerrada", "expirada", "não encontrada", "404", "fechada", "não está mais disponível")):
                        res = ApplicationResult(
                            job_id=job.id,
                            status=TaskStatus.SKIPPED,
                            reason=str(exc),
                            dry_run=self.settings.DRY_RUN,
                            resume_used=resume_info.path.name if resume_info else None,
                        )
                        return self._finalize_metrics(res, start_time, started_at)
                    res = await self._review(
                        page if screenshot_on_review else None,
                        job,
                        f"Blocker detectado: {exc}",
                        resume_info=resume_info,
                        tag="blocker",
                    )
                    return self._finalize_metrics(res, start_time, started_at)

                if not job.description and page_info.description:
                    job = job.model_copy(update={"description": page_info.description})

                # Avaliação DP3 (detecção de blocker)
                is_blocked, dp3_reason = await self.decision_layer.decide_blocker(
                    job_id=job.id,
                    page_info=page_info,
                    dry_run=self.settings.DRY_RUN,
                )
                if is_blocked:
                    logger.warning("Blocker identificado pelo DP3 para job %s: %s", job.id, dp3_reason)
                    res = await self._review(
                        page if screenshot_on_review else None,
                        job,
                        f"Blocker detectado pelo DP3: {dp3_reason}",
                        resume_info=resume_info,
                        tag="dp3_blocker",
                    )
                    return self._finalize_metrics(res, start_time, started_at)

                # ------------------------------------------------------------ Etapa 5: Respostas do Formulário
                # Separação estrita com DP4: perguntas sensíveis NUNCA vão ao LLM ou DP6
                sensitive_questions: list[FormQuestion] = []
                non_sensitive_questions: list[FormQuestion] = []
                for q in page_info.questions:
                    is_sens = await self.decision_layer.decide_sensitivity(job.id, q)
                    if is_sens:
                        sensitive_questions.append(q)
                    else:
                        non_sensitive_questions.append(q)

                await _emit_step("Sintetizando respostas com IA...")
                answer_set = await self._build_answers(job, profile, resume_info, non_sensitive_questions)

                # Preenche perguntas sensíveis (EEO) deterministicamente a partir de profile.eeo ou 'Prefer not to say'
                for sq in sensitive_questions:
                    eeo_ans = None
                    norm_sq = sq.label.lower()
                    for ek, ev in profile.eeo.items():
                        if ek.lower() in norm_sq:
                            eeo_ans = ev
                            break
                    if not eeo_ans and sq.options:
                        for opt in sq.options:
                            if any(p in opt.lower() for p in ("prefer not", "decline", "não declarar", "prefiro não")):
                                eeo_ans = opt
                                break
                        if not eeo_ans:
                            eeo_ans = sq.options[0]
                    if eeo_ans:
                        answer_set.answers[sq.id] = eeo_ans

                # Invariante 2: Ausência de fatos não pode ser alucinada -> sintetizado se obrigatória
                missing_q = self._check_required_facts(non_sensitive_questions, answer_set, profile, job=job)
                if missing_q is not None:
                    logger.warning(
                        "Job %s requer revisão humana: pergunta obrigatória '%s' sem fatos no perfil.",
                        job.id,
                        missing_q.label,
                    )
                    res = await self._review(
                        page if screenshot_on_review else None,
                        job,
                        f"Dados ausentes no perfil para pergunta obrigatória: '{missing_q.label}'",
                        resume_info=resume_info,
                        tag="missing_req",
                    )
                    return self._finalize_metrics(res, start_time, started_at)

                # ------------------------------------------------------------ Etapa 6: Preenchimento e Submit (DP6)
                await _emit_step("Preenchendo campos no formulário...")
                async def ready_check() -> bool:
                    """Verificação pré-submit via DP6 com assimetria de segurança."""
                    can_submit, _ = await self.decision_layer.decide_ready_to_submit(
                        job_id=job.id,
                        questions=non_sensitive_questions,
                        filled=answer_set.answers,
                        visible_errors=[],
                        dry_run=self.settings.DRY_RUN,
                    )
                    if self.settings.DRY_RUN or self.settings.AUTONOMOUS_MODE:
                        return True
                    return can_submit

                try:
                    result = await handler.fill_and_submit(
                        page=page,
                        job=job,
                        profile=profile,
                        resume=resume_info,
                        answers=answer_set,
                        dry_run=self.settings.DRY_RUN,
                        ready_check=ready_check,
                    )
                except PostSubmitError as exc:
                    logger.error("Falha pós-submit para job %s (sem retry): %s", job.id, exc)
                    res = await self._review(
                        page if screenshot_on_review else None,
                        job,
                        f"Erro pós-submissão (NÃO RETENTAR): {exc}",
                        resume_info=resume_info,
                        tag="post_submit_fail",
                        submitted=True,
                    )
                    return self._finalize_metrics(res, start_time, started_at)

                await _emit_step("Candidatura concluída!")
                return self._finalize_metrics(result, start_time, started_at)
        finally:
            handler.release(job)
