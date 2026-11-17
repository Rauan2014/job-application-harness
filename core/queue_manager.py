"""Gerenciador de fila assíncrona com concorrência limitada e política estrita de retentativas."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from config.settings import Settings
from core.models import (
    ApplicationResult,
    Job,
    PostSubmitError,
    Profile,
    TaskStatus,
    TransientError,
)
from core.pipeline import Pipeline

logger = logging.getLogger(__name__)


class QueueManager:
    """Controla a execução paralela de candidaturas respeitando limites e isolamento de escrita."""

    def __init__(
        self,
        settings: Settings,
        pipeline: Pipeline,
        profile: Profile,
        sleep_fn: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.profile = profile
        self._sleep = sleep_fn
        self._lock = asyncio.Lock()
        self._processed_job_ids: set[str] = set()
        self._in_flight_job_ids: set[str] = set()
        self.results: list[ApplicationResult] = []

    async def _persist_result(self, result: ApplicationResult) -> None:
        """Grava atomicamente o resultado no arquivo JSONL de resultados."""
        line = result.model_dump_json() + "\n"
        out_file = self.settings.RESULTS_FILE
        async with self._lock:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with out_file.open("a", encoding="utf-8") as f:
                f.write(line)

    async def _process_single_job_with_retries(
        self,
        job: Job,
        on_step: Callable[[Job, str], Any] | None = None,
    ) -> ApplicationResult:
        """Executa uma vaga com política de retry: transientes pré-submit têm retry; pós-submit NUNCA."""
        max_attempts = max(1, self.settings.MAX_RETRIES + 1)
        last_result: ApplicationResult | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                job_kwargs: dict[str, Any] = {"job": job, "profile": self.profile}
                if on_step is not None:
                    job_kwargs["on_step"] = on_step
                result = await asyncio.wait_for(
                    self.pipeline.process_job(**job_kwargs),
                    timeout=self.settings.TASK_TIMEOUT_S,
                )
                result.attempts = attempt
                # Se completou ou pulou ou foi para revisão sem erro transitório
                last_result = result
                break

            except TimeoutError:
                logger.error(
                    "Timeout (%ss) no processamento do job %s. RETENTATIVA PROIBIDA.",
                    self.settings.TASK_TIMEOUT_S,
                    job.id,
                )
                last_result = ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.NEEDS_REVIEW,
                    reason=f"Timeout de {self.settings.TASK_TIMEOUT_S}s esgotado (NÃO RETENTAR)",
                    dry_run=self.settings.DRY_RUN,
                    submitted=False,
                    attempts=attempt,
                )
                break

            except TransientError as exc:
                logger.warning(
                    "Erro transitório no job %s (tentativa %d/%d): %s",
                    job.id,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    backoff = self.settings.BACKOFF_BASE_S * (2 ** (attempt - 1))
                    await self._sleep(backoff)
                else:
                    last_result = ApplicationResult(
                        job_id=job.id,
                        status=TaskStatus.FAILED,
                        reason=f"Esgotadas {max_attempts} tentativas por erro transitório: {exc}",
                        dry_run=self.settings.DRY_RUN,
                        attempts=attempt,
                    )

            except PostSubmitError as exc:
                # Regra de Ouro: NUNCA retentar se o submit foi disparado
                logger.error("Erro pós-submissão no job %s. RETENTATIVA PROIBIDA: %s", job.id, exc)
                last_result = ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.NEEDS_REVIEW,
                    reason=f"Erro pós-submissão (NÃO RETENTAR): {exc}",
                    dry_run=self.settings.DRY_RUN,
                    submitted=True,
                    attempts=attempt,
                )
                break

            except Exception as exc:
                logger.exception("Erro inesperado no processamento do job %s: %s", job.id, exc)
                last_result = ApplicationResult(
                    job_id=job.id,
                    status=TaskStatus.FAILED,
                    reason=f"Erro não tratado: {type(exc).__name__}: {exc}",
                    dry_run=self.settings.DRY_RUN,
                    attempts=attempt,
                )
                break

        assert last_result is not None
        await self._persist_result(last_result)
        async with self._lock:
            self.results.append(last_result)
            self._processed_job_ids.add(last_result.job_id)
        return last_result

    async def run(
        self,
        jobs: list[Job],
        limit: int | None = None,
        on_progress: Callable[[Job, ApplicationResult], Any] | None = None,
        on_step: Callable[[Job, str], Any] | None = None,
    ) -> list[ApplicationResult]:
        """Processa a lista de vagas com concorrência limitada por Semaphore e idempotência estrita."""
        raw_slice = jobs[:limit] if limit is not None else jobs
        # Deduplica lista de vagas preservando a ordem
        seen: set[str] = set()
        jobs_to_process: list[Job] = []
        for j in raw_slice:
            if j.id not in seen:
                seen.add(j.id)
                jobs_to_process.append(j)

        if not jobs_to_process:
            logger.info("Nenhuma vaga para processar.")
            return []

        semaphore = asyncio.Semaphore(self.settings.MAX_CONCURRENCY)

        async def worker(job: Job) -> ApplicationResult:
            async with self._lock:
                if job.id in self._processed_job_ids or job.id in self._in_flight_job_ids:
                    logger.info("Vaga '%s' já processada ou em execução. Ignorando para idempotência.", job.id)
                    for r in self.results:
                        if r.job_id == job.id:
                            return r
                    return ApplicationResult(
                        job_id=job.id,
                        status=TaskStatus.SKIPPED,
                        reason="Vaga já processada anteriormente (idempotência garantida).",
                        dry_run=self.settings.DRY_RUN,
                    )
                self._in_flight_job_ids.add(job.id)

            async with semaphore:
                try:
                    res = await self._process_single_job_with_retries(job, on_step=on_step)
                finally:
                    async with self._lock:
                        self._in_flight_job_ids.discard(job.id)

                if on_progress:
                    try:
                        callback_res = on_progress(job, res)
                        if asyncio.iscoroutine(callback_res):
                            await callback_res
                    except Exception as cb_exc:
                        logger.debug("Erro no callback de progresso: %s", cb_exc)
                return res

        tasks = [asyncio.create_task(worker(job)) for job in jobs_to_process]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=False)
            return results
        except asyncio.CancelledError:
            logger.warning("Execução cancelada. Cancelando tarefas pendentes...")
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return list(self.results)

    def get_summary(self) -> dict[str, Any]:
        """Calcula estatísticas de execução."""
        total = len(self.results)
        by_status: dict[str, int] = {}
        submitted_count = 0
        dry_run_count = 0

        for r in self.results:
            by_status[r.status.value] = by_status.get(r.status.value, 0) + 1
            if r.submitted:
                submitted_count += 1
            if r.dry_run:
                dry_run_count += 1

        return {
            "total": total,
            "completed": by_status.get(TaskStatus.COMPLETED.value, 0),
            "skipped": by_status.get(TaskStatus.SKIPPED.value, 0),
            "needs_review": by_status.get(TaskStatus.NEEDS_REVIEW.value, 0),
            "failed": by_status.get(TaskStatus.FAILED.value, 0),
            "submitted_live": submitted_count,
            "dry_run": dry_run_count,
        }
