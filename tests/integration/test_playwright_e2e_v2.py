"""Testes E2E reais com Playwright (Chromium) cobrindo a Web UI, ReportLab PDF e handlers Gupy/LinkedIn."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import uvicorn
from aiohttp import web

from automation.browser import BrowserManager
from automation.handlers.gupy import GupyHandler
from automation.handlers.linkedin import LinkedInHandler
from config.settings import Settings
from core.browser_job_reader import parse_gupy_search_html, parse_linkedin_search_html
from core.models import AnswerSet, BlockerDetected, ExperienceItem, Job, Profile, ProjectItem, TaskStatus
from core.pdf_text import extract_text
from core.resume_pdf import generate_tailored_pdf
from core.resume_tailor import ResumeTailor

pytestmark = pytest.mark.integration

_LINKEDIN_SEARCH_HTML = """
<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>LinkedIn Jobs</title></head>
<body>
  <ul class="jobs-search__results-list">
    <li data-occludable-job-id="9001122">
      <a class="job-card-list__title" href="/jobs/view/9001122/">Engenheiro Python Sênior</a>
      <span class="job-card-container__primary-description">Nubank</span>
      <ul><li class="job-card-container__metadata-item">São Paulo, Brasil (Remoto)</li></ul>
      <span>Candidatura simplificada</span>
    </li>
  </ul>
</body>
</html>
"""

_GUPY_SEARCH_HTML = """
<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>Portal Gupy</title></head>
<body>
  <main>
    <a href="https://stone.gupy.io/jobs/887766">
      <h3>Desenvolvedor Backend Python</h3>
      <p>Stone - 100% Remoto</p>
    </a>
  </main>
</body>
</html>
"""

_GUPY_JOB_HTML = """
<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>Gupy - Vaga Backend Python</title></head>
<body>
  <script>
    const params = new URLSearchParams(window.location.search);
    if (params.get('auth') === '1') {
      document.write(
        '<form action="https://auth.gupy.io/signin">' +
        '<input name="identification" type="text" /><button>Entrar com CPF</button></form>'
      );
    }
  </script>
  <div id="job-desc">
    <h1>Desenvolvedor Backend Python</h1>
    <button
      data-testid="job-apply-button"
      type="button"
      onclick="document.getElementById('apply-form').style.display='block'"
    >
      Candidatar-se
    </button>
  </div>
  <form
    id="apply-form"
    style="display:none;"
    onsubmit="event.preventDefault(); document.getElementById('gupy-ok').style.display='block';"
  >
    <label for="g-name">Nome completo</label>
    <input id="g-name" name="fullName" type="text" required />
    <label for="g-email">E-mail</label>
    <input id="g-email" name="email" type="email" required />
    <label for="g-phone">Telefone</label>
    <input id="g-phone" name="phone" type="tel" />
    <label for="g-cv">Currículo PDF</label>
    <input id="g-cv" name="resume" type="file" />
    <label for="g-city">Cidade atual</label>
    <input id="g-city" name="city_extra" type="text" required />
    <button type="submit">Confirmar candidatura</button>
  </form>
  <h1 id="gupy-ok" style="display:none;">Candidatura enviada com sucesso!</h1>
</body>
</html>
"""

_LINKEDIN_EASY_APPLY_HTML = """
<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>LinkedIn - Backend Python</title></head>
<body>
  <div class="jobs-description__content">
    Buscamos Engenheiro Backend com experiência em Python, FastAPI, PostgreSQL e Docker.
  </div>
  <button class="jobs-apply-button" type="button" onclick="document.getElementById('li-modal').style.display='block'">
    Candidatura simplificada
  </button>

  <div id="li-modal" class="jobs-easy-apply-modal" role="dialog" aria-modal="true" style="display:none;">
    <div id="step-1">
      <label for="li-email">E-mail</label>
      <input id="li-email" type="email" required />
      <label for="li-phone">Telefone</label>
      <input id="li-phone" type="tel" />
      <input id="li-cv" type="file" />
      <button
        type="button"
        aria-label="Avançar"
        onclick="document.getElementById('step-1').style.display='none';
                 document.getElementById('step-2').style.display='block';"
      >
        Avançar
      </button>
    </div>
    <div id="step-2" style="display:none;">
      <button
        type="button"
        aria-label="Enviar candidatura"
        onclick="document.getElementById('li-modal').style.display='none';
                 document.getElementById('li-done').style.display='block';"
      >
        Enviar candidatura
      </button>
    </div>
  </div>
  <h2 id="li-done" style="display:none;">Candidatura enviada</h2>
</body>
</html>
"""


class _EventServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.started_event = asyncio.Event()

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        self.started_event.set()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _e2e_profile() -> Profile:
    return Profile(
        full_name="Carlos Drummond",
        email="carlos@example.com",
        phone="+55 11 99999-8888",
        location="São Paulo, SP",
        headline="Engenheiro Backend Python",
        summary="Experiência sólida em Python, FastAPI e PostgreSQL.",
        skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        experiences=[
            ExperienceItem(
                company="Empresa Real Ltda",
                role="Engenheiro Python",
                period="2022 - Atual",
                highlights=["APIs FastAPI de alta vazão com PostgreSQL e Docker."],
                skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
            )
        ],
        projects=[
            ProjectItem(
                name="Motor ATS",
                description="Pipeline de currículos em Python.",
                skills=["Python"],
            )
        ],
    )


async def test_playwright_web_ui_radar_discover_and_tailor_pdf_e2e(
    browser_manager: BrowserManager,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Testa a UI real no Chromium (Radar -> Conectar -> Buscar Vagas -> Gerar CV PDF -> Modal)."""
    storage_file = tmp_path / "storage_state.json"
    storage_file.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "li_at",
                        "domain": ".linkedin.com",
                        "path": "/",
                        "value": "li_session_ok",
                        "expires": time.time() + 86400,
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    queue_file = tmp_path / "discovered_jobs.json"
    resumes_dir = tmp_path / "generated_resumes"
    results_file = tmp_path / "results.jsonl"
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(_e2e_profile().model_dump_json(indent=2), encoding="utf-8")

    fake_settings = Settings(
        DRY_RUN=True,
        NO_LLM=True,
        OPENROUTER_API_KEY="sk-test",
        GOOGLE_SHEETS_ID="sheet-test",
        GOOGLE_SERVICE_ACCOUNT_JSON="./sa.json",
        STORAGE_STATE_PATH=storage_file,
        DISCOVERED_JOBS_FILE=queue_file,
        GENERATED_RESUMES_DIR=resumes_dir,
        RESULTS_FILE=results_file,
        TAILOR_RESUME_PER_JOB=True,
    )
    fake_settings.ensure_dirs()

    monkeypatch.setattr("web.app.load_settings", lambda: fake_settings)
    monkeypatch.setattr("web.data_provider.load_settings", lambda: fake_settings)
    monkeypatch.setattr("web.cookie_manager.load_settings", lambda: fake_settings)
    monkeypatch.setattr("web.cookie_manager.get_storage_state_path", lambda: storage_file)
    monkeypatch.setattr(
        "web.data_provider.get_results_history",
        lambda limit=500: _fake_results_only_from_queue(queue_file),
    )
    monkeypatch.setattr(
        "web.app.get_results_history",
        lambda limit=500: _fake_results_only_from_queue(queue_file),
    )

    # Garante leitura do perfil isolado temporário quando app.py abrir Path("data/profile.json")
    real_read_text = Path.read_text

    def _isolated_read_text(self: Path, *args, **kwargs):
        if self.as_posix().endswith("data/profile.json"):
            return profile_file.read_text(*args, **kwargs)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _isolated_read_text)

    async def _fake_search_jobs(**_kwargs):
        return [
            {
                "id": "gh-nubank-42",
                "external_job_id": "gh-nubank-42",
                "title": "Senior Python FastAPI Engineer",
                "company": "Nubank",
                "job_url": "https://boards.greenhouse.io/nubank/jobs/42",
                "ats": "Greenhouse",
                "modality": "100% Remoto",
                "stack": ["Python", "FastAPI", "PostgreSQL"],
                "seniority": "Sênior",
                "source_platform": "greenhouse_board",
                "job_date": "13/11/2026",
                "fit_score": 96,
                "description": "Buscamos dev com experiência em Python, FastAPI, PostgreSQL e Docker.",
            }
        ]

    from web.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = _EventServer(config)

    with (
        patch("core.job_search.search_jobs", side_effect=_fake_search_jobs),
        patch(
            "web.app.launch_visual_login",
            return_value={
                "success": True,
                "portal": "linkedin",
                "url": "https://www.linkedin.com/login",
                "auto_save": True,
                "pid": 99999,
                "message": "Janela aberta",
            },
        ) as mock_connect,
    ):
        serve_task = asyncio.create_task(server.serve())
        await asyncio.wait_for(server.started_event.wait(), timeout=5.0)

        try:
            async with browser_manager.new_page() as page:
                js_errors: list[str] = []
                console_errors: list[str] = []
                page.on("pageerror", lambda err: js_errors.append(str(err)))
                page.on(
                    "console",
                    lambda msg: (
                        console_errors.append(msg.text)
                        if msg.type == "error" and "ERR_BLOCKED_BY_CLIENT" not in msg.text
                        else None
                    ),
                )

                await page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                await page.wait_for_timeout(800)

                # 1. Verifica status de portais na aba Radar
                li_status = page.locator("#radar-status-linkedin")
                gupy_status = page.locator("#radar-status-gupy")
                await li_status.wait_for(state="visible", timeout=5000)
                await page.locator("#radar-status-linkedin", has_text="Conectado").wait_for(timeout=5000)
                assert "Conectado" in await li_status.inner_text()
                assert "Desconectado" in await gupy_status.inner_text()

                # 2. Testa clique no botão 1-clique "Conectar LinkedIn"
                await page.locator("#btn-connect-linkedin").click()
                await page.wait_for_timeout(300)
                mock_connect.assert_called_once()

                # 3. Preenche busca mantendo "Boards ATS" e clica em "Buscar Vagas Agora"
                for cb_id in ("#src-linkedin", "#src-gupy", "#src-open-feeds"):
                    cb = page.locator(cb_id)
                    if await cb.is_checked():
                        await cb.uncheck()
                ats_cb = page.locator("#src-ats-boards")
                if not await ats_cb.is_checked():
                    await ats_cb.check()

                await page.locator("#discover-query").fill("Python")
                await page.locator("#btn-discover-jobs").click()

                feedback = page.locator("#discover-feedback")
                await feedback.filter(has_text="1 vagas encontradas").wait_for(timeout=5000)

                # 4. Troca para aba Candidaturas e valida a vaga descoberta na tabela
                await page.locator("button.nav-tab[data-target='jobs']").click()
                row = page.locator("tbody tr", has_text="Nubank").first
                await row.wait_for(state="visible", timeout=5000)
                assert "Senior Python FastAPI Engineer" in await row.inner_text()

                # 5. Clica em "Gerar CV" e aguarda o link "CV PDF" surgir
                await row.get_by_role("button", name="Gerar CV").click()
                cv_link = row.get_by_role("link", name="CV PDF")
                await cv_link.wait_for(state="visible", timeout=8000)
                pdf_href = await cv_link.get_attribute("href")
                assert pdf_href is not None
                assert pdf_href.startswith("/api/resumes/generated/")

                # 6. Faz download real do PDF via HTTP e verifica conteúdo ATS legível
                pdf_resp = await page.request.get(f"http://127.0.0.1:{port}{pdf_href}")
                assert pdf_resp.status == 200
                assert "application/pdf" in pdf_resp.headers.get("content-type", "")
                pdf_bytes = await pdf_resp.body()
                assert pdf_bytes.startswith(b"%PDF")

                downloaded_pdf = tmp_path / "downloaded_cv.pdf"
                downloaded_pdf.write_bytes(pdf_bytes)
                extracted = extract_text(downloaded_pdf)
                assert "Carlos Drummond" in extracted
                assert "FastAPI" in extracted

                # 7. Abre modal Detalhes e confere skills em destaque + link de visualização do PDF
                await row.get_by_role("button", name="Detalhes").click()
                modal = page.locator("#modal-job-details")
                await modal.wait_for(state="visible", timeout=5000)
                modal_text = await modal.inner_text()
                assert "Visualizar PDF Gerado" in modal_text
                assert "Python" in modal_text

                assert js_errors == [], f"Erros JavaScript não tratados: {js_errors}"
                assert console_errors == [], f"Erros de console na UI: {console_errors}"
        finally:
            server.should_exit = True
            await serve_task


def _fake_results_only_from_queue(queue_file: Path) -> list[dict]:
    from core.job_search import load_discovered_jobs

    items = load_discovered_jobs(queue_file)
    entries = []
    for idx, d in enumerate(items, start=1):
        jid = str(d.get("id") or f"disc-{idx}")
        tailored_path = d.get("tailored_resume_path")
        entries.append(
            {
                "job_id": jid,
                "company": d.get("company") or "Empresa",
                "title": d.get("title") or "Cargo",
                "ats": d.get("ats") or "ATS",
                "url": d.get("job_url"),
                "status": "PENDING",
                "display_status": d.get("sheet_status") or "A Fazer",
                "resume_used": Path(tailored_path).name if tailored_path else d.get("resume_label"),
                "tailored_resume_url": (
                    f"/api/resumes/generated/{Path(tailored_path).name}" if tailored_path else None
                ),
                "matched_skills": d.get("matched_skills") or [],
                "duration_s": None,
                "tokens_used": 0,
                "reason": "Aguardando disparo",
            }
        )
    return entries


async def test_playwright_gupy_and_linkedin_handlers_real_browser(
    browser_manager: BrowserManager,
    tmp_path: Path,
) -> None:
    """Executa GupyHandler, LinkedInHandler e parsers HTML contra páginas HTML reais no Chromium."""
    async def _serve_html(html: str):
        async def _handler(_req: web.Request) -> web.Response:
            return web.Response(text=html, content_type="text/html")
        return _handler

    http_app = web.Application()
    http_app.router.add_get("/li-search", await _serve_html(_LINKEDIN_SEARCH_HTML))
    http_app.router.add_get("/gupy-search", await _serve_html(_GUPY_SEARCH_HTML))
    http_app.router.add_get("/gupy-job", await _serve_html(_GUPY_JOB_HTML))
    http_app.router.add_get("/li-easy", await _serve_html(_LINKEDIN_EASY_APPLY_HTML))

    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    profile = _e2e_profile()
    settings = Settings(
        DRY_RUN=True,
        NO_LLM=True,
        OPENROUTER_API_KEY="sk-test",
        GOOGLE_SHEETS_ID="sheet-test",
        GOOGLE_SERVICE_ACCOUNT_JSON="./sa.json",
    )
    tailor = ResumeTailor(settings=settings)
    job_model = Job(
        id="e2e-gupy-1",
        company="Stone",
        title="Desenvolvedor Backend Python",
        url=f"{base_url}/gupy-job",
        ats="Gupy",
        description="Python FastAPI PostgreSQL Docker",
    )
    spec = await tailor.build_spec(job_model, profile)
    resume_info = generate_tailored_pdf(spec, profile, tmp_path / "pdfs")

    try:
        async with browser_manager.new_page() as page:
            # 1. Parsers em HTML renderizado pelo Chromium
            await page.goto(f"{base_url}/li-search", wait_until="domcontentloaded")
            li_parsed = parse_linkedin_search_html(await page.content(), limit=5)
            assert len(li_parsed) == 1
            assert li_parsed[0]["easy_apply"] is True
            assert li_parsed[0]["company"] == "Nubank"

            await page.goto(f"{base_url}/gupy-search", wait_until="domcontentloaded")
            gupy_parsed = parse_gupy_search_html(await page.content(), limit=5)
            assert len(gupy_parsed) == 1
            assert gupy_parsed[0]["job_url"] == "https://stone.gupy.io/jobs/887766"

            # 2. GupyHandler: auth gate -> BlockerDetected; depois dry_run + live submission com PDF anexado
            gupy_handler = GupyHandler()
            gate_job = job_model.model_copy(update={"url": f"{base_url}/gupy-job?auth=1"})
            with pytest.raises(BlockerDetected):
                await gupy_handler.inspect(page, gate_job)

            gupy_info = await gupy_handler.inspect(page, job_model)
            assert any(q.id == "g-city" for q in gupy_info.questions)

            dry_res = await gupy_handler.fill_and_submit(
                page=page,
                job=job_model,
                profile=profile,
                resume=resume_info,
                answers=AnswerSet(answers={"g-city": "São Paulo"}),
                dry_run=True,
            )
            assert dry_res.status == TaskStatus.COMPLETED
            assert dry_res.dry_run is True
            assert dry_res.submitted is False
            assert await page.locator("input[name='email']").input_value() == profile.email
            assert not await page.locator("#gupy-ok").is_visible()

            live_res = await gupy_handler.fill_and_submit(
                page=page,
                job=job_model,
                profile=profile,
                resume=resume_info,
                answers=AnswerSet(answers={"g-city": "São Paulo"}),
                dry_run=False,
            )
            assert live_res.status == TaskStatus.COMPLETED
            assert live_res.submitted is True
            assert await page.locator("#gupy-ok").is_visible()

            # 3. LinkedInHandler: Easy Apply multi-step (Avançar -> Enviar candidatura) no Chromium real
            li_handler = LinkedInHandler()
            li_job = Job(
                id="e2e-li-1",
                company="Nubank",
                title="Engenheiro Python",
                url=f"{base_url}/li-easy",
                ats="LinkedIn",
            )
            li_info = await li_handler.inspect(page, li_job)
            assert "Easy Apply" in li_info.state_summary
            assert "FastAPI" in li_info.description

            li_live = await li_handler.fill_and_submit(
                page=page,
                job=li_job,
                profile=profile,
                resume=resume_info,
                answers=AnswerSet(),
                dry_run=False,
            )
            assert li_live.status == TaskStatus.COMPLETED
            assert li_live.submitted is True
            assert await page.locator("#li-done").is_visible()
    finally:
        await runner.cleanup()
