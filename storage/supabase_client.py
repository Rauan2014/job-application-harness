"""Cliente assíncrono para integração do worker com o backend Supabase (PostgREST + Storage).

Executa operações de fila (RPC worker_claim_next_job), leitura de perfil/currículo,
upload de screenshots de evidência e atualização de telemetria em tempo real.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import httpx

logger = logging.getLogger("ia_candidata.supabase")


class SupabaseCloudClient:
    """Cliente assíncrono leve e robusto para o backend Supabase da Candidata Cloud."""

    def __init__(self, supabase_url: str, service_role_key: str, timeout_s: float = 30.0) -> None:
        self.base_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        self.rest_url = f"{self.base_url}/rest/v1"
        self.storage_url = f"{self.base_url}/storage/v1"

        self._headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._client: httpx.AsyncClient | None = None
        self._timeout_s = timeout_s

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=self._timeout_s,
            )
        return self._client

    async def aclose(self) -> None:
        """Encerra a sessão HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # =========================================================================
    # Fila de Execução (Queue Locking)
    # =========================================================================

    async def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        """Invoca a RPC worker_claim_next_job via transação segura SKIP LOCKED."""
        client = await self._get_client()
        url = f"{self.rest_url}/rpc/worker_claim_next_job"
        payload = {"p_worker_id": worker_id}

        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                jobs = resp.json()
                if isinstance(jobs, list) and len(jobs) > 0:
                    job = jobs[0]
                    extra = (job.get("answers_payload") or {}).get("spreadsheet_fields") or {}
                    for k, v in extra.items():
                        if job.get(k) is None:
                            job[k] = v
                    return job
            elif resp.status_code != 404:
                logger.error("Erro ao reivindicar vaga da fila (HTTP %d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Falha de rede ao conectar no Supabase RPC worker_claim_next_job: %s", exc)

        return None

    async def fetch_pending_jobs(self, workspace_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Busca vagas pendentes cadastradas no Supabase job_applications."""
        client = await self._get_client()
        url = f"{self.rest_url}/job_applications?status=eq.PENDING&order=created_at.asc&limit={limit}"
        if workspace_id:
            url += f"&workspace_id=eq.{workspace_id}"

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for row in data:
                        extra = (row.get("answers_payload") or {}).get("spreadsheet_fields") or {}
                        for k, v in extra.items():
                            if row.get(k) is None:
                                row[k] = v
                    return data
                return []
            logger.error("Erro ao listar vagas pendentes (HTTP %d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Falha ao buscar vagas pendentes no Supabase: %s", exc)

        return []

    async def insert_job_applications(self, jobs_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insere novas vagas diretamente na tabela job_applications do Supabase com fallback inteligente."""
        if not jobs_payload:
            return []
        client = await self._get_client()
        url = f"{self.rest_url}/job_applications"

        try:
            resp = await client.post(url, json=jobs_payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                return data if isinstance(data, list) else []

            # Se falhou por colunas não migradas no cache de schema remoto (PGRST204)
            if resp.status_code == 400 and "PGRST204" in resp.text:
                logger.info("Detectado cache sem colunas novas (migration 0004). Aplicando fallback seguro para answers_payload.")
                fallback_payload = []
                for j in jobs_payload:
                    item = dict(j)
                    extra = {
                        "modality": item.pop("modality", None),
                        "resume_label": item.pop("resume_label", None),
                        "base_used": item.pop("base_used", []),
                        "stack": item.pop("stack", []),
                        "job_date": item.pop("job_date", None),
                        "description": item.pop("description", None),
                        "location": item.pop("location", None),
                        "salary": item.pop("salary", None),
                        "seniority": item.pop("seniority", None),
                        "source_platform": item.pop("source_platform", None),
                        "posted_at": item.pop("posted_at", None),
                        "external_job_id": item.pop("external_job_id", None),
                        "match_breakdown": item.pop("match_breakdown", None),
                        "cover_letter": item.pop("cover_letter", None),
                        "application_stage": item.pop("application_stage", None),
                        "confirmation_code": item.pop("confirmation_code", None),
                        "notes": item.pop("notes", None),
                        "metadata": item.pop("metadata", None),
                    }
                    answers = item.get("answers_payload") or {}
                    if not isinstance(answers, dict):
                        answers = {}
                    answers["spreadsheet_fields"] = {k: v for k, v in extra.items() if v is not None}
                    item["answers_payload"] = answers
                    fallback_payload.append(item)

                retry_resp = await client.post(url, json=fallback_payload)
                if retry_resp.status_code in (200, 201):
                    data = retry_resp.json()
                    return data if isinstance(data, list) else []
                logger.error("Erro no fallback de inserção (HTTP %d): %s", retry_resp.status_code, retry_resp.text)
            else:
                logger.error("Erro ao inserir vagas no Supabase (HTTP %d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Falha de rede ao inserir vagas no Supabase: %s", exc)

        return []

    # =========================================================================
    # Perfil e Currículo do Candidato
    # =========================================================================

    async def get_candidate_profile(self, profile_id: str) -> dict[str, Any] | None:
        """Obtém dados completos do perfil do candidato."""
        client = await self._get_client()
        url = f"{self.rest_url}/candidate_profiles?id=eq.{profile_id}&select=*"

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                profiles = resp.json()
                if profiles and len(profiles) > 0:
                    return profiles[0]
        except Exception as exc:
            logger.error("Erro ao buscar perfil %s: %s", profile_id, exc)

        return None

    async def get_candidate_resume(self, resume_id: str) -> dict[str, Any] | None:
        """Busca metadados do currículo por ID."""
        client = await self._get_client()
        url = f"{self.rest_url}/candidate_resumes?id=eq.{resume_id}&select=*"

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                resumes = resp.json()
                if resumes and len(resumes) > 0:
                    return resumes[0]
        except Exception as exc:
            logger.error("Erro ao buscar currículo %s: %s", resume_id, exc)

        return None

    async def get_primary_resume(self, workspace_id: str, profile_id: str) -> dict[str, Any] | None:
        """Busca o currículo principal marcado como is_primary no workspace."""
        client = await self._get_client()
        url = (
            f"{self.rest_url}/candidate_resumes?"
            f"workspace_id=eq.{workspace_id}&profile_id=eq.{profile_id}&order=is_primary.desc,created_at.desc&limit=1"
        )

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                resumes = resp.json()
                if resumes and len(resumes) > 0:
                    return resumes[0]
        except Exception as exc:
            logger.error("Erro ao buscar currículo principal: %s", exc)

        return None

    async def download_resume_file(self, storage_path: str, local_dest: Path) -> Path | None:
        """Faz download do arquivo PDF do Supabase Storage bucket 'resumes'."""
        client = await self._get_client()
        url = f"{self.storage_url}/object/authenticated/resumes/{storage_path}"

        try:
            local_dest.parent.mkdir(parents=True, exist_ok=True)
            resp = await client.get(url)
            if resp.status_code == 200:
                local_dest.write_bytes(resp.content)
                logger.info("Currículo baixado com sucesso: %s", local_dest)
                return local_dest
            else:
                logger.error("Falha ao baixar PDF do Storage (HTTP %d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Erro ao baixar currículo do Storage (%s): %s", storage_path, exc)

        return None

    # =========================================================================
    # Upload de Evidências (Screenshots)
    # =========================================================================

    async def upload_evidence_screenshot(self, local_path: Path, remote_path: str) -> str | None:
        """Faz upload do screenshot comprovante para o bucket 'evidence-screenshots'."""
        if not local_path.exists():
            logger.warning("Arquivo de screenshot não encontrado para upload: %s", local_path)
            return None

        client = await self._get_client()
        url = f"{self.storage_url}/object/evidence-screenshots/{remote_path}"

        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "image/png" if local_path.suffix.lower() == ".png" else "image/jpeg",
            "x-upsert": "true",
        }

        try:
            data = local_path.read_bytes()
            resp = await client.post(url, content=data, headers=headers)
            if resp.status_code in (200, 201):
                logger.info("Screenshot de evidência enviado: %s", remote_path)
                return remote_path
            else:
                logger.error("Falha ao enviar screenshot para o Storage (HTTP %d): %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Erro no upload do screenshot (%s): %s", remote_path, exc)

        return None

    # =========================================================================
    # Atualização de Vagas e Telemetria
    # =========================================================================

    async def update_job_application(self, job_id: str, fields: dict[str, Any]) -> bool:
        """Atualiza o registro de job_applications com status, evidência e métricas."""
        client = await self._get_client()
        url = f"{self.rest_url}/job_applications?id=eq.{job_id}"

        try:
            resp = await client.patch(url, json=fields)
            if resp.status_code in (200, 204):
                return True
            logger.error("Erro ao atualizar job_applications %s (HTTP %d): %s", job_id, resp.status_code, resp.text)
        except Exception as exc:
            logger.error("Erro de rede ao atualizar job_application %s: %s", job_id, exc)

        return False

    async def update_run_telemetry(self, run_id: str, fields: dict[str, Any]) -> bool:
        """Atualiza progresso e telemetria ao vivo da automação na tabela automation_runs."""
        client = await self._get_client()
        url = f"{self.rest_url}/automation_runs?id=eq.{run_id}"

        try:
            resp = await client.patch(url, json=fields)
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.debug("Erro ao atualizar telemetria do run %s: %s", run_id, exc)

        return False

    async def check_and_finalize_run(self, run_id: str) -> None:
        """Verifica se todas as vagas de um run foram concluídas e fecha a execução."""
        client = await self._get_client()
        try:
            # Conta vagas pendentes ou em processamento associadas a esse run
            url = f"{self.rest_url}/job_applications?run_id=eq.{run_id}&status=in.(PENDING,PROCESSING)&select=id"
            resp = await client.get(url)
            if resp.status_code == 200:
                pending_jobs = resp.json()
                if len(pending_jobs) == 0:
                    # Nenhuma vaga pendente: marca run como concluído
                    from datetime import datetime, timezone

                    now_iso = datetime.now(timezone.utc).isoformat()
                    await self.update_run_telemetry(
                        run_id,
                        {
                            "status": "completed",
                            "current_step": "Todas as candidaturas foram finalizadas com sucesso.",
                            "ended_at": now_iso,
                        },
                    )
                    logger.info("Execução de automação %s concluída e finalizada.", run_id)
        except Exception as exc:
            logger.debug("Erro ao verificar finalização do run %s: %s", run_id, exc)
