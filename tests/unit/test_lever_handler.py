"""Testes unitários para o LeverHandler (jobs.lever.co)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation.handlers.lever import LeverHandler
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
    loc.first = loc
    loc.last = loc
    loc.nth = MagicMock(return_value=loc)
    loc.locator = MagicMock(return_value=loc)
    return loc


def _make_job(url: str = "https://jobs.lever.co/acme/job-123", ats: str | None = "lever") -> Job:
    return Job(
        id="lever-job-1",
        row_index=1,
        company="Acme Corp",
        title="Senior Python Engineer",
        ats=ats,  # type: ignore[arg-type]
        url=url,
    )


def test_lever_can_handle() -> None:
    handler = LeverHandler()
    assert handler.can_handle(_make_job("https://jobs.lever.co/acme/123", ats=None)) is True
    assert handler.can_handle(_make_job("https://lever.co/apply/456", ats=None)) is True
    assert handler.can_handle(_make_job("https://example.com/apply", ats="lever")) is True

    # Outras plataformas
    assert handler.can_handle(_make_job("https://boards.greenhouse.io/acme/123", ats=None)) is False
    assert handler.can_handle(_make_job("https://apply.workable.com/acme/123", ats=None)) is False

    # URLs inválidas
    assert handler.can_handle(_make_job("javascript:alert(1)", ats="lever")) is False
    assert handler.can_handle(_make_job("", ats="lever")) is False


@pytest.mark.asyncio
async def test_lever_check_blockers_captcha() -> None:
    handler = LeverHandler()
    page = MagicMock()

    captcha_elem = _make_mock_locator(count=1, is_visible=True)
    captcha_elem.locator = MagicMock(return_value=_make_mock_locator(count=0))

    def locator_side_effect(sel: str):
        if sel == ".g-recaptcha":
            return captcha_elem
        return _make_mock_locator(count=0)

    page.locator = MagicMock(side_effect=locator_side_effect)

    with pytest.raises(BlockerDetected) as exc_info:
        await handler._check_blockers(page)
    assert "CAPTCHA detectado" in str(exc_info.value)


@pytest.mark.asyncio
async def test_lever_inspect_empty_url_raises_value_error() -> None:
    handler = LeverHandler()
    page = MagicMock()
    job = _make_job(url="")

    with pytest.raises(ValueError) as exc_info:
        await handler.inspect(page, job)
    assert "não possui URL" in str(exc_info.value)


@pytest.mark.asyncio
async def test_lever_inspect_extracts_description_and_questions() -> None:
    handler = LeverHandler()
    job = _make_job("https://jobs.lever.co/acme/123")

    page = MagicMock()
    page.url = job.url
    page.goto = AsyncMock()

    desc_loc = _make_mock_locator(count=1, text="Descrição detalhada da vaga de Python.")
    card_loc = _make_mock_locator(count=1, text="Years of experience with Python? *", attrs={"name": "years_python"})

    def page_locator(sel: str):
        if sel in (".posting-description", ".section.page-centered", ".posting-headline"):
            return desc_loc
        if sel == handler.selectors.CUSTOM_CARDS:
            return card_loc
        return _make_mock_locator(count=0)

    page.locator = MagicMock(side_effect=page_locator)

    info = await handler.inspect(page, job)
    assert "Descrição detalhada" in info.description
    assert len(info.questions) == 1
    assert "Years of experience" in info.questions[0].label


@pytest.mark.asyncio
async def test_lever_fill_and_submit_dry_run() -> None:
    handler = LeverHandler()
    job = _make_job()
    profile = Profile(
        full_name="Carlos Drummond",
        email="carlos@example.com",
        phone="+55 31 99999-8888",
        location="Belo Horizonte, MG",
        links={"linkedin": "https://linkedin.com/in/carlos", "github": "https://github.com/carlos"},
    )
    resume = ResumeInfo(path=Path("resume.pdf"), summary="Senior Dev", text="Python")
    answers = AnswerSet(answers={"years_python": "10"})

    page = MagicMock()
    page.url = job.url

    file_loc = _make_mock_locator(count=1, is_visible=True)

    def locator_side_effect(sel: str):
        if sel == handler.selectors.RESUME:
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
async def test_lever_fill_and_submit_missing_resume_returns_needs_review() -> None:
    handler = LeverHandler()
    job = _make_job()
    profile = Profile(
        full_name="Carlos Drummond",
        email="carlos@example.com",
        phone="+55 31 99999-8888",
        location="Belo Horizonte, MG",
    )
    resume = ResumeInfo(path=Path("resume.pdf"), summary="Senior Dev", text="Python")

    page = MagicMock()
    page.url = job.url
    page.locator = MagicMock(return_value=_make_mock_locator(count=0))

    result = await handler.fill_and_submit(
        page=page,
        job=job,
        profile=profile,
        resume=resume,
        answers=AnswerSet(),
        dry_run=True,
    )

    assert result.status == TaskStatus.NEEDS_REVIEW
    assert result.submitted is False
    assert "currículo não encontrado" in (result.reason or "")
