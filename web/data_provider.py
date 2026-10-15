"""Provedor de dados para a UI: lê planilhas, histórico de resultados e métricas consolidadas."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config.settings import load_settings
from core.models import ApplicationResult, Job, TaskStatus
from storage.sheet_loader import (
    download_google_sheet,
    extract_spreadsheet_id,
    is_google_sheet_source,
    load_jobs,
    normalize_text,
)
from storage.sheet_writer import sync_sheet_with_local_state

logger = logging.getLogger(__name__)

SHEET_META_FILE = Path("cache/.sheet_source_meta.json")
LATEST_CACHE_XLSX = Path("cache/vagas_sheets_latest.xlsx")


def _read_cached_sheet_id() -> str | None:
    if not SHEET_META_FILE.exists():
        return None
    try:
        data = json.loads(SHEET_META_FILE.read_text(encoding="utf-8"))
        return data.get("spreadsheet_id")
    except Exception:
        return None


def _write_cached_sheet_id(spreadsheet_id: str, url: str) -> None:
    try:
        SHEET_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        SHEET_META_FILE.write_text(
            json.dumps({"spreadsheet_id": spreadsheet_id, "url": url}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("Não foi possível salvar metadados da planilha: %s", exc)


def refresh_sheet_cache(sheet_url: str | None = None, force_download: bool = False) -> Path | None:
    """Garante que a planilha local corresponde ao GOOGLE_SHEET_URL configurado no .env."""
    settings = load_settings()
    target_source = (sheet_url if sheet_url is not None else settings.GOOGLE_SHEET_URL) or ""
    target_source = str(target_source).strip()
    if not target_source:
        return None

    if is_google_sheet_source(target_source):
        sheet_id = extract_spreadsheet_id(target_source)
        if not sheet_id:
            return None
        cached_id = _read_cached_sheet_id()
        needs_download = (
            force_download
            or cached_id != sheet_id
            or not LATEST_CACHE_XLSX.exists()
        )
        if needs_download:
            try:
                downloaded = download_google_sheet(
                    target_source,
                    dest_path=LATEST_CACHE_XLSX,
                    service_account_file=settings.GOOGLE_SERVICE_ACCOUNT_FILE,
                    oauth_client_file=settings.GOOGLE_OAUTH_CLIENT_FILE,
                    token_file=settings.GOOGLE_TOKEN_FILE,
                )
                sync_sheet_with_local_state(
                    downloaded,
                    results_jsonl_path=settings.RESULTS_FILE,
                    extra_out_paths=[settings.SHEET_RESULT_FILE],
                    overwrite_todo=False,
                )
                _write_cached_sheet_id(sheet_id, target_source)
                return downloaded
            except Exception as exc:
                logger.warning("Falha ao atualizar planilha do Google Sheets '%s': %s", target_source, exc)
        if settings.SHEET_RESULT_FILE and settings.SHEET_RESULT_FILE.exists():
            return settings.SHEET_RESULT_FILE
        if LATEST_CACHE_XLSX.exists():
            return LATEST_CACHE_XLSX
        return None

    local_candidate = Path(target_source)
    if local_candidate.exists():
        return local_candidate
    return None


def _detect_ats_label(job: Job) -> str:
    """Identifica o nome da plataforma ATS a partir do campo ats ou da URL da vaga."""
    if job.ats:
        return job.ats.capitalize()
    url = (job.url or "").lower()
    if "lever.co" in url:
        return "Lever"
    if "greenhouse.io" in url:
        return "Greenhouse"
    if "workable.com" in url:
        return "Workable"
    if "ashbyhq.com" in url:
        return "Ashby"
    if "quickin.io" in url:
        return "Quickin"
    if "inhire.app" in url:
        return "Inhire"
    if "solides.com" in url:
        return "Sólides"
    if "indeed.com" in url:
        return "Indeed"
    if "gupy.io" in url:
        return "Gupy"
    if "linkedin.com" in url:
        return "LinkedIn"
    if url.startswith(("http://", "https://")):
        return "Portal Direto"
    return "Desconhecido"


def get_jobs_cache() -> list[dict[str, Any]]:
    """Carrega lista de vagas disponíveis da planilha local ou de cache."""
    settings = load_settings()
    refreshed_path = refresh_sheet_cache(force_download=False)
    sheet_candidates = [
        refreshed_path,
        settings.SHEET_RESULT_FILE,
        LATEST_CACHE_XLSX,
        Path("data/vagas.xlsx"),
    ]

    sheet_path: Path | None = None
    for p in sheet_candidates:
        if p and p.exists():
            sheet_path = p
            break

    if not sheet_path:
        return []

    try:
        jobs = load_jobs(
            sheet_path,
            only_todo=False,
            include_blank=True,
            include_review=True,
            include_failed=True,
            include_completed=True,
        )
        return [
            {
                "id": j.id,
                "row_index": j.row_index,
                "date": j.date,
                "company": j.company,
                "title": j.title,
                "modality": j.modality,
                "url": j.url,
                "ats": _detect_ats_label(j),
                "fit_score": j.fit_score,
                "resume_label": j.resume_label,
                "sheet_status": j.sheet_status or "A Fazer",
                "language": j.language,
            }
            for j in jobs
        ]
    except Exception as exc:
        logger.warning("Falha ao carregar vagas da planilha '%s': %s", sheet_path, exc)
        return []


def _entry_from_sheet_job(job: dict[str, Any]) -> dict[str, Any]:
    """Cria registro de exibição na UI para vagas da planilha ainda não presentes no results.jsonl."""
    norm_st = normalize_text(job.get("sheet_status") or "A Fazer")
    submitted = False
    dry_run = False
    if norm_st in ("candidatado", "aplicado", "ja candidatado", "ja aplicado"):
        status = TaskStatus.COMPLETED.value
        display_status = "Candidatura Enviada"
        submitted = True
        reason = "Registrado na planilha como Candidatado"
    elif norm_st in ("pronta (dry-run)", "pronta (dry run)", "pronta"):
        status = TaskStatus.COMPLETED.value
        display_status = "Simulada com Sucesso"
        dry_run = True
        reason = "Simulada anteriormente na planilha"
    elif norm_st == "revisar":
        status = TaskStatus.NEEDS_REVIEW.value
        display_status = "Revisão Necessária"
        reason = "Marcada para revisão na planilha"
    elif norm_st == "falhou":
        status = TaskStatus.FAILED.value
        display_status = "Falhou / Erro"
        reason = "Marcada com falha na planilha"
    elif norm_st == "ignorada":
        status = TaskStatus.SKIPPED.value
        display_status = "Ignorada / Descartada"
        reason = "Ignorada na planilha"
    else:
        status = "PENDING"
        display_status = "Na Fila (A Fazer)"
        fit = job.get("fit_score")
        reason = f"Aguardando execução na fila (Fit: {fit}%)" if fit is not None else "Aguardando execução na fila"

    return {
        "job_id": job["id"],
        "company": job.get("company") or _extract_company_from_id(job["id"]),
        "title": job.get("title") or _extract_title_from_id(job["id"]),
        "ats": job.get("ats", "ATS"),
        "url": job.get("url"),
        "status": status,
        "display_status": display_status,
        "reason": reason,
        "dry_run": dry_run,
        "submitted": submitted,
        "resume_used": job.get("resume_label") or "Padrão",
        "duration_s": None,
        "tokens_used": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "screenshot_filename": None,
        "screenshot_url": None,
        "attempts": 0,
        "started_at": job.get("date") or None,
        "completed_at": None,
    }


def get_results_history(limit: int = 500, deduplicate: bool = True) -> list[dict[str, Any]]:
    """Lê artifacts/results.jsonl e devolve histórico enriquecido com as vagas da planilha ativa."""
    settings = load_settings()
    results_file = settings.RESULTS_FILE

    # Mapeamento rápido de jobs da planilha ativa para complementar título e empresa
    jobs_list = get_jobs_cache()
    jobs_map = {j["id"]: j for j in jobs_list}

    parsed_results: list[dict[str, Any]] = []
    seen_jobs: dict[str, dict[str, Any]] = {}

    if results_file.exists():
        raw_lines = results_file.read_text(encoding="utf-8").splitlines()
        # Itera de trás para frente (mais recentes primeiro)
        for line in reversed(raw_lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                job_id = data.get("job_id", "")
                if jobs_map and job_id not in jobs_map:
                    continue
                raw_status = data.get("status", "UNKNOWN")
                if deduplicate and job_id and job_id in seen_jobs:
                    existing = seen_jobs[job_id]
                    existing["attempts"] = existing.get("attempts", 1) + 1
                    is_submitted = data.get("submitted", False)
                    is_dry_run = data.get("dry_run", True)
                    # Se a vaga já teve uma execução concluída com sucesso (ou enviada ao vivo), preserva o melhor estado
                    if existing["status"] != TaskStatus.COMPLETED.value and raw_status == TaskStatus.COMPLETED.value:
                        existing["status"] = raw_status
                        existing["display_status"] = "Simulada com Sucesso" if is_dry_run else "Candidatura Enviada"
                        existing["reason"] = data.get("reason")
                        existing["dry_run"] = is_dry_run
                        existing["submitted"] = is_submitted
                        existing["resume_used"] = data.get("resume_used") or existing["resume_used"]
                    if is_submitted and not existing.get("submitted", False):
                        existing["submitted"] = True
                        existing["dry_run"] = False
                        existing["display_status"] = "Candidatura Enviada"
                    continue

                job_meta = jobs_map.get(job_id, {})

                # Trata screenshot
                raw_screen = data.get("screenshot_path")
                screenshot_filename = None
                if raw_screen:
                    screen_path = Path(raw_screen)
                    if screen_path.name:
                        screenshot_filename = screen_path.name

                # Status amigável
                raw_status = data.get("status", "UNKNOWN")
                is_submitted = data.get("submitted", False)
                is_dry_run = data.get("dry_run", True)

                display_status = raw_status
                if raw_status == TaskStatus.COMPLETED.value:
                    display_status = "Simulada com Sucesso" if is_dry_run else "Candidatura Enviada"
                elif raw_status == TaskStatus.NEEDS_REVIEW.value:
                    display_status = "Revisão Necessária"
                elif raw_status == TaskStatus.FAILED.value:
                    display_status = "Falhou / Erro"
                elif raw_status == TaskStatus.SKIPPED.value:
                    display_status = "Ignorada / Descartada"

                entry = {
                    "job_id": job_id,
                    "company": job_meta.get("company") or _extract_company_from_id(job_id),
                    "title": job_meta.get("title") or _extract_title_from_id(job_id),
                    "ats": job_meta.get("ats", "ATS"),
                    "url": job_meta.get("url"),
                    "status": raw_status,
                    "display_status": display_status,
                    "reason": data.get("reason"),
                    "dry_run": is_dry_run,
                    "submitted": is_submitted,
                    "resume_used": data.get("resume_used") or job_meta.get("resume_label") or "Padrão",
                    "duration_s": data.get("duration_s"),
                    "tokens_used": data.get("tokens_used", 0) or 0,
                    "prompt_tokens": data.get("prompt_tokens", 0) or 0,
                    "completion_tokens": data.get("completion_tokens", 0) or 0,
                    "screenshot_filename": screenshot_filename,
                    "screenshot_url": f"/api/screenshots/{screenshot_filename}" if screenshot_filename else None,
                    "attempts": data.get("attempts", 1),
                    "started_at": data.get("started_at"),
                    "completed_at": data.get("completed_at"),
                }
                if deduplicate and job_id:
                    seen_jobs[job_id] = entry
                parsed_results.append(entry)
                if len(parsed_results) >= limit:
                    break
            except Exception:
                continue

    if deduplicate and jobs_list and len(parsed_results) < limit:
        for job in jobs_list:
            if job["id"] not in seen_jobs:
                sheet_entry = _entry_from_sheet_job(job)
                seen_jobs[job["id"]] = sheet_entry
                parsed_results.append(sheet_entry)
                if len(parsed_results) >= limit:
                    break

    return parsed_results


def _extract_company_from_id(job_id: str) -> str:
    """Extrai nome amigável da empresa a partir do slug do ID."""
    parts = job_id.split("-")
    if parts:
        return parts[0].capitalize()
    return "Empresa"


def _extract_title_from_id(job_id: str) -> str:
    """Extrai título amigável a partir do slug do ID."""
    parts = job_id.split("-")
    if len(parts) > 1:
        return " ".join(parts[1:]).capitalize()
    return job_id


def get_summary_metrics() -> dict[str, Any]:
    """Calcula estatísticas agregadas completas para os cards do painel."""
    history = get_results_history(limit=5000)

    processed_items = [h for h in history if h.get("status") != "PENDING"]
    pending_count = sum(1 for h in history if h.get("status") == "PENDING")
    total_count = len(processed_items)
    completed_count = sum(1 for h in processed_items if h["status"] == TaskStatus.COMPLETED.value)
    submitted_live = sum(1 for h in processed_items if h.get("submitted", False))
    dry_run_count = sum(1 for h in processed_items if h.get("dry_run", True) and h["status"] == TaskStatus.COMPLETED.value)
    needs_review_count = sum(1 for h in processed_items if h["status"] == TaskStatus.NEEDS_REVIEW.value)
    failed_count = sum(1 for h in processed_items if h["status"] == TaskStatus.FAILED.value)
    skipped_count = sum(1 for h in processed_items if h["status"] == TaskStatus.SKIPPED.value)

    total_tokens = sum(h.get("tokens_used") or 0 for h in processed_items)
    prompt_tokens = sum(h.get("prompt_tokens") or 0 for h in processed_items)
    completion_tokens = sum(h.get("completion_tokens") or 0 for h in processed_items)

    durations = [h["duration_s"] for h in processed_items if h.get("duration_s") is not None]
    total_duration_s = round(sum(durations), 1)
    avg_duration_s = round(total_duration_s / len(durations), 1) if durations else 0.0

    # Resumos de currículos utilizados
    resumes_counter: dict[str, int] = {}
    for h in processed_items:
        res = h.get("resume_used")
        if res:
            resumes_counter[res] = resumes_counter.get(res, 0) + 1

    top_resumes = [
        {"name": k, "count": v}
        for k, v in sorted(resumes_counter.items(), key=lambda item: item[1], reverse=True)
    ]

    return {
        "total_processed": total_count,
        "pending": pending_count,
        "total_in_sheet": len(history),
        "completed": completed_count,
        "submitted_live": submitted_live,
        "dry_run": dry_run_count,
        "needs_review": needs_review_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "tokens": {
            "total": total_tokens,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
        },
        "time": {
            "total_duration_s": total_duration_s,
            "avg_duration_s": avg_duration_s,
            "formatted_total": _format_duration(total_duration_s),
        },
        "top_resumes": top_resumes[:5],
    }


def _format_duration(seconds: float) -> str:
    """Formata segundos em texto amigável: ex: 2m 15s."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours}h {mins}m"
