"""Testes unitários para o pipeline principal de candidaturas."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation.base_handler import BaseATSHandler
from automation.browser import BrowserManager
from automation.registry import register_handler, reset_registry
from config.settings import Settings
from core.decision.layer import DecisionLayer
from core.matcher import JobMatcher
from core.models import (
    AnswerSet,
    ApplicationResult,
    BlockerDetected,
    FormQuestion,
    Job,
    PageInfo,
    PostSubmitError,
    Profile,
    ResumeInfo,
    TaskStatus,
)
from core.pipeline import Pipeline
from core.resume_resolver import ResumeResolver

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_pipeline_deps(test_settings: Settings, fake_decision_model, tmp_path: Path):
    decision_layer = DecisionLayer(model=fake_decision_model, settings=test_settings)

    resume_file = tmp_path / "resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 dummy")
    dummy_resume = ResumeInfo(path=resume_file, summary="Summary", text="Python Dev")

    resolver = MagicMock(spec=ResumeResolver)
    resolver.resolve = AsyncMock(return_value=dummy_resume)
    resolver.get_resumes = MagicMock(return_value={})
    resolver.get_base_resumes = MagicMock(return_value={})

    matcher = MagicMock(spec=JobMatcher)
    matcher.answer_questions = AsyncMock(return_value=AnswerSet(answers={"q1": "Answer 1"}))

    # Mock BrowserManager and Page
    page = AsyncMock()
    page.url = "https://example.com/apply"
    page.screenshot = AsyncMock()

    browser_mgr = MagicMock(spec=BrowserManager)

    @asynccontextmanager
    async def mock_new_page():
        yield page

    browser_mgr.new_page = mock_new_page
    browser_mgr.save_screenshot = AsyncMock(return_value=tmp_path / "shot.png")

    pipeline = Pipeline(
        settings=test_settings,
        decision_layer=decision_layer,
        resume_resolver=resolver,
        browser_manager=browser_mgr,
        matcher=matcher,
    )

    profile = Profile(
        full_name="Ana Teste",
        email="ana@example.com",
        phone="123456",
        location="Remote",
        links={"linkedin": "https://linkedin.com/in/ana"},
        base_answers={"anos": "5"},
        eeo={"gender": "Female"},
    )

    return pipeline, profile, page, matcher, resolver


@pytest.mark.asyncio
async def test_pipeline_skips_already_applied_job(mock_pipeline_deps) -> None:
    pipeline, profile, _, _, _ = mock_pipeline_deps
    job = Job(
        id="j1",
        row_index=1,
        company="Empresa",
        title="Dev",
        sheet_status="Candidatado",
        url="https://jobs.lever.co/empresa/1",
    )

    result = await pipeline.process_job(job, profile)
    assert result.status == TaskStatus.SKIPPED
    assert "status prévio" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_pipeline_skips_low_fit_score(mock_pipeline_deps) -> None:
    pipeline, profile, _, _, _ = mock_pipeline_deps
    job = Job(
        id="j2",
        row_index=2,
        company="Empresa",
        title="Dev",
        fit_score=50,  # Limiar mínimo é 70
        url="https://jobs.lever.co/empresa/2",
    )

    result = await pipeline.process_job(job, profile)
    assert result.status == TaskStatus.SKIPPED
    assert "fit score" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_pipeline_unsupported_ats_skipped(mock_pipeline_deps) -> None:
    pipeline, profile, _, _, _ = mock_pipeline_deps
    job = Job(
        id="j3",
        row_index=3,
        company="Empresa",
        title="Dev",
        url="https://workday.com/empresa/3",
    )

    result = await pipeline.process_job(job, profile)
    assert result.status == TaskStatus.SKIPPED
    assert "não suportada" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_pipeline_missing_resume_needs_review(mock_pipeline_deps) -> None:
    pipeline, profile, _, _, resolver = mock_pipeline_deps
    resolver.resolve = AsyncMock(return_value=None)
    job = Job(
        id="j4",
        row_index=4,
        company="Empresa",
        title="Dev",
        url="https://jobs.lever.co/empresa/4",
        ats="lever",
    )

    result = await pipeline.process_job(job, profile)
    assert result.status == TaskStatus.NEEDS_REVIEW
    assert "currículo" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_pipeline_blocker_detected_needs_review(mock_pipeline_deps) -> None:
    pipeline, profile, page, _, _ = mock_pipeline_deps
    job = Job(
        id="j5",
        row_index=5,
        company="Empresa",
        title="Dev",
        url="https://jobs.lever.co/empresa/5",
        ats="lever",
    )

    class CaptchaHandler(BaseATSHandler):
        name = "lever"

        def can_handle(self, j: Job) -> bool:
            return True

        async def inspect(self, page, job):
            raise BlockerDetected("CAPTCHA no form")

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            pass

    register_handler(CaptchaHandler())
    try:
        result = await pipeline.process_job(job, profile)
        assert result.status == TaskStatus.NEEDS_REVIEW
        assert "blocker detectado" in (result.reason or "").lower()
    finally:
        reset_registry()


@pytest.mark.asyncio
async def test_pipeline_missing_required_facts_needs_review(mock_pipeline_deps) -> None:
    pipeline, profile, page, matcher, _ = mock_pipeline_deps
    # Matcher retorna None para pergunta obrigatória (sem fatos no perfil)
    matcher.answer_questions = AsyncMock(return_value=AnswerSet(answers={"req_q": None}))

    job = Job(
        id="j6",
        row_index=6,
        company="Empresa",
        title="Dev",
        url="https://jobs.lever.co/empresa/6",
        ats="lever",
    )

    class QuestionHandler(BaseATSHandler):
        name = "lever"

        def can_handle(self, j: Job) -> bool:
            return True

        async def inspect(self, page, job):
            return PageInfo(
                description="Vaga",
                questions=[
                    FormQuestion(id="req_q", label="Pretensão salarial exata em euros", kind="text", required=True)
                ],
            )

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            pass

    register_handler(QuestionHandler())
    try:
        result = await pipeline.process_job(job, profile)
        assert result.status == TaskStatus.NEEDS_REVIEW
        assert "dados ausentes no perfil" in (result.reason or "").lower()
    finally:
        reset_registry()


@pytest.mark.asyncio
async def test_pipeline_post_submit_error_not_retried(mock_pipeline_deps) -> None:
    pipeline, profile, page, _, _ = mock_pipeline_deps
    job = Job(
        id="j7",
        row_index=7,
        company="Empresa",
        title="Dev",
        url="https://jobs.lever.co/empresa/7",
        ats="lever",
    )

    class FailingSubmitHandler(BaseATSHandler):
        name = "lever"

        def can_handle(self, j: Job) -> bool:
            return True

        async def inspect(self, page, job):
            return PageInfo(description="Vaga", questions=[])

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            raise PostSubmitError("Timeout aguardando confirmação")

    register_handler(FailingSubmitHandler())
    try:
        result = await pipeline.process_job(job, profile)
        assert result.status == TaskStatus.NEEDS_REVIEW
        assert "erro pós-submissão" in (result.reason or "").lower()
        assert result.submitted is True  # Marcado como submetido para evitar retry
    finally:
        reset_registry()


@pytest.mark.asyncio
async def test_pipeline_propagates_page_description_to_job(mock_pipeline_deps) -> None:
    from automation.base_handler import BaseATSHandler
    from automation.registry import register_handler, reset_registry
    from core.models import FormQuestion, PageInfo

    pipeline, profile, page, matcher, _ = mock_pipeline_deps
    job = Job(
        id="j8",
        row_index=8,
        company="Acme Corp",
        title="Python Dev",
        url="https://jobs.lever.co/acme/8",
        ats="lever",
        description=None,
    )

    class InspectDescriptionHandler(BaseATSHandler):
        name = "lever"

        def can_handle(self, j: Job) -> bool:
            return True

        async def inspect(self, page, job):
            return PageInfo(
                description="Vaga detalhada extraída da página",
                questions=[FormQuestion(id="q1", label="Experiência")],
            )

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            return ApplicationResult(job_id=job.id, status=TaskStatus.COMPLETED, dry_run=dry_run)

    register_handler(InspectDescriptionHandler())
    try:
        result = await pipeline.process_job(job, profile)
        assert result.status == TaskStatus.COMPLETED
        assert matcher.answer_questions.called
        call_job = matcher.answer_questions.call_args.kwargs["job"]
        assert call_job.description == "Vaga detalhada extraída da página"
    finally:
        reset_registry()


@pytest.mark.asyncio
async def test_pipeline_calls_handler_release_on_early_return(mock_pipeline_deps) -> None:
    """REF-3: Pipeline.process_job deve chamar handler.release(job) no finally."""
    from automation.base_handler import BaseATSHandler
    from automation.registry import register_handler, reset_registry
    from core.models import BlockerDetected

    pipeline, profile, page, _, _ = mock_pipeline_deps
    job = Job(
        id="j-release",
        row_index=9,
        company="Acme Corp",
        title="Python Dev",
        url="https://jobs.lever.co/acme/9",
        ats="lever",
    )

    release_called_with = []

    class BlockerHandler(BaseATSHandler):
        name = "lever"

        def can_handle(self, j: Job) -> bool:
            return True

        async def inspect(self, page, job):
            raise BlockerDetected("Captcha bloqueando")

        def release(self, j: Job) -> None:
            release_called_with.append(j.id)

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            return ApplicationResult(job_id=job.id, status=TaskStatus.COMPLETED)

    register_handler(BlockerHandler())
    try:
        result = await pipeline.process_job(job, profile)
        assert result.status == TaskStatus.NEEDS_REVIEW
        assert release_called_with == [job.id]
    finally:
        reset_registry()


@pytest.mark.asyncio
async def test_pipeline_dp6_respects_dry_run(mock_pipeline_deps) -> None:
    """QA-4: DP6 respeita dry_run=True no pipeline."""
    from automation.base_handler import BaseATSHandler
    from automation.registry import register_handler, reset_registry
    from core.models import FormQuestion, PageInfo

    pipeline, profile, page, matcher, _ = mock_pipeline_deps
    job = Job(
        id="j-dry",
        row_index=10,
        company="Acme Corp",
        title="Python Dev",
        url="https://jobs.lever.co/acme/10",
        ats="lever",
    )

    passed_ready_check_result = []

    class DryRunHandler(BaseATSHandler):
        name = "lever"

        def can_handle(self, j: Job) -> bool:
            return True

        async def inspect(self, page, job):
            return PageInfo(
                description="Desc",
                questions=[FormQuestion(id="q1", label="Experiência")],
            )

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            if ready_check is not None:
                rc = await ready_check()
                passed_ready_check_result.append(rc)
            return ApplicationResult(
                job_id=job.id,
                status=TaskStatus.COMPLETED,
                dry_run=dry_run,
                submitted=False,
            )

    register_handler(DryRunHandler())
    try:
        pipeline.settings.DRY_RUN = True
        result = await pipeline.process_job(job, profile)
        assert result.status == TaskStatus.COMPLETED
        assert result.dry_run is True
        assert result.submitted is False
        assert passed_ready_check_result == [True]
    finally:
        reset_registry()


