"""Testes unitários para o registro de handlers de ATS."""

from __future__ import annotations

from automation.base_handler import BaseATSHandler
from automation.handlers.greenhouse import GreenhouseHandler
from automation.handlers.lever import LeverHandler
from automation.registry import get_handler, register_handler, reset_registry
from core.models import Job


def _make_job(job_id: str, ats: str | None = None, url: str | None = None) -> Job:
    return Job(
        id=job_id,
        row_index=1,
        company="Acme Corp",
        title="Software Engineer",
        ats=ats,  # type: ignore[arg-type]
        url=url,
    )


def test_get_handler_by_ats_name() -> None:
    reset_registry()
    lever_job = _make_job("1", ats="lever", url="https://example.com/apply")
    gh_job = _make_job("2", ats="greenhouse", url="https://example.com/apply")
    indeed_job = _make_job("3", ats="indeed", url="https://example.com/apply")

    h1 = get_handler(lever_job)
    h2 = get_handler(gh_job)
    h3 = get_handler(indeed_job)

    assert isinstance(h1, LeverHandler)
    assert isinstance(h2, GreenhouseHandler)
    from automation.handlers.indeed import IndeedApplyHandler
    assert isinstance(h3, IndeedApplyHandler)


def test_get_handler_by_url_detection() -> None:
    reset_registry()
    lever_job = _make_job("4", ats=None, url="https://jobs.lever.co/techco/abc-123")
    gh_job = _make_job("5", ats=None, url="https://boards.greenhouse.io/awesome/jobs/999")
    indeed_url = "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module"
    indeed_job = _make_job("6", ats=None, url=indeed_url)

    h1 = get_handler(lever_job)
    h2 = get_handler(gh_job)
    h3 = get_handler(indeed_job)

    assert isinstance(h1, LeverHandler)
    assert isinstance(h2, GreenhouseHandler)
    from automation.handlers.indeed import IndeedApplyHandler
    assert isinstance(h3, IndeedApplyHandler)


def test_get_handler_unsupported_returns_none() -> None:
    reset_registry()
    workday_job = _make_job("5", ats=None, url="https://myworkdayjobs.com/apply")
    assert get_handler(workday_job) is None


def test_register_custom_handler() -> None:
    reset_registry()

    class CustomHandler(BaseATSHandler):
        name = "custom"

        def can_handle(self, job: Job) -> bool:
            return "custom-ats.com" in (job.url or "")

        async def inspect(self, page, job):
            raise NotImplementedError

        async def fill_and_submit(self, page, job, profile, resume, answers, dry_run=True, ready_check=None):
            raise NotImplementedError

    custom_job = _make_job("6", url="https://custom-ats.com/job/1")
    assert get_handler(custom_job) is None

    register_handler(CustomHandler())
    h = get_handler(custom_job)
    assert isinstance(h, CustomHandler)

    reset_registry()
    assert get_handler(custom_job) is None
