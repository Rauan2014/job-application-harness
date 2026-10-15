"""Testes unitários para a finalização dos handlers (SAFE-3, SAFE-6)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation.handlers.lever import LeverHandler
from core.models import Job, ResumeInfo, TaskStatus


@pytest.mark.asyncio
async def test_finalize_missing_submit_button_returns_needs_review_not_submitted() -> None:
    """SAFE-3: Quando o botão de submit não é encontrado, retorna NEEDS_REVIEW com submitted=False."""
    handler = LeverHandler()
    job = Job(id="job-1", row_index=1, company="Acme", title="Dev", url="https://jobs.lever.co/acme/1")
    resume = ResumeInfo(path=Path("dummy.pdf"), summary="Summary", text="Text")

    page = MagicMock()
    page.url = job.url

    # Botão de submit ausente (count = 0)
    def locator_side_effect(selector: str):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        loc.first = loc
        return loc

    page.locator = MagicMock(side_effect=locator_side_effect)

    result = await handler._finalize(
        page=page,
        job=job,
        resume=resume,
        dry_run=False,
        ready_check=None,
        submit_selector=handler.selectors.SUBMIT_BTN,
        confirmation_selector=handler.selectors.CONFIRMATION,
    )

    assert result.status == TaskStatus.NEEDS_REVIEW
    assert result.submitted is False
    assert "Botão de submit não encontrado" in (result.reason or "")


@pytest.mark.asyncio
async def test_find_empty_required_catches_unchecked_checkbox() -> None:
    """SAFE-6: Checkbox obrigatório não marcado é detectado como vazio."""
    handler = LeverHandler()

    page = MagicMock()
    cb_elem = MagicMock()
    cb_elem.is_visible = AsyncMock(return_value=True)
    cb_elem.is_enabled = AsyncMock(return_value=True)
    cb_elem.evaluate = AsyncMock(return_value="input")
    cb_elem.get_attribute = AsyncMock(
        side_effect=lambda attr: "checkbox" if attr == "type" else ("terms_agree" if attr == "name" else None)
    )
    cb_elem.is_checked = AsyncMock(return_value=False)

    req_loc = MagicMock()
    req_loc.count = AsyncMock(return_value=1)
    req_loc.nth = MagicMock(return_value=cb_elem)
    page.locator = MagicMock(return_value=req_loc)

    empty_field = await handler._find_empty_required(page)
    assert empty_field == "terms_agree"


@pytest.mark.asyncio
async def test_finalize_live_submission_confirms_when_no_validation_errors() -> None:
    """Garante que _finalize em modo real (dry_run=False) confirma o envio quando não há erros de validação."""
    handler = LeverHandler()
    job = Job(id="job-live-1", row_index=1, company="Kake", title="AI Dev", url="https://www.kake.co/jobs/1")
    resume = ResumeInfo(path=Path("dummy.pdf"), summary="Summary", text="Text")

    page = MagicMock()
    page.url = job.url
    page.wait_for_selector = AsyncMock(side_effect=TimeoutError("Hidden selector timeout"))
    page.wait_for_timeout = AsyncMock()
    page.inner_text = AsyncMock(return_value="Formulário enviado")

    btn_elem = MagicMock()
    btn_elem.is_visible = AsyncMock(return_value=True)
    btn_elem.scroll_into_view_if_needed = AsyncMock()
    btn_elem.click = AsyncMock()

    def locator_side_effect(selector: str):
        loc = MagicMock()
        if selector == handler.selectors.SUBMIT_BTN:
            loc.count = AsyncMock(return_value=1)
            loc.nth = MagicMock(return_value=btn_elem)
            loc.last = btn_elem
            loc.first = btn_elem
        else:
            loc.count = AsyncMock(return_value=0)
            loc.first = loc
        return loc

    page.locator = MagicMock(side_effect=locator_side_effect)

    result = await handler._finalize(
        page=page,
        job=job,
        resume=resume,
        dry_run=False,
        ready_check=None,
        submit_selector=handler.selectors.SUBMIT_BTN,
        confirmation_selector=handler.selectors.CONFIRMATION,
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.submitted is True
    assert result.dry_run is False

