"""Testes de integração Playwright reais do navegador web genérico guiado pela Laya."""

from __future__ import annotations

import asyncio
import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from aiohttp import web
from playwright.async_api import async_playwright

import web.app as web_app
from automation.browser import BrowserManager
from automation.laya_demo import _launch_demo_browser, run_visual_demo
from automation.laya_navigator import LayaGenericHandler
from config.settings import Settings
from core.decision.layer import DecisionLayer
from core.decision.models import ChoiceVerdict, NoulVerdict
from core.matcher import Matcher
from core.models import (
    AnswerSet,
    BlockerDetected,
    Job,
    PostSubmitError,
    Profile,
    ResumeInfo,
    TaskStatus,
)
from core.pipeline import Pipeline
from web.runner import global_runner


def _build_unknown_wizard_html() -> str:
    """Landing page desconhecida -> modal CTA -> Wizard em 2 etapas sem id/name nos inputs."""
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>Carreiras | Portal Desconhecido</title></head>
<body>
  <header>
    <input type="search" aria-label="Buscar vagas no site" placeholder="Buscar...">
    <a href="#privacidade">Privacy Policy</a>
    <a href="#login">Sign In</a>
  </header>
  <main>
    <h1>Engenheira Python Sênior - Portal X</h1>
    <p>Atuação 100% remota construindo automações e serviços em FastAPI e Playwright.</p>
    <button type="button" class="_x9a" onclick="document.getElementById('appModal').style.display='block'">
      Candidatar-se para esta vaga
    </button>
  </main>

  <div id="appModal" role="dialog" aria-modal="true" style="display:none;">
    <form id="wizardForm" onsubmit="return handleSubmit(event);">
      <div id="step1">
        <h2>Etapa 1 - Dados Pessoais</h2>
        <div class="_fbox">
          <span>Como podemos te chamar? *</span>
          <input type="text" class="_in1" required>
        </div>
        <div class="_fbox">
          <span>E-mail de contato *</span>
          <input type="email" class="_in2" required>
        </div>
        <div class="_fbox">
          <span>Melhor telefone / WhatsApp *</span>
          <input type="tel" class="_in3" required>
        </div>
        <div class="_fbox">
          <span>Foto de perfil (opcional)</span>
          <input type="file" class="_photo" accept="image/*" aria-label="Foto de perfil">
        </div>
        <div class="_fbox">
          <span>Currículo (PDF) *</span>
          <input type="file" class="_cv" accept=".pdf" required>
        </div>
        <button type="button" class="_btn_next" onclick="goStep2()">Avançar para Etapa 2</button>
      </div>

      <div id="step2" style="display:none;">
        <h2>Etapa 2 - Perguntas da Vaga</h2>
        <div class="_fbox">
          <span>Senioridade desejada *</span>
          <select class="_sel1" required>
            <option value="">Selecione...</option>
            <option value="jr">Júnior</option>
            <option value="pl">Pleno</option>
            <option value="sr">Sênior</option>
          </select>
        </div>
        <fieldset>
          <legend>Possui autorização de trabalho no Brasil? *</legend>
          <label><input type="radio" name="wa_choice" value="sim" required> Sim, sem necessidade de visto</label>
          <label><input type="radio" name="wa_choice" value="nao" required> Não, preciso de patrocínio</label>
        </fieldset>
        <div class="_fbox">
          <span>Raça / Etnia</span>
          <select class="_eeo1">
            <option value="">Selecione...</option>
            <option value="Branca">Branca</option>
            <option value="Preta">Preta</option>
            <option value="Prefer not to say">Prefer not to say</option>
          </select>
        </div>
        <div class="_fbox">
          <span>Conte sobre sua experiência com Python *</span>
          <textarea class="_ta1" required></textarea>
        </div>
        <label>
          <input type="checkbox" class="_chk1" required> Declaro que li e concordo com os termos LGPD
        </label>
        <button type="submit" class="_btn_sub">Enviar Candidatura</button>
      </div>
    </form>
  </div>

  <div id="doneBox" class="confirmation-message" style="display:none;">
    Candidatura recebida com sucesso!
  </div>

  <script>
    function goStep2() {
      document.getElementById('step1').style.display = 'none';
      document.getElementById('step2').style.display = 'block';
    }
    async function handleSubmit(ev) {
      ev.preventDefault();
      const s1 = document.getElementById('step1');
      const s2 = document.getElementById('step2');
      const wa = s2.querySelector('input[name="wa_choice"]:checked');
      const photoEl = s1.querySelector('._photo');
      const cvEl = s1.querySelector('._cv');
      const payload = {
        full_name: s1.querySelector('._in1').value,
        email: s1.querySelector('._in2').value,
        phone: s1.querySelector('._in3').value,
        photo_uploaded: photoEl.files.length > 0,
        cv_name: cvEl.files.length > 0 ? cvEl.files[0].name : '',
        seniority: s2.querySelector('._sel1').selectedOptions[0]?.text || '',
        work_auth: wa ? wa.parentElement.innerText.trim() : '',
        eeo_race: s2.querySelector('._eeo1').value,
        python_exp: s2.querySelector('._ta1').value,
        consent: s2.querySelector('._chk1').checked
      };
      await fetch('/unknown-wizard/submit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      document.getElementById('appModal').style.display = 'none';
      document.getElementById('doneBox').style.display = 'block';
      return false;
    }
  </script>
</body>
</html>"""


def _build_escalation_html() -> str:
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>Seleção Ambígua</title></head>
<body>
  <main>
    <form id="escForm" onsubmit="return submitEsc(event);">
      <div class="_fbox">
        <span>Modalidade de trabalho preferida *</span>
        <select class="_mode" required>
          <option value="">Selecione...</option>
          <option value="Presencial">Presencial</option>
          <option value="Híbrido">Modelo Híbrido</option>
          <option value="Remoto">100% Remoto</option>
        </select>
      </div>
      <div class="_fbox">
        <span>E-mail de contato *</span>
        <input type="email" class="_email" required>
      </div>
      <button type="submit">Finalizar Inscrição</button>
    </form>
    <div class="confirmation-message" id="escDone" style="display:none;">Inscrição enviada!</div>
  </main>
  <script>
    async function submitEsc(ev) {
      ev.preventDefault();
      const payload = {
        mode: document.querySelector('._mode').value,
        email: document.querySelector('._email').value
      };
      await fetch('/unknown-escalation/submit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      document.getElementById('escDone').style.display = 'block';
      return false;
    }
  </script>
</body>
</html>"""


def _build_missing_req_html() -> str:
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>Conselho Profissional</title></head>
<body>
  <main>
    <form onsubmit="return false;">
      <div class="_fbox">
        <span>E-mail *</span>
        <input type="email" required>
      </div>
      <div class="_fbox">
        <span>Número do registro no conselho profissional CREA *</span>
        <input type="text" required>
      </div>
      <button type="submit">Enviar Candidatura</button>
    </form>
  </main>
</body>
</html>"""


def _build_no_confirm_html() -> str:
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="utf-8"><title>Sem Confirmação</title></head>
<body>
  <main>
    <form onsubmit="return false;">
      <div class="_fbox">
        <span>E-mail *</span>
        <input type="email" required>
      </div>
      <button type="submit">Enviar Candidatura</button>
    </form>
  </main>
</body>
</html>"""


class _SemanticLayaModel:
    """DecisionModel realista para testes E2E: responde noul/choice semanticamente e força incerteza quando pedido."""

    def __init__(self, uncertain_select: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.uncertain_select = uncertain_select

    async def noul(self, state: dict[str, str], instructions: str, criteria=None) -> NoulVerdict:
        self.calls.append({"type": "noul", "state": state, "instructions": instructions})
        inst = instructions.lower()
        if "bloquead" in inst or "captcha" in inst:
            return NoulVerdict(p_true=0.05, band="no")
        if "sensíve" in inst or "sensitive" in inst or "eeo" in inst:
            return NoulVerdict(p_true=0.05, band="no")
        if "preenchido" in inst or "pronto" in inst or "envio" in inst:
            ready = state.get("missing_required") == "none" and state.get("visible_errors") == "none"
            return NoulVerdict(p_true=0.92 if ready else 0.08, band="yes" if ready else "no")
        if "compatível" in inst or "requisito" in inst:
            return NoulVerdict(p_true=0.92, band="yes")
        fields = state.get("fields", "").lower()
        modal = state.get("modal", "no")
        has_form = bool(fields and ("chamar" in fields or "email" in fields or "telefone" in fields or modal == "yes"))
        return NoulVerdict(p_true=0.92 if has_form else 0.08, band="yes" if has_form else "no")

    async def choice(self, state: dict[str, str], instructions: str, options: dict[str, str]) -> ChoiceVerdict:
        self.calls.append({"type": "choice", "state": state, "instructions": instructions, "options": options})
        inst = instructions.lower()

        if self.uncertain_select and "opção" in inst:
            first_key = next(iter(options))
            return ChoiceVerdict(
                probs={k: 1.0 / len(options) for k in options},
                top=first_key,
                margin=0.02,
                order_consistent=False,
            )

        best_key = next(iter(options))
        label_ctx = state.get("label", "").lower()

        if "perfil corresponde" in inst:
            if "chamar" in label_ctx or "nome" in label_ctx:
                best_key = "full_name" if "full_name" in options else "custom_question"
            elif "telefone" in label_ctx or "whatsapp" in label_ctx:
                best_key = "phone" if "phone" in options else "custom_question"
            elif "mail" in label_ctx:
                best_key = "email" if "email" in options else "custom_question"
            else:
                best_key = "custom_question"
        elif "abrir ou iniciar" in inst:
            for k, v in options.items():
                if "candidatar" in v.lower():
                    best_key = k
                    break
        elif "avançar" in inst:
            for k, v in options.items():
                if "avançar" in v.lower() or "etapa 2" in v.lower():
                    best_key = k
                    break
        elif "enviar finalmente" in inst:
            for k, v in options.items():
                if "enviar" in v.lower() or "finalizar" in v.lower():
                    best_key = k
                    break
        elif "opção mais compatível" in inst:
            q_txt = state.get("question", "").lower()
            fact_txt = state.get("fact", "").lower()
            if "senioridade" in q_txt:
                for k, v in options.items():
                    if "pleno" in v.lower():
                        best_key = k
                        break
            else:
                for k, v in options.items():
                    if any(tok in v.lower() for tok in fact_txt.split() if len(tok) > 2):
                        best_key = k
                        break

        return ChoiceVerdict(
            probs={k: (0.88 if k == best_key else 0.02) for k in options},
            top=best_key,
            margin=0.86,
            order_consistent=True,
        )


class _ControlledMatcher(Matcher):
    """Matcher controlado offline para validar respostas customizadas e escalação System 2."""

    def __init__(self, settings: Settings, picked_choice: str = "opt_2") -> None:
        super().__init__(settings=settings)
        self.picked_choice = picked_choice
        self.llm_choice_calls = 0
        self.question_calls = 0

    async def _ask_json(self, messages, parse):
        self.llm_choice_calls += 1
        return parse({"choice": self.picked_choice})

    async def answer_questions(self, job, questions, profile, resume_text="") -> AnswerSet:
        self.question_calls += 1
        out: dict[str, str | None] = {}
        for q in questions:
            norm = q.label.lower()
            if "senioridade" in norm:
                out[q.id] = "Pleno"
            elif "python" in norm:
                out[q.id] = "6 anos desenvolvendo APIs FastAPI, automação web com Playwright e testes."
            else:
                out[q.id] = None
        return AnswerSet(answers=out)


SUBMISSIONS_KEY: web.AppKey[dict[str, list[dict[str, Any]]]] = web.AppKey("submissions", dict)


@pytest.fixture
async def portal_server():
    """Servidor HTTP local com páginas desconhecidas (sem seletores de ATS conhecidos)."""
    submissions: dict[str, list[dict[str, Any]]] = {"wizard": [], "escalation": []}

    async def wizard_get(request: web.Request) -> web.Response:
        return web.Response(text=_build_unknown_wizard_html(), content_type="text/html")

    async def wizard_post(request: web.Request) -> web.Response:
        submissions["wizard"].append(await request.json())
        return web.json_response({"ok": True})

    async def esc_get(request: web.Request) -> web.Response:
        return web.Response(text=_build_escalation_html(), content_type="text/html")

    async def esc_post(request: web.Request) -> web.Response:
        submissions["escalation"].append(await request.json())
        return web.json_response({"ok": True})

    async def missing_get(request: web.Request) -> web.Response:
        return web.Response(text=_build_missing_req_html(), content_type="text/html")

    async def captcha_get(request: web.Request) -> web.Response:
        html = (
            "<html><body><h1>Verificação de segurança</h1>"
            "<div class='g-recaptcha' data-sitekey='mock-key'>Please solve the reCAPTCHA before applying</div>"
            "</body></html>"
        )
        return web.Response(text=html, content_type="text/html")

    async def no_confirm_get(request: web.Request) -> web.Response:
        return web.Response(text=_build_no_confirm_html(), content_type="text/html")

    app = web.Application()
    app[SUBMISSIONS_KEY] = submissions
    app.router.add_get("/unknown-wizard/job-1", wizard_get)
    app.router.add_post("/unknown-wizard/submit", wizard_post)
    app.router.add_get("/unknown-escalation", esc_get)
    app.router.add_post("/unknown-escalation/submit", esc_post)
    app.router.add_get("/unknown-missing-req", missing_get)
    app.router.add_get("/unknown-captcha", captcha_get)
    app.router.add_get("/unknown-no-confirm", no_confirm_get)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url, submissions
    finally:
        await runner.cleanup()


def _sample_profile() -> Profile:
    return Profile(
        full_name="Ana Silva",
        email="ana.silva@example.com",
        phone="+55 11 99999-0000",
        location="São Paulo, SP",
        links={"linkedin": "https://linkedin.com/in/anasilva"},
        work_authorization="Sim, cidadã brasileira sem visto",
        skills=["Python", "FastAPI", "Playwright"],
        eeo={"race": "Prefer not to say", "gender": "Prefer not to say"},
    )


def _sample_resume(pdf_path: Path) -> ResumeInfo:
    return ResumeInfo(
        path=pdf_path,
        summary="Engenheira Python Sênior com FastAPI e Playwright.",
        text="Ana Silva - 6 anos de experiência em Python, FastAPI e Playwright.",
    )


@pytest.mark.asyncio
async def test_e2e_playwright_unknown_multistep_wizard_dry_and_submit(
    browser_manager: BrowserManager,
    portal_server: tuple[str, dict[str, list[dict[str, Any]]]],
    test_settings: Settings,
    sample_resume_pdf: Path,
) -> None:
    base_url, submissions = portal_server
    test_settings.NO_LLM = False
    profile = _sample_profile()
    resume = _sample_resume(sample_resume_pdf)
    model = _SemanticLayaModel()
    matcher = _ControlledMatcher(test_settings)
    handler = LayaGenericHandler(decision_model=model, matcher=matcher, settings=test_settings)
    job = Job(
        id="e2e_wiz",
        row_index=2,
        company="Portal X",
        title="Engenheira Python Sênior",
        url=f"{base_url}/unknown-wizard/job-1",
        ats="unknown",
        language="pt",
    )

    # 1. Dry-run: descobre modal CTA -> Etapa 1 -> avança Etapa 2 -> preenche e para antes do submit
    async with browser_manager.new_page() as page:
        page_info = await handler.inspect(page, job)
        assert "Portal X" in page_info.description or "FastAPI" in page_info.description
        assert any("chamar" in q.label.lower() for q in page_info.questions)

        answers = await matcher.answer_questions(job, page_info.questions, profile, resume.text)
        dry_res = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=profile,
            resume=resume,
            answers=answers,
            dry_run=True,
        )
        assert dry_res.status == TaskStatus.COMPLETED
        assert dry_res.dry_run is True
        assert dry_res.submitted is False
        assert len(submissions["wizard"]) == 0
        assert handler.navigator.stats.laya_decisions > 0

    # 2. Submissão real (dry_run=False): envia e valida todos os campos + EEO isolation + foto ignorada
    async with browser_manager.new_page() as page:
        page_info = await handler.inspect(page, job)
        answers = await matcher.answer_questions(job, page_info.questions, profile, resume.text)
        submit_res = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=profile,
            resume=resume,
            answers=answers,
            dry_run=False,
        )
        assert submit_res.status == TaskStatus.COMPLETED
        assert submit_res.submitted is True
        assert len(submissions["wizard"]) == 1

        sub = submissions["wizard"][0]
        assert sub["full_name"] == "Ana Silva"
        assert sub["email"] == "ana.silva@example.com"
        assert sub["phone"] == "+55 11 99999-0000"
        assert sub["photo_uploaded"] is False
        assert sub["cv_name"] == sample_resume_pdf.name
        assert sub["seniority"] == "Pleno"
        assert "sim" in sub["work_auth"].lower()
        assert sub["eeo_race"] == "Prefer not to say"
        assert "FastAPI" in sub["python_exp"]
        assert sub["consent"] is True

        # Invariante EEO: nenhum prompt enviado à Laya menciona "raça" ou "etnia"
        all_prompts = json.dumps(model.calls, ensure_ascii=False).lower()
        assert not re.search(r"ra[çc]a|etnia", all_prompts)


@pytest.mark.asyncio
async def test_e2e_playwright_system1_to_system2_escalation(
    browser_manager: BrowserManager,
    portal_server: tuple[str, dict[str, list[dict[str, Any]]]],
    test_settings: Settings,
    sample_resume_pdf: Path,
) -> None:
    base_url, submissions = portal_server
    test_settings.NO_LLM = False
    profile = _sample_profile()
    resume = _sample_resume(sample_resume_pdf)
    model = _SemanticLayaModel(uncertain_select=True)
    # No select Modalidade: options 0="Presencial", 1="Modelo Híbrido", 2="100% Remoto" -> escolhe opt_2
    matcher = _ControlledMatcher(test_settings, picked_choice="opt_2")
    handler = LayaGenericHandler(decision_model=model, matcher=matcher, settings=test_settings)
    job = Job(
        id="e2e_esc",
        row_index=3,
        company="Portal Esc",
        title="Dev Backend",
        url=f"{base_url}/unknown-escalation",
        ats="unknown",
    )

    async with browser_manager.new_page() as page:
        await handler.inspect(page, job)
        res = await handler.fill_and_submit(
            page=page,
            job=job,
            profile=profile,
            resume=resume,
            answers=AnswerSet(),
            dry_run=False,
        )
        assert res.status == TaskStatus.COMPLETED
        assert res.submitted is True
        assert handler.navigator.stats.llm_escalations >= 1
        assert matcher.llm_choice_calls >= 1
        assert submissions["escalation"][0]["mode"] == "Remoto"


@pytest.mark.asyncio
async def test_e2e_playwright_pipeline_full_run_and_safety_guards(
    browser_manager: BrowserManager,
    portal_server: tuple[str, dict[str, list[dict[str, Any]]]],
    test_settings: Settings,
    sample_resume_pdf: Path,
) -> None:
    base_url, submissions = portal_server
    test_settings.NO_LLM = False
    profile = _sample_profile()
    resume = _sample_resume(sample_resume_pdf)

    class _LocalResolver:
        async def resolve(self, _job):
            return resume

        def get_base_resumes(self, _used):
            return [resume]

        def get_resumes(self):
            return {"default": resume}

    # 1. Pipeline completo no wizard desconhecido com LAYA_WEB_NAVIGATOR_ENABLED=True
    test_settings.LAYA_WEB_NAVIGATOR_ENABLED = True
    test_settings.DRY_RUN = False
    test_settings.NO_LLM = False
    model = _SemanticLayaModel()
    decision_layer = DecisionLayer(model=model, settings=test_settings)
    matcher = _ControlledMatcher(test_settings)
    pipeline = Pipeline(
        settings=test_settings,
        decision_layer=decision_layer,
        resume_resolver=_LocalResolver(),  # type: ignore[arg-type]
        browser_manager=browser_manager,
        matcher=matcher,
    )

    job_wizard = Job(
        id="pipe_wiz",
        row_index=4,
        company="Portal X",
        title="Engenheira Python",
        url=f"{base_url}/unknown-wizard/job-1",
        ats="unknown",
        fit_score=95,
    )
    submissions["wizard"].clear()
    res_wiz = await pipeline.process_job(job_wizard, profile)
    assert res_wiz.status == TaskStatus.COMPLETED
    assert res_wiz.submitted is True
    assert len(submissions["wizard"]) == 1

    # 2. Zero-Guess com AUTONOMOUS_MODE=False e NO_LLM=True -> NEEDS_REVIEW em campo obrigatório desconhecido
    test_settings.NO_LLM = True
    test_settings.AUTONOMOUS_MODE = False
    job_missing = Job(
        id="pipe_miss",
        row_index=5,
        company="Conselho X",
        title="Engenheira Civil",
        url=f"{base_url}/unknown-missing-req",
        ats="unknown",
        fit_score=95,
    )
    res_miss = await pipeline.process_job(job_missing, profile)
    assert res_miss.status == TaskStatus.NEEDS_REVIEW
    assert "crea" in (res_miss.reason or "").lower()

    # 3. BlockerDetected (CAPTCHA) e PostSubmitError diretamente no handler genérico
    handler = LayaGenericHandler(decision_model=model, matcher=matcher, settings=test_settings)
    async with browser_manager.new_page() as page:
        with pytest.raises(BlockerDetected):
            await handler.inspect(
                page,
                Job(id="cap", row_index=6, company="Cap", title="Dev", url=f"{base_url}/unknown-captcha"),
            )

    async with browser_manager.new_page() as page:
        no_conf_job = Job(
            id="noconf",
            row_index=7,
            company="NoConf",
            title="Dev",
            url=f"{base_url}/unknown-no-confirm",
        )
        await handler.inspect(page, no_conf_job)
        with pytest.raises(PostSubmitError):
            await handler.fill_and_submit(
                page=page,
                job=no_conf_job,
                profile=profile,
                resume=resume,
                answers=AnswerSet(),
                dry_run=False,
            )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_laya_visual_demo_sandbox_e2e_streams_frames_and_submits() -> None:
    events: list[dict[str, Any]] = []
    summary = await run_visual_demo(
        on_visual_event=events.append,
        mode="demo",
        headless=True,
        delay_ms=0,
        dry_run=False,
    )

    assert summary["submitted"] is True
    assert summary["status"] == "COMPLETED"
    assert summary["s1"] >= 2
    assert summary["s2"] >= 1
    assert summary["eeo"] >= 1

    payload = summary["payload"]
    assert payload is not None
    assert payload["nome"] == "Ana Clara Mendes"
    assert payload["senioridade"] == "Pleno"
    assert payload["modalidade"] == "100% Remoto"
    assert "Sim" in payload["autorizacao_trabalho"]
    assert payload["eeo_raca"] == "Parda"
    assert payload["curriculo_pdf"] == "curriculo_demo_laya.pdf"
    assert payload["foto_enviada"] is False
    assert payload["lgpd_aceito"] is True

    kinds = {e["kind"] for e in events}
    assert {"laya", "llm", "eeo", "action"}.issubset(kinds)
    frames = [e["frame_bytes"] for e in events if e.get("frame_bytes")]
    assert len(frames) >= 5
    assert all(bytes(frame).startswith(b"\xff\xd8\xff") for frame in frames)


@pytest.mark.asyncio
async def test_web_dashboard_renders_laya_live_panel_and_streams_demo_in_playwright() -> None:
    port = _find_free_port()
    config = uvicorn.Config(web_app.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    page_errors: list[str] = []

    try:
        for _ in range(80):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started

        async with async_playwright() as pw:
            browser = await _launch_demo_browser(pw, headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1366, "height": 900})
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                await page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                await page.wait_for_selector("#laya-live-card", state="visible", timeout=10000)
                await page.wait_for_selector("#btn-laya-demo", state="visible", timeout=10000)
                await page.wait_for_selector("#laya-real-job-select", state="visible", timeout=10000)
                await page.wait_for_selector("#laya-real-url-input", state="visible", timeout=10000)
                await page.wait_for_selector("#laya-real-dryrun", state="visible", timeout=10000)
                await page.wait_for_selector("#btn-laya-real-run", state="visible", timeout=10000)

                # Verifica controles no modal de execução
                await page.click("#btn-start-automation")
                await page.wait_for_selector("#run-opt-laya-navigator", state="visible", timeout=5000)
                await page.wait_for_selector("#run-opt-laya-visual", state="visible", timeout=5000)
                await page.wait_for_selector("#run-opt-headed", state="visible", timeout=5000)
                await page.evaluate("closeRunModal()")

                # Verifica controles na aba de configurações
                await page.evaluate("switchTab('config')")
                await page.wait_for_selector("#cfg-laya-navigator", state="visible", timeout=5000)
                await page.wait_for_selector("#cfg-laya-visual", state="visible", timeout=5000)
                await page.wait_for_selector("#cfg-laya-visual-delay", state="visible", timeout=5000)

                # Volta ao Dashboard e dispara a demonstração visual ao vivo via botão real
                await page.evaluate("switchTab('dashboard')")
                await page.click("#btn-laya-demo")

                await page.wait_for_selector(
                    "#laya-live-img[src*='/api/laya/live-frame']",
                    state="visible",
                    timeout=25000,
                )
                await page.wait_for_function(
                    """() => {
                        const s1 = parseInt(document.getElementById('laya-live-s1')?.textContent || '0', 10);
                        const s2 = parseInt(document.getElementById('laya-live-s2')?.textContent || '0', 10);
                        const eeo = parseInt(document.getElementById('laya-live-eeo')?.textContent || '0', 10);
                        const feed = document.getElementById('laya-live-feed')?.innerText || '';
                        return s1 >= 2 && s2 >= 1 && eeo >= 1 && feed.includes('CANDIDATURA ENVIADA NO SANDBOX');
                    }""",
                    timeout=35000,
                )

                img_ok = await page.evaluate(
                    """() => {
                        const img = document.getElementById('laya-live-img');
                        return Boolean(img && img.complete && img.naturalWidth > 200);
                    }"""
                )
                assert img_ok is True
                assert page_errors == []
            finally:
                await browser.close()
        assert global_runner.laya_latest_frame_bytes is not None
    finally:
        if global_runner.is_running:
            await global_runner.stop()
        global_runner.reset_laya_live(active=False)
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10.0)


@pytest.mark.asyncio
async def test_real_pipeline_classic_ats_dry_run_streams_to_dashboard_e2e(
    browser_manager: BrowserManager,
    test_settings: Settings,
    sample_resume_pdf: Path,
) -> None:
    """Valida dry-run real via Pipeline + LeverHandler emitindo eventos e JPEGs ao vivo."""
    from web.runner import HarnessRunner

    submitted_posts: list[dict[str, Any]] = []

    async def _lever_job(_request: web.Request) -> web.Response:
        html = """<!DOCTYPE html><html><body>
        <div class="posting-description">Engenheira Python & IA na Acme.</div>
        <a class="postings-btn" href="/lever-apply">Apply for this job</a>
        </body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def _lever_apply(_request: web.Request) -> web.Response:
        html = """<!DOCTYPE html><html><body>
        <form id="application-form" action="/lever-submit" method="post">
          <input type="text" name="name" required>
          <input type="email" name="email" required>
          <input type="tel" name="phone" required>
          <input type="file" name="resume" accept=".pdf" required>
          <button id="btn-submit" type="submit">Submit application</button>
        </form>
        </body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def _lever_submit(request: web.Request) -> web.Response:
        submitted_posts.append({"body": await request.text()})
        return web.Response(text='<div class="application-success">OK</div>', content_type="text/html")

    app = web.Application()
    app.router.add_get("/lever-job", _lever_job)
    app.router.add_get("/lever-apply", _lever_apply)
    app.router.add_post("/lever-submit", _lever_submit)
    runner_srv = web.AppRunner(app)
    await runner_srv.setup()
    site = web.TCPSite(runner_srv, "127.0.0.1", 0)
    await site.start()
    port = int(site._server.sockets[0].getsockname()[1])  # type: ignore[union-attr]

    try:
        profile = _sample_profile()
        resume = _sample_resume(sample_resume_pdf)

        class _StaticResolver:
            async def resolve(self, _job: Job) -> ResumeInfo:
                return resume

            def get_base_resumes(self, _used: list[str]) -> list[ResumeInfo]:
                return [resume]

            def get_resumes(self) -> dict[str, ResumeInfo]:
                return {"default": resume}

        test_settings.DRY_RUN = True
        test_settings.LAYA_VISUAL_WATCH = True
        test_settings.LAYA_VISUAL_DELAY_MS = 0
        test_settings.LAYA_WEB_NAVIGATOR_ENABLED = True
        test_settings.NO_LLM = True

        live_runner = HarnessRunner()
        pipeline = Pipeline(
            settings=test_settings,
            decision_layer=DecisionLayer(model=_SemanticLayaModel(), settings=test_settings),
            resume_resolver=_StaticResolver(),  # type: ignore[arg-type]
            browser_manager=browser_manager,
            matcher=_ControlledMatcher(test_settings),
            on_laya_event=live_runner.emit_laya_event,
        )
        job = Job(
            id="real_lever_dry",
            row_index=1,
            company="Acme",
            title="Engenheira IA",
            url=f"http://127.0.0.1:{port}/lever-job",
            ats="lever",
            fit_score=95,
        )
        res = await pipeline.process_job(job, profile)

        assert res.status == TaskStatus.COMPLETED
        assert res.dry_run is True
        assert res.submitted is False
        assert submitted_posts == []

        live_state = live_runner.get_status()["laya_live"]
        titles = [ev["title"] for ev in live_state["events"]]
        assert "NAVEGAÇÃO · PÁGINA ABERTA" in titles
        assert "AÇÃO · PREENCHER" in titles
        assert "AÇÃO · UPLOAD PDF" in titles
        assert any(t.startswith("CANDIDATURA · DRY-RUN") for t in titles)
        assert live_state["frame_seq"] >= 4
        assert live_runner.laya_latest_frame_bytes is not None
        assert live_runner.laya_latest_frame_bytes.startswith(b"\xff\xd8\xff")
    finally:
        await runner_srv.cleanup()


