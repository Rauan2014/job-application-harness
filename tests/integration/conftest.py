"""Fixtures para testes de integração com servidor HTTP mock e BrowserManager."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

from automation.browser import BrowserManager
from config.settings import Settings
from core.models import AnswerSet, Profile, ResumeInfo

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@dataclass
class MockServerInfo:
    base_url: str
    lever_url: str
    greenhouse_url: str
    requests: list[str] = field(default_factory=list)


@pytest_asyncio.fixture(loop_scope="session")
async def mock_server() -> AsyncIterator[MockServerInfo]:
    recorded_requests: list[str] = []

    async def handle_lever(request: web.Request) -> web.Response:
        recorded_requests.append(str(request.rel_url))
        content = (FIXTURES_DIR / "lever_mock.html").read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/html")

    async def handle_greenhouse(request: web.Request) -> web.Response:
        recorded_requests.append(str(request.rel_url))
        content = (FIXTURES_DIR / "greenhouse_mock.html").read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/html")

    async def handle_analytics(request: web.Request) -> web.Response:
        recorded_requests.append(str(request.rel_url))
        return web.Response(text="/* analytics */", content_type="application/javascript")

    app = web.Application()
    app.router.add_get("/lever", handle_lever)
    app.router.add_get("/greenhouse", handle_greenhouse)
    app.router.add_get("/analytics.js", handle_analytics)
    app.router.add_get("/gtm.js", handle_analytics)
    app.router.add_get("/collect", handle_analytics)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    server = site._server
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    info = MockServerInfo(
        base_url=base_url,
        lever_url=f"{base_url}/lever",
        greenhouse_url=f"{base_url}/greenhouse",
        requests=recorded_requests,
    )

    try:
        yield info
    finally:
        await runner.cleanup()


@pytest_asyncio.fixture(loop_scope="session")
async def browser_manager(test_settings: Settings) -> AsyncIterator[BrowserManager]:
    bm = BrowserManager(test_settings)
    await bm.start()
    try:
        yield bm
    finally:
        await bm.stop()


@pytest.fixture
def sample_profile() -> Profile:
    return Profile(
        full_name="Carlos Drummond",
        email="carlos.drummond@example.com",
        phone="+55 31 99999-8888",
        location="Belo Horizonte, MG",
        headline="Senior Python Engineer & Distributed Systems Architect",
        links={
            "linkedin": "https://linkedin.com/in/carlos-drummond",
            "github": "https://github.com/carlosdrummond",
            "portfolio": "https://drummond.dev",
        },
        work_authorization="Citizen / Permanent Resident",
        salary_expectation="R$ 25.000 / mês",
        skills=["Python", "AsyncIO", "PostgreSQL", "Playwright"],
        base_answers={"years_python": "8", "work_auth": "Citizen / Permanent Resident"},
        eeo={"gender": "Male", "race": "White", "disability": "Decline to self-identify"},
    )


@pytest.fixture
def sample_resume(tmp_path: Path) -> ResumeInfo:
    resume_file = tmp_path / "carlos_drummond_cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 mock pdf content for resume upload")
    return ResumeInfo(
        path=resume_file,
        summary="Senior Python Engineer com 8 anos de experiência em microsserviços.",
        text="Carlos Drummond - Engenheiro de Software Sênior. Python, AsyncIO, APIs.",
    )


@pytest.fixture
def sample_answers() -> AnswerSet:
    return AnswerSet(
        answers={
            "years_python": "8",
            "work_auth": "Citizen / Permanent Resident",
            "work_auth_gh": "Yes",
            "comp_gh": "25000",
            "custom_field_0": "8",
        }
    )
