"""Sandbox visual interno do Laya Web Navigator para demonstração e streaming ao vivo."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from aiohttp import web
from playwright.async_api import Browser, async_playwright

from automation.laya_navigator import LayaGenericHandler
from config.settings import Settings, load_settings
from core.decision.laya_client import DecisionModel, LayaSidecarClient
from core.decision.laya_service import is_laya_healthy
from core.decision.models import ChoiceVerdict, NoulVerdict
from core.matcher import Matcher
from core.models import AnswerSet, Job, Profile, ResumeInfo

DEMO_PORTAL_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>TechNova Carreiras — Engenheira de Automação & IA</title>
  <style>
    * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
    body { margin: 0; background: #0f172a; color: #e2e8f0; padding-right: 372px; }
    header {
      background: #1e293b; padding: 14px 24px; display: flex; gap: 14px;
      align-items: center; border-bottom: 1px solid #334155; flex-wrap: wrap;
    }
    header .brand { font-weight: 700; color: #38bdf8; font-size: 17px; margin-right: auto; }
    header input[type="search"] {
      padding: 7px 12px; border-radius: 6px; border: 1px solid #475569;
      background: #0f172a; color: #e2e8f0; width: 210px;
    }
    header a, .noise-btn {
      color: #94a3b8; text-decoration: none; font-size: 12px; padding: 6px 10px;
      border-radius: 6px; border: 1px solid transparent; background: transparent; cursor: pointer;
    }
    header a:hover, .noise-btn:hover { background: #334155; color: #fff; }
    main { max-width: 760px; margin: 28px auto; padding: 0 22px; }
    .card {
      background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 24px;
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
    }
    .tag {
      display: inline-block; background: rgba(56, 189, 248, 0.15); color: #38bdf8;
      padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
    }
    h1 { margin: 12px 0 8px; font-size: 24px; color: #f8fafc; }
    p { line-height: 1.55; color: #cbd5e1; font-size: 14px; }
    .actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }
    ._tn_cta {
      background: linear-gradient(135deg, #06b6d4, #2563eb); color: #fff; border: none;
      padding: 12px 20px; border-radius: 10px; font-size: 14px; font-weight: 600; cursor: pointer;
    }
    ._sec_btn {
      background: #334155; color: #e2e8f0; border: 1px solid #475569;
      padding: 11px 16px; border-radius: 10px; font-size: 13px; cursor: pointer;
    }
    .dialog-wrap {
      display: none; position: fixed; inset: 0 368px 0 0; background: rgba(2, 6, 23, 0.8);
      align-items: center; justify-content: center; padding: 20px; z-index: 1000;
    }
    .dialog-card {
      background: #1e293b; border: 1px solid #475569; border-radius: 14px;
      width: 100%; max-width: 600px; max-height: 92vh; overflow-y: auto; padding: 22px;
    }
    ._fbox { margin-bottom: 12px; }
    ._fbox span { display: block; font-size: 12px; color: #cbd5e1; margin-bottom: 5px; font-weight: 600; }
    ._fbox input[type="text"], ._fbox input[type="email"], ._fbox input[type="tel"],
    ._fbox select, ._fbox textarea {
      width: 100%; padding: 9px 11px; border-radius: 8px; border: 1px solid #475569;
      background: #0f172a; color: #f8fafc; font-size: 13px;
    }
    fieldset { border: 1px solid #334155; border-radius: 8px; padding: 10px; margin-bottom: 12px; }
    legend { font-size: 12px; font-weight: 600; color: #cbd5e1; padding: 0 6px; }
    fieldset label { display: block; margin: 5px 0; font-size: 13px; cursor: pointer; }
    .confirmation-message {
      display: none; background: rgba(34, 197, 94, 0.12); border: 1px solid #22c55e;
      border-radius: 12px; padding: 20px; margin-top: 20px;
    }
    .confirmation-message h2 { color: #4ade80; margin-top: 0; font-size: 18px; }
    pre { background: #020617; padding: 10px; border-radius: 8px; font-size: 11px; overflow-x: auto; color: #93c5fd; }
  </style>
</head>
<body>
  <header>
    <div class="brand">TechNova Careers · Sandbox Semântico</div>
    <input type="search" aria-label="Buscar vagas" placeholder="Buscar outras vagas...">
    <a href="#privacidade">Privacy Policy</a>
    <a href="#login">Sign In</a>
    <button type="button" class="noise-btn" onclick="this.innerText='Cookies aceitos'">Aceitar Cookies</button>
  </header>

  <main>
    <div class="card" id="jobCard">
      <span class="tag">Remoto · Brasil · Sem seletores fixos de ATS</span>
      <h1>Engenheira de Automação &amp; IA</h1>
      <p>
        Portal de demonstração criado sem <code>id</code> nem <code>name</code> fixos nos campos,
        com botões distratores no topo e formulário multi-etapas em modal dinâmico.
      </p>
      <p><strong>Stack:</strong> Python, FastAPI, Playwright, Laya System 1 + System 2 + Escudo EEO.</p>
      <div class="actions">
        <button type="button" class="_sec_btn" onclick="this.innerText='Vaga salva ★'">Salvar Vaga</button>
        <button type="button" class="_sec_btn" onclick="this.innerText='Link copiado'">Compartilhar</button>
        <button type="button" class="_tn_cta" onclick="openAppModal()">Candidatar-se para esta vaga</button>
      </div>
    </div>

    <div id="doneBox" class="confirmation-message">
      <h2>✓ Candidatura enviada e confirmada no sandbox!</h2>
      <p>Payload recebido localmente (campos preenchidos por Laya System 1 + System 2 + Escudo EEO):</p>
      <pre id="payloadOut"></pre>
    </div>
  </main>

  <div id="appModal" class="dialog-wrap" role="dialog" aria-modal="true">
    <div class="dialog-card">
      <form id="sandboxForm" onsubmit="return submitDemo(event);">
        <div id="step1">
          <h2>Etapa 1 de 2 — Dados de Contato &amp; Currículo</h2>
          <div class="_fbox">
            <span>Como podemos te chamar? *</span>
            <input type="text" class="_f1" required>
          </div>
          <div class="_fbox">
            <span>E-mail de contato *</span>
            <input type="email" class="_f2" required>
          </div>
          <div class="_fbox">
            <span>Melhor telefone / WhatsApp *</span>
            <input type="tel" class="_f3" required>
          </div>
          <div class="_fbox">
            <span>Foto de perfil (opcional — ignorada para currículo)</span>
            <input type="file" class="_photo" accept="image/*" aria-label="Foto de perfil">
          </div>
          <div class="_fbox">
            <span>Currículo (PDF) *</span>
            <input type="file" class="_cv" accept=".pdf" required>
          </div>
          <div class="actions">
            <button type="button" class="_sec_btn" onclick="closeAppModal()">Cancelar</button>
            <button type="button" class="_tn_cta" onclick="goStep2()">Avançar para Etapa 2 →</button>
          </div>
        </div>

        <div id="step2" style="display:none;">
          <h2>Etapa 2 de 2 — Perguntas Técnicas &amp; Compliance</h2>
          <div class="_fbox">
            <span>Senioridade desejada *</span>
            <select class="_sel1" required>
              <option value="">Selecione...</option>
              <option value="jr">Júnior</option>
              <option value="pl">Pleno</option>
              <option value="sr">Sênior</option>
            </select>
          </div>
          <div class="_fbox">
            <span>Modalidade preferida *</span>
            <select class="_sel2" required>
              <option value="">Selecione...</option>
              <option value="Presencial">Presencial</option>
              <option value="Híbrido">Modelo Híbrido</option>
              <option value="Remoto">100% Remoto</option>
            </select>
          </div>
          <fieldset>
            <legend>Possui autorização de trabalho no Brasil? *</legend>
            <label><input type="radio" name="work_auth" value="Sim"> Sim, sem necessidade de visto</label>
            <label><input type="radio" name="work_auth" value="Não"> Não, preciso de patrocínio</label>
          </fieldset>
          <div class="_fbox">
            <span>Raça / Etnia (Escudo EEO/LGPD — nunca sai para Laya/LLM)</span>
            <select class="_eeo">
              <option value="">Selecione...</option>
              <option value="Branca">Branca</option>
              <option value="Preta">Preta</option>
              <option value="Parda">Parda</option>
              <option value="Prefer not to say">Prefer not to say</option>
            </select>
          </div>
          <div class="_fbox">
            <span>Conte sobre sua experiência com Python e automação *</span>
            <textarea class="_ta1" rows="3" required></textarea>
          </div>
          <label style="display:flex; gap:8px; align-items:center; margin-bottom:14px; font-size:13px;">
            <input type="checkbox" class="_chk" required> Declaro que li e concordo com os termos LGPD *
          </label>
          <div class="actions">
            <button type="submit" class="_tn_cta">Enviar Candidatura</button>
          </div>
        </div>
      </form>
    </div>
  </div>

  <script>
    function openAppModal() { document.getElementById('appModal').style.display = 'flex'; }
    function closeAppModal() { document.getElementById('appModal').style.display = 'none'; }
    function goStep2() {
      document.getElementById('step1').style.display = 'none';
      document.getElementById('step2').style.display = 'block';
    }
    async function submitDemo(ev) {
      ev.preventDefault();
      const s1 = document.getElementById('step1');
      const s2 = document.getElementById('step2');
      const wa = s2.querySelector('input[name="work_auth"]:checked');
      const cvEl = s1.querySelector('._cv');
      const photoEl = s1.querySelector('._photo');
      const payload = {
        nome: s1.querySelector('._f1').value,
        email: s1.querySelector('._f2').value,
        telefone: s1.querySelector('._f3').value,
        foto_enviada: photoEl.files.length > 0,
        curriculo_pdf: cvEl.files.length > 0 ? cvEl.files[0].name : '',
        senioridade: s2.querySelector('._sel1').selectedOptions[0]?.text || '',
        modalidade: s2.querySelector('._sel2').selectedOptions[0]?.text || '',
        autorizacao_trabalho: wa ? wa.parentElement.innerText.trim() : '',
        eeo_raca: s2.querySelector('._eeo').value,
        experiencia_python: s2.querySelector('._ta1').value,
        lgpd_aceito: s2.querySelector('._chk').checked
      };
      await fetch('/submit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      closeAppModal();
      document.getElementById('payloadOut').textContent = JSON.stringify(payload, null, 2);
      document.getElementById('doneBox').style.display = 'block';
      return false;
    }
  </script>
</body>
</html>"""

_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] >>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<< /Size 4 /Root 1 0 R >>\nstartxref\n183\n%%EOF\n"
)


class DemoLayaModel:
    """Modelo System 1 calibrado para demonstrar Laya + escalação System 2 sem depender de GPU/sidecar."""

    async def noul(
        self,
        state: dict[str, str],
        instructions: str,
        criteria: dict[str, str] | None = None,
    ) -> NoulVerdict:
        fields = state.get("fields", "").lower()
        modal = state.get("modal", "no")
        has_form = bool(
            fields
            and ("chamar" in fields or "email" in fields or "telefone" in fields or modal == "yes")
        )
        return NoulVerdict(p_true=0.93 if has_form else 0.06, band="yes" if has_form else "no")

    async def choice(
        self,
        state: dict[str, str],
        instructions: str,
        options: dict[str, str],
    ) -> ChoiceVerdict:
        inst = instructions.lower()
        q_txt = state.get("question", "").lower()

        # Demonstra escalação System 1 -> System 2 quando a pergunta envolve nuance ("Modalidade preferida")
        if "opção" in inst and "modalidade" in q_txt:
            first_key = next(iter(options))
            return ChoiceVerdict(
                probs={k: 1.0 / len(options) for k in options},
                top=first_key,
                margin=0.04,
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
                if "enviar" in v.lower():
                    best_key = k
                    break
        elif "opção mais compatível" in inst:
            for k, v in options.items():
                if "pleno" in v.lower() or "sim" in v.lower():
                    best_key = k
                    break

        return ChoiceVerdict(
            probs={k: (0.91 if k == best_key else 0.02) for k in options},
            top=best_key,
            margin=0.89,
            order_consistent=True,
        )


class DemoMatcher(Matcher):
    """System 2 demonstrativo offline para respostas dissertativas e desempates escalados."""

    async def _ask_json(self, messages: Any, parse: Callable[[Any], Any], **_kwargs: Any) -> Any:
        content = json.dumps(messages, ensure_ascii=False)
        for line in content.splitlines():
            if "remoto" in line.lower() and ":" in line:
                key = line.split(":", 1)[0].strip(" -\"'")
                if key.startswith("opt_"):
                    return parse({"choice": key})
        return parse({"choice": "opt_2"})

    async def answer_questions(
        self,
        job: Job | str,
        questions: list[Any],
        profile: Profile,
        resume_text: str = "",
    ) -> AnswerSet:
        out: dict[str, str | None] = {}
        for q in questions:
            norm = q.label.lower()
            if "senioridade" in norm:
                out[q.id] = "Pleno"
            elif "python" in norm:
                out[q.id] = "Experiência prática construindo agentes web com Python, FastAPI e Playwright."
            else:
                out[q.id] = None
        return AnswerSet(answers=out)


def _safe_load_settings(settings: Settings | None) -> Settings:
    if settings is not None:
        return settings
    try:
        return load_settings()
    except Exception:
        return Settings()


def _prepare_demo_pdf(tmp_dir: str) -> Path:
    pdf_path = Path(tmp_dir) / "curriculo_demo_laya.pdf"
    pdf_path.write_bytes(_MINIMAL_PDF)
    return pdf_path


async def _launch_demo_browser(pw: Any, *, headless: bool) -> Browser:
    last_exc: Exception | None = None
    for channel in (None, "chrome", "msedge"):
        try:
            launch_kwargs: dict[str, Any] = {"headless": headless}
            if channel is not None:
                launch_kwargs["channel"] = channel
            return await pw.chromium.launch(**launch_kwargs)
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"Nenhum navegador Chromium disponível para o sandbox Laya: {last_exc}")


async def _ready_ok() -> bool:
    return True


async def run_visual_demo(
    *,
    on_visual_event: Callable[[dict[str, Any]], Any] | None = None,
    mode: Literal["auto", "demo", "live"] = "auto",
    headless: bool = True,
    delay_ms: int = 850,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Executa uma candidatura real em sandbox local observando a Laya passo a passo."""
    resolved_settings = _safe_load_settings(settings)
    received_payload: dict[str, Any] | None = None

    async def handle_index(_request: web.Request) -> web.Response:
        return web.Response(text=DEMO_PORTAL_HTML, content_type="text/html")

    async def handle_submit(request: web.Request) -> web.Response:
        nonlocal received_payload
        received_payload = await request.json()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/submit", handle_submit)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = int(sockets[0].getsockname()[1])
    target_url = f"http://127.0.0.1:{port}/"

    sidecar_client: LayaSidecarClient | None = None
    decision_model: DecisionModel
    engine_label: str

    if mode != "demo":
        sidecar_online = await is_laya_healthy(resolved_settings.LAYA_URL, timeout_s=1.5)
        if sidecar_online:
            candidate_client = LayaSidecarClient(settings=resolved_settings)
            if await candidate_client.warmup():
                sidecar_client = candidate_client
                decision_model = candidate_client
                engine_label = f"Laya Live Sidecar ({resolved_settings.LAYA_URL})"
            else:
                await candidate_client.aclose()
                decision_model = DemoLayaModel()
                engine_label = "Sandbox Calibrado (Laya warmup indisponível — S1 + S2 + EEO)"
        elif mode == "live":
            await runner.cleanup()
            raise RuntimeError(f"Sidecar Laya offline em {resolved_settings.LAYA_URL}")
        else:
            decision_model = DemoLayaModel()
            engine_label = "Sandbox Calibrado (S1 Laya + Escalação S2 + Escudo EEO)"
    else:
        decision_model = DemoLayaModel()
        engine_label = "Sandbox Calibrado (S1 Laya + Escalação S2 + Escudo EEO)"

    has_llm_creds = bool(
        resolved_settings.LLM_MODE == "managed" or resolved_settings.LLM_API_KEY.get_secret_value().strip()
    )
    matcher: Matcher = (
        Matcher(resolved_settings) if mode == "live" and has_llm_creds else DemoMatcher(resolved_settings)
    )

    with tempfile.TemporaryDirectory(prefix="laya_visual_demo_") as tmp_dir:
        pdf_path = await asyncio.to_thread(_prepare_demo_pdf, tmp_dir)
        profile = Profile(
            full_name="Ana Clara Mendes",
            email="ana.mendes@example.com",
            phone="+55 11 99999-8888",
            location="São Paulo, SP",
            work_authorization="Sim, cidadã brasileira sem necessidade de patrocínio",
            eeo={"race": "Parda", "etnia": "Parda", "gender": "Mulher"},
        )
        job = Job(
            id="sandbox-laya-01",
            company="TechNova",
            title="Engenheira de Automação & IA",
            url=target_url,
        )
        resume = ResumeInfo(
            path=pdf_path,
            summary="Engenheira de Software Python, Playwright e IA.",
            text="Python FastAPI Playwright LLM Automação",
        )

        async def _forward_event(ev: dict[str, Any]) -> None:
            if on_visual_event is None:
                return
            payload = {
                "job_id": job.id,
                "company": job.company,
                "job_title": job.title,
                **ev,
            }
            res = on_visual_event(payload)
            if hasattr(res, "__await__"):
                await res

        handler = LayaGenericHandler(
            decision_model=decision_model,
            matcher=matcher,
            settings=resolved_settings,
            visual_watch=True,
            visual_delay_ms=delay_ms,
            engine_label=engine_label,
            on_visual_event=_forward_event,
        )

        browser: Browser | None = None
        try:
            async with async_playwright() as pw:
                browser = await _launch_demo_browser(pw, headless=headless)
                context = await browser.new_context(viewport={"width": 1280, "height": 820})
                page = await context.new_page()
                await page.goto(target_url, wait_until="domcontentloaded")
                wrapped_page = handler.wrap_visual_page(page)

                await handler.navigator.emit_visual(
                    title="SANDBOX INICIADO",
                    detail=f"Portal {job.company} carregado — Laya inspecionando DOM sem seletores fixos.",
                    kind="laya",
                    pause=delay_ms > 0,
                )

                page_info = await handler.inspect(wrapped_page, job)
                non_sensitive = [q for q in page_info.questions if q.kind != "checkbox"]
                answers = (
                    await matcher.answer_questions(job, non_sensitive, profile, resume.text)
                    if non_sensitive
                    else AnswerSet()
                )
                result = await handler.fill_and_submit(
                    page=wrapped_page,
                    job=job,
                    profile=profile,
                    resume=resume,
                    answers=answers,
                    dry_run=dry_run,
                    ready_check=_ready_ok,
                )

                await page.wait_for_timeout(250)
                await handler.navigator.clear_visual_highlight()
                done_title = "SIMULAÇÃO DRY-RUN CONCLUÍDA" if dry_run else "CANDIDATURA ENVIADA NO SANDBOX"
                await handler.navigator.emit_visual(
                    title=done_title,
                    detail=f"Status: {result.status.value} · Passos: {handler.navigator.stats.steps_taken}",
                    kind="action",
                    pause=delay_ms > 0,
                )

                return {
                    "status": str(result.status.value),
                    "submitted": bool(result.submitted),
                    "dry_run": bool(dry_run),
                    "engine": engine_label,
                    "s1": handler.navigator.stats.laya_decisions,
                    "s2": handler.navigator.stats.llm_escalations,
                    "eeo": handler.navigator.eeo_hits + handler.navigator.stats.rule_fallbacks,
                    "payload": received_payload,
                    "reason": result.reason,
                }
        finally:
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
            if sidecar_client is not None:
                with contextlib.suppress(Exception):
                    await sidecar_client.aclose()
            with contextlib.suppress(Exception):
                await matcher.aclose()
            with contextlib.suppress(Exception):
                await runner.cleanup()
