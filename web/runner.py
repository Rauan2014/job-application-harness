"""Orquestrador assíncrono do harness em segundo plano com eventos SSE em tempo real."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import AsyncExitStack, suppress
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any

from automation.browser import BrowserManager
from config.settings import Settings, load_settings
from core.decision.laya_client import LayaSidecarClient
from core.decision.layer import DecisionLayer
from core.matcher import Matcher
from core.models import ApplicationResult, Job, Profile
from core.pipeline import Pipeline
from core.queue_manager import QueueManager
from core.resume_resolver import ResumeResolver
from storage.sheet_loader import (
    download_google_sheet,
    extract_spreadsheet_id,
    find_header,
    is_google_sheet_source,
    load_jobs,
    read_raw,
)
from storage.sheet_writer import (
    sync_sheet_with_local_state,
    update_google_sheet_results,
    write_results,
)

logger = logging.getLogger("web.runner")


class HarnessRunner:
    """Gerencia a execução ativa do harness, acompanhando progresso e emitindo SSE."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

        # Estado da execução
        self.status: str = "idle"  # idle, running, completed, cancelled, error
        self.is_running: bool = False
        self.total_jobs: int = 0
        self.completed_jobs: int = 0
        self.remaining_jobs: int = 0
        self.start_time: float | None = None
        self.end_time: float | None = None

        # Vagas atualmente em processamento nos navegadores
        self.active_jobs: dict[str, dict[str, Any]] = {}
        # Resultados desta rodada
        self.run_results: list[dict[str, Any]] = []
        # Métricas da rodada
        self.run_tokens: int = 0
        self.run_prompt_tokens: int = 0
        self.run_completion_tokens: int = 0

        # Logs recentes em memória
        self.logs: deque[dict[str, str]] = deque(maxlen=200)

    def log(self, level: str, message: str) -> None:
        """Adiciona log ao buffer e transmite via SSE."""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"timestamp": ts, "level": level, "message": message}
        self.logs.append(entry)
        self.broadcast("log", entry)
        if level == "ERROR":
            logger.error(message)
        else:
            logger.info(message)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Registra um ouvinte SSE."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove um ouvinte SSE."""
        self._subscribers.discard(q)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Envia evento para todos os clientes conectados."""
        payload = {"event": event_type, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except Exception:
                self._subscribers.discard(q)

    def get_status(self) -> dict[str, Any]:
        """Retorna snapshot completo do estado atual para a UI."""
        elapsed_s = 0.0
        if self.start_time:
            now = time.perf_counter() if self.is_running else (self.end_time or time.perf_counter())
            elapsed_s = round(now - self.start_time, 1)

        progress_pct = 0.0
        if self.total_jobs > 0:
            progress_pct = round((self.completed_jobs / self.total_jobs) * 100, 1)

        active_list = list(self.active_jobs.values())

        return {
            "status": self.status,
            "is_running": self.is_running,
            "total_jobs": self.total_jobs,
            "completed_jobs": self.completed_jobs,
            "remaining_jobs": max(0, self.total_jobs - self.completed_jobs),
            "progress_percent": progress_pct,
            "elapsed_seconds": elapsed_s,
            "active_jobs": active_list,
            "run_tokens": {
                "total": self.run_tokens,
                "prompt": self.run_prompt_tokens,
                "completion": self.run_completion_tokens,
            },
            "recent_results": self.run_results[-50:],
            "logs": list(self.logs),
        }

    async def start(self, options: dict[str, Any]) -> dict[str, Any]:
        """Inicia uma nova rodada de execução em background se não estiver rodando."""
        async with self._lock:
            if self.is_running:
                return {"success": False, "error": "A automação já está em execução."}

            self.status = "running"
            self.is_running = True
            self.total_jobs = 0
            self.completed_jobs = 0
            self.remaining_jobs = 0
            self.active_jobs = {}
            self.run_results = []
            self.run_tokens = 0
            self.run_prompt_tokens = 0
            self.run_completion_tokens = 0
            self.start_time = time.perf_counter()
            self.end_time = None
            self.logs.clear()

            self.log("INFO", "Iniciando sessão do Candidata IA...")
            self.broadcast("status_change", self.get_status())

            self._task = asyncio.create_task(self._execute_harness(options))
            return {"success": True, "message": "Automação iniciada com sucesso!"}

    async def stop(self) -> dict[str, Any]:
        """Cancela com segurança a execução ativa."""
        async with self._lock:
            if not self.is_running or self._task is None:
                return {"success": False, "error": "Nenhuma automação em execução no momento."}

            self.log("WARNING", "Solicitação de parada recebida. Encerrando processos...")
            self._task.cancel()
            self.status = "cancelling"
            self.broadcast("status_change", self.get_status())

            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

            self.is_running = False
            self.status = "cancelled"
            self.end_time = time.perf_counter()
            self.broadcast("status_change", self.get_status())
            self.log("INFO", "Automação interrompida pelo usuário.")
            return {"success": True, "message": "Automação parada com sucesso."}

    async def _execute_harness(self, options: dict[str, Any]) -> None:
        """Loop principal de execução integrando QueueManager e Pipeline."""
        try:
            overrides: dict[str, Any] = {}
            if "dry_run" in options:
                overrides["DRY_RUN"] = bool(options["dry_run"])
            if "concurrency" in options:
                overrides["MAX_CONCURRENCY"] = int(options["concurrency"])
            if "no_llm" in options:
                overrides["NO_LLM"] = bool(options["no_llm"])
            if "autonomous" in options:
                overrides["AUTONOMOUS_MODE"] = bool(options["autonomous"])
            if "include_blank_status" in options:
                overrides["INCLUDE_BLANK_STATUS"] = bool(options["include_blank_status"])

            settings = load_settings(**overrides)
            settings.ensure_dirs()

            profile_path = Path("data/profile.json")
            if not profile_path.exists():
                self.log("ERROR", "Arquivo data/profile.json não encontrado.")
                self.status = "error"
                return

            profile = Profile.model_validate_json(profile_path.read_text(encoding="utf-8"))
            self.log("INFO", f"Perfil do candidato '{profile.full_name}' carregado.")

            sheet_opt = str(options.get("sheet") or "").strip()
            if sheet_opt and sheet_opt != (settings.GOOGLE_SHEET_URL or ""):
                with suppress(Exception):
                    from web.config_manager import save_ui_config

                    save_ui_config({"sheet": {"google_sheet_url": sheet_opt}})

            sheet_input = sheet_opt or settings.GOOGLE_SHEET_URL or "data/vagas.xlsx"
            if is_google_sheet_source(sheet_input):
                self.log("INFO", "Baixando dados atualizados do Google Sheets...")
                local_sheet_path = download_google_sheet(
                    sheet_input,
                    dest_path=Path("cache/vagas_sheets_latest.xlsx"),
                    service_account_file=settings.GOOGLE_SERVICE_ACCOUNT_FILE,
                    oauth_client_file=settings.GOOGLE_OAUTH_CLIENT_FILE,
                    token_file=settings.GOOGLE_TOKEN_FILE,
                )
                with suppress(Exception):
                    from web.data_provider import _write_cached_sheet_id

                    sid = extract_spreadsheet_id(sheet_input)
                    if sid:
                        _write_cached_sheet_id(sid, str(sheet_input))
                    sync_sheet_with_local_state(
                        local_sheet_path,
                        results_jsonl_path=settings.RESULTS_FILE,
                        extra_out_paths=[settings.SHEET_RESULT_FILE],
                        overwrite_todo=False,
                    )
            else:
                local_sheet_path = Path(sheet_input)

            retry_all = bool(options.get("retry_all", False))
            include_review = retry_all or bool(options.get("include_review", False))
            include_failed = retry_all or bool(options.get("include_failed", False))
            include_blank = retry_all or settings.INCLUDE_BLANK_STATUS

            jobs = load_jobs(
                local_sheet_path,
                only_todo=not retry_all,
                include_blank=include_blank,
                include_review=include_review,
                include_failed=include_failed,
            )

            # Filtro por IDs específicos se solicitado
            job_ids = options.get("job_ids")
            if job_ids and isinstance(job_ids, list):
                jobs = [j for j in jobs if j.id in job_ids]

            limit = options.get("limit")
            if limit and int(limit) > 0:
                jobs = jobs[: int(limit)]

            self.total_jobs = len(jobs)
            self.remaining_jobs = len(jobs)
            self.completed_jobs = 0

            self.log("INFO", f"{len(jobs)} vagas selecionadas para processamento.")
            self.broadcast("status_change", self.get_status())

            if not jobs:
                self.log("INFO", "Nenhuma vaga pendente encontrada na planilha.")
                self.status = "completed"
                return

            resolver = ResumeResolver(
                cache_dir=settings.CACHE_DIR,
                threshold=settings.RESUME_MATCH_THRESHOLD,
            )
            resolver.load_cache()
            self.log("INFO", f"{len(resolver.get_resumes())} currículos indexados no cache local.")

            async with AsyncExitStack() as stack:
                decision_model = None
                if settings.DECISION_BACKEND == "laya":
                    from core.decision.laya_service import ensure_laya_running

                    is_up, laya_proc = await ensure_laya_running(
                        base_url=settings.LAYA_URL,
                        auto_start=True,
                        wait_timeout_s=15.0,
                    )
                    if laya_proc is not None:
                        stack.callback(laya_proc.terminate)
                    if is_up:
                        candidate_model = LayaSidecarClient(settings=settings)
                        stack.push_async_callback(candidate_model.aclose)
                        if await candidate_model.warmup():
                            decision_model = candidate_model
                            self.log("INFO", "Modelo neural local Laya ativado com sucesso.")

                eval_report = {}
                if settings.LAYA_EVAL_FILE.exists():
                    with suppress(Exception):
                        eval_report = json.loads(settings.LAYA_EVAL_FILE.read_text(encoding="utf-8"))

                decision_layer = DecisionLayer(
                    model=decision_model,
                    settings=settings,
                    eval_report=eval_report,
                )

                matcher = Matcher(settings=settings)
                stack.push_async_callback(matcher.aclose)

                browser_mgr = await stack.enter_async_context(BrowserManager(settings))
                pipeline = Pipeline(
                    settings=settings,
                    decision_layer=decision_layer,
                    resume_resolver=resolver,
                    browser_manager=browser_mgr,
                    matcher=matcher,
                )

                queue_mgr = QueueManager(
                    settings=settings,
                    pipeline=pipeline,
                    profile=profile,
                )

                # Callbacks em tempo real
                def on_step(job: Job, step_name: str) -> None:
                    self.active_jobs[job.id] = {
                        "id": job.id,
                        "company": job.company,
                        "title": job.title,
                        "ats": job.ats or "Web",
                        "current_step": step_name,
                        "started_at": datetime.now().strftime("%H:%M:%S"),
                    }
                    self.log("INFO", f"[{job.company} - {job.title}] {step_name}")
                    self.broadcast("step_update", self.get_status())

                def on_progress(job: Job, result: ApplicationResult) -> None:
                    self.active_jobs.pop(job.id, None)
                    self.completed_jobs += 1
                    self.remaining_jobs = max(0, self.total_jobs - self.completed_jobs)

                    # Acumula tokens gastos
                    toks = result.tokens_used or 0
                    p_toks = result.prompt_tokens or 0
                    c_toks = result.completion_tokens or 0
                    self.run_tokens += toks
                    self.run_prompt_tokens += p_toks
                    self.run_completion_tokens += c_toks

                    screen_filename = result.screenshot_path.name if result.screenshot_path else None
                    item_dict = {
                        "job_id": result.job_id,
                        "company": job.company,
                        "title": job.title,
                        "status": result.status.value,
                        "reason": result.reason,
                        "resume_used": result.resume_used,
                        "duration_s": result.duration_s,
                        "tokens_used": toks,
                        "prompt_tokens": p_toks,
                        "completion_tokens": c_toks,
                        "screenshot_url": f"/api/screenshots/{screen_filename}" if screen_filename else None,
                        "submitted": result.submitted,
                        "dry_run": result.dry_run,
                    }
                    self.run_results.append(item_dict)

                    status_msg = f"Vaga '{job.company}' finalizada com status: {result.status.value}"
                    if result.reason:
                        status_msg += f" ({result.reason})"
                    self.log("INFO", status_msg)
                    self.broadcast("progress_update", self.get_status())

                results = await queue_mgr.run(
                    jobs,
                    on_progress=on_progress,
                    on_step=on_step,
                )

                # Salva cópia atualizada da planilha e sincroniza cache local
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                copy_path = Path(f"artifacts/planilha_atualizada_{timestamp}.xlsx")
                jobs_map = {j.id: j for j in jobs}
                try:
                    write_results(
                        source_path=local_sheet_path,
                        results=results,
                        out_path=copy_path,
                        jobs=jobs_map,
                    )
                    import shutil

                    shutil.copy2(copy_path, settings.SHEET_RESULT_FILE)
                    if local_sheet_path.resolve() != copy_path.resolve():
                        shutil.copy2(copy_path, local_sheet_path)
                    latest_cache = Path("cache/vagas_sheets_latest.xlsx")
                    if latest_cache.exists() and latest_cache.resolve() != copy_path.resolve():
                        shutil.copy2(copy_path, latest_cache)
                    sync_sheet_with_local_state(
                        latest_cache if latest_cache.exists() else settings.SHEET_RESULT_FILE,
                        results_jsonl_path=settings.RESULTS_FILE,
                        extra_out_paths=[settings.SHEET_RESULT_FILE],
                        overwrite_todo=False,
                    )
                    self.log("INFO", f"Planilha de resultados atualizada e sincronizada em {copy_path.name}.")
                except Exception as exc:
                    self.log("WARNING", f"Erro ao atualizar planilha local: {exc}")

                if is_google_sheet_source(sheet_input):
                    sheet_id = extract_spreadsheet_id(sheet_input)
                    if sheet_id:
                        try:
                            from storage.drive_client import build_sheets_service

                            raw_sheet = read_raw(local_sheet_path)
                            header_pos, cols = find_header(raw_sheet)
                            header_row_num = raw_sheet.rows[header_pos][0]
                            sheets_srv = await asyncio.to_thread(
                                build_sheets_service,
                                settings.GOOGLE_SERVICE_ACCOUNT_FILE,
                                settings.GOOGLE_OAUTH_CLIENT_FILE,
                                settings.GOOGLE_TOKEN_FILE,
                                False,
                            )
                            await asyncio.to_thread(
                                update_google_sheet_results,
                                sheet_id,
                                results,
                                jobs_map,
                                cols,
                                header_row_num,
                                sheets_srv,
                            )
                            self.log("INFO", "Planilha Google Sheets online atualizada com sucesso!")
                        except Exception as exc:
                            logger.debug("Sincronização online Google Sheets indisponível (%s); cache local atualizado.", exc)

            self.status = "completed"
            self.log("INFO", f"Execução concluída! {self.completed_jobs} vagas processadas.")
        except asyncio.CancelledError:
            self.status = "cancelled"
            self.log("WARNING", "Execução cancelada.")
        except Exception as exc:
            logger.exception("Erro crítico no harness runner: %s", exc)
            self.status = "error"
            self.log("ERROR", f"Erro durante a execução: {type(exc).__name__}: {exc}")
        finally:
            self.is_running = False
            self.end_time = time.perf_counter()
            self.active_jobs.clear()
            self.broadcast("status_change", self.get_status())


# Instância singleton global do runner para ser consumida pela API
global_runner = HarnessRunner()
