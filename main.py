"""Ponto de entrada (CLI) do harness assíncrono de candidaturas em lote."""

from __future__ import annotations

import argparse
import asyncio
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
import json
import logging
import sys
from contextlib import AsyncExitStack, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from automation.browser import BrowserManager
from config.settings import Settings, load_settings
from core.decision.laya_client import LayaSidecarClient
from core.decision.layer import DecisionLayer
from core.matcher import Matcher
from core.models import Profile
from core.pipeline import Pipeline
from core.queue_manager import QueueManager
from core.resume_resolver import ResumeResolver
from storage.drive_client import DriveClient
from storage.sheet_loader import download_google_sheet, is_google_sheet_source, load_jobs
from storage.sheet_writer import write_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ia_candidata")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ia_candidata",
        description="Harness de candidaturas automatizadas com Playwright, Laya e LLM.",
    )
    parser.add_argument(
        "--sheet",
        type=str,
        default=None,
        help="Caminho da planilha de vagas (.xlsx/.csv) ou URL/ID do Google Sheets.",
    )
    parser.add_argument(
        "--include-blank-status",
        dest="include_blank_status",
        action="store_true",
        default=None,
        help="Considera também vagas com status vazio como 'A Fazer' (padrão: ativado via configurações).",
    )
    parser.add_argument(
        "--no-include-blank-status",
        dest="include_blank_status",
        action="store_false",
        help="Ignora vagas com status vazio, considerando apenas status estrito 'A Fazer'.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("data/profile.json"),
        help="Caminho do arquivo JSON com dados do candidato (ver exemplo em data/profile.example.json).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite máximo de vagas a processar nesta execução.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Executa em modo simulação (não clica em submit). Padrão do sistema.",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="ATENÇÃO: Desativa dry-run e envia as candidaturas de verdade nos ATSs.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Número máximo de navegadores/vagas concorrentes.",
    )
    parser.add_argument(
        "--backend",
        choices=["laya", "rules"],
        default=None,
        help="Camada de decisão: 'laya' (sidecar) ou 'rules' (determinístico).",
    )
    parser.add_argument(
        "--browser",
        choices=["camoufox", "chromium"],
        default=None,
        help="Motor de navegação: 'camoufox' (anti-detect, recomendado) ou 'chromium'.",
    )
    parser.add_argument(
        "--sync-drive",
        action="store_true",
        default=False,
        help="Sincroniza currículos do Google Drive antes de iniciar.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        default=False,
        help="Desativa chamadas a LLMs externas (Gemini/OpenAI), operando 100%% com Laya + regras do perfil.",
    )
    parser.add_argument(
        "--include-review-status",
        "--retry-review",
        dest="include_review_status",
        action="store_true",
        default=False,
        help="Inclui vagas marcadas como 'Revisar' além das marcadas como 'A Fazer'.",
    )
    parser.add_argument(
        "--include-failed",
        action="store_true",
        default=False,
        help="Inclui vagas marcadas como 'Falhou' para nova tentativa.",
    )
    parser.add_argument(
        "--retry-all",
        "--all",
        dest="retry_all",
        action="store_true",
        default=False,
        help="Processa todas as vagas pendentes na planilha ('A Fazer', 'Revisar', 'Falhou' e vazias).",
    )
    parser.add_argument(
        "--autonomous",
        dest="autonomous",
        action="store_true",
        default=True,
        help="Modo autônomo (padrão): sintetiza respostas para campos obrigatórios e não interrompe por revisão humana.",
    )
    parser.add_argument(
        "--no-autonomous",
        dest="autonomous",
        action="store_false",
        help="Desativa o modo autônomo, exigindo revisão humana caso faltem dados.",
    )
    return parser.parse_args(argv)


def _print_summary(summary: dict[str, Any], sheet_copy: Path) -> None:
    print("\n" + "=" * 65)
    print("           RESUMO DA EXECUÇÃO DAS CANDIDATURAS")
    print("=" * 65)
    print(f" Total de vagas processadas:  {summary.get('total', 0)}")
    print(f" Concluídas com sucesso:      {summary.get('completed', 0)}")
    print(f" Ignoradas (skip/triagem):    {summary.get('skipped', 0)}")
    print(f" Requerem revisão humana:     {summary.get('needs_review', 0)}")
    print(f" Falhas de processamento:     {summary.get('failed', 0)}")
    print("-" * 65)
    print(f" Submissões reais enviadas:   {summary.get('submitted_live', 0)}")
    print(f" Simulações (dry-run):        {summary.get('dry_run', 0)}")
    print("-" * 65)
    print(f" Cópia da planilha salva em:  {sheet_copy.resolve()}")
    print("=" * 65 + "\n")


async def run_harness(args: argparse.Namespace) -> int:
    """Executa o ciclo completo do harness."""
    # 1. Carrega configurações com overrides da CLI
    overrides: dict[str, Any] = {}
    if args.dry_run is not None:
        overrides["DRY_RUN"] = args.dry_run
    if args.concurrency is not None:
        overrides["MAX_CONCURRENCY"] = args.concurrency
    if args.backend is not None:
        overrides["DECISION_BACKEND"] = args.backend
    if args.browser is not None:
        overrides["USE_CAMOUFOX"] = (args.browser == "camoufox")
    if getattr(args, "no_llm", False):
        overrides["NO_LLM"] = True
    if getattr(args, "autonomous", None) is not None:
        overrides["AUTONOMOUS_MODE"] = args.autonomous
    if getattr(args, "include_blank_status", None) is not None:
        overrides["INCLUDE_BLANK_STATUS"] = args.include_blank_status

    try:
        settings: Settings = load_settings(**overrides)
        settings.ensure_dirs()
        if settings.NO_LLM:
            logger.info("Modo 100% local ativado (--no-llm): Laya + regras do perfil (sem requisições externas).")
        if settings.AUTONOMOUS_MODE:
            logger.info("Modo autônomo ativado: resolução inteligente de formulários sem interrupção humana.")
    except Exception as exc:
        logger.error(
            "Erro ao carregar configurações (.env ausente ou variáveis obrigatórias faltando):\n%s\n"
            "Verifique se o arquivo .env existe com LLM_API_KEY, LLM_BASE_URL, LLM_MODEL e DRIVE_FOLDER_ID.",
            exc,
        )
        return 1

    # 2. Carrega perfil do candidato
    if not args.profile.exists():
        logger.error("Arquivo de perfil não encontrado em '%s'", args.profile)
        return 1

    try:
        profile = Profile.model_validate_json(args.profile.read_text(encoding="utf-8"))
        logger.info("Perfil do candidato '%s' carregado com sucesso.", profile.full_name)
    except Exception as exc:
        logger.error("Erro ao ler JSON de perfil em '%s': %s", args.profile, exc)
        return 1

    # 3. Determina a planilha de vagas (argumento CLI -> GOOGLE_SHEET_URL no .env -> data/vagas.xlsx)
    sheet_input = args.sheet or settings.GOOGLE_SHEET_URL or "data/vagas.xlsx"
    include_blank = settings.INCLUDE_BLANK_STATUS

    if is_google_sheet_source(sheet_input):
        logger.info("Detectada fonte do Google Sheets ('%s').", sheet_input)
        try:
            local_sheet_path = download_google_sheet(
                sheet_input,
                dest_path=Path("cache/vagas_sheets_latest.xlsx"),
                drive_service=None,
                service_account_file=settings.GOOGLE_SERVICE_ACCOUNT_FILE,
                oauth_client_file=settings.GOOGLE_OAUTH_CLIENT_FILE,
                token_file=settings.GOOGLE_TOKEN_FILE,
            )
        except Exception as exc:
            logger.error("Erro ao baixar planilha do Google Sheets: %s", exc)
            return 1
    else:
        local_sheet_path = Path(sheet_input)
        if not local_sheet_path.exists():
            logger.error("Planilha de vagas não encontrada em '%s'.", sheet_input)
            return 1

    retry_all = getattr(args, "retry_all", False)
    include_review = retry_all or getattr(args, "include_review_status", False)
    include_failed = retry_all or getattr(args, "include_failed", False)
    include_blank = retry_all or include_blank

    try:
        jobs = load_jobs(
            local_sheet_path,
            only_todo=not retry_all,
            include_blank=include_blank,
            include_review=include_review,
            include_failed=include_failed,
            drive_service=None,
        )
        logger.info("%d vagas carregadas para processamento da planilha '%s'.", len(jobs), local_sheet_path)
    except Exception as exc:
        logger.error("Erro ao carregar planilha de vagas: %s", exc)
        return 1

    if args.limit:
        jobs = jobs[:args.limit]
        logger.info("Limite de execução aplicado: %d vagas selecionadas.", len(jobs))

    # 4. Sincronização opcional do Drive
    if args.sync_drive:
        logger.info("Sincronizando currículos do Google Drive...")
        try:
            drive_client = DriveClient.from_settings(settings)
            await drive_client.sync_resumes()
            logger.info("Sincronização com o Drive finalizada.")
        except Exception as exc:
            logger.warning("Falha na sincronização do Google Drive (seguindo com cache local): %s", exc)

    # 5. Inicialização do resolvedor de currículos locais
    resolver = ResumeResolver(
        cache_dir=settings.CACHE_DIR,
        threshold=settings.RESUME_MATCH_THRESHOLD,
    )
    resolver.load_cache()
    logger.info("%d currículos indexados no cache local.", len(resolver.get_resumes()))

    results: list[Any] = []
    queue_mgr: QueueManager | None = None
    out_sheet_path = settings.SHEET_RESULT_FILE

    try:
        async with AsyncExitStack() as stack:
            # 6. Inicialização da camada de decisão
            decision_model = None
            if settings.DECISION_BACKEND == "laya":
                from core.decision.laya_service import ensure_laya_running

                is_up, laya_proc = await ensure_laya_running(
                    base_url=settings.LAYA_URL,
                    auto_start=True,
                    wait_timeout_s=30.0,
                )
                if laya_proc is not None:
                    stack.callback(laya_proc.terminate)

                if is_up:
                    candidate_model = LayaSidecarClient(settings=settings)
                    stack.push_async_callback(candidate_model.aclose)
                    warm_ok = await candidate_model.warmup()
                    if warm_ok:
                        decision_model = candidate_model
                        logger.info("Modelo de decisão neural Laya ativado com sucesso.")
                    else:
                        logger.warning(
                            "Warmup do Laya falhou. Alternando automaticamente para backend determinístico ('rules')."
                        )
                        decision_model = None
                else:
                    logger.warning(
                        "Sidecar Laya não está acessível na porta %s. Alternando automaticamente para backend "
                        "determinístico ('rules'). Para ativar o modelo neural local, execute 'laya-mcp serve'.",
                        settings.LAYA_URL,
                    )
                    decision_model = None

            eval_report = {}
            if settings.LAYA_EVAL_FILE.exists():
                with suppress(Exception):
                    eval_report = json.loads(settings.LAYA_EVAL_FILE.read_text(encoding="utf-8"))

            decision_layer = DecisionLayer(
                model=decision_model,
                settings=settings,
                eval_report=eval_report,
            )

            # 7. Cliente LLM
            matcher = Matcher(settings=settings)
            stack.push_async_callback(matcher.aclose)

            # 8. Execução do pipeline via QueueManager com BrowserManager
            logger.info(
                "Iniciando processamento (concorrência=%d, dry_run=%s, backend=%s)...",
                settings.MAX_CONCURRENCY,
                settings.DRY_RUN,
                settings.DECISION_BACKEND,
            )

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

            results = await queue_mgr.run(jobs)
    finally:
        if queue_mgr is not None:
            if not results and queue_mgr.results:
                results = list(queue_mgr.results)

            # 9. Grava cópia dos resultados na planilha local
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            artifacts_dir = Path("artifacts")
            await asyncio.to_thread(artifacts_dir.mkdir, parents=True, exist_ok=True)
            copy_path = artifacts_dir / f"planilha_atualizada_{timestamp}.xlsx"

            jobs_map = {j.id: j for j in jobs}
            try:
                out_sheet_path = write_results(
                    source_path=local_sheet_path,
                    results=results,
                    out_path=copy_path,
                    jobs=jobs_map,
                )
                if copy_path != settings.SHEET_RESULT_FILE:
                    import shutil

                    shutil.copy2(out_sheet_path, settings.SHEET_RESULT_FILE)
            except Exception as exc:
                logger.error("Erro ao gravar cópia da planilha com os resultados: %s", exc)
                out_sheet_path = settings.SHEET_RESULT_FILE

            # 10. Se a fonte for Google Sheets, sincroniza os resultados diretamente na planilha online
            if is_google_sheet_source(sheet_input):
                from storage.sheet_loader import extract_spreadsheet_id, find_header, read_raw
                from storage.sheet_writer import update_google_sheet_results

                spreadsheet_id = extract_spreadsheet_id(sheet_input)
                if spreadsheet_id:
                    logger.info(
                        "Sincronizando status atualizados diretamente no Google Sheets online (%s)...",
                        spreadsheet_id,
                    )
                    try:
                        from storage.drive_client import build_sheets_service

                        service_file = settings.GOOGLE_SERVICE_ACCOUNT_FILE
                        oauth_file = settings.GOOGLE_OAUTH_CLIENT_FILE
                        token_file = settings.GOOGLE_TOKEN_FILE
                        sheets_svc = build_sheets_service(
                            service_file,
                            oauth_file if (oauth_file and oauth_file.exists()) else None,
                            token_file=token_file,
                            interactive=False,
                        )

                        raw = read_raw(local_sheet_path)
                        header_pos, columns = find_header(raw)
                        header_row = raw.rows[header_pos][0]

                        update_google_sheet_results(
                            spreadsheet_id=spreadsheet_id,
                            results=results,
                            jobs=jobs_map,
                            columns=columns,
                            header_row=header_row,
                            sheets_service=sheets_svc,
                        )
                        print(f"[OK] Google Sheets online atualizado com sucesso! ({sheet_input})")
                    except Exception as exc:
                        logger.warning(
                            "Não foi possível sincronizar no Google Sheets online: %s\n"
                            "Para habilitar, ative a 'Google Sheets API' no Cloud Console e rode auth_google.py.\n"
                            "A cópia local foi salva em: %s",
                            exc,
                            copy_path.resolve(),
                        )

            # 11. Exibe resumo no terminal
            _print_summary(queue_mgr.get_summary(), out_sheet_path)
    return 0


def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(run_harness(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
