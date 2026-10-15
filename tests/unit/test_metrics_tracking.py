"""Testes unitários para medição de tokens e duração por vaga."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from config.settings import Settings
from core.matcher import Matcher
from core.models import ApplicationResult, Job, Profile, TaskStatus
from core.pipeline import Pipeline


@pytest.mark.asyncio
async def test_matcher_token_tracking(test_settings: Settings):
    """Testa se o Matcher acumula tokens de prompt e completion das respostas do LLM."""
    matcher = Matcher(settings=test_settings)

    mock_llm_response = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"answers": {"q1": "resposta"}}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        },
    }

    with respx.mock(base_url="http://test.llm.local/v1") as respx_mock:
        respx_mock.post("/chat/completions").respond(
            status_code=200,
            json=mock_llm_response,
        )

        # Dispara uma chamada
        res_text = await matcher._post([{"role": "user", "content": "olá"}])
        assert res_text is not None

        # Confere tokens acumulados
        job_tok = matcher.get_and_reset_job_tokens()
        assert job_tok["prompt_tokens"] == 120
        assert job_tok["completion_tokens"] == 30
        assert job_tok["total_tokens"] == 150

        # Após o reset, o contador do job deve estar zerado
        job_tok_reset = matcher.get_and_reset_job_tokens()
        assert job_tok_reset["total_tokens"] == 0

        # Mas o total acumulado global deve persistir
        total_tok = matcher.get_total_tokens()
        assert total_tok["total_tokens"] == 150


@pytest.mark.asyncio
async def test_pipeline_records_duration_and_tokens():
    """Testa se o Pipeline anexa duration_s e tokens_used no ApplicationResult."""
    mock_settings = MagicMock(spec=Settings)
    mock_settings.DRY_RUN = True
    mock_settings.MIN_FIT_SCORE = 70
    mock_settings.AUTONOMOUS_MODE = True

    mock_decision = MagicMock()
    # Triagem aceita
    mock_decision.decide_job_fit = AsyncMock(return_value=("PROCEED", "Fit alto"))

    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(return_value=None)
    mock_resolver.get_base_resumes = MagicMock(return_value={})
    mock_resolver.get_resumes = MagicMock(return_value={})

    mock_matcher = MagicMock()
    mock_matcher.get_and_reset_job_tokens = MagicMock(
        return_value={"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125}
    )

    mock_browser = MagicMock()

    pipeline = Pipeline(
        settings=mock_settings,
        decision_layer=mock_decision,
        resume_resolver=mock_resolver,
        browser_manager=mock_browser,
        matcher=mock_matcher,
    )

    job = Job(id="job_test_duration", row_index=1, company="Empresa Teste", title="Engenheiro IA")
    profile = Profile(
        full_name="Candidato Teste",
        email="teste@email.com",
        phone="11999999999",
        location="SP",
    )

    result = await pipeline.process_job(job=job, profile=profile)

    assert result is not None
    assert result.duration_s is not None
    assert result.duration_s >= 0.0
    assert result.tokens_used == 125
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 25
    assert result.started_at is not None
    assert result.completed_at is not None
