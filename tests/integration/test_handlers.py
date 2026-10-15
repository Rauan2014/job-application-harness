"""Testes de integração para os handlers de automação Lever e Greenhouse."""

from __future__ import annotations

import pytest

from automation.browser import BrowserManager
from automation.handlers.greenhouse import GreenhouseHandler
from automation.handlers.lever import LeverHandler
from core.models import (
    AnswerSet,
    BlockerDetected,
    Job,
    PostSubmitError,
    Profile,
    ResumeInfo,
    TaskStatus,
)
from tests.integration.conftest import MockServerInfo

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------- Lever Tests

async def test_lever_inspect(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    handler = LeverHandler()
    job = Job(
        id="lev_1", row_index=1, company="LeverCorp", title="Senior Python", url=mock_server.lever_url, ats="lever"
    )

    async with browser_manager.new_page() as page:
        info = await handler.inspect(page, job)
        assert "Senior Python Engineer" in info.description or "distributed systems" in info.description
        assert len(info.questions) >= 2
        labels = [q.label for q in info.questions]
        assert any("Years of experience" in lbl for lbl in labels)
        assert any("Work authorization" in lbl for lbl in labels)


async def test_lever_inspect_captcha_blocker(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    handler = LeverHandler()
    job = Job(
        id="lev_captcha", row_index=2, company="LeverCorp", title="Engineer",
        url=f"{mock_server.lever_url}?captcha=1", ats="lever"
    )

    async with browser_manager.new_page() as page:
        with pytest.raises(BlockerDetected) as exc_info:
            await handler.inspect(page, job)
        assert "CAPTCHA detectado" in str(exc_info.value)


async def test_lever_fill_and_submit_dry_run(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_profile: Profile,
    sample_resume: ResumeInfo,
    sample_answers: AnswerSet,
) -> None:
    handler = LeverHandler()
    job = Job(
        id="lev_dry", row_index=3, company="LeverCorp", title="Senior Python", url=mock_server.lever_url, ats="lever"
    )

    async with browser_manager.new_page() as page:
        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=sample_profile,
            resume=sample_resume,
            answers=sample_answers,
            dry_run=True,
        )

        assert result.status == TaskStatus.COMPLETED
        assert result.dry_run is True
        assert result.submitted is False
        assert result.resume_used == sample_resume.path.name

        # Verifica se campos foram preenchidos na página
        name_val = await page.locator("input[name='name']").input_value()
        email_val = await page.locator("input[name='email']").input_value()
        assert name_val == sample_profile.full_name
        assert email_val == sample_profile.email

        # Garante que form não foi submetido (sucesso continua oculto)
        success_loc = page.locator("#success-box")
        assert not await success_loc.is_visible()


async def test_lever_fill_and_submit_live(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_profile: Profile,
    sample_resume: ResumeInfo,
    sample_answers: AnswerSet,
) -> None:
    handler = LeverHandler()
    job = Job(
        id="lev_live", row_index=4, company="LeverCorp", title="Senior Python", url=mock_server.lever_url, ats="lever"
    )

    async with browser_manager.new_page() as page:
        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=sample_profile,
            resume=sample_resume,
            answers=sample_answers,
            dry_run=False,
        )

        assert result.status == TaskStatus.COMPLETED
        assert result.dry_run is False
        assert result.submitted is True

        success_loc = page.locator("#success-box")
        assert await success_loc.is_visible()


async def test_lever_fill_and_submit_no_confirm_raises_post_submit_error(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_profile: Profile,
    sample_resume: ResumeInfo,
    sample_answers: AnswerSet,
) -> None:
    handler = LeverHandler()
    job = Job(
        id="lev_noconfirm", row_index=5, company="LeverCorp", title="Engineer",
        url=f"{mock_server.lever_url}?no_confirm=1", ats="lever"
    )

    async with browser_manager.new_page() as page:
        with pytest.raises(PostSubmitError) as exc_info:
            await handler.fill_and_submit(
                page=page,
                job=job,
                profile=sample_profile,
                resume=sample_resume,
                answers=sample_answers,
                dry_run=False,
            )
        assert "confirmação não foi detectada" in str(exc_info.value)


async def test_lever_ready_check_blocks_submission(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_profile: Profile,
    sample_resume: ResumeInfo,
    sample_answers: AnswerSet,
) -> None:
    handler = LeverHandler()
    job = Job(
        id="lev_check", row_index=6, company="LeverCorp", title="Engineer", url=mock_server.lever_url, ats="lever"
    )

    async def unsafe_ready_check() -> bool:
        return False

    async with browser_manager.new_page() as page:
        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=sample_profile,
            resume=sample_resume,
            answers=sample_answers,
            dry_run=True,
            ready_check=unsafe_ready_check,
        )
        assert result.status == TaskStatus.NEEDS_REVIEW
        assert "ready_check" in (result.reason or "")


# ---------------------------------------------------------------- Greenhouse Tests

async def test_greenhouse_inspect(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    handler = GreenhouseHandler()
    job = Job(
        id="gh_1", row_index=1, company="Acme", title="Staff Backend", url=mock_server.greenhouse_url, ats="greenhouse"
    )

    async with browser_manager.new_page() as page:
        info = await handler.inspect(page, job)
        assert "Staff Backend Engineer" in info.description or "distributed systems" in info.description
        assert len(info.questions) >= 2


async def test_greenhouse_inspect_captcha_blocker(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    handler = GreenhouseHandler()
    job = Job(
        id="gh_captcha", row_index=2, company="Acme", title="Staff Backend",
        url=f"{mock_server.greenhouse_url}?captcha=1", ats="greenhouse"
    )

    async with browser_manager.new_page() as page:
        with pytest.raises(BlockerDetected) as exc_info:
            await handler.inspect(page, job)
        assert "CAPTCHA detectado" in str(exc_info.value)


async def test_greenhouse_fill_and_submit_dry_run(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_profile: Profile,
    sample_resume: ResumeInfo,
    sample_answers: AnswerSet,
) -> None:
    handler = GreenhouseHandler()
    job = Job(
        id="gh_dry", row_index=3, company="Acme",
        title="Staff Backend", url=mock_server.greenhouse_url, ats="greenhouse",
    )

    async with browser_manager.new_page() as page:
        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=sample_profile,
            resume=sample_resume,
            answers=sample_answers,
            dry_run=True,
        )

        assert result.status == TaskStatus.COMPLETED
        assert result.dry_run is True
        assert result.submitted is False

        # Verifica dados preenchidos
        first_name = await page.locator("input#first_name").input_value()
        last_name = await page.locator("input#last_name").input_value()
        email_val = await page.locator("input#email").input_value()

        assert first_name == "Carlos"
        assert last_name == "Drummond"
        assert email_val == sample_profile.email

        # Confirmação deve estar oculta
        confirm_loc = page.locator("#application_confirmation")
        assert not await confirm_loc.is_visible()


async def test_greenhouse_fill_and_submit_live(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_profile: Profile,
    sample_resume: ResumeInfo,
    sample_answers: AnswerSet,
) -> None:
    handler = GreenhouseHandler()
    job = Job(
        id="gh_live", row_index=4, company="Acme",
        title="Staff Backend", url=mock_server.greenhouse_url, ats="greenhouse",
    )

    async with browser_manager.new_page() as page:
        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=sample_profile,
            resume=sample_resume,
            answers=sample_answers,
            dry_run=False,
        )

        assert result.status == TaskStatus.COMPLETED
        assert result.dry_run is False
        assert result.submitted is True

        confirm_loc = page.locator("#application_confirmation")
        assert await confirm_loc.is_visible()


async def test_greenhouse_missing_required_field_returns_needs_review(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
    sample_resume: ResumeInfo,
) -> None:
    handler = GreenhouseHandler()
    job = Job(
        id="gh_missing", row_index=5, company="Acme", title="Staff Backend",
        url=mock_server.greenhouse_url, ats="greenhouse"
    )

    # Perfil com nome vazio
    empty_name_profile = Profile(
        full_name="",
        email="test@example.com",
        phone="123",
        location="Remote",
    )

    async with browser_manager.new_page() as page:
        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=empty_name_profile,
            resume=sample_resume,
            answers=AnswerSet(),
            dry_run=True,
        )
        assert result.status == TaskStatus.NEEDS_REVIEW
        assert "Campo obrigatório não preenchido" in (result.reason or "")
