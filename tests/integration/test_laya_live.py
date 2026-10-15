"""Teste de fumaça contra uma instância viva do laya-mcp (executado apenas com -m laya_live)."""

from __future__ import annotations

import httpx
import pytest

from core.decision.laya_client import LayaSidecarClient

pytestmark = [pytest.mark.integration, pytest.mark.laya_live]


@pytest.mark.asyncio
async def test_laya_live_sidecar_smoke() -> None:
    """Valida comunicação ponta a ponta com o servidor laya-mcp se estiver ativo."""
    base_url = "http://127.0.0.1:8787"

    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            resp = await client.get(f"{base_url}/health")
            if resp.status_code != 200:
                pytest.skip(f"laya-mcp retornou status {resp.status_code}")
        except Exception as exc:
            pytest.skip(f"laya-mcp não está respondendo em {base_url}: {exc}")

    laya = LayaSidecarClient(base_url=base_url, timeout_s=5.0)

    # 1. Health check
    health = await laya.health()
    assert health.status == "healthy" or health.status == "ready"

    # 2. Pergunta Noul
    verdict_noul = await laya.ask_noul(
        question="O candidato possui experiência com Python e microsserviços?",
        state={"candidato": "Senior Python Engineer com 8 anos de experiência em APIs"},
        criteria={"true": "experiência comprovada em python", "false": "nenhuma menção a python"},
    )
    assert verdict_noul.p_true >= 0.0 and verdict_noul.p_true <= 1.0
    assert verdict_noul.band in {"yes", "no", "uncertain"}

    # 3. Pergunta Choice
    verdict_choice = await laya.ask_choice(
        question="Qual formato de trabalho é requerido?",
        options=["remoto", "presencial", "híbrido"],
        state={"vaga": "Vaga 100% remota com contratação PJ"},
    )
    assert verdict_choice.selected in ["remoto", "presencial", "híbrido"]
