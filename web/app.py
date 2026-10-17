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

from web.config_manager import get_ui_config, save_ui_config, test_llm_connection
from web.cookie_manager import clear_cookies, get_cookies_info, launch_visual_login, save_cookies
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
    url = (payload or {}).get("url", "https://br.indeed.com")
    use_camoufox = (payload or {}).get("use_camoufox", True)
    return await launch_visual_login(url=url, use_camoufox=use_camoufox)


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
