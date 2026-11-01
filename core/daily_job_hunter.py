"""Rotina agendada de busca ativa diária de vagas na web para o Candidata IA.

Executa diariamente às 11:00 (ou sob demanda via --run-now):
1. Lê o perfil ativo e as habilidades do candidato no Supabase (candidate_profiles).
2. Realiza busca ativa na web (GitHub Tech BR, Jobicy, RemoteOK) para cada tecnologia do candidato.
3. Remove vagas duplicadas ou já cadastradas no workspace.
4. Salva as novas vagas diretamente no Supabase (`job_applications`) com status PENDING e todos os campos da planilha.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import sys
import time
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import httpx
from core.job_search import persist_jobs_to_supabase, search_jobs
from storage.supabase_client import SupabaseCloudClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [JobHunter] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ia_candidata.daily_job_hunter")


async def get_candidate_target_skills(
    supabase: SupabaseCloudClient, workspace_id: str, profile_id: str
) -> tuple[str, list[str]]:
    """Obtém nome do candidato e suas principais habilidades cadastradas."""
    client = await supabase._get_client()
    url = f"{supabase.rest_url}/candidate_profiles?id=eq.{profile_id}&select=full_name,skills,headline"

    try:
        resp = await client.get(url)
        if resp.status_code == 200 and resp.json():
            data = resp.json()[0]
            name = data.get("full_name") or "Candidato"
            skills = data.get("skills") or []
            if isinstance(skills, list) and skills:
                return name, skills
    except Exception as exc:
        logger.warning("Falha ao ler skills do perfil no Supabase: %s", exc)

    return "Candidato", ["Python", "FastAPI", "React", "TypeScript"]


async def run_daily_job_hunt(
    workspace_id: str = "",
    profile_id: str = "",
    target_limit_per_query: int = 10,
) -> int:
    """Executa a rotina de busca ativa na web e salva novas vagas no Supabase."""
    url = (os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")).rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    if not url or not key:
        logger.error("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórios.")
        return 0

    supabase = SupabaseCloudClient(url, key)

    # 1. Resolver Workspace e Profile se não fornecidos
    if not workspace_id or not profile_id:
        client = await supabase._get_client()
        try:
            if not workspace_id:
                r_ws = await client.get(f"{supabase.rest_url}/workspaces?select=id&limit=1")
                if r_ws.status_code == 200 and r_ws.json():
                    workspace_id = r_ws.json()[0]["id"]
            if not profile_id and workspace_id:
                r_pf = await client.get(
                    f"{supabase.rest_url}/candidate_profiles?workspace_id=eq.{workspace_id}&select=id&limit=1"
                )
                if r_pf.status_code == 200 and r_pf.json():
                    profile_id = r_pf.json()[0]["id"]
        except Exception as exc:
            logger.error("Erro ao resolver workspace/profile: %s", exc)
            return 0

    if not workspace_id or not profile_id:
        logger.error("Workspace e Profile não localizados no Supabase.")
        return 0

    candidate_name, skills = await get_candidate_target_skills(supabase, workspace_id, profile_id)
    logger.info("Iniciando rotina de busca ativa para %s (Skills: %s)...", candidate_name, ", ".join(skills[:5]))

    # 2. Consultar vagas já existentes no workspace para não duplicar
    client = await supabase._get_client()
    existing_urls = set()
    try:
        r_exist = await client.get(
            f"{supabase.rest_url}/job_applications?workspace_id=eq.{workspace_id}&select=job_url&limit=1000"
        )
        if r_exist.status_code == 200 and isinstance(r_exist.json(), list):
            for row in r_exist.json():
                u = row.get("job_url")
                if u:
                    existing_urls.add(u)
    except Exception as exc:
        logger.warning("Falha ao obter histórico de vagas existentes: %s", exc)

    logger.info("%d vagas já cadastradas anteriormente no workspace.", len(existing_urls))

    # 3. Executar buscas na web por stack
    # Seleciona as stacks principais do candidato (ex: Python, React, etc.)
    search_terms = skills[:3] if skills else ["Python", "React"]
    all_found_jobs: list[dict[str, Any]] = []

    for term in search_terms:
        logger.info("Buscando oportunidades abertas na web para '%s'...", term)
        found = await search_jobs(query=term, market="all", limit=target_limit_per_query)
        logger.info("  -> %d vagas retornadas para '%s'.", len(found), term)
        all_found_jobs.extend(found)

    # 4. Deduplicar por URL e remover as que já estão no Supabase
    novel_jobs: list[dict[str, Any]] = []
    seen_in_batch = set()

    for job in all_found_jobs:
        u = job.get("job_url")
        if u and u not in existing_urls and u not in seen_in_batch:
            seen_in_batch.add(u)
            novel_jobs.append(job)

    logger.info(
        "Total de vagas coletadas: %d | Novas vagas inéditas para o workspace: %d",
        len(all_found_jobs),
        len(novel_jobs),
    )

    if not novel_jobs:
        logger.info("Nenhuma vaga inédita nova encontrada nesta rodada. Base já está atualizada.")
        return 0

    # 5. Persistir no Supabase com todos os campos da planilha
    saved_count = await persist_jobs_to_supabase(
        jobs=novel_jobs,
        workspace_id=workspace_id,
        profile_id=profile_id,
        supabase_client=supabase,
    )

    logger.info(
        "🚀 Sucesso! %d novas vagas abastecidas diretamente no Supabase em 'job_applications' (status = PENDING)!",
        saved_count,
    )
    return saved_count


def calculate_seconds_until_target_time(target_hour: int = 11, target_minute: int = 0) -> float:
    """Calcula quantos segundos faltam até o próximo horário alvo (ex: 11:00)."""
    now = datetime.datetime.now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


async def run_scheduler_loop(target_hour: int = 11, target_minute: int = 0) -> None:
    """Loop contínuo que dispara a busca de vagas diariamente no horário especificado."""
    logger.info("⏰ Agendador ativo: busca diária programada para às %02d:%02d todos os dias.", target_hour, target_minute)

    while True:
        wait_seconds = calculate_seconds_until_target_time(target_hour, target_minute)
        next_run = datetime.datetime.now() + datetime.timedelta(seconds=wait_seconds)
        logger.info("Próxima execução agendada para: %s (daqui a %.1f horas)", next_run.strftime("%d/%m/%Y %H:%M:%S"), wait_seconds / 3600.0)

        await asyncio.sleep(wait_seconds)

        logger.info("🔔 Horário atingido (%02d:%02d)! Disparando busca diária na web...", target_hour, target_minute)
        try:
            await run_daily_job_hunt()
        except Exception as exc:
            logger.error("Erro durante a execução da busca diária: %s", exc)

        # Espera 65 segundos para não disparar no mesmo minuto
        await asyncio.sleep(65)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotina diária de busca de vagas na web para Candidata IA.")
    parser.add_argument("--run-now", action="store_true", help="Executa a busca imediatamente sem aguardar o agendamento.")
    parser.add_argument("--hour", type=int, default=11, help="Hora do agendamento diário (padrão: 11).")
    parser.add_argument("--minute", type=int, default=0, help="Minuto do agendamento diário (padrão: 0).")
    parser.add_argument("--workspace-id", type=str, default="", help="ID do workspace no Supabase.")
    parser.add_argument("--profile-id", type=str, default="", help="ID do perfil de candidato no Supabase.")

    args = parser.parse_args()

    if args.run_now:
        logger.info("Modo imediato solicitado (--run-now).")
        asyncio.run(
            run_daily_job_hunt(
                workspace_id=args.workspace_id,
                profile_id=args.profile_id,
            )
        )
    else:
        asyncio.run(run_scheduler_loop(target_hour=args.hour, target_minute=args.minute))


if __name__ == "__main__":
    main()
