"""Camada de decisão (DecisionLayer): DP1 a DP6, modos, fallbacks e auditoria.

Invariantes de segurança:
- DP3, DP4 e DP6: Laya nunca remove bloqueios impostos por regras determinísticas.
- Auditoria sem PII (apenas state_sha256).
- Indisponibilidade do Laya nunca derruba a execução (aplica fallback do DP).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from config.settings import DP_NAMES, RELEASING_DPS, Settings
from core.decision.laya_client import DecisionModel
from core.decision.models import Band, DecisionRecord, DecisionSource, NoulVerdict
from core.decision.rules import (
    has_blocker_marker,
    is_definitely_standard_question,
    is_sensitive_question,
    missing_required,
    rank_profile_keys,
)
from core.decision.states import (
    build_dp1_job_fit_state,
    build_dp2_resume_fallback_state,
    build_dp3_blocker_state,
    build_dp4_sensitivity_state,
    build_dp5_field_mapping_state,
    build_dp6_ready_to_submit_state,
    clean_option_label,
)
from core.models import DecisionUnavailable, FormQuestion, Job, PageInfo, Profile, ResumeInfo

log = logging.getLogger(__name__)


def _noul_probs(verdict: NoulVerdict | None) -> dict[str, float] | None:
    """Retorna probabilidades normalizadas para veredicto noul."""
    if verdict is None:
        return None
    return {"true": verdict.p_true, "false": round(1.0 - verdict.p_true, 4)}


class DecisionAudit:
    """Escritor atômico/concorrente de registros de auditoria em decisions.jsonl."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self.records: list[DecisionRecord] = []

    async def log_decision(self, record: DecisionRecord) -> None:
        self.records.append(record)
        line = record.model_dump_json() + "\n"
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)


class DecisionLayer:
    """Orquestrador dos pontos de decisão (DP1 a DP6)."""

    def __init__(
        self,
        model: DecisionModel | None,
        settings: Settings,
        audit: DecisionAudit | None = None,
        eval_report: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._settings = settings
        self._audit = audit or DecisionAudit(settings.DECISION_AUDIT_FILE)
        self._eval_report = eval_report or {}
        self._effective_modes: dict[str, str] = {}
        self._resolve_modes()

    def _resolve_modes(self) -> None:
        """Calcula o modo efetivo de cada DP, aplicando rebaixamento quando exigido."""
        now = datetime.now(UTC)
        for dp in DP_NAMES:
            configured = self._settings.LAYA_DP_MODES.get(dp, "shadow")
            if (
                configured == "active"
                and dp in RELEASING_DPS
                and self._settings.LAYA_REQUIRE_EVAL_FOR_ACTIVE
            ):
                dp_eval = self._eval_report.get(dp)
                is_valid = False
                if dp_eval and dp_eval.get("passed"):
                    eval_ts_str = dp_eval.get("ts")
                    if eval_ts_str:
                        try:
                            eval_dt = datetime.fromisoformat(eval_ts_str)
                            age_days = (now - eval_dt).total_seconds() / 86400
                            if age_days <= self._settings.LAYA_EVAL_MAX_AGE_DAYS:
                                is_valid = True
                        except ValueError:
                            pass
                if not is_valid:
                    log.warning(
                        "Ponto de decisão %s rebaixado de 'active' para 'shadow' "
                        "(avaliação ausente, reprovada ou com mais de %d dias).",
                        dp,
                        self._settings.LAYA_EVAL_MAX_AGE_DAYS,
                    )
                    self._effective_modes[dp] = "shadow"
                    continue

            self._effective_modes[dp] = configured

    @property
    def effective_modes(self) -> dict[str, str]:
        return dict(self._effective_modes)

    def _state_hash(self, state: dict[str, str]) -> str:
        serialized = json.dumps(state, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    async def _timed(self, coro: Any) -> tuple[Any | None, float]:
        """Executa chamada ao Laya medindo latência e tratando DecisionUnavailable."""
        start = time.perf_counter()
        try:
            return await coro, (time.perf_counter() - start) * 1000
        except DecisionUnavailable as exc:
            if self._model and getattr(self._model, "breaker_open", False):
                log.debug("Laya indisponível (circuit breaker aberto): %s", exc)
            else:
                log.warning("Laya indisponível: %s", exc)
            return None, (time.perf_counter() - start) * 1000

    async def _audit_record(
        self,
        dp: str,
        job_id: str,
        question_id: str | None,
        kind: str,
        mode: str,
        probs: dict[str, float] | None,
        band: Band | None,
        laya_action: str | None,
        applied_action: str,
        source: Literal["laya", "rules", "llm", "sheet", "fallback"],
        latency_ms: float,
        state: dict[str, str],
    ) -> None:
        record = DecisionRecord(
            dp=dp,
            job_id=job_id,
            question_id=question_id,
            kind=kind,
            mode="active" if mode == "active" else "shadow",
            probs=probs,
            band=band,
            laya_action=laya_action,
            applied_action=applied_action,
            source=source,
            latency_ms=latency_ms,
            state_sha256=self._state_hash(state),
            ts=datetime.now(UTC).isoformat(),
        )
        await self._audit.log_decision(record)

    # -------------------------------------------------------------------------
    # DP1: job_fit
    # -------------------------------------------------------------------------
    async def decide_job_fit(self, job: Job, profile: Profile) -> tuple[str, str | None]:
        """DP1: Decide se a vaga prossegue, é ignorada (SKIPPED) ou precisa de revisão.

        Retorna (ação, motivo): ação em {"PROCEED", "SKIPPED", "NEEDS_REVIEW"}.
        """
        dp = "DP1"
        mode = self._effective_modes[dp]
        state = build_dp1_job_fit_state(job, profile, self._settings.LAYA_STATE_MAX_CHARS)

        # Regra determinística da planilha (sempre tem precedência de corte mínimo se FIT_GATE_ENABLED)
        if (
            self._settings.FIT_GATE_ENABLED
            and job.fit_score is not None
            and job.fit_score < self._settings.MIN_FIT_SCORE
        ):
            sheet_action = "SKIPPED"
            sheet_reason = f"Fit score ({job.fit_score}%) abaixo do mínimo ({self._settings.MIN_FIT_SCORE}%)"
        else:
            sheet_action = "PROCEED"
            sheet_reason = None

        if mode == "off" or self._model is None:
            await self._audit_record(
                dp, job.id, None, "noul", mode, None, None, None, sheet_action, "sheet", 0.0, state
            )
            return sheet_action, sheet_reason

        laya_verdict, latency = await self._timed(
            self._model.noul(
                state=state,
                instructions="O perfil do candidato é compatível com os requisitos e tecnologias desta vaga?",
            )
        )

        laya_action: str | None = None
        if laya_verdict is not None:
            if laya_verdict.band == "no":
                laya_action = "SKIPPED" if job.fit_score is None else "NEEDS_REVIEW"
            elif laya_verdict.band == "uncertain":
                laya_action = "NEEDS_REVIEW"
            else:
                laya_action = "PROCEED"

        # Se Laya falhou
        if laya_verdict is None:
            await self._audit_record(
                dp, job.id, None, "noul", mode, None, None, None, sheet_action, "fallback", latency, state
            )
            return sheet_action, sheet_reason

        # Se a planilha impôs corte determinístico por nota baixa, isso NUNCA é revertido
        source: DecisionSource
        if sheet_action == "SKIPPED":
            applied_action = "SKIPPED"
            applied_reason = sheet_reason
            source = "sheet"
        elif mode == "active":
            if (
                job.fit_score is not None
                and job.fit_score >= self._settings.MIN_FIT_SCORE
                and laya_verdict.band == "no"
            ):
                applied_action = "NEEDS_REVIEW"
                applied_reason = "Divergência: fit score da planilha alto mas Laya indicou incompatibilidade"
                source = "laya"
            elif job.fit_score is None:
                if laya_verdict.band == "no":
                    applied_action = "SKIPPED"
                    applied_reason = "Laya indicou incompatibilidade com perfil sem fit score prévio"
                elif laya_verdict.band == "uncertain":
                    applied_action = "NEEDS_REVIEW"
                    applied_reason = "Laya incerto sobre compatibilidade sem fit score prévio"
                else:
                    applied_action = "PROCEED"
                    applied_reason = None
                source = "laya"
            else:
                applied_action = "PROCEED"
                applied_reason = None
                source = "laya"
        else:
            # Modo shadow: aplica ação da planilha
            applied_action = sheet_action
            applied_reason = sheet_reason
            source = "sheet"

        probs = _noul_probs(laya_verdict)
        await self._audit_record(
            dp,
            job.id,
            None,
            "noul",
            mode,
            probs,
            laya_verdict.band,
            laya_action,
            applied_action,
            source,
            latency,
            state,
        )
        return applied_action, applied_reason

    # -------------------------------------------------------------------------
    # DP2: resume_fallback
    # -------------------------------------------------------------------------
    async def decide_resume_fallback(
        self,
        job: Job,
        job_description: str,
        base_resumes: dict[str, ResumeInfo],
        matcher_fallback_fn: Any,
    ) -> tuple[ResumeInfo | None, str | None]:
        """DP2: Escolha de currículo-base substituto quando não há otimizado."""
        dp = "DP2"
        mode = self._effective_modes[dp]
        state = build_dp2_resume_fallback_state(job, job_description, self._settings.LAYA_STATE_MAX_CHARS)

        if not base_resumes:
            return None, "Nenhum currículo-base disponível"

        options = {clean_option_label(name): info.summary[:40] for name, info in base_resumes.items()}
        # Mapeia rótulo limpo -> ResumeInfo
        opt_to_resume = {clean_option_label(name): info for name, info in base_resumes.items()}

        if mode == "off" or self._model is None:
            # Chama LLM direto
            chosen = await matcher_fallback_fn()
            chosen_name = chosen.filename if chosen else None
            found = base_resumes.get(chosen_name) if chosen_name else None
            await self._audit_record(
                dp, job.id, None, "choice", mode, None, None, None,
                chosen_name or "none", "llm", 0.0, state
            )
            return found, None if found else "LLM não selecionou currículo válido"

        laya_verdict, latency = await self._timed(
            self._model.choice(
                state=state,
                instructions="Qual destes currículos-base é o mais adequado para esta vaga?",
                options=options,
            )
        )

        laya_chosen = laya_verdict.top if laya_verdict else None
        laya_qualifies = (
            laya_verdict is not None
            and laya_verdict.order_consistent
            and laya_verdict.margin >= self._settings.LAYA_MIN_MARGIN
        )

        applied_resume: ResumeInfo | None = None
        source: DecisionSource
        if mode == "active" and laya_qualifies and laya_chosen in opt_to_resume:
            applied_resume = opt_to_resume[laya_chosen]
            applied_action = laya_chosen
            source = "laya"
        else:
            # Fallback para LLM em shadow, indisponível ou baixa margem
            try:
                chosen = await matcher_fallback_fn()
                chosen_name = chosen.filename if chosen else None
            except Exception as exc:
                log.warning("Matcher fallback indisponível para DP2 (%s); usando primeiro currículo-base.", exc)
                chosen_name = next(iter(base_resumes.keys()), None)
            applied_resume = base_resumes.get(chosen_name) if chosen_name else None
            applied_action = str(chosen_name) if chosen_name else "none"
            source = "fallback" if laya_verdict is None else "llm"

        probs = laya_verdict.probs if laya_verdict else None
        await self._audit_record(
            dp, job.id, None, "choice", mode, probs, None, laya_chosen, applied_action, source, latency, state
        )
        return applied_resume, None if applied_resume else "Nenhum currículo selecionado"

    # -------------------------------------------------------------------------
    # DP3: blocker_check
    # -------------------------------------------------------------------------
    async def decide_blocker(self, job_id: str, page_info: PageInfo, dry_run: bool) -> tuple[bool, str | None]:
        """DP3: Detecta CAPTCHA / login / verificação antibot.

        Retorna (is_blocked, reason). Se True -> disparar NEEDS_REVIEW.
        """
        dp = "DP3"
        mode = self._effective_modes[dp]
        state = build_dp3_blocker_state(page_info, self._settings.LAYA_STATE_MAX_CHARS)

        # Regra determinística
        rule_blocked = has_blocker_marker(page_info.page_signals)
        rule_action = "BLOCKED" if rule_blocked else "CLEAR"

        if mode == "off" or self._model is None:
            await self._audit_record(
                dp, job_id, None, "noul", mode, None, None, None, rule_action, "rules", 0.0, state
            )
            return rule_blocked, "Bloqueio antibot/login detectado por regra determinística" if rule_blocked else None

        laya_verdict, latency = await self._timed(
            self._model.noul(
                state=state,
                instructions="O acesso ao formulário está bloqueado ou liberado?",
                criteria={
                    "true": "Bloqueado por erro 403/500, desafio antibot Cloudflare ativo ou tela obrigatória de login",
                    "false": (
                        "Liberado com formulário de candidatura acessível, "
                        "seleção de currículo ou visualização de vaga"
                    ),
                },
            )
        )

        laya_action: str | None = None
        if laya_verdict is not None:
            if laya_verdict.band == "yes":
                laya_action = "BLOCKED"
            elif laya_verdict.band == "uncertain":
                laya_action = "BLOCKED" if not dry_run else "WARNING"
            else:
                laya_action = "CLEAR"

        # ASSIMETRIA DE SEGURANÇA: Se regra detectou bloqueio, Laya NUNCA pode liberar!
        source: DecisionSource
        if rule_blocked:
            applied_action = "BLOCKED"
            reason = "Bloqueio antibot/login detectado deterministamente"
            source = "rules"
        elif laya_verdict is None:
            applied_action = rule_action
            reason = None
            source = "fallback"
        elif mode == "active":
            if getattr(self._settings, "AUTONOMOUS_MODE", False) and not rule_blocked:
                applied_action = "CLEAR"
                reason = None
                source = "rules"
            elif laya_verdict.band == "yes":
                applied_action = "BLOCKED"
                reason = "Laya detectou bloqueio de página (CAPTCHA/login)"
                source = "laya"
            elif laya_verdict.band == "uncertain":
                if not dry_run:
                    applied_action = "BLOCKED"
                    reason = "Laya incerto sobre bloqueio de página em execução real (DRY_RUN=False)"
                else:
                    applied_action = "CLEAR"
                    reason = None
                    log.warning("DP3: Laya incerto sobre bloqueio em dry-run; seguindo com aviso.")
                source = "laya"
            else:
                applied_action = "CLEAR"
                reason = None
                source = "laya"
        else:
            applied_action = rule_action
            reason = None
            source = "rules"

        probs = _noul_probs(laya_verdict)
        band = laya_verdict.band if laya_verdict else None
        await self._audit_record(
            dp, job_id, None, "noul", mode, probs, band, laya_action, applied_action, source, latency, state
        )
        return (applied_action == "BLOCKED"), reason

    # -------------------------------------------------------------------------
    # DP4: question_sensitivity
    # -------------------------------------------------------------------------
    async def decide_sensitivity(self, job_id: str, question: FormQuestion) -> bool:
        """DP4: Identifica perguntas sensíveis (EEO). Se sensível, NUNCA vai ao LLM."""
        dp = "DP4"
        mode = self._effective_modes[dp]
        state = build_dp4_sensitivity_state(question, self._settings.LAYA_STATE_MAX_CHARS)

        rule_sensitive = is_sensitive_question(question.label)
        rule_action = "SENSITIVE" if rule_sensitive else "STANDARD"

        if mode == "off" or self._model is None:
            await self._audit_record(
                dp, job_id, question.id, "noul", mode, None, None, None, rule_action, "rules", 0.0, state
            )
            return rule_sensitive

        laya_verdict, latency = await self._timed(
            self._model.noul(
                state=state,
                instructions=(
                    "Esta pergunta de formulário é sobre dados sensíveis EEO "
                    "(gênero, raça, etnia, deficiência, veterano ou orientação sexual)?"
                ),
                criteria={
                    "true": (
                        "Sim, a pergunta é explicitamente sobre dados sensíveis de diversidade "
                        "ou EEO (gênero, raça, etnia, deficiência, veterano, orientação sexual)"
                    ),
                    "false": (
                        "Não, a pergunta é sobre histórico profissional, empresa atual, ferramentas, "
                        "salário, localização, disponibilidade ou outros dados gerais de emprego"
                    ),
                },
            )
        )

        laya_action = None
        if laya_verdict is not None:
            laya_action = "SENSITIVE" if laya_verdict.band == "yes" else "STANDARD"

        # ASSIMETRIA DE SEGURANÇA: Se regra marcou como sensível, Laya NUNCA desmarca!
        source: DecisionSource
        if rule_sensitive:
            applied_action = "SENSITIVE"
            source = "rules"
        elif is_definitely_standard_question(question.label):
            applied_action = "STANDARD"
            source = "rules"
        elif laya_verdict is None:
            applied_action = rule_action
            source = "fallback"
        elif mode == "active":
            applied_action = "SENSITIVE" if laya_verdict.band == "yes" else "STANDARD"
            source = "laya"
        else:
            applied_action = rule_action
            source = "rules"

        probs = _noul_probs(laya_verdict)
        band = laya_verdict.band if laya_verdict else None
        await self._audit_record(
            dp, job_id, question.id, "noul", mode, probs, band, laya_action, applied_action, source, latency, state
        )
        return (applied_action == "SENSITIVE")

    # -------------------------------------------------------------------------
    # DP5: field_mapping
    # -------------------------------------------------------------------------
    async def decide_field_mapping(
        self,
        job_id: str,
        question: FormQuestion,
        profile: Profile,
    ) -> str | None:
        """DP5: Mapeia pergunta para atributo do perfil sem chamar o LLM quando confiante."""
        dp = "DP5"
        mode = self._effective_modes[dp]
        state = build_dp5_field_mapping_state(question, self._settings.LAYA_STATE_MAX_CHARS)

        # Chaves candidatas do perfil
        profile_facts = profile.model_dump(exclude={"eeo"})
        candidate_keys = [k for k in profile_facts if profile_facts[k] is not None]
        ranked_keys = rank_profile_keys(question.label, candidate_keys, top_k=7)
        ranked_keys.append("none")

        options = {k: f"Atributo {k}" if k != "none" else "Nenhum atributo corresponde" for k in ranked_keys}

        if mode == "off" or self._model is None:
            await self._audit_record(
                dp, job_id, question.id, "choice", mode, None, None, None, "none", "llm", 0.0, state
            )
            return None

        laya_verdict, latency = await self._timed(
            self._model.choice(
                state=state,
                instructions="Qual atributo do perfil do candidato responde exatamente a este campo de formulário?",
                options=options,
            )
        )

        laya_action = laya_verdict.top if laya_verdict else None
        qualifies = (
            laya_verdict is not None
            and laya_verdict.order_consistent
            and laya_verdict.top != "none"
            and laya_verdict.margin >= self._settings.LAYA_MIN_MARGIN
        )

        source: DecisionSource
        if mode == "active" and qualifies and laya_action in candidate_keys:
            applied_action = laya_action
            resolved_value = str(profile_facts[laya_action])
            source = "laya"
        else:
            applied_action = "none"
            resolved_value = None
            source = "fallback" if laya_verdict is None else "llm"

        probs = laya_verdict.probs if laya_verdict else None
        await self._audit_record(
            dp, job_id, question.id, "choice", mode, probs, None, laya_action, applied_action, source, latency, state
        )
        return resolved_value

    # -------------------------------------------------------------------------
    # DP6: ready_to_submit
    # -------------------------------------------------------------------------
    async def decide_ready_to_submit(
        self,
        job_id: str,
        questions: list[FormQuestion],
        filled: dict[str, Any],
        visible_errors: list[str],
        dry_run: bool,
    ) -> tuple[bool, str | None]:
        """DP6: Verificação final pré-submissão (ativa apenas se dry_run=False).

        Retorna (can_submit, reason).
        """
        dp = "DP6"
        mode = self._effective_modes[dp]
        state = build_dp6_ready_to_submit_state(
            questions, filled, visible_errors, self._settings.LAYA_STATE_MAX_CHARS
        )

        missing = missing_required(questions, filled)
        rule_ready = (len(missing) == 0) and (len(visible_errors) == 0)
        rule_action = "SUBMIT" if rule_ready else "NEEDS_REVIEW"

        if dry_run:
            # Em dry-run grava auditoria antes de retornar
            await self._audit_record(
                dp, job_id, None, "noul", mode, None, None, None, rule_action, "rules", 0.0, state
            )
            return False, "Simulação dry-run: envio não realizado"

        if mode == "off" or self._model is None:
            reason = None if rule_ready else f"Campos obrigatórios pendentes: {', '.join(missing)}"
            await self._audit_record(
                dp, job_id, None, "noul", mode, None, None, None, rule_action, "rules", 0.0, state
            )
            return rule_ready, reason

        laya_verdict, latency = await self._timed(
            self._model.noul(
                state=state,
                instructions="O formulário está integralmente preenchido sem erros para envio?",
            )
        )

        laya_action = None
        if laya_verdict is not None:
            laya_action = "SUBMIT" if laya_verdict.band == "yes" else "NEEDS_REVIEW"

        # ASSIMETRIA DE SEGURANÇA: Se faltar obrigatório, Laya NUNCA autoriza envio!
        source: DecisionSource
        if not rule_ready:
            applied_action = "NEEDS_REVIEW"
            reason = f"Campos obrigatórios ausentes ou erros visíveis: {missing or visible_errors}"
            source = "rules"
        elif laya_verdict is None:
            applied_action = rule_action
            reason = None
            source = "fallback"
        elif mode == "active":
            if laya_verdict.band == "yes":
                applied_action = "SUBMIT"
                reason = None
                source = "laya"
            else:
                applied_action = "NEEDS_REVIEW"
                reason = f"Laya não confirmou prontidão de envio (band={laya_verdict.band})"
                source = "laya"
        else:
            applied_action = rule_action
            reason = None
            source = "rules"

        probs = _noul_probs(laya_verdict)
        band = laya_verdict.band if laya_verdict else None
        await self._audit_record(
            dp, job_id, None, "noul", mode, probs, band, laya_action, applied_action, source, latency, state
        )
        return (applied_action == "SUBMIT"), reason
