"""Testes unitários para o LayaSidecarClient com respx simulando o sidecar."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from config.settings import Settings
from core.decision.laya_client import LayaSidecarClient
from core.models import DecisionUnavailable


def _fixture(name: str) -> dict:
    p = Path(f"tests/fixtures/laya/{name}.json")
    return json.loads(p.read_text(encoding="utf-8"))


@respx.mock
@pytest.mark.asyncio
async def test_noul_recalculated_band_and_ignores_confidence(test_settings: Settings) -> None:
    # Settings default: LAYA_YES_THRESHOLD = 0.8, LAYA_NO_THRESHOLD = 0.2
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        # 1. p_true = 0.85 -> yes
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(200, json=_fixture("ask_noul"))
        )
        v1 = await sidecar.noul(state={"k": "v"}, instructions="test")
        assert v1.p_true == 0.85
        assert v1.band == "yes"

        # 2. p_true = 0.50 e confidence alta do servidor (0.95) -> deve ser uncertain!
        # Pois o harness ignora a confidence e usa p_true.
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "answers": {
                        "q_noul": {
                            "noul": 0.50,
                            "confidence": 0.95,  # alta no servidor, mas p_true é 0.50
                            "band": "yes",       # servidor diz yes, mas harness recalcula
                        }
                    },
                },
            )
        )
        v2 = await sidecar.noul(state={"k": "v"}, instructions="test")
        assert v2.p_true == 0.50
        assert v2.band == "uncertain"
        assert v2.server_band == "yes"

        # 3. Limite exato NO (0.2)
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "answers": {"q_noul": {"noul": 0.20}}},
            )
        )
        v3 = await sidecar.noul(state={"k": "v"}, instructions="test")
        assert v3.band == "no"

        # 4. Limite exato YES (0.8)
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "answers": {"q_noul": {"noul": 0.80}}},
            )
        )
        v4 = await sidecar.noul(state={"k": "v"}, instructions="test")
        assert v4.band == "yes"


@respx.mock
@pytest.mark.asyncio
async def test_choice_order_swap_consistent(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        route = respx.post("http://127.0.0.1:8787/ask").mock(
            side_effect=[
                httpx.Response(200, json=_fixture("ask_choice_1")),
                httpx.Response(200, json=_fixture("ask_choice_2")),
            ]
        )

        options = {"opt_a": "Opção A", "opt_b": "Opção B"}
        verdict = await sidecar.choice(state={"job": "dev"}, instructions="choose", options=options)

        assert route.call_count == 2
        # Chamada 2 deve ter opções invertidas
        body2 = json.loads(route.calls[1].request.content)
        opts_sent2 = list(body2["questions"]["q_choice"]["criteria"].keys())
        assert opts_sent2 == ["opt_b", "opt_a"]

        assert verdict.order_consistent is True
        assert verdict.top == "opt_a"
        # Média de opt_a: (0.70 + 0.68) / 2 = 0.69; opt_b: (0.30 + 0.32) / 2 = 0.31
        assert verdict.probs["opt_a"] == 0.69
        assert verdict.probs["opt_b"] == 0.31
        assert verdict.margin == pytest.approx(0.38, 0.01)


@respx.mock
@pytest.mark.asyncio
async def test_choice_order_swap_divergent(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        # Na 1ª ordem escolhe opt_a, na 2ª escolhe opt_b devido a viés de posição
        respx.post("http://127.0.0.1:8787/ask").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "answers": {"q_choice": {"choice": "opt_a", "probabilities": {"opt_a": 0.6, "opt_b": 0.4}}},
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "answers": {"q_choice": {"choice": "opt_b", "probabilities": {"opt_b": 0.7, "opt_a": 0.3}}},
                    },
                ),
            ]
        )

        options = {"opt_a": "A", "opt_b": "B"}
        verdict = await sidecar.choice(state={"x": "y"}, instructions="choose", options=options)

        assert verdict.order_consistent is False
        assert verdict.margin == 0.0


@respx.mock
@pytest.mark.asyncio
async def test_long_state_plan_and_persistent_truncation(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        # /plan sempre retorna truncado mesmo após encurtar
        respx.post("http://127.0.0.1:8787/plan").mock(
            return_value=httpx.Response(200, json=_fixture("plan_truncated"))
        )

        long_state = {"desc": "X" * 1200}
        with pytest.raises(DecisionUnavailable, match="truncated"):
            await sidecar.noul(state=long_state, instructions="test")


@respx.mock
@pytest.mark.asyncio
async def test_health_demoted_to_cpu_increases_timeout(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        respx.get("http://127.0.0.1:8787/health").mock(
            return_value=httpx.Response(200, json=_fixture("health_degraded"))
        )

        info = await sidecar.health()
        assert info.demoted_to_cpu is True
        # Timeout deve ser multiplicado por LAYA_CPU_TIMEOUT_FACTOR (5.0 * 5 = 25.0)
        assert sidecar._effective_timeout == test_settings.LAYA_TIMEOUT_S * test_settings.LAYA_CPU_TIMEOUT_FACTOR


@respx.mock
@pytest.mark.asyncio
async def test_circuit_breaker_behavior(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        # 4xx não aciona circuit breaker
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(400, text="Invalid question")
        )
        with pytest.raises(DecisionUnavailable):
            await sidecar.noul(state={"k": "v"}, instructions="test")
        assert sidecar.breaker_open is False
        assert sidecar._consecutive_failures == 0

        # Falhas 5xx incrementam contador
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        for _ in range(test_settings.LAYA_MAX_FAILURES):
            with pytest.raises(DecisionUnavailable):
                await sidecar.noul(state={"k": "v"}, instructions="test")

        # Breaker abriu após LAYA_MAX_FAILURES falhas consecutivas
        assert sidecar.breaker_open is True

        # Nova chamada deve falhar de imediato SEM fazer requisição HTTP
        with pytest.raises(DecisionUnavailable, match="circuit breaker está ABERTO"):
            await sidecar.noul(state={"k": "v"}, instructions="test")

        # Reset ou sucesso zera
        sidecar.reset_breaker()
        assert sidecar.breaker_open is False


@respx.mock
@pytest.mark.asyncio
async def test_concurrent_calls_are_serialized(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        active_calls = 0
        peak_active = 0

        async def slow_response(request: httpx.Request) -> httpx.Response:
            nonlocal active_calls, peak_active
            active_calls += 1
            peak_active = max(peak_active, active_calls)
            await asyncio.sleep(0.05)
            active_calls -= 1
            return httpx.Response(200, json=_fixture("ask_noul"))

        respx.post("http://127.0.0.1:8787/ask").mock(side_effect=slow_response)

        # Lança 5 chamadas simultâneas
        tasks = [sidecar.noul(state={"idx": str(i)}, instructions="ping") for i in range(5)]
        await asyncio.gather(*tasks)

        # O pico de chamadas simultâneas ao sidecar deve ser rigorosamente 1
        assert peak_active == 1


@respx.mock
@pytest.mark.asyncio
async def test_choice_empty_answers_raises_decision_unavailable(test_settings: Settings) -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(200, json={"ok": True, "answers": {}})
        )
        options = {"opt_a": "A", "opt_b": "B"}
        with pytest.raises(DecisionUnavailable):
            await sidecar.choice(state={"k": "v"}, instructions="choose", options=options)


@respx.mock
@pytest.mark.asyncio
async def test_laya_client_with_settings_none() -> None:
    """BUG-10: LayaSidecarClient não deve quebrar quando instanciado com settings=None."""
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(settings=None, client=client)

        # Health
        respx.get("http://127.0.0.1:8787/health").mock(
            return_value=httpx.Response(200, json={"ok": True, "degraded": True})
        )
        h = await sidecar.health()
        assert h.ok is True
        assert h.demoted_to_cpu is True

        # Noul
        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(200, json=_fixture("ask_noul"))
        )
        v = await sidecar.noul(state={"k": "v"}, instructions="test")
        assert v.band == "yes"

        # Choice
        respx.post("http://127.0.0.1:8787/ask").mock(
            side_effect=[
                httpx.Response(200, json=_fixture("ask_choice_1")),
                httpx.Response(200, json=_fixture("ask_choice_2")),
            ]
        )
        c = await sidecar.choice(state={"job": "dev"}, instructions="choose", options={"opt_a": "A", "opt_b": "B"})
        assert c.top == "opt_a"


@respx.mock
@pytest.mark.asyncio
async def test_circuit_breaker_half_open(test_settings: Settings) -> None:
    """BUG-11: Circuit breaker deve permitir requisição de teste após cooldown (half-open)."""
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        route = respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        for _ in range(sidecar._max_failures):
            with pytest.raises(DecisionUnavailable):
                await sidecar.noul(state={"k": "v"}, instructions="test")

        assert sidecar.breaker_open is True
        initial_calls = route.call_count

        # Ainda em cooldown: chamada imediata é rejeitada sem nova requisição HTTP
        with pytest.raises(DecisionUnavailable, match="circuit breaker está ABERTO"):
            await sidecar.noul(state={"k": "v"}, instructions="test")
        assert route.call_count == initial_calls

        # Simula expiração do cooldown (half-open)
        assert sidecar._opened_at is not None
        sidecar._opened_at -= sidecar._breaker_cooldown_s + 5.0

        # Resposta de sucesso na tentativa half-open fecha o breaker
        route.mock(return_value=httpx.Response(200, json=_fixture("ask_noul")))
        v = await sidecar.noul(state={"k": "v"}, instructions="test")
        assert v.band == "yes"
        assert sidecar.breaker_open is False
        assert sidecar._opened_at is None
        assert sidecar._consecutive_failures == 0


@respx.mock
@respx.mock
@pytest.mark.asyncio
async def test_choice_malformed_answers_raises_decision_unavailable(test_settings: Settings) -> None:
    """BUG-9: Resposta sem answers válidos ou com payload malformado lança DecisionUnavailable."""
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8787") as client:
        sidecar = LayaSidecarClient(test_settings, client=client)

        respx.post("http://127.0.0.1:8787/ask").mock(
            return_value=httpx.Response(200, json={"ok": True, "answers": {}})
        )

        options = {"opt_a": "Opção A", "opt_b": "Opção B"}
        with pytest.raises(DecisionUnavailable, match="sem 'answers' válido"):
            await sidecar.choice(state={"job": "dev"}, instructions="choose", options=options)



