"""Adaptador do sidecar Laya (httpx, lock, circuit breaker, /plan, /health).

Toda dependência do formato de comunicação do sidecar é concentrada aqui.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

import httpx

from config.settings import Settings
from core.decision.models import ChoiceVerdict, HealthInfo, NoulVerdict, PlanInfo
from core.models import DecisionUnavailable

log = logging.getLogger(__name__)


class DecisionModel(Protocol):
    """Protocolo abstrato da camada de decisão rápida."""

    async def noul(
        self,
        state: dict[str, str],
        instructions: str,
        criteria: dict[str, str] | None = None,
    ) -> NoulVerdict: ...

    async def choice(
        self,
        state: dict[str, str],
        instructions: str,
        options: dict[str, str],
    ) -> ChoiceVerdict: ...


class LayaSidecarClient:
    """Cliente HTTP com lock assíncrono, circuit breaker e mitigação de viés de ordem."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._settings = settings
        resolved_base = base_url or (settings.LAYA_URL if settings else "http://127.0.0.1:8787")
        self._client = client or httpx.AsyncClient(base_url=resolved_base.rstrip("/"))
        self._lock = asyncio.Lock()
        self._max_failures = settings.LAYA_MAX_FAILURES if settings else 3
        self._breaker_cooldown_s = settings.LAYA_BREAKER_COOLDOWN_S if settings else 60.0
        self._order_swap = settings.LAYA_ORDER_SWAP if settings else True
        self._yes_th = settings.LAYA_YES_THRESHOLD if settings else 0.8
        self._no_th = settings.LAYA_NO_THRESHOLD if settings else 0.2
        self._cpu_factor = settings.LAYA_CPU_TIMEOUT_FACTOR if settings else 5.0
        self._base_timeout = (
            timeout_s
            if timeout_s is not None
            else (settings.LAYA_TIMEOUT_S if settings else 5.0)
        )
        self._effective_timeout = self._base_timeout
        self._plan_above = settings.LAYA_PLAN_ABOVE_CHARS if settings else 600
        self._consecutive_failures = 0
        self._breaker_open = False
        self._opened_at: float | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def breaker_open(self) -> bool:
        return self._breaker_open

    def reset_breaker(self) -> None:
        self._consecutive_failures = 0
        self._breaker_open = False
        self._opened_at = None

    async def health(self) -> HealthInfo:
        """Verifica /health e detecta se o modelo foi rebaixado para CPU."""
        async with self._lock:
            try:
                resp = await self._client.get("/health", timeout=self._effective_timeout)
                if resp.status_code >= 400:
                    raise DecisionUnavailable(f"Health check falhou: HTTP {resp.status_code}")
                data = resp.json()
            except (httpx.TransportError, httpx.TimeoutException, ValueError) as exc:
                raise DecisionUnavailable(f"Sidecar Laya inacessível no health: {exc}") from exc

            degraded = bool(data.get("degraded", False))
            if degraded:
                self._effective_timeout = self._base_timeout * self._cpu_factor
                log.warning(
                    "Laya operando em modo CPU (degraded=True). Timeout ajustado para %.1fs",
                    self._effective_timeout,
                )
            return HealthInfo(
                ok=bool(data.get("ok", True)),
                demoted_to_cpu=degraded,
                raw=data,
            )

    async def plan(self, state: dict[str, str], questions: dict[str, Any]) -> PlanInfo:
        """Consulta /plan para estimar limites e truncamento sem inferência."""
        async with self._lock:
            payload = {"state": state, "questions": questions}
            try:
                resp = await self._client.post("/plan", json=payload, timeout=self._effective_timeout)
                if resp.status_code >= 400:
                    raise DecisionUnavailable(f"Plan falhou com HTTP {resp.status_code}")
                data = resp.json()
            except (httpx.TransportError, httpx.TimeoutException, ValueError) as exc:
                raise DecisionUnavailable(f"Erro ao consultar /plan: {exc}") from exc

            truncated_state = False
            truncated_options = False
            for q_plan in data.get("questions", []):
                if q_plan.get("would_truncate_state"):
                    truncated_state = True
                if q_plan.get("options_compressed"):
                    truncated_options = True

            if not data.get("fits", True):
                truncated_state = True

            return PlanInfo(
                truncated_state=truncated_state,
                truncated_options=truncated_options,
                raw=data,
            )

    async def _post_ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Executa POST /ask sob lock e atualiza o estado do circuit breaker."""
        async with self._lock:
            if self._breaker_open:
                now = time.monotonic()
                if self._opened_at is not None and (now - self._opened_at >= self._breaker_cooldown_s):
                    log.info("Laya circuit breaker em estado HALF-OPEN (testando recuperação)")
                else:
                    raise DecisionUnavailable("Laya circuit breaker está ABERTO devido a falhas prévias")

            try:
                resp = await self._client.post("/ask", json=payload, timeout=self._effective_timeout)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                self._record_failure()
                raise DecisionUnavailable(f"Falha de rede/timeout com sidecar Laya: {exc}") from exc

            if resp.status_code >= 500:
                self._record_failure()
                raise DecisionUnavailable(f"Laya retornou erro de servidor: HTTP {resp.status_code}")

            if resp.status_code >= 400:
                # 4xx é erro de formatação de pergunta (bug do harness), não aciona o circuit breaker
                log.error("Laya rejeitou a requisição (4xx): %s", resp.text[:300])
                raise DecisionUnavailable(f"Laya rejeitou a pergunta (HTTP {resp.status_code})")

            try:
                data = resp.json()
                if not data.get("ok", True):
                    self._record_failure()
                    raise DecisionUnavailable(f"Laya retornou ok=False: {data}")
            except (ValueError, TypeError) as exc:
                self._record_failure()
                raise DecisionUnavailable("Resposta do Laya não é um JSON válido") from exc

            # Sucesso zera o contador e fecha o breaker
            self.reset_breaker()
            return data

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._max_failures or self._breaker_open:
            self._breaker_open = True
            self._opened_at = time.monotonic()
            log.warning(
                "Laya circuit breaker ABERTO após %d falhas consecutivas!",
                self._consecutive_failures,
            )

    def _shorten_state(self, state: dict[str, str]) -> dict[str, str]:
        """Reduz o tamanho do estado mantendo as chaves prioritárias intactas."""
        shortened: dict[str, str] = {}
        for idx, (k, v) in enumerate(state.items()):
            if idx < 2:
                # primeiras chaves preservadas
                shortened[k] = v[:200]
            else:
                shortened[k] = v[:80]
        return shortened

    async def _ensure_state_fits(
        self, state: dict[str, str], questions: dict[str, Any]
    ) -> dict[str, str]:
        total_chars = sum(len(str(v)) for v in state.values())
        if total_chars <= self._plan_above:
            return state

        plan_info = await self.plan(state, questions)
        if not plan_info.truncated_state:
            return state

        # Encurta uma vez
        short_state = self._shorten_state(state)
        second_plan = await self.plan(short_state, questions)
        if second_plan.truncated_state:
            raise DecisionUnavailable("truncated")
        return short_state

    async def noul(
        self,
        state: dict[str, str],
        instructions: str,
        criteria: dict[str, str] | None = None,
    ) -> NoulVerdict:
        """Pergunta sim/não (noul) com critérios explícitos para evitar colapso de rótulos."""
        crit = criteria or {
            "true": "Sim, a condição é plenamente atendida",
            "false": "Não, a condição não é atendida",
        }
        questions_def = {
            "q_noul": {
                "type": "noul",
                "instructions": instructions,
                "criteria": crit,
            }
        }

        checked_state = await self._ensure_state_fits(state, questions_def)
        payload = {"state": checked_state, "questions": questions_def}
        data = await self._post_ask(payload)

        try:
            answers = data.get("answers", {})
            answer = answers.get("q_noul") or answers.get("q1") or (next(iter(answers.values())) if answers else {})
            # Prioriza valor numérico de 'noul' retornado
            raw_p = answer.get("noul")
            if raw_p is None:
                # Caso venha de noul_as_choice neutral labels
                probs = answer.get("probabilities", {})
                raw_p = probs.get("A", probs.get("true", 0.5))
            p_true = float(raw_p)
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            raise DecisionUnavailable(f"Formato inválido de resposta noul: {exc}") from exc

        # Recalcula faixa localmente a partir de p_true com limiares resolvidos
        if p_true >= self._yes_th:
            band = "yes"
        elif p_true <= self._no_th:
            band = "no"
        else:
            band = "uncertain"

        server_band = answer.get("band")
        truncated = bool(data.get("truncated"))
        return NoulVerdict(
            p_true=p_true,
            band=band,
            server_band=server_band,
            truncated=truncated,
        )

    @staticmethod
    def _parse_choice_answer(data: dict, options: dict) -> tuple[dict[str, float], str]:
        try:
            answers = data.get("answers")
            if not isinstance(answers, dict) or not answers:
                raise DecisionUnavailable("Resposta do Laya sem 'answers' válido para choice")
            ans = answers.get("q_choice") or next(iter(answers.values()))
            if not isinstance(ans, dict):
                raise DecisionUnavailable("Item de resposta inválido no Laya")
            raw_probs = ans.get("probabilities", {})
            if not isinstance(raw_probs, dict) or not raw_probs:
                raise DecisionUnavailable("Probabilidades ausentes ou vazias na resposta do Laya")
            probs = {str(k): float(v) for k, v in raw_probs.items()}
            if not probs:
                raise DecisionUnavailable("Probabilidades vazias após conversão")
            top = ans.get("choice") or max(probs, key=probs.get)  # type: ignore[arg-type]
            return probs, str(top)
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            raise DecisionUnavailable(f"Falha ao interpretar resposta choice do Laya: {exc}") from exc

    async def choice(
        self,
        state: dict[str, str],
        instructions: str,
        options: dict[str, str],
    ) -> ChoiceVerdict:
        """Múltipla escolha (choice) com mitigação de viés de ordem (duas passadas invertidas)."""
        if not options:
            raise DecisionUnavailable("Nenhuma opção fornecida para choice")

        if len(options) == 1:
            only_key = next(iter(options))
            return ChoiceVerdict(
                probs={only_key: 1.0},
                top=only_key,
                margin=1.0,
                order_consistent=True,
                truncated=False,
            )

        q1 = {"q_choice": {"type": "choice", "instructions": instructions, "criteria": options}}
        checked_state = await self._ensure_state_fits(state, q1)

        # Chamada 1: ordem original
        data1 = await self._post_ask({"state": checked_state, "questions": q1})
        probs1, top1 = self._parse_choice_answer(data1, options)

        if not self._order_swap:
            sorted_p = sorted(probs1.values(), reverse=True)
            margin = (sorted_p[0] - sorted_p[1]) if len(sorted_p) > 1 else (sorted_p[0] if sorted_p else 0.0)
            return ChoiceVerdict(
                probs=probs1,
                top=top1,
                margin=margin,
                order_consistent=True,
                truncated=bool(data1.get("truncated")),
            )

        # Chamada 2: ordem invertida das opções
        reversed_options = dict(reversed(list(options.items())))
        q2 = {
            "q_choice": {
                "type": "choice",
                "instructions": instructions,
                "criteria": reversed_options,
            }
        }
        data2 = await self._post_ask({"state": checked_state, "questions": q2})
        probs2, top2 = self._parse_choice_answer(data2, options)

        # Se o top diferir entre a ordem direta e invertida
        order_consistent = top1 == top2

        # Médias alinhadas por rótulo
        aligned_probs: dict[str, float] = {}
        for opt in options:
            p1 = probs1.get(opt, 0.0)
            p2 = probs2.get(opt, 0.0)
            aligned_probs[opt] = round((p1 + p2) / 2.0, 4)

        if not order_consistent:
            # Top discordou -> inconsistente, margem zero
            return ChoiceVerdict(
                probs=aligned_probs,
                top=max(aligned_probs, key=lambda k: aligned_probs.get(k, 0.0)),
                margin=0.0,
                order_consistent=False,
                truncated=bool(data1.get("truncated") or data2.get("truncated")),
            )

        sorted_p = sorted(aligned_probs.values(), reverse=True)
        margin = round(sorted_p[0] - sorted_p[1], 4) if len(sorted_p) > 1 else sorted_p[0]
        return ChoiceVerdict(
            probs=aligned_probs,
            top=top1,
            margin=margin,
            order_consistent=True,
            truncated=bool(data1.get("truncated") or data2.get("truncated")),
        )

    async def is_available(self) -> bool:
        """Verifica se o cliente consegue se comunicar com o sidecar."""
        if self._breaker_open:
            return False
        try:
            info = await self.health()
            return info.ok
        except Exception:
            return False

    async def warmup(self) -> bool:
        """Aquecimento inicial: chama health() e pré-carrega o modelo. Retorna True se ok."""
        try:
            info = await self.health()
            if not info.ok:
                log.warning("Laya health reportou ok=False")
                return False

            # Forward pass barato de aquecimento
            await self.noul(
                state={"system": "warmup"},
                instructions="Responda se o sistema está inicializado.",
            )
            log.info("Laya sidecar aquecido com sucesso.")
            return True
        except DecisionUnavailable as exc:
            log.warning("Warmup do Laya falhou (seguirá com fallback se necessário): %s", exc)
            return False
