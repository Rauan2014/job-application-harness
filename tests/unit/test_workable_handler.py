"""Testes unitários para o WorkableHandler (apply.workable.com)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation.handlers.workable import WorkableHandler
from core.models import (
    AnswerSet,
    BlockerDetected,
    Job,
    Profile,
    ResumeInfo,
    TaskStatus,
)


def _make_mock_locator(
    count: int = 0,
    is_visible: bool = True,
    is_enabled: bool = True,
    text: str = "",
    attrs: dict[str, str] | None = None,
) -> MagicMock:
    attrs = attrs or {}
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    loc.is_visible = AsyncMock(return_value=is_visible)
    loc.is_enabled = AsyncMock(return_value=is_enabled)
    loc.evaluate = AsyncMock(return_value="input")
    loc.input_value = AsyncMock(return_value=text or "filled")
    loc.inner_text = AsyncMock(return_value=text)
    loc.all_inner_texts = AsyncMock(return_value=[text] if text else [])
    loc.get_attribute = AsyncMock(side_effect=lambda a: attrs.get(a, None))
    loc.fill = AsyncMock()
    loc.click = AsyncMock()
    loc.set_input_files = AsyncMock()
    loc.select_option = AsyncMock()
    loc.wait_for = AsyncMock()
    loc.first = loc
    loc.last = loc
    loc.nth = MagicMock(return_value=loc)
    loc.locator = MagicMock(return_value=loc)
    return loc


def _make_job(url: str = "https://apply.workable.com/acme/j/123", ats: str | None = "workable") -> Job:
    return Job(
        id="workable-job-1",
        row_index=1,
        company="Acme Corp",
        title="Full Stack Engineer",
        ats=ats,  # type: ignore[arg-type]
        url=url,
    )


def test_workable_can_handle() -> None:
    handler = WorkableHandler()
    assert handler.can_handle(_make_job("https://apply.workable.com/acme/j/123", ats=None)) is True
    assert handler.can_handle(_make_job("https://jobs.workable.com/view/456", ats=None)) is True
    assert handler.can_handle(_make_job("https://example.com/apply", ats="workable")) is True

    # Outras ATSs
    assert handler.can_handle(_make_job("https://jobs.lever.co/acme/123", ats=None)) is False
    assert handler.can_handle(_make_job("https://boards.greenhouse.io/acme/123", ats=None)) is False

    # URLs inválidas
    assert handler.can_handle(_make_job("not-a-url", ats="workable")) is False
    assert handler.can_handle(_make_job("", ats="workable")) is False


@pytest.mark.asyncio
async def test_workable_check_blockers_captcha() -> None:
    handler = WorkableHandler()
    page = MagicMock()

    captcha_elem = _make_mock_locator(count=1, is_visible=True)

    def locator_side_effect(sel: str):
        if sel == ".cf-turnstile":
            return captcha_elem
        return _make_mock_locator(count=0)

    page.locator = MagicMock(side_effect=locator_side_effect)

    with pytest.raises(BlockerDetected) as exc_info:
        await handler._check_blockers(page)
    assert "CAPTCHA detectado" in str(exc_info.value)


@pytest.mark.asyncio
async def test_workable_inspect_empty_url_raises_value_error() -> None:
    handler = WorkableHandler()
    page = MagicMock()
    job = _make_job(url="")

    with pytest.raises(ValueError) as exc_info:
        await handler.inspect(page, job)
    assert "não possui URL" in str(exc_info.value)


@pytest.mark.asyncio
async def test_workable_fill_and_submit_dry_run() -> None:
    handler = WorkableHandler()
    job = _make_job()
    profile = Profile(
        full_name="Carlos Drummond",
        email="carlos@example.com",
        phone="+55 31 99999-8888",
        location="Belo Horizonte, MG",
        headline="Senior AI Engineer",
    )
    resume = ResumeInfo(path=Path("resume.pdf"), summary="Senior Dev", text="Python")
    answers = AnswerSet(answers={})

    page = MagicMock()
    page.url = job.url
    page.evaluate = AsyncMock()

    file_loc = _make_mock_locator(count=1, attrs={"accept": ".pdf"})

    def locator_side_effect(sel: str):
        if sel == "input[type='file']":
            return file_loc
        if "input[required]" in sel or "select[required]" in sel:
            return _make_mock_locator(count=0)
        return _make_mock_locator(count=0)

    page.locator = MagicMock(side_effect=locator_side_effect)

    result = await handler.fill_and_submit(
        page=page,
        job=job,
        profile=profile,
        resume=resume,
        answers=answers,
        dry_run=True,
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.dry_run is True
    assert result.submitted is False
    assert "Dry run concluído" in (result.reason or "")


@pytest.mark.asyncio
async def test_workable_fill_and_submit_empty_url_returns_failed() -> None:
    handler = WorkableHandler()
    job = _make_job(url="")
    profile = Profile(
        full_name="Carlos Drummond",
        email="carlos@example.com",
        phone="+55 31 99999-8888",
        location="Belo Horizonte, MG",
    )
    resume = ResumeInfo(path=Path("resume.pdf"), summary="", text="")

    page = MagicMock()
    result = await handler.fill_and_submit(
        page=page,
        job=job,
        profile=profile,
        resume=resume,
        answers=AnswerSet(),
        dry_run=True,
    )
    assert result.status == TaskStatus.FAILED
    assert "URL da vaga vazia" in (result.reason or "")
