"""Daemon de execução contínua para processamento de candidaturas na nuvem (Candidata Cloud Worker).

Consome vagas da fila do Supabase via RPC worker_claim_next_job (concorrência segura SKIP LOCKED),
executa o fluxo no navegador (Camoufox/Chromium), gera respostas com IA e registra telemetria e comprovantes.
"""

from __future__ import annotations

import asyncio
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

from automation.browser import BrowserManager
from config.settings import Settings
from core.decision.layer import DecisionLayer
from core.matcher import JobMatcher
from core.models import Job, Profile, ResumeInfo, TaskStatus
from core.pdf_text import extract_text_from_pdf
from core.pipeline import Pipeline
from core.resume_resolver import ResumeResolver
from storage.supabase_client import SupabaseCloudClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Worker] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("candidata_cloud.worker")


class CloudWorker:
    """Orquestrador do ciclo contínuo de processamento de vagas em produção."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = os.getenv("WORKER_ID") or f"worker-{socket.gethostname()}-{os.getpid()}"
        self.poll_interval = float(os.getenv("POLL_INTERVAL_S", "5.0"))
        self.running = True

        # Validação de credenciais do Supabase
        self.supabase_url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")
        self.service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

        if not self.supabase_url or not self.service_role_key:
            logger.error("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórios para o modo Cloud Worker!")
            sys.exit(1)

        self.supabase = SupabaseCloudClient(self.supabase_url, self.service_role_key)
        self.browser_manager = BrowserManager(settings)
        self.matcher = JobMatcher(settings)
        self.decision_layer = DecisionLayer(settings)
        self.resume_resolver = ResumeResolver(settings)
        self.pipeline: Pipeline | None = None

        self._cache_dir = Path(settings.CACHE_DIR)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Inicializa componentes pesados (Navegador e Pipeline)."""
        logger.info("Iniciando Worker ID: %s", self.worker_id)
        logger.info("Motor do navegador: %s (Headless: %s)", "Camoufox Anti-Detect" if self.settings.USE_CAMOUFOX else "Playwright Chromium", self.settings.HEADLESS)
        logger.info("Modelo LLM: %s", self.settings.LLM_MODEL)

        await self.browser_manager.start()
        self.pipeline = Pipeline(
            settings=self.settings,
            decision_layer=self.decision_layer,
            resume_resolver=self.resume_resolver,
            browser_manager=self.browser_manager,
            matcher=self.matcher,
        )

    async def _resolve_worker_profile(self, profile_row: dict[str, Any]) -> Profile:
        """Converte o registro do banco de dados no modelo Profile de domínio."""
        links = profile_row.get("links") or {}
        if not isinstance(links, dict):
            links = {}

        base_answers = profile_row.get("base_answers") or {}
        if not isinstance(base_answers, dict):
            base_answers = {}

        eeo = profile_row.get("eeo") or {}
        if not isinstance(eeo, dict):
            eeo = {}

        return Profile(
            full_name=profile_row.get("full_name") or "Candidato",
            email=profile_row.get("email") or "",
            phone=profile_row.get("phone") or "",
            location=profile_row.get("location") or "Brasil",
            headline=profile_row.get("headline"),
            links=links,
            work_authorization=profile_row.get("work_authorization") or "",
            salary_expectation=profile_row.get("salary_expectation"),
            skills=profile_row.get("skills") or [],
            base_answers=base_answers,
            eeo=eeo,
        )

    async def _resolve_worker_resume(
        self,
        workspace_id: str,
        profile_id: str,
        resume_id: str | None = None,
    ) -> ResumeInfo | None:
        """Baixa e prepara o currículo PDF para o processamento."""
        resume_row = None
        if resume_id:
            resume_row = await self.supabase.get_candidate_resume(resume_id)
        if not resume_row:
            resume_row = await self.supabase.get_primary_resume(workspace_id, profile_id)

        if not resume_row:
            logger.warning("Nenhum currículo cadastrado no workspace %s", workspace_id)
            return None

        storage_path = resume_row.get("storage_path") or ""
        filename = resume_row.get("filename") or "curriculo.pdf"
        local_dest = self._cache_dir / f"{workspace_id}_{filename}"

        if not local_dest.exists() and storage_path:
            downloaded = await self.supabase.download_resume_file(storage_path, local_dest)
            if not downloaded:
                logger.error("Falha ao obter arquivo PDF do currículo: %s", storage_path)
                return None

        # Extrai texto do PDF
        text = resume_row.get("raw_text") or ""
        if not text and local_dest.exists():
            try:
                text = extract_text_from_pdf(local_dest)
            except Exception as exc:
                logger.warning("Falha ao extrair texto do PDF local %s: %s", local_dest, exc)

        summary = resume_row.get("summary") or f"Currículo de {filename}"
        return ResumeInfo(
            path=local_dest,
            summary=summary,
            text=text,
        )

    async def process_claimed_job(self, job_row: dict[str, Any]) -> None:
        """Processa uma vaga reivindicada da fila."""
        job_id = job_row["id"]
        run_id = job_row.get("run_id")
        workspace_id = job_row["workspace_id"]
        profile_id = job_row["profile_id"]
        company = job_row.get("company") or "Empresa"
        title = job_row.get("title") or "Vaga"
        job_url = job_row.get("job_url") or ""
        is_dry_run = bool(job_row.get("dry_run", True))

        logger.info("Processando vaga %s (%s - %s) [Dry Run: %s]", job_id, company, title, is_dry_run)

        # Atualiza configurações dinâmicas de envio (respeita se a vaga foi disparada como Ensaio ou Real)
        self.settings.DRY_RUN = is_dry_run

        # 1. Carrega Perfil do Candidato
        profile_row = await self.supabase.get_candidate_profile(profile_id)
        if not profile_row:
            logger.error("Perfil do candidato %s não encontrado. Marcando como FAILED.", profile_id)
            await self.supabase.update_job_application(
                job_id,
                {"status": "FAILED", "review_reason": "Perfil do candidato não encontrado no banco de dados."},
            )
            return

        profile = await self._resolve_worker_profile(profile_row)

        # 2. Carrega Currículo PDF
        resume_info = await self._resolve_worker_resume(workspace_id, profile_id, job_row.get("resume_used_id"))

        # Prepara o objeto Job de domínio com campos da planilha e busca
        resume_lbl = job_row.get("resume_label")
        base_used = job_row.get("base_used") or []
        lang = "en" if (resume_lbl and "resume" in str(resume_lbl).lower()) or any("ingles" in str(b).lower() for b in base_used) else "pt"

        job = Job(
            id=job_id,
            row_index=1,
            date=job_row.get("job_date") or "",
            company=company,
            title=title,
            modality=job_row.get("modality") or "",
            url=job_url,
            ats=job_row.get("ats"),
            description=job_row.get("description"),
            fit_score=job_row.get("fit_score"),
            resume_label=resume_lbl,
            base_used=base_used,
            stack=job_row.get("stack") or [],
            language=lang,
            seniority=job_row.get("seniority"),
            source_platform=job_row.get("source_platform"),
            external_job_id=job_row.get("external_job_id"),
            posted_at=job_row.get("posted_at"),
            location=job_row.get("location"),
            salary=job_row.get("salary"),
            notes=job_row.get("notes"),
        )

        # Callback em tempo real para o HUD do usuário (/app)
        async def on_step_callback(_job: Job, step_name: str) -> None:
            if run_id:
                await self.supabase.update_run_telemetry(
                    run_id,
                    {"current_step": f"[{company}] {step_name}"},
                )

        assert self.pipeline is not None, "Pipeline não inicializado"

        # 3. Executa a automação no navegador
        try:
            result = await self.pipeline.process_job(
                job=job,
                profile=profile,
                screenshot_on_review=True,
                on_step=on_step_callback,
            )
        except Exception as exc:
            logger.exception("Erro crítico não tratado ao processar vaga %s: %s", job_id, exc)
            await self.supabase.update_job_application(
                job_id,
                {
                    "status": "FAILED",
                    "review_reason": f"Erro de execução do robô: {str(exc)[:300]}",
                },
            )
            return

        # 4. Upload de Screenshot de Evidência se houver
        screenshot_storage_path = None
        if result.screenshot_path and result.screenshot_path.exists():
            remote_name = f"{workspace_id}/{job_id}_{int(time.time())}.png"
            screenshot_storage_path = await self.supabase.upload_evidence_screenshot(
                result.screenshot_path,
                remote_name,
            )

        # 5. Atualiza o status final da candidatura no Supabase
        update_payload: dict[str, Any] = {
            "status": result.status.value,
            "submitted_live": result.submitted,
            "duration_seconds": result.duration_s or 0,
            "tokens_consumed": result.tokens_used or 0,
            "review_reason": result.reason if result.status == TaskStatus.NEEDS_REVIEW else None,
        }
        if screenshot_storage_path:
            update_payload["screenshot_storage_path"] = screenshot_storage_path

        await self.supabase.update_job_application(job_id, update_payload)

        # 6. Atualiza contadores do lote (automation_runs)
        if run_id:
            tokens_inc = result.tokens_used or 0
            if result.status == TaskStatus.COMPLETED:
                delta_completed = 1
                delta_failed = 0
                delta_review = 0
            elif result.status == TaskStatus.FAILED:
                delta_completed = 0
                delta_failed = 1
                delta_review = 0
            elif result.status == TaskStatus.NEEDS_REVIEW:
                delta_completed = 0
                delta_failed = 0
                delta_review = 1
            else:
                delta_completed = 0
                delta_failed = 0
                delta_review = 0

            # Atualiza telemetria de progresso
            await self.supabase.update_run_telemetry(
                run_id,
                {
                    "current_step": f"Candidatura finalizada em {company} com status {result.status.value}."
                },
            )
            # Verifica se o lote todo foi concluído
            await self.supabase.check_and_finalize_run(run_id)

        logger.info(
            "Vaga %s finalizada: status=%s, duracao=%.1fs, tokens=%s",
            job_id,
            result.status.value,
            result.duration_s or 0,
            result.tokens_used or 0,
        )

    async def run(self) -> None:
        """Loop contínuo de consumo da fila."""
        await self.initialize()
        logger.info("Worker pronto e escutando fila de candidaturas (intervalo: %.1fs)...", self.poll_interval)

        while self.running:
            try:
                job_row = await self.supabase.claim_next_job(self.worker_id)
                if job_row:
                    await self.process_claimed_job(job_row)
                else:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Erro inesperado no loop principal do worker: %s", exc)
                await asyncio.sleep(self.poll_interval)

        logger.info("Encerrando Worker com segurança...")
        await self.cleanup()

    async def cleanup(self) -> None:
        """Libera recursos do navegador e sessões de rede."""
        if self.browser_manager:
            try:
                if self.browser_manager.is_running:
                    # Encerra navegador
                    if hasattr(self.browser_manager, "_browser") and self.browser_manager._browser:
                        await self.browser_manager._browser.close()
            except Exception as exc:
                logger.debug("Erro ao fechar navegador: %s", exc)

        if self.supabase:
            await self.supabase.aclose()
        if self.matcher:
            await self.matcher.aclose()


def main() -> None:
    settings = Settings()
    worker = CloudWorker(settings)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_shutdown(*_args: Any) -> None:
        logger.info("Sinal de encerramento recebido. Parando worker...")
        worker.running = False
        # Cancela tarefas em execução
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # Registra sinais do sistema operacional
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, handle_shutdown)
        loop.add_signal_handler(signal.SIGTERM, handle_shutdown)

    try:
        loop.run_until_complete(worker.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        loop.run_until_complete(worker.cleanup())
        loop.close()
        logger.info("Worker finalizado com sucesso.")


if __name__ == "__main__":
    main()
