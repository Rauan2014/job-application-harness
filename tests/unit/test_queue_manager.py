"""Testes unitários para o QueueManager: concorrência, retentativas e persistência."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import Settings
from core.models import (
    ApplicationResult,
    Job,
    PostSubmitError,
    Profile,
    TaskStatus,
    TransientError,
)
from core.pipeline import Pipeline
from core.queue_manager import QueueManager

pytestmark = pytest.mark.unit


def _make_dummy_job(job_id: str) -> Job:
    return Job(
        id=job_id,
        row_index=int(job_id) if job_id.isdigit() else 1,
        company="Empresa Teste",
        title="Software Engineer",
        url=f"https://jobs.lever.co/empresa/{job_id}",
    )


@pytest.fixture
def sample_profile() -> Profile:
    return Profile(
        full_name="Ana Teste",
        email="ana@example.com",
        phone="123456",
        location="Remote",
    )


@pytest.mark.asyncio
async def test_queue_manager_respects_max_concurrency(
    test_settings: Settings,
    sample_profile: Profile,
    tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(
        update={
            "MAX_CONCURRENCY": 3,
            "RESULTS_FILE": tmp_path / "results_concurrency.jsonl",
        }
    )

    current_concurrent = 0
    max_observed_concurrency = 0

    mock_pipeline = MagicMock(spec=Pipeline)

    async def fake_process_job(job: Job, profile: Profile) -> ApplicationResult:
        nonlocal current_concurrent, max_observed_concurrency
        current_concurrent += 1
        if current_concurrent > max_observed_concurrency:
            max_observed_concurrency = current_concurrent

        # Simula trabalho assíncrono
        await asyncio.sleep(0.02)

        current_concurrent -= 1
        return ApplicationResult(
            job_id=job.id,
            status=TaskStatus.COMPLETED,
            dry_run=True,
            submitted=False,
        )

    mock_pipeline.process_job = AsyncMock(side_effect=fake_process_job)

    qm = QueueManager(settings=settings, pipeline=mock_pipeline, profile=sample_profile)
    jobs = [_make_dummy_job(str(i)) for i in range(10)]

    results = await qm.run(jobs)

    assert len(results) == 10
    assert max_observed_concurrency <= 3
    assert max_observed_concurrency > 1


@pytest.mark.asyncio
async def test_queue_manager_retries_transient_error(
    test_settings: Settings,
    sample_profile: Profile,
    tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(
        update={
            "MAX_RETRIES": 2,
            "BACKOFF_BASE_S": 0.01,
            "RESULTS_FILE": tmp_path / "results_retry.jsonl",
        }
    )

    call_count = 0
    mock_pipeline = MagicMock(spec=Pipeline)

    async def failing_process_job(job: Job, profile: Profile) -> ApplicationResult:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TransientError("Timeout de rede pré-submit")
        return ApplicationResult(
            job_id=job.id,
            status=TaskStatus.COMPLETED,
            dry_run=True,
        )

    mock_pipeline.process_job = AsyncMock(side_effect=failing_process_job)

    qm = QueueManager(settings=settings, pipeline=mock_pipeline, profile=sample_profile)
    job = _make_dummy_job("retry_1")

    results = await qm.run([job])

    assert len(results) == 1
    assert results[0].status == TaskStatus.COMPLETED
    assert results[0].attempts == 3
    assert call_count == 3


@pytest.mark.asyncio
async def test_queue_manager_never_retries_post_submit_error(
    test_settings: Settings,
    sample_profile: Profile,
    tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(
        update={
            "MAX_RETRIES": 3,
            "RESULTS_FILE": tmp_path / "results_post_submit.jsonl",
        }
    )

    call_count = 0
    mock_pipeline = MagicMock(spec=Pipeline)

    async def post_submit_failing_job(job: Job, profile: Profile) -> ApplicationResult:
        nonlocal call_count
        call_count += 1
        raise PostSubmitError("Submit clicado mas tela travou")

    mock_pipeline.process_job = AsyncMock(side_effect=post_submit_failing_job)

    qm = QueueManager(settings=settings, pipeline=mock_pipeline, profile=sample_profile)
    job = _make_dummy_job("post_1")

    results = await qm.run([job])

    assert len(results) == 1
    assert results[0].status == TaskStatus.NEEDS_REVIEW
    assert results[0].submitted is True
    assert "NÃO RETENTAR" in (results[0].reason or "")
    # Regra de ouro: chamada deve ser disparada EXATAMENTE UMA VEZ
    assert call_count == 1


@pytest.mark.asyncio
async def test_queue_manager_persists_results_and_summary(
    test_settings: Settings,
    sample_profile: Profile,
    tmp_path: Path,
) -> None:
    out_file = tmp_path / "results_summary.jsonl"
    settings = test_settings.model_copy(
        update={
            "RESULTS_FILE": out_file,
            "MAX_CONCURRENCY": 2,
        }
    )

    mock_pipeline = MagicMock(spec=Pipeline)

    async def simple_process(job: Job, profile: Profile) -> ApplicationResult:
        if job.id == "1":
            return ApplicationResult(job_id=job.id, status=TaskStatus.COMPLETED, dry_run=True)
        elif job.id == "2":
            return ApplicationResult(job_id=job.id, status=TaskStatus.SKIPPED, reason="Low fit")
        else:
            return ApplicationResult(job_id=job.id, status=TaskStatus.NEEDS_REVIEW, reason="Manual")

    mock_pipeline.process_job = AsyncMock(side_effect=simple_process)

    qm = QueueManager(settings=settings, pipeline=mock_pipeline, profile=sample_profile)
    jobs = [_make_dummy_job("1"), _make_dummy_job("2"), _make_dummy_job("3")]

    progress_reports: list[str] = []

    def on_progress(job: Job, res: ApplicationResult) -> None:
        progress_reports.append(job.id)

    results = await qm.run(jobs, on_progress=on_progress)
    assert len(results) == 3
    assert len(progress_reports) == 3

    # Valida arquivo JSONL gravado
    assert out_file.exists()
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert {p["job_id"] for p in parsed} == {"1", "2", "3"}

    # Valida resumo
    summary = qm.get_summary()
    assert summary["total"] == 3
    assert summary["completed"] == 1
    assert summary["skipped"] == 1
    assert summary["needs_review"] == 1


@pytest.mark.asyncio
async def test_queue_manager_task_timeout_returns_needs_review(
    test_settings: Settings,
    sample_profile: Profile,
    tmp_path: Path,
) -> None:
    """REF-6: Timeout de tarefa gera NEEDS_REVIEW e não é retentado."""
    settings = test_settings.model_copy(
        update={
            "TASK_TIMEOUT_S": 0.05,
            "MAX_RETRIES": 3,
            "RESULTS_FILE": tmp_path / "results_timeout.jsonl",
        }
    )

    call_count = 0
    mock_pipeline = MagicMock(spec=Pipeline)

    async def hanging_job(job: Job, profile: Profile) -> ApplicationResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.5)
        return ApplicationResult(job_id=job.id, status=TaskStatus.COMPLETED)

    mock_pipeline.process_job = AsyncMock(side_effect=hanging_job)

    qm = QueueManager(settings=settings, pipeline=mock_pipeline, profile=sample_profile)
    job = _make_dummy_job("timeout_1")

    results = await qm.run([job])

    assert len(results) == 1
    assert results[0].status == TaskStatus.NEEDS_REVIEW
    assert "Timeout" in (results[0].reason or "")
    assert call_count == 1


@pytest.mark.asyncio
async def test_queue_manager_cancellation_preserves_results(
    test_settings: Settings,
    sample_profile: Profile,
    tmp_path: Path,
) -> None:
    """REF-5: Cancelamento (Ctrl+C / CancelledError) preserva resultados já processados."""
    settings = test_settings.model_copy(
        update={
            "MAX_CONCURRENCY": 1,
            "RESULTS_FILE": tmp_path / "results_cancel.jsonl",
        }
    )

    mock_pipeline = MagicMock(spec=Pipeline)

    async def step_process(job: Job, profile: Profile) -> ApplicationResult:
        if job.id == "1":
            return ApplicationResult(job_id=job.id, status=TaskStatus.COMPLETED, dry_run=True)
        # Job 2 trava para cancelarmos
        await asyncio.sleep(10.0)
        return ApplicationResult(job_id=job.id, status=TaskStatus.COMPLETED)

    mock_pipeline.process_job = AsyncMock(side_effect=step_process)

    qm = QueueManager(settings=settings, pipeline=mock_pipeline, profile=sample_profile)
    jobs = [_make_dummy_job("1"), _make_dummy_job("2")]

    task = asyncio.create_task(qm.run(jobs))
    await asyncio.sleep(0.05)  # Garante que job 1 terminou e job 2 iniciou
    task.cancel()

    results = await task

    # Resultados já concluídos devem estar preservados em results e qm.results
    assert len(results) >= 1
    assert results[0].job_id == "1"
    assert results[0].status == TaskStatus.COMPLETED
    assert len(qm.results) >= 1

