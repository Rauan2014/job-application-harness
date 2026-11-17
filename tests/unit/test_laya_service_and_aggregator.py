"""Testes para o serviço de auto-start do Laya e melhorias no AggregatorHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Page

from automation.handlers.aggregator import AggregatorHandler
from core.decision.laya_service import ensure_laya_running, is_laya_healthy
from core.models import BlockerDetected, Job


@pytest.mark.asyncio
async def test_is_laya_healthy_false_when_unreachable() -> None:
    res = await is_laya_healthy("http://127.0.0.1:59999", timeout_s=0.2)
    assert res is False


@pytest.mark.asyncio
async def test_ensure_laya_running_no_autostart_returns_false() -> None:
    is_up, proc = await ensure_laya_running("http://127.0.0.1:59999", auto_start=False)
    assert is_up is False
    assert proc is None


@pytest.mark.asyncio
async def test_aggregator_detects_listing_page_and_raises_informative_blocker() -> None:
    handler = AggregatorHandler()
    job = Job(
        id="job_search_1",
        row_index=2,
        title="Engenheiro de IA",
        company="TechCorp",
        url="https://br.indeed.com/q-engenheiro-de-ia-vagas.html",
        ats=None,
    )

    page = MagicMock(spec=Page)
    page.url = "https://br.indeed.com/q-engenheiro-de-ia-vagas.html"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.title = AsyncMock(return_value="Busca de Vagas")

    # Simula que nenhum card específico foi encontrado na listagem
    with (
        patch.object(handler, "_check_blockers", new=AsyncMock()),
        patch.object(handler, "_find_job_card_link", new=AsyncMock(return_value=None)),
        pytest.raises(BlockerDetected) as exc_info,
    ):
        await handler.inspect(page, job)
    assert "busca/listagem" in str(exc_info.value)
    assert "TechCorp" in str(exc_info.value)


@pytest.mark.asyncio
async def test_aggregator_delegates_smartapply() -> None:
    handler = AggregatorHandler()
    job = Job(
        id="job_smartapply",
        row_index=3,
        title="Engenheiro de IA",
        company="SmartCorp",
        url="https://smartapply.indeed.com/beta/indeedapply/form",
        ats=None,
    )

    page = MagicMock(spec=Page)
    page.url = "https://smartapply.indeed.com/beta/indeedapply/form"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.title = AsyncMock(return_value="Indeed Apply")

    with (
        patch.object(handler, "_check_blockers", new=AsyncMock()),
        patch.object(handler._indeed, "inspect", new_callable=AsyncMock) as mock_ind,
    ):
        mock_ind.return_value = MagicMock(description="Indeed SmartApply")
        res = await handler.inspect(page, job)
        assert res.description == "Indeed SmartApply"
        assert job.id in handler._delegated
        assert handler._delegated[job.id][0] == handler._indeed


@pytest.mark.asyncio
async def test_fill_first_visible_skips_hidden_elements() -> None:
    handler = AggregatorHandler()
    page = MagicMock(spec=Page)

    loc = MagicMock()
    loc.count = AsyncMock(return_value=2)

    # Elemento 0 oculto
    el0 = MagicMock()
    el0.is_visible = AsyncMock(return_value=False)
    el0.is_enabled = AsyncMock(return_value=True)

    # Elemento 1 visível
    el1 = MagicMock()
    el1.is_visible = AsyncMock(return_value=True)
    el1.is_enabled = AsyncMock(return_value=True)
    el1.fill = AsyncMock()

    loc.nth.side_effect = [el0, el1]
    page.locator.return_value = loc

    res = await handler._fill_first_visible(page, "input[name='test']", "hello")
    assert res is True
    el0.fill.assert_not_called()
    el1.fill.assert_called_once_with("hello", timeout=2000)
