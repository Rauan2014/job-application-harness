"""Orquestrador assíncrono do harness em segundo plano com eventos SSE em tempo real."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from contextlib import AsyncExitStack, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from automation.browser import BrowserManager
from config.settings import Settings, load_settings
from core.cloud_auth import CloudAuthError, SupabaseSessionManager
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
        self._thread_lock = threading.Lock()
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

        # Telemetria visual e frame JPEG mais recente da Laya Web Navigator
        self._demo_task: asyncio.Task[None] | None = None
        self.laya_events: deque[dict[str, Any]] = deque(maxlen=60)
        self.laya_stats: dict[str, int] = {"s1": 0, "s2": 0, "eeo": 0}
        self.laya_live_meta: dict[str, Any] = {
            "active": False,
            "url": "",
            "engine": "",
            "job_id": "",
            "company": "",
            "job_title": "",
            "frame_seq": 0,
            "last_title": "",
            "last_detail": "",
        }
        self.laya_latest_frame_bytes: bytes | None = None

    def reset_laya_live(self, *, active: bool = False) -> None:
        """Reinicia histórico visual e métricas da Laya mantendo sequência monotônica."""
        with self._thread_lock:
            self.laya_events.clear()
            self.laya_stats = {"s1": 0, "s2": 0, "eeo": 0}
            seq = int(self.laya_live_meta.get("frame_seq", 0))
            self.laya_live_meta = {
                "active": active,
                "url": "",
                "engine": "",
                "job_id": "",
                "company": "",
                "job_title": "",
                "frame_seq": seq,
                "last_title": "",
                "last_detail": "",
            }
            self.laya_latest_frame_bytes = None

    def emit_laya_event(self, payload: dict[str, Any]) -> None:
        """Recebe decisão/ação da Laya (+ frame JPEG opcional) e difunde metadados via SSE."""
        frame_bytes = payload.get("frame_bytes")
        ts = datetime.now().strftime("%H:%M:%S")
        with self._thread_lock:
            if isinstance(frame_bytes, (bytes, bytearray)) and len(frame_bytes) > 0:
                self.laya_latest_frame_bytes = bytes(frame_bytes)
                self.laya_live_meta["frame_seq"] = int(self.laya_live_meta.get("frame_seq", 0)) + 1

            s1 = int(payload.get("s1", self.laya_stats["s1"]) or 0)
            s2 = int(payload.get("s2", self.laya_stats["s2"]) or 0)
            eeo = int(payload.get("eeo", self.laya_stats["eeo"]) or 0)
            self.laya_stats = {"s1": s1, "s2": s2, "eeo": eeo}

            url = str(payload.get("url") or self.laya_live_meta.get("url") or "")
            engine = str(payload.get("engine") or self.laya_live_meta.get("engine") or "")
            job_id = str(payload.get("job_id") or self.laya_live_meta.get("job_id") or "")
            company = str(payload.get("company") or self.laya_live_meta.get("company") or "")
            job_title = str(payload.get("job_title") or self.laya_live_meta.get("job_title") or "")
            title = str(payload.get("title") or "LAYA EVENT")
            detail = str(payload.get("detail") or "")
            kind = str(payload.get("kind") or "laya")
            frame_seq = int(self.laya_live_meta.get("frame_seq", 0))

            self.laya_live_meta.update(
                {
                    "active": True,
                    "url": url,
                    "engine": engine,
                    "job_id": job_id,
                    "company": company,
                    "job_title": job_title,
                    "last_title": title,
                    "last_detail": detail,
                }
            )
            entry = {
                "timestamp": ts,
                "title": title,
                "detail": detail,
                "kind": kind,
                "s1": s1,
                "s2": s2,
                "eeo": eeo,
                "url": url,
                "engine": engine,
                "job_id": job_id,
                "company": company,
                "job_title": job_title,
                "frame_seq": frame_seq,
            }
            self.laya_events.append(entry)
            if job_id and job_id in self.active_jobs:
                step_txt = f"{title}: {detail}" if detail else title
                self.active_jobs[job_id]["current_step"] = step_txt[:95]

        self.broadcast("laya_event", entry)

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
        payload = {"event": event_type, "data": data, "timestamp": datetime.now(UTC).isoformat()}
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
            "laya_live": {
                **self.laya_live_meta,
                "stats": dict(self.laya_stats),
                "has_frame": self.laya_latest_frame_bytes is not None,
                "events": list(self.laya_events),
            },
        }

    async def start(self, options: dict[str, Any]) -> dict[str, Any]:
        """Inicia uma nova rodada de execução em background se não estiver rodando."""
        with self._thread_lock:
            demo_busy = self._demo_task is not None and not self._demo_task.done()
            run_busy = self._task is not None and not self._task.done()
            if self.is_running or run_busy or demo_busy:
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

        self.reset_laya_live(active=bool(options.get("laya_visual")))
        self.log("INFO", "Iniciando sessão do Candidata IA...")
        self.broadcast("status_change", self.get_status())
        self._task = asyncio.create_task(self._execute_harness(options))
        return {"success": True, "message": "Automação iniciada com sucesso!"}

    async def start_laya_demo(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Inicia uma candidatura real em sandbox local para visualizar a Laya ao vivo."""
        with self._thread_lock:
            demo_busy = self._demo_task is not None and not self._demo_task.done()
            run_busy = self._task is not None and not self._task.done()
            if self.is_running or run_busy or demo_busy:
                return {"success": False, "error": "Já existe uma execução ativa no momento."}

            self.status = "running"
            self.is_running = True
            self.total_jobs = 1
            self.completed_jobs = 0
            self.remaining_jobs = 1
            self.active_jobs = {
                "sandbox-laya-01": {
                    "id": "sandbox-laya-01",
                    "company": "TechNova (Sandbox)",
                    "title": "Engenheira de Automação & IA",
                    "ats": "Laya Sandbox",
                    "current_step": "Inicializando visor neural da Laya...",
                }
            }
            self.run_results = []
            self.run_tokens = 0
            self.run_prompt_tokens = 0
            self.run_completion_tokens = 0
            self.start_time = time.perf_counter()
            self.end_time = None
            self.logs.clear()

        self.reset_laya_live(active=True)
        self.log("INFO", "Iniciando Sandbox Visual do Laya Web Navigator...")
        self.broadcast("status_change", self.get_status())
        self._demo_task = asyncio.create_task(self._execute_laya_demo(options or {}))
        return {"success": True, "message": "Sandbox visual da Laya iniciado!"}

    async def _execute_laya_demo(self, options: dict[str, Any]) -> None:
        """Executa o sandbox local emitindo frames e decisões Laya para o dashboard."""
        try:
            from automation.laya_demo import run_visual_demo

            headless = bool(options.get("headless", True))
            delay_ms = max(0, int(options.get("delay_ms", 850)))
            dry_run = bool(options.get("dry_run", False))
            raw_mode = str(options.get("mode") or "auto")

            settings: Settings | None = None
            with suppress(Exception):
                settings = load_settings()

            def _pick_mode() -> Any:
                if raw_mode == "demo":
                    return "demo"
                if raw_mode == "live":
                    return "live"
                return "auto"

            summary = await run_visual_demo(
                on_visual_event=self.emit_laya_event,
                mode=_pick_mode(),
                headless=headless,
                delay_ms=delay_ms,
                dry_run=dry_run,
                settings=settings,
            )
            with self._thread_lock:
                self.completed_jobs = 1
                self.remaining_jobs = 0
                self.status = "completed"
            self.log(
                "INFO",
                f"Sandbox Visual concluído ({summary.get('status')}) — "
                f"S1={summary.get('s1', 0)}, S2={summary.get('s2', 0)}, EEO={summary.get('eeo', 0)}.",
            )
        except asyncio.CancelledError:
            self.status = "cancelled"
            self.log("WARNING", "Sandbox Visual da Laya cancelado.")
        except Exception as exc:
            logger.exception("Erro no Sandbox Visual Laya: %s", exc)
            self.status = "error"
            self.log("ERROR", f"Erro no Sandbox Visual Laya: {type(exc).__name__}: {exc}")
        finally:
            with self._thread_lock:
                self.is_running = False
                self.end_time = time.perf_counter()
                self.active_jobs.clear()
                self._demo_task = None
                self.laya_live_meta["active"] = False
            self.broadcast("status_change", self.get_status())

    async def stop(self) -> dict[str, Any]:
        """Cancela com segurança a execução ativa e libera subprocessos."""
        with self._thread_lock:
            task_to_cancel = self._task or self._demo_task
            if not self.is_running or task_to_cancel is None:
                return {"success": False, "error": "Nenhuma automação em execução no momento."}

            self.log("WARNING", "Solicitação de parada recebida. Encerrando processos...")
            self.status = "cancelling"
            self.broadcast("status_change", self.get_status())

        task_to_cancel.cancel()

        try:
            await asyncio.wait_for(asyncio.shield(task_to_cancel), timeout=5.0)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass
        finally:
            with self._thread_lock:
                self.is_running = False
                self.status = "cancelled"
                self.end_time = time.perf_counter()
                self._task = None
                self._demo_task = None
                self.active_jobs.clear()
                self.laya_live_meta["active"] = False
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
            if options.get("laya_navigator") is not None:
                overrides["LAYA_WEB_NAVIGATOR_ENABLED"] = bool(options["laya_navigator"])
            if options.get("force_laya_generic") is not None:
                overrides["LAYA_FORCE_GENERIC"] = bool(options["force_laya_generic"])
            if options.get("laya_visual") is not None:
                overrides["LAYA_VISUAL_WATCH"] = bool(options["laya_visual"])
            if options.get("headless") is not None:
                overrides["HEADLESS"] = bool(options["headless"])
            single_job_watch = bool(options.get("single_job_watch", False))
            if single_job_watch:
                overrides["FIT_GATE_ENABLED"] = False
                overrides["MAX_CONCURRENCY"] = 1

            settings = load_settings(**overrides)
            if settings.LAYA_VISUAL_WATCH and settings.MAX_CONCURRENCY > 1:
                settings.MAX_CONCURRENCY = 1
                self.log("INFO", "Visor Visual Ao Vivo ativo: concorrência ajustada para 1 janela por vez.")
            settings.ensure_dirs()

            profile_path = Path("data/profile.json")
            if not profile_path.exists():
                self.log("ERROR", "Arquivo data/profile.json não encontrado.")
                self.status = "error"
                return

            profile = Profile.model_validate_json(profile_path.read_text(encoding="utf-8"))
            self.log("INFO", f"Perfil do candidato '{profile.full_name}' carregado.")

            custom_url = str(options.get("custom_url") or "").strip()

            from core.job_search import (
                jobs_to_domain_models as discovered_to_jobs,
            )
            from core.job_search import (
                load_discovered_jobs,
                update_discovered_job,
            )

            discovered_raw = load_discovered_jobs(settings.DISCOVERED_JOBS_FILE)
            discovered_jobs = discovered_to_jobs(discovered_raw)
            discovered_ids = {j.id for j in discovered_jobs}

            source_opt = str(options.get("source") or "").strip().lower()
            requested_ids = options.get("job_ids") or []
            if not isinstance(requested_ids, list):
                requested_ids = []

            retry_all = bool(options.get("retry_all", False))
            effective_retry_all = retry_all or bool(requested_ids)
            include_review = effective_retry_all or bool(options.get("include_review", False))
            include_failed = effective_retry_all or bool(options.get("include_failed", False))
            include_blank = effective_retry_all or settings.INCLUDE_BLANK_STATUS

            local_sheet_path: Path | None = None
            sheet_input = ""
            cloud_mgr: SupabaseSessionManager | None = None
            jobs: list[Job] = []

            if custom_url:
                from urllib.parse import urlparse

                parsed_url = urlparse(custom_url)
                host = (parsed_url.hostname or "Portal Externo").replace("www.", "")
                company_name = str(options.get("company") or host.split(".")[0].capitalize() or "Empresa")
                job_title = str(options.get("job_title") or f"Candidatura ao vivo ({host})")
                jobs = [
                    Job(
                        id=f"live-{int(time.time())}",
                        company=company_name,
                        title=job_title,
                        url=custom_url,
                        fit_score=100,
                        sheet_status="A Fazer",
                    )
                ]
                self.log("INFO", f"Vaga direta carregada para execução ao vivo: {custom_url}")
            else:
                sheet_opt = str(options.get("sheet") or "").strip()
                if sheet_opt and sheet_opt != (settings.GOOGLE_SHEET_URL or ""):
                    with suppress(Exception):
                        from web.config_manager import save_ui_config

                        save_ui_config({"sheet": {"google_sheet_url": sheet_opt}})

                sheet_input = sheet_opt or settings.GOOGLE_SHEET_URL or "data/vagas.xlsx"
                cached_sheet = Path("cache/vagas_sheets_latest.xlsx")

                only_discovered = source_opt == "discovered" or (
                    bool(requested_ids) and all(jid in discovered_ids for jid in requested_ids)
                )

                # Para simulação rápida de 1 vaga já presente nos arquivos locais, evita baixar planilha online.
                skip_remote_sheet_dl = bool(single_job_watch and requested_ids and only_discovered)
                if single_job_watch and requested_ids and not skip_remote_sheet_dl and cached_sheet.exists():
                    with suppress(Exception):
                        cached_ids = {
                            j.id
                            for j in load_jobs(
                                cached_sheet,
                                only_todo=False,
                                include_blank=True,
                                include_review=True,
                                include_failed=True,
                            )
                        }
                        if all(jid in cached_ids or jid in discovered_ids for jid in requested_ids):
                            skip_remote_sheet_dl = True

                if is_google_sheet_source(sheet_input) and not only_discovered and not skip_remote_sheet_dl:
                    self.log("INFO", "Baixando dados atualizados do Google Sheets...")
                    local_sheet_path = await asyncio.to_thread(
                        download_google_sheet,
                        sheet_input,
                        dest_path=cached_sheet,
                        service_account_file=settings.GOOGLE_SERVICE_ACCOUNT_FILE,
                        oauth_client_file=settings.GOOGLE_OAUTH_CLIENT_FILE,
                        token_file=settings.GOOGLE_TOKEN_FILE,
                    )
                    with suppress(Exception):
                        from web.data_provider import _write_cached_sheet_id

                        sid = extract_spreadsheet_id(sheet_input)
                        if sid:
                            _write_cached_sheet_id(sid, str(sheet_input))
                        await asyncio.to_thread(
                            sync_sheet_with_local_state,
                            local_sheet_path,
                            results_jsonl_path=settings.RESULTS_FILE,
                            extra_out_paths=[settings.SHEET_RESULT_FILE],
                            overwrite_todo=False,
                        )
                elif is_google_sheet_source(sheet_input):
                    local_sheet_path = cached_sheet
                else:
                    local_sheet_path = Path(sheet_input)

                if only_discovered:
                    jobs = [
                        j
                        for j in discovered_jobs
                        if effective_retry_all
                        or (j.sheet_status or "A Fazer").strip().lower() in ("a fazer", "")
                        or (include_review and "revis" in (j.sheet_status or "").lower())
                        or (include_failed and "falh" in (j.sheet_status or "").lower())
                    ]
                else:
                    if local_sheet_path is not None and local_sheet_path.exists():
                        jobs = load_jobs(
                            local_sheet_path,
                            only_todo=not effective_retry_all,
                            include_blank=include_blank,
                            include_review=include_review,
                            include_failed=include_failed,
                        )
                    if source_opt in ("", "all") and discovered_jobs:
                        seen_urls = {(j.url or "").strip().rstrip("/") for j in jobs if j.url}
                        for dj in discovered_jobs:
                            norm_u = (dj.url or "").strip().rstrip("/")
                            st_lower = (dj.sheet_status or "A Fazer").strip().lower()
                            is_eligible = (
                                effective_retry_all
                                or st_lower in ("a fazer", "")
                                or (include_review and "revis" in st_lower)
                                or (include_failed and "falh" in st_lower)
                            )
                            if is_eligible and (not norm_u or norm_u not in seen_urls):
                                jobs.append(dj)
                                if norm_u:
                                    seen_urls.add(norm_u)

                # Carrega vagas da fila remota no Supabase (se sessão ativa e sync habilitado)
                if settings.SUPABASE_SYNC_ENABLED and source_opt in ("", "all", "supabase", "discovered"):
                    candidate_mgr = SupabaseSessionManager(settings)
                    if candidate_mgr.load_session():
                        cloud_mgr = candidate_mgr
                        try:
                            sb_rows = await cloud_mgr.fetch_supabase_jobs(
                                status_filter=None if effective_retry_all else "PENDING",
                                limit=150,
                            )
                            sb_jobs = discovered_to_jobs(sb_rows)
                            sb_by_url = {(sj.url or "").strip().rstrip("/").lower(): sj for sj in sb_jobs if sj.url}
                            seen_urls_sb = {(j.url or "").strip().rstrip("/").lower() for j in jobs if j.url}
                            for j in jobs:
                                nu = (j.url or "").strip().rstrip("/").lower()
                                if nu and nu in sb_by_url and not j.supabase_id:
                                    j.supabase_id = sb_by_url[nu].supabase_id
                            if source_opt in ("", "all", "supabase"):
                                for sj in sb_jobs:
                                    nu = (sj.url or "").strip().rstrip("/").lower()
                                    st_lower = (sj.sheet_status or "A Fazer").strip().lower()
                                    eligible = (
                                        effective_retry_all
                                        or st_lower in ("a fazer", "")
                                        or (include_review and "revis" in st_lower)
                                        or (include_failed and "falh" in st_lower)
                                    )
                                    if eligible and (not nu or nu not in seen_urls_sb):
                                        jobs.append(sj)
                                        if nu:
                                            seen_urls_sb.add(nu)
                        except CloudAuthError as exc:
                            self.log("WARNING", f"Supabase sync indisponível ao ler fila: {exc}")

                # Filtro por IDs específicos se solicitado
                if requested_ids:
                    jobs = [j for j in jobs if j.id in requested_ids]
                    if single_job_watch:
                        jobs = [
                            j.model_copy(
                                update={
                                    "sheet_status": "A Fazer",
                                    "fit_score": max(j.fit_score or 100, settings.MIN_FIT_SCORE),
                                }
                            )
                            for j in jobs
                        ]

                limit = options.get("limit")
                if limit and int(limit) > 0:
                    jobs = jobs[: int(limit)]

            # Proteção de conta: força concorrência 1 quando houver vagas do LinkedIn
            has_linkedin = any("linkedin.com" in (j.url or "").lower() or (j.ats or "").lower() == "linkedin" for j in jobs)
            if has_linkedin and settings.MAX_CONCURRENCY > 1:
                settings.MAX_CONCURRENCY = 1
                self.log("INFO", "Modo seguro LinkedIn ativado: concorrência ajustada para 1.")

            self.total_jobs = len(jobs)
            self.remaining_jobs = len(jobs)
            self.completed_jobs = 0

            self.log("INFO", f"{len(jobs)} vagas selecionadas para processamento.")
            self.broadcast("status_change", self.get_status())

            if not jobs:
                self.log("INFO", "Nenhuma vaga pendente encontrada na planilha.")
                if settings.LAYA_VISUAL_WATCH:
                    self.emit_laya_event(
                        {
                            "title": "FILA VAZIA",
                            "detail": "Nenhuma vaga pendente encontrada para executar.",
                            "kind": "eeo",
                        }
                    )
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
                        def _cleanup_proc(p):
                            try:
                                p.terminate()
                                p.wait(timeout=2.0)
                            except Exception:
                                with suppress(Exception):
                                    p.kill()
                        stack.callback(_cleanup_proc, laya_proc)
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
                    on_laya_event=self.emit_laya_event,
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
                    tailored_url = (
                        f"/api/resumes/generated/{Path(job.tailored_resume_path).name}"
                        if job.tailored_resume_path
                        else None
                    )
                    if job.id in discovered_ids:
                        new_sheet_st = "A Fazer"
                        if result.status.value == "COMPLETED":
                            new_sheet_st = "Candidatado" if result.submitted else "Pronta (Dry-Run)"
                        elif result.status.value == "NEEDS_REVIEW":
                            new_sheet_st = "Revisar"
                        elif result.status.value == "FAILED":
                            new_sheet_st = "Falhou"
                        elif result.status.value == "SKIPPED":
                            new_sheet_st = "Ignorada"
                        with suppress(Exception):
                            update_discovered_job(
                                job.id,
                                {
                                    "sheet_status": new_sheet_st,
                                    "tailored_resume_path": job.tailored_resume_path,
                                    "matched_skills": job.matched_skills,
                                },
                                queue_path=settings.DISCOVERED_JOBS_FILE,
                            )

                    item_dict = {
                        "job_id": result.job_id,
                        "company": job.company,
                        "title": job.title,
                        "status": result.status.value,
                        "reason": result.reason,
                        "resume_used": result.resume_used,
                        "tailored_resume_url": tailored_url,
                        "matched_skills": job.matched_skills,
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
                    if settings.LAYA_VISUAL_WATCH:
                        last_title = str(self.laya_live_meta.get("last_title") or "")
                        terminal_prefixes = (
                            "CANDIDATURA ·",
                            "VAGA INDISPONÍVEL",
                            "BLOCKER ·",
                            "DP3 ·",
                            "REVISÃO ·",
                            "ERRO",
                            "TRIAGEM ·",
                            "CURRÍCULO ·",
                        )
                        if not last_title.startswith(terminal_prefixes):
                            self.emit_laya_event(
                                {
                                    "job_id": job.id,
                                    "company": job.company,
                                    "job_title": job.title,
                                    "title": f"STATUS FINAL · {result.status.value}",
                                    "detail": result.reason or status_msg,
                                    "kind": "action" if result.status.value == "COMPLETED" else "llm",
                                    "url": str(job.url or ""),
                                }
                            )
                    self.broadcast("progress_update", self.get_status())

                results = await queue_mgr.run(
                    jobs,
                    on_progress=on_progress,
                    on_step=on_step,
                )

                # Salva cópia atualizada da planilha e sincroniza cache local em thread separada
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                copy_path = Path(f"artifacts/planilha_atualizada_{timestamp}.xlsx")
                jobs_map = {j.id: j for j in jobs}
                if not custom_url and local_sheet_path is not None and local_sheet_path.exists():
                    try:
                        import shutil

                        await asyncio.to_thread(
                            write_results,
                            source_path=local_sheet_path,
                            results=results,
                            out_path=copy_path,
                            jobs=jobs_map,
                        )
                        await asyncio.to_thread(shutil.copy2, copy_path, settings.SHEET_RESULT_FILE)
                        if local_sheet_path.resolve() != copy_path.resolve():
                            await asyncio.to_thread(shutil.copy2, copy_path, local_sheet_path)
                        latest_cache = Path("cache/vagas_sheets_latest.xlsx")
                        if latest_cache.exists() and latest_cache.resolve() != copy_path.resolve():
                            await asyncio.to_thread(shutil.copy2, copy_path, latest_cache)
                        await asyncio.to_thread(
                            sync_sheet_with_local_state,
                            latest_cache if latest_cache.exists() else settings.SHEET_RESULT_FILE,
                            results_jsonl_path=settings.RESULTS_FILE,
                            extra_out_paths=[settings.SHEET_RESULT_FILE],
                            overwrite_todo=False,
                        )
                        self.log("INFO", f"Planilha de resultados atualizada e sincronizada em {copy_path.name}.")
                    except Exception as exc:
                        self.log("WARNING", f"Erro ao atualizar planilha local: {exc}")

                # Sincroniza resultados diretamente com public.job_applications no Supabase
                if cloud_mgr is not None and settings.SUPABASE_SYNC_ENABLED:
                    sb_updated = 0
                    missing_in_sb: list[dict[str, Any]] = []
                    for res in results:
                        job_obj = jobs_map.get(res.job_id)
                        try:
                            ok = await cloud_mgr.update_supabase_job_result(
                                job_obj.supabase_id if job_obj else None,
                                job_obj.url if job_obj else None,
                                {
                                    "status": res.status.value,
                                    "dry_run": res.dry_run,
                                    "submitted_live": res.submitted,
                                    "review_reason": res.reason,
                                    "duration_seconds": res.duration_s,
                                    "tokens_consumed": res.tokens_used or 0,
                                    "resume_label": res.resume_used,
                                },
                            )
                            if ok:
                                sb_updated += 1
                            elif job_obj and job_obj.url:
                                missing_in_sb.append(
                                    {
                                        "id": job_obj.id,
                                        "company": job_obj.company,
                                        "title": job_obj.title,
                                        "job_url": job_obj.url,
                                        "ats": job_obj.ats,
                                        "fit_score": job_obj.fit_score,
                                        "modality": job_obj.modality,
                                        "resume_label": res.resume_used or job_obj.resume_label,
                                        "sheet_status": SupabaseSessionManager._task_status_to_sheet(
                                            res.status.value, res.submitted
                                        ),
                                        "source_platform": job_obj.source_platform or "harness",
                                        "external_job_id": job_obj.external_job_id or job_obj.id,
                                        "tailored_resume_path": job_obj.tailored_resume_path,
                                        "matched_skills": job_obj.matched_skills,
                                    }
                                )
                        except CloudAuthError as exc:
                            logger.debug("Falha ao atualizar vaga %s no Supabase: %s", res.job_id, exc)
                    if missing_in_sb:
                        try:
                            push_res = await cloud_mgr.sync_jobs_to_supabase(missing_in_sb)
                            sb_updated += push_res.get("inserted", 0)
                        except CloudAuthError as exc:
                            logger.debug("Falha ao inserir resultados no Supabase: %s", exc)
                    if sb_updated > 0:
                        self.log("INFO", f"{sb_updated} candidatura(s) sincronizada(s) com o Supabase.")

                if (
                    not custom_url
                    and local_sheet_path is not None
                    and local_sheet_path.exists()
                    and is_google_sheet_source(sheet_input)
                ):
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
            with self._thread_lock:
                self.is_running = False
                self.end_time = time.perf_counter()
                self.active_jobs.clear()
                self._task = None
                self.laya_live_meta["active"] = False
            self.broadcast("status_change", self.get_status())


# Instância singleton global do runner para ser consumida pela API
global_runner = HarnessRunner()
