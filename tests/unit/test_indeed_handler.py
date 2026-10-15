"""Testes unitários para o IndeedApplyHandler, delegação no Aggregator e fallback determinístico do Matcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from automation.handlers.aggregator import AggregatorHandler
from automation.handlers.indeed import IndeedApplyHandler
from config.settings import Settings
from core.matcher import Matcher
from core.models import (
    AnswerSet,
    BlockerDetected,
    FormQuestion,
    Job,
    Profile,
    ResumeInfo,
    TaskStatus,
)


def _make_indeed_job(
    url: str = "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module",
) -> Job:
    return Job(
        id="job-indeed-1",
        row_index=1,
        company="TechCorp",
        title="Desenvolvedor Python Pleno",
        ats="indeed",
        url=url,
    )


@pytest.fixture
def mock_profile() -> Profile:
    return Profile(
        full_name="Jane Doe",
        email="jane.doe@example.com",
        phone="+5511999999999",
        location="São Paulo, SP, Brasil",
        links={"linkedin": "https://linkedin.com/in/jane-doe", "github": "https://github.com/jane-doe"},
        skills=["Python", "FastAPI", "React", "Docker"],
        headline="Software Engineer",
        salary_expectation="R$ 15.000 / mês (PJ)",
        base_answers={"years_python": "4 anos", "english_level": "Avançado", "notice_period": "Imediato"},
    )


@pytest.fixture
def mock_resume(tmp_path: Path) -> ResumeInfo:
    resume_file = tmp_path / "curriculo_teste.pdf"
    resume_file.write_bytes(b"%PDF-1.4 test")
    return ResumeInfo(
        path=resume_file,
        summary="Desenvolvedor Python com 4 anos de experiência.",
        text="Desenvolvedor Python com experiência em FastAPI, Docker e React.",
    )


def test_indeed_can_handle() -> None:
    handler = IndeedApplyHandler()

    smartapply_url = "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module"
    assert handler.can_handle(_make_indeed_job(smartapply_url)) is True
    assert handler.can_handle(_make_indeed_job("https://br.indeed.com/viewjob?jk=abcdef123456")) is True
    assert handler.can_handle(_make_indeed_job("https://www.indeed.com/rc/clk?jk=xyz987")) is True
    assert handler.can_handle(_make_indeed_job("https://example.com/apply")) is True  # ats="indeed"

    # Não deve tratar outras plataformas
    job_other = Job(id="other", row_index=2, company="Other", title="Dev", ats=None, url="https://jobs.lever.co/empresa/1")
    assert handler.can_handle(job_other) is False


@pytest.mark.asyncio
async def test_indeed_check_blockers_auth_expired() -> None:
    handler = IndeedApplyHandler()
    page = MagicMock()
    page.url = "https://secure.indeed.com/auth?continue=https://smartapply.indeed.com"

    with pytest.raises(BlockerDetected, match="Sessão do Indeed expirada ou login necessário"):
        await handler._check_blockers(page)


@pytest.mark.asyncio
async def test_indeed_handle_resume_module_radio_checked(mock_resume: ResumeInfo) -> None:
    handler = IndeedApplyHandler()
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()

    # Simula radio previamente marcado
    radio_mock = MagicMock()
    radio_mock.is_checked = AsyncMock(return_value=True)

    locator_mock = MagicMock()
    locator_mock.count = AsyncMock(return_value=1)
    locator_mock.nth = MagicMock(return_value=radio_mock)
    page.locator = MagicMock(return_value=locator_mock)

    handled = await handler._handle_resume_module(page, mock_resume)
    assert handled is True
    assert radio_mock.is_checked.called


@pytest.mark.asyncio
async def test_indeed_handle_resume_module_upload_file(mock_resume: ResumeInfo) -> None:
    handler = IndeedApplyHandler()
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()

    # Sem radios, com file input
    file_input_mock = MagicMock()
    file_input_mock.set_input_files = AsyncMock()

    def locator_side_effect(selector: str):
        loc = MagicMock()
        if "file" in selector:
            loc.count = AsyncMock(return_value=1)
            loc.first = file_input_mock
        else:
            loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=locator_side_effect)

    handled = await handler._handle_resume_module(page, mock_resume)
    assert handled is True
    assert file_input_mock.set_input_files.called


@pytest.mark.asyncio
async def test_indeed_click_continue() -> None:
    handler = IndeedApplyHandler()
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()

    button_mock = MagicMock()
    button_mock.is_visible = AsyncMock(return_value=True)
    button_mock.is_enabled = AsyncMock(return_value=True)
    button_mock.get_attribute = AsyncMock(return_value="")
    button_mock.click = AsyncMock()

    locator_mock = MagicMock()
    locator_mock.count = AsyncMock(return_value=1)
    locator_mock.first = button_mock
    locator_mock.nth = MagicMock(return_value=button_mock)
    page.locator = MagicMock(return_value=locator_mock)

    clicked = await handler._click_continue(page)
    assert clicked is True
    assert button_mock.click.called


@pytest.mark.asyncio
async def test_indeed_fill_and_submit_dry_run(mock_profile: Profile, mock_resume: ResumeInfo) -> None:
    handler = IndeedApplyHandler()
    job = _make_indeed_job()
    answers = AnswerSet(answers={"textarea_0": "Tenho grande interesse na vaga"})

    page = MagicMock()
    page.url = "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module"
    page.wait_for_timeout = AsyncMock()
    page.locator.return_value.count = AsyncMock(return_value=0)

    # Simula 1º passo: resume selection; 2º passo: review module
    step_urls = [
        "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module",
        "https://smartapply.indeed.com/beta/indeedapply/form/review-module",
    ]
    url_idx = 0

    def get_url():
        nonlocal url_idx
        u = step_urls[min(url_idx, len(step_urls) - 1)]
        url_idx += 1
        return u

    type(page).url = property(lambda self: get_url())

    with (
        patch.object(handler, "_ensure_on_smartapply", new_callable=AsyncMock),
        patch.object(handler, "_check_blockers", new_callable=AsyncMock),
        patch.object(handler, "_handle_resume_module", new_callable=AsyncMock) as mock_handle_res,
        patch.object(handler, "_fill_visible_step_fields", new_callable=AsyncMock) as mock_fill,
        patch.object(handler, "_click_continue", new_callable=AsyncMock) as mock_cont,
        patch.object(handler, "_is_review_page", new_callable=AsyncMock) as mock_review,
    ):
        mock_handle_res.return_value = True
        mock_cont.return_value = True
        # Na 1ª iteração não é review; na 2ª é review
        mock_review.side_effect = [False, True]

        result = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=mock_profile,
            resume=mock_resume,
            answers=answers,
            dry_run=True,
        )

        assert result.status == TaskStatus.COMPLETED
        assert result.dry_run is True
        assert result.submitted is False
        assert mock_handle_res.called
        assert mock_fill.called


@pytest.mark.asyncio
async def test_aggregator_delegates_to_indeed_on_smartapply(mock_profile: Profile, mock_resume: ResumeInfo) -> None:
    """Verifica que o AggregatorHandler faz handover limpo para o IndeedApplyHandler em smartapply.indeed.com."""
    aggregator = AggregatorHandler()
    job = Job(id="agg-indeed", row_index=3, company="X", title="Dev", ats="aggregator", url="https://remotar.com.br/vaga/indeed-1")

    page = MagicMock()
    page.url = "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module"
    page.wait_for_timeout = AsyncMock()
    page.goto = AsyncMock()

    with (
        patch.object(aggregator._indeed, "inspect", new_callable=AsyncMock) as mock_ind_inspect,
        patch.object(aggregator, "_check_blockers", new_callable=AsyncMock),
    ):
        mock_ind_inspect.return_value = MagicMock(description="Indeed SmartApply")
        res = await aggregator.inspect(page, job)

        assert mock_ind_inspect.called
        assert res.description == "Indeed SmartApply"
        assert job.id in aggregator._delegated
        assert aggregator._delegated[job.id][0] == aggregator._indeed


@pytest.mark.asyncio
async def test_matcher_fallback_answers_on_llm_failure(test_settings: Settings, mock_profile: Profile) -> None:
    """Garante que quando a LLM retorna 429/503 após retentativas, fallback_answers usa fatos do perfil."""
    async def fast_sleep(s: float) -> None:
        pass

    async with httpx.AsyncClient() as client:
        matcher = Matcher(test_settings, client=client, sleep=fast_sleep)

        # Simula LLM respondendo HTTP 429 (Resource Exhausted / Quota Exceeded)
        respx.post("http://test.llm.local/v1/chat/completions").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"})
        )

        questions = [
            FormQuestion(id="q_exp_python", label="Anos de experiência com Python?"),
            FormQuestion(id="q_salary", label="Qual a sua pretensão salarial?"),
            FormQuestion(id="q_location", label="Qual a sua cidade ou localização atual?"),
        ]

        answers = await matcher.generate_answers(
            job_description="Vaga Python",
            questions=questions,
            profile=mock_profile,
            resume_text="Curriculo texto",
            language="pt",
        )

        # Não deve falhar nem lançar TransientError: preenche com base no perfil
        assert answers.answers["q_exp_python"] == "4 anos"
        assert answers.answers["q_salary"] == "R$ 15.000 / mês (PJ)"
        assert answers.answers["q_location"] == "São Paulo, SP, Brasil"


@pytest.mark.asyncio
async def test_indeed_check_blockers_detects_404_expired_job() -> None:
    """Garante que IndeedApplyHandler._check_blockers detecta páginas 404/encerradas como BlockerDetected."""
    handler = IndeedApplyHandler()
    page = MagicMock()
    page.url = "https://smartapply.indeed.com/beta/indeedapply/apply?jk=6359055e8c459fc5"
    page.title = AsyncMock(return_value="404 Page not Found")
    page.inner_text = AsyncMock(return_value="A vaga que você está procurando não está mais disponível")

    with pytest.raises(BlockerDetected, match="HTTP 404"):
        await handler._check_blockers(page)

