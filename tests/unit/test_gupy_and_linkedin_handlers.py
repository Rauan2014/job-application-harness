"""Testes unitários para GupyHandler e LinkedInHandler (roteamento, inspect e dry_run)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from automation.handlers.gupy import GupyHandler
from automation.handlers.linkedin import LinkedInHandler
from automation.registry import get_handler, reset_registry
from core.models import AnswerSet, BlockerDetected, Job, Profile, ResumeInfo, TaskStatus


class _FakeLocator:
    def __init__(self, count_val: int = 0, visible: bool = False, text: str = "") -> None:
        self._count = count_val
        self._visible = visible
        self._text = text

    @property
    def first(self) -> _FakeLocator:
        return self

    def nth(self, _idx: int) -> _FakeLocator:
        return self

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator(0, False, "")

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def inner_text(self) -> str:
        return self._text

    async def click(self, **_kwargs) -> None:
        return None

    async def fill(self, _val: str) -> None:
        return None

    async def set_input_files(self, _path: str) -> None:
        return None

    async def get_attribute(self, _name: str) -> str | None:
        return None

    async def evaluate(self, _fn: str) -> str:
        return ""


class _FakePage:
    def __init__(self, url: str, selectors_map: dict[str, _FakeLocator] | None = None) -> None:
        self.url = url
        self._map = selectors_map or {}
        self.goto = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.screenshot = AsyncMock()

    def locator(self, selector: str) -> _FakeLocator:
        for key, loc in self._map.items():
            if key in selector:
                return loc
        return _FakeLocator(0, False, "")


def _profile() -> Profile:
    return Profile(
        full_name="João Souza",
        email="joao@example.com",
        phone="+55 11 98888-7777",
        location="São Paulo, Brasil",
    )


def _resume(tmp_path: Path) -> ResumeInfo:
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    return ResumeInfo(
        path=pdf,
        summary="Python FastAPI",
        text="Python FastAPI",
    )


def test_registry_recognizes_gupy_and_linkedin_urls():
    reset_registry()
    gupy_job = Job(id="g1", company="Stone", title="Dev Python", url="https://stone.gupy.io/jobs/12345")
    li_job = Job(id="l1", company="Nubank", title="Backend", url="https://www.linkedin.com/jobs/view/987654321/")

    assert isinstance(get_handler(gupy_job), GupyHandler)
    assert isinstance(get_handler(li_job), LinkedInHandler)


@pytest.mark.asyncio
async def test_gupy_handler_inspect_detects_auth_gate_and_dry_run_submits(tmp_path: Path):
    handler = GupyHandler()
    job = Job(id="g1", company="Stone", title="Dev Python", url="https://stone.gupy.io/jobs/12345")

    # 1. Auth gate redirecionando para auth.gupy.io deve levantar BlockerDetected
    gate_page = _FakePage(url="https://auth.gupy.io/signin")
    with pytest.raises(BlockerDetected):
        await handler.inspect(gate_page, job)

    # 2. Dry-run na página da Gupy deve preencher e retornar DRY_RUN
    ok_page = _FakePage(
        url="https://stone.gupy.io/jobs/12345/apply",
        selectors_map={
            "type='email'": _FakeLocator(1, True),
            "name='fullName'": _FakeLocator(1, True),
            "type='file'": _FakeLocator(1, False),
            "type='submit'": _FakeLocator(1, True),
        },
    )
    info = await handler.inspect(ok_page, job)
    assert "Gupy" in info.state_summary

    result = await handler.fill_and_submit(
        ok_page,
        job,
        _profile(),
        _resume(tmp_path),
        AnswerSet(),
        dry_run=True,
    )
    assert result.status == TaskStatus.COMPLETED
    assert result.dry_run is True
    assert result.submitted is False


@pytest.mark.asyncio
async def test_linkedin_handler_auth_wall_and_easy_apply_dry_run(tmp_path: Path):
    handler = LinkedInHandler()
    job = Job(
        id="l1",
        company="Nubank",
        title="Backend Python",
        url="https://www.linkedin.com/jobs/view/987654321/",
    )

    # 1. Checkpoint / login deve disparar BlockerDetected
    wall_page = _FakePage(url="https://www.linkedin.com/checkpoint/challenge")
    with pytest.raises(BlockerDetected):
        await handler.inspect(wall_page, job)

    # 2. Easy Apply em dry_run retorna DRY_RUN com sucesso
    easy_page = _FakePage(
        url="https://www.linkedin.com/jobs/view/987654321/",
        selectors_map={
            "jobs-apply-button": _FakeLocator(1, True),
            "jobs-easy-apply-modal": _FakeLocator(1, True),
            "Enviar candidatura": _FakeLocator(1, True),
        },
    )
    info = await handler.inspect(easy_page, job)
    assert "Easy Apply" in info.state_summary

    result = await handler.fill_and_submit(
        easy_page,
        job,
        _profile(),
        _resume(tmp_path),
        AnswerSet(),
        dry_run=True,
    )
    assert result.status == TaskStatus.COMPLETED
    assert result.dry_run is True
    assert result.submitted is False
