"""Aplicação principal FastAPI com endpoints REST e stream SSE para a UI da harness."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config.settings import load_settings
from web.config_manager import get_ui_config, save_ui_config, test_llm_connection
from web.cookie_manager import (
    PORTAL_CONFIGS,
    clear_cookies,
    get_cookies_info,
    launch_visual_login,
    save_cookies,
)
from web.data_provider import get_jobs_cache, get_results_history, get_summary_metrics
from web.runner import global_runner

logger = logging.getLogger("web.app")

app = FastAPI(
    title="Candidata IA - Harness UI",
    description="Painel de controle visual e amigável para monitoramento e configuração da IA Candidata.",
    version="1.0.0",
)

# Habilita CORS restrito ao localhost / 127.0.0.1 para segurança
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# Rotas da API de Controle e Status
# ==============================================================================


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    """Retorna o status em tempo real da automação e tarefas ativas."""
    return global_runner.get_status()


@app.get("/api/events")
async def sse_events(request: Request) -> StreamingResponse:
    """Stream de Server-Sent Events (SSE) para atualização instantânea da UI."""
    q = global_runner.subscribe()

    async def event_generator():
        try:
            # Envia status inicial imediatamente na conexão
            initial = {"event": "init", "data": global_runner.get_status()}
            yield f"data: {json.dumps(initial)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Timeout de 20s para enviar keep-alive ping
                    item = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            global_runner.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


PROJECT_ROOT = Path(".").resolve()
SCREENSHOTS_DIR = (PROJECT_ROOT / "artifacts" / "screenshots").resolve()


@app.post("/api/run")
async def run_automation(payload: dict[str, Any]) -> dict[str, Any]:
    """Inicia a execução da automação em segundo plano."""
    sheet_param = payload.get("sheet")
    if sheet_param and isinstance(sheet_param, str) and not sheet_param.startswith("http"):
        resolved_sheet = Path(sheet_param).resolve()
        if not str(resolved_sheet).startswith(str(PROJECT_ROOT)):
            raise HTTPException(status_code=400, detail="Caminho de arquivo de planilha fora do projeto não é permitido.")

    res = await global_runner.start(payload)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


@app.post("/api/stop")
async def stop_automation() -> dict[str, Any]:
    """Interrompe a execução ativa da automação."""
    res = await global_runner.stop()
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


# ==============================================================================
# Rotas de Dados, Vagas e Histórico
# ==============================================================================


@app.get("/api/jobs")
async def list_jobs() -> dict[str, Any]:
    """Retorna a lista de vagas encontradas na planilha."""
    jobs = get_jobs_cache()
    return {"total": len(jobs), "jobs": jobs}


@app.get("/api/results")
async def list_results(limit: int = 200) -> dict[str, Any]:
    """Retorna o histórico detalhado de candidaturas do results.jsonl."""
    history = get_results_history(limit=limit)
    return {"total": len(history), "results": history}


@app.get("/api/metrics")
async def get_metrics() -> dict[str, Any]:
    """Retorna o consolidado de KPIs (tokens gastos, tempo médio, submissões, etc.)."""
    return get_summary_metrics()


@app.get("/api/screenshots/{filename}")
async def get_screenshot(filename: str):
    """Serve as capturas de tela salvas durante blockers ou revisões humanas."""
    # Protege contra path traversal
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", "..") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido.")

    target_path = (SCREENSHOTS_DIR / safe_name).resolve()
    if not str(target_path).startswith(str(SCREENSHOTS_DIR)):
        raise HTTPException(status_code=403, detail="Acesso negado: caminho fora do diretório permitido.")

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot não encontrada.")
    return FileResponse(target_path, media_type="image/png")


# ==============================================================================
# Rotas de Configuração e Chaves de API
# ==============================================================================


@app.get("/api/config")
async def read_config() -> dict[str, Any]:
    """Lê as configurações atuais do .env com segredos mascarados."""
    return get_ui_config()


@app.post("/api/config")
async def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Atualiza as variáveis no arquivo .env."""
    return save_ui_config(payload)


@app.post("/api/config/test-llm")
async def test_llm(payload: dict[str, Any]) -> dict[str, Any]:
    """Testa a conectividade com o provedor de IA (Gemini/OpenAI)."""
    return await test_llm_connection(
        api_key=payload.get("api_key"),
        base_url=payload.get("base_url"),
        model=payload.get("model"),
    )


# ==============================================================================
# Rotas de Cookies e Contas
# ==============================================================================


@app.get("/api/cookies")
async def get_cookies() -> dict[str, Any]:
    """Retorna informações da sessão de cookies salva (storage_state.json)."""
    return get_cookies_info()


@app.post("/api/cookies")
async def import_cookies(payload: dict[str, Any]) -> dict[str, Any]:
    """Importa ou substitui cookies a partir de JSON colado pelo usuário."""
    raw = payload.get("data")
    if not raw:
        raise HTTPException(status_code=400, detail="Dados de cookies ausentes.")
    return save_cookies(raw)


@app.delete("/api/cookies")
async def delete_cookies() -> dict[str, Any]:
    """Limpa a sessão de cookies salva."""
    return clear_cookies()


@app.post("/api/cookies/launch-login")
async def launch_login(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Abre uma janela de navegador para o usuário fazer login visualmente."""
    url = (payload or {}).get("url")
    portal = (payload or {}).get("portal")
    use_camoufox = (payload or {}).get("use_camoufox", True)
    auto_save = (payload or {}).get("auto_save", True)
    return await launch_visual_login(
        url=url,
        portal=portal,
        use_camoufox=use_camoufox,
        auto_save=auto_save,
    )


@app.post("/api/cookies/connect/{portal}")
async def connect_portal(portal: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atalho 1-clique para conectar LinkedIn, Gupy ou Indeed via navegador."""
    portal_key = portal.strip().lower()
    if portal_key not in PORTAL_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Portal desconhecido: {portal}")
    use_camoufox = (payload or {}).get("use_camoufox", True)
    return await launch_visual_login(
        portal=portal_key,
        use_camoufox=use_camoufox,
        auto_save=True,
    )


# ==============================================================================
# Rotas de Descoberta de Vagas e Currículo Sob Medida
# ==============================================================================


@app.post("/api/jobs/discover")
async def discover_jobs_endpoint(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Busca vagas reais em LinkedIn/Gupy (browser), Boards ATS e feeds abertos, salvando na fila local."""
    body = payload or {}
    query = str(body.get("query") or "").strip()
    location = str(body.get("location") or "Brasil").strip()
    market = str(body.get("market") or "all").strip()
    limit = min(max(int(body.get("limit") or 15), 1), 50)
    sources = body.get("sources") or ["open_feeds", "ats_boards"]
    if not isinstance(sources, list):
        sources = ["open_feeds"]

    settings = load_settings()
    settings.ensure_dirs()

    candidate_skills: list[str] = []
    profile_path = Path("data/profile.json")
    if profile_path.exists():
        try:
            from core.models import Profile

            prof = Profile.model_validate_json(profile_path.read_text(encoding="utf-8"))
            candidate_skills = list(prof.skills or [])
        except Exception:
            candidate_skills = []

    from core.job_search import compute_dynamic_fit_score, merge_discovered_jobs, search_jobs

    collected: list[dict[str, Any]] = []
    warnings: list[str] = []

    api_sources = [s for s in sources if s in ("open_feeds", "ats_boards")]
    if api_sources:
        api_jobs = await search_jobs(
            query=query,
            market=market,
            limit=limit,
            sources=api_sources,
            greenhouse_boards=settings.TARGET_GREENHOUSE_BOARDS or None,
            lever_boards=settings.TARGET_LEVER_BOARDS or None,
            ashby_boards=settings.TARGET_ASHBY_BOARDS or None,
            candidate_skills=candidate_skills or None,
        )
        collected.extend(api_jobs)

    if "linkedin" in sources:
        try:
            from core.browser_job_reader import SessionExpiredError, read_linkedin_jobs

            li_jobs = await read_linkedin_jobs(
                query=query or "Software Engineer",
                settings=settings,
                location=location or settings.LINKEDIN_SEARCH_LOCATION,
                limit=min(limit, settings.LINKEDIN_DAILY_LIMIT),
                resolve_external_links=True,
            )
            for j in li_jobs:
                if candidate_skills:
                    j["fit_score"] = compute_dynamic_fit_score(j, candidate_skills)
            collected.extend(li_jobs)
        except SessionExpiredError as exc:
            warnings.append(str(exc))
        except Exception as exc:
            warnings.append(f"Falha ao ler LinkedIn: {exc}")

    if "gupy" in sources:
        try:
            from core.browser_job_reader import SessionExpiredError, read_gupy_jobs

            gupy_jobs = await read_gupy_jobs(
                query=query or "Desenvolvedor",
                settings=settings,
                limit=limit,
            )
            for j in gupy_jobs:
                if candidate_skills:
                    j["fit_score"] = compute_dynamic_fit_score(j, candidate_skills)
            collected.extend(gupy_jobs)
        except SessionExpiredError as exc:
            warnings.append(str(exc))
        except Exception as exc:
            warnings.append(f"Falha ao ler Gupy: {exc}")

    all_queue, added = merge_discovered_jobs(collected, queue_path=settings.DISCOVERED_JOBS_FILE)
    return {
        "success": True,
        "total_found": len(collected),
        "added": added,
        "queue_total": len(all_queue),
        "warnings": warnings,
        "jobs": collected,
    }


@app.post("/api/jobs/{job_id}/tailor-resume")
async def tailor_resume_for_job(job_id: str) -> dict[str, Any]:
    """Gera (ou regenera) o PDF sob medida para uma vaga da fila local/planilha."""
    settings = load_settings()
    settings.ensure_dirs()

    profile_path = Path("data/profile.json")
    if not profile_path.exists():
        raise HTTPException(status_code=400, detail="Arquivo data/profile.json não encontrado.")

    from core.job_search import load_discovered_jobs, update_discovered_job
    from core.matcher import Matcher
    from core.models import Job, Profile
    from core.resume_pdf import generate_tailored_pdf
    from core.resume_tailor import ResumeTailor

    profile = Profile.model_validate_json(profile_path.read_text(encoding="utf-8"))

    # Localiza vaga na fila local ou no cache geral
    raw_discovered = load_discovered_jobs(settings.DISCOVERED_JOBS_FILE)
    match_raw = next(
        (d for d in raw_discovered if str(d.get("id") or d.get("external_job_id")) == str(job_id)),
        None,
    )
    if match_raw is not None:
        from core.job_search import jobs_to_domain_models

        target_job = jobs_to_domain_models([match_raw])[0]
    else:
        cached = get_jobs_cache()
        found_meta = next((j for j in cached if str(j["id"]) == str(job_id)), None)
        if not found_meta:
            raise HTTPException(status_code=404, detail=f"Vaga '{job_id}' não encontrada.")
        target_job = Job(
            id=str(found_meta["id"]),
            row_index=int(found_meta.get("row_index") or 1),
            company=str(found_meta.get("company") or "Empresa"),
            title=str(found_meta.get("title") or "Cargo"),
            url=found_meta.get("url"),
            ats=found_meta.get("ats"),
            language=found_meta.get("language") or "pt",
            resume_label=found_meta.get("resume_label"),
        )

    matcher = Matcher(settings=settings) if not settings.NO_LLM else None
    try:
        tailor = ResumeTailor(settings=settings, matcher=matcher)
        spec = await tailor.build_spec(target_job, profile)
        resume_info = generate_tailored_pdf(spec, profile, settings.GENERATED_RESUMES_DIR)
    finally:
        if matcher is not None:
            await matcher.aclose()

    if match_raw is not None:
        update_discovered_job(
            job_id,
            {
                "tailored_resume_path": str(resume_info.path),
                "matched_skills": spec.matched_skills,
                "resume_label": resume_info.path.name,
            },
            queue_path=settings.DISCOVERED_JOBS_FILE,
        )

    return {
        "success": True,
        "job_id": job_id,
        "filename": resume_info.path.name,
        "pdf_url": f"/api/resumes/generated/{resume_info.path.name}",
        "matched_skills": spec.matched_skills,
        "prioritized_skills": spec.prioritized_skills[:12],
        "summary": spec.summary,
    }


@app.get("/api/resumes/generated/{filename}")
async def get_generated_resume(filename: str):
    """Serve PDF sob medida gerado para a vaga com proteção contra path traversal."""
    safe_name = Path(filename).name
    if (
        not safe_name
        or safe_name in (".", "..")
        or "/" in filename
        or "\\" in filename
        or not safe_name.lower().endswith(".pdf")
    ):
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido.")

    settings = load_settings()
    base_dir = settings.GENERATED_RESUMES_DIR.resolve()
    target_path = (base_dir / safe_name).resolve()
    if not str(target_path).startswith(str(base_dir)):
        raise HTTPException(status_code=403, detail="Acesso negado: caminho fora do diretório permitido.")
    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="Currículo PDF gerado não encontrado.")
    return FileResponse(target_path, media_type="application/pdf", filename=safe_name)


# ==============================================================================
# Rota Raiz e Arquivos Estáticos da UI
# ==============================================================================

STATIC_DIR = Path("web/static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
async def serve_index():
    """Entrega a página principal da interface."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {
            "status": "ready",
            "message": "Candidata IA API está online. Construa a UI estática em web/static/index.html.",
            "api_docs": "/docs",
        }
    )


# Monta arquivos estáticos adicionais se existirem
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
