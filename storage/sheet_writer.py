"""Grava os resultados numa CÓPIA .xlsx da planilha. O arquivo de origem nunca é alterado."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from core.models import ApplicationResult, Job, TaskStatus
from storage.sheet_loader import (
    COL_LINK,
    COL_STATUS,
    _sheet_has_job_headers,
    find_header,
    load_jobs,
    normalize_text,
    read_raw,
)

log = logging.getLogger(__name__)

COL_REASON = "Motivo"
COL_PROCESSED_AT = "Processado em"


def status_label(result: ApplicationResult) -> str:
    """Mapeia o status do harness para o texto da planilha."""
    if result.submitted and not result.dry_run:
        return "Candidatado"
    if result.status == TaskStatus.COMPLETED:
        return "Pronta (dry-run)"
    mapping = {
        TaskStatus.NEEDS_REVIEW: "Revisar",
        TaskStatus.FAILED: "Falhou",
        TaskStatus.SKIPPED: "Ignorada",
    }
    return mapping.get(result.status, result.status.value)


def _select_jobs_worksheet(workbook: Workbook) -> Worksheet:
    """Retorna a aba que possui o cabeçalho de vagas ('Empresa' e 'Cargo'), ou a aba ativa."""
    sheet = workbook.active
    if not _sheet_has_job_headers(sheet):
        for name in workbook.sheetnames:
            candidate = workbook[name]
            if _sheet_has_job_headers(candidate):
                sheet = candidate
                break
    assert isinstance(sheet, Worksheet)
    return sheet


def _load_as_workbook(source: Path) -> Workbook:
    """Abre .xlsx em memória, ou converte .csv numa planilha com as mesmas linhas."""
    if source.suffix.lower() == ".xlsx":
        return load_workbook(source)
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    text = source.read_bytes().decode("utf-8-sig")
    delimiter = ";" if text[:4096].count(";") > text[:4096].count(",") else ","
    for r_idx, record in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1):
        for c_idx, value in enumerate(record, start=1):
            if value != "":
                sheet.cell(row=r_idx, column=c_idx, value=value)
    return workbook


def write_results(
    source_path: Path | str,
    results: Iterable[ApplicationResult],
    out_path: Path | str,
    jobs: dict[str, Job],
    now: datetime | None = None,
) -> Path:
    """Escreve status, `Motivo`, `Processado em` e URL corrigida (`Link da Vaga`) numa cópia."""
    source = Path(source_path)
    out = Path(out_path)
    if out.resolve() == source.resolve():
        raise ValueError("out_path não pode ser a planilha original")
    raw = read_raw(source)
    header_pos, columns = find_header(raw)
    header_row = raw.rows[header_pos][0]
    status_col = columns.get(COL_STATUS)
    if status_col is None:
        raise ValueError(f"coluna '{COL_STATUS}' ausente")
    link_col = columns.get(COL_LINK)

    workbook = _load_as_workbook(source)
    sheet = _select_jobs_worksheet(workbook)
    reason_col, processed_col = _ensure_extra_columns(sheet, header_row, columns)
    timestamp = (now or datetime.now()).strftime("%d/%m/%Y %H:%M")

    for result in results:
        job = jobs.get(result.job_id)
        if job is None:
            log.warning("resultado sem linha correspondente: %s", result.job_id)
            continue
        sheet.cell(row=job.row_index, column=status_col + 1, value=status_label(result))
        sheet.cell(row=job.row_index, column=reason_col, value=result.reason or "")
        sheet.cell(row=job.row_index, column=processed_col, value=timestamp)
        if link_col is not None and job.url and job.url.startswith(("http://", "https://")):
            link_cell = sheet.cell(row=job.row_index, column=link_col + 1)
            link_cell.value = job.url
            link_cell.hyperlink = job.url

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        workbook.save(out)
    except PermissionError as exc:
        fallback_out = out.with_name(f"{out.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{out.suffix}")
        log.warning("Arquivo '%s' bloqueado para escrita (%s). Salvando em fallback: '%s'", out, exc, fallback_out)
        workbook.save(fallback_out)
        return fallback_out
    except Exception as exc:
        log.error("Erro ao salvar planilha de resultados em %s: %s. Estado permanece íntegro em memória.", out, exc)
        raise
    return out


def _ensure_extra_columns(
    sheet: Worksheet, header_row: int, columns: dict[str, int]
) -> tuple[int, int]:
    """Garante as colunas `Motivo` e `Processado em` (1-based) no cabeçalho."""
    last = max(columns.values()) + 1
    reason_col = columns[COL_REASON] + 1 if COL_REASON in columns else None
    processed_col = columns[COL_PROCESSED_AT] + 1 if COL_PROCESSED_AT in columns else None
    if reason_col is None:
        last += 1
        reason_col = last
        sheet.cell(row=header_row, column=reason_col, value=COL_REASON)
    if processed_col is None:
        last += 1
        processed_col = last
        sheet.cell(row=header_row, column=processed_col, value=COL_PROCESSED_AT)
    return reason_col, processed_col


def update_google_sheet_results(
    spreadsheet_id: str,
    results: Iterable[ApplicationResult],
    jobs: dict[str, Any],
    columns: dict[str, int],
    header_row: int,
    sheets_service: Any,
    now: datetime | None = None,
) -> bool:
    """Atualiza as colunas de Status, Motivo, Processado em e Link da Vaga na planilha online do Google Sheets."""
    from openpyxl.utils import get_column_letter

    # Identifica o nome da primeira aba da planilha
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_title = meta.get("sheets", [{}])[0].get("properties", {}).get("title", "Página1")

    # Mapeia colunas (0-based da leitura da planilha)
    status_col = columns.get(COL_STATUS)
    if status_col is None:
        log.warning("Coluna '%s' não encontrada para atualização no Google Sheets.", COL_STATUS)
        return False
    link_col = columns.get(COL_LINK)

    last = max(columns.values()) + 1
    reason_col = columns.get(COL_REASON)
    processed_col = columns.get(COL_PROCESSED_AT)

    # Garante cabeçalhos para Motivo e Processado em se não existirem
    header_updates: list[dict[str, Any]] = []
    if reason_col is None:
        reason_col = last
        last += 1
        header_updates.append({
            "range": f"'{sheet_title}'!{get_column_letter(reason_col + 1)}{header_row}",
            "values": [[COL_REASON]],
        })
    if processed_col is None:
        processed_col = last
        header_updates.append({
            "range": f"'{sheet_title}'!{get_column_letter(processed_col + 1)}{header_row}",
            "values": [[COL_PROCESSED_AT]],
        })

    timestamp = (now or datetime.now()).strftime("%d/%m/%Y %H:%M")
    data_updates = list(header_updates)

    for result in results:
        job = jobs.get(result.job_id)
        if job is None:
            continue
        row = job.row_index
        data_updates.append({
            "range": f"'{sheet_title}'!{get_column_letter(status_col + 1)}{row}",
            "values": [[status_label(result)]],
        })
        data_updates.append({
            "range": f"'{sheet_title}'!{get_column_letter(reason_col + 1)}{row}",
            "values": [[result.reason or ""]],
        })
        data_updates.append({
            "range": f"'{sheet_title}'!{get_column_letter(processed_col + 1)}{row}",
            "values": [[timestamp]],
        })
        if link_col is not None and getattr(job, "url", None) and str(job.url).startswith(("http://", "https://")):
            data_updates.append({
                "range": f"'{sheet_title}'!{get_column_letter(link_col + 1)}{row}",
                "values": [[job.url]],
            })

    if not data_updates:
        return True

    body = {
        "valueInputOption": "USER_ENTERED",
        "data": data_updates,
    }
    try:
        from storage.drive_client import _execute_api_call_with_retry

        def _do_batch_update():
            return sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body,
            ).execute()

        _execute_api_call_with_retry(_do_batch_update, "atualizar Google Sheets online")
        log.info("Google Sheets '%s' atualizado com sucesso (%d células modificadas).", spreadsheet_id, len(data_updates))
        return True
    except Exception as exc:
        log.error(
            "Falha ao atualizar Google Sheets online (%s). Os resultados permanecem seguros em memória e no cache local.",
            exc,
        )
        return False


def sync_sheet_with_local_state(
    sheet_path: Path | str,
    results_jsonl_path: Path | str | None = None,
    extra_out_paths: Iterable[Path | str] = (),
    overwrite_todo: bool = True,
) -> Path:
    """Sincroniza planilha local com URLs diretas resolvidas (_URL_FALLBACKS) e histórico de results.jsonl."""
    import json
    import shutil
    from automation.handlers.aggregator import _URL_FALLBACKS

    src = Path(sheet_path)
    if not src.exists():
        return src

    raw = read_raw(src)
    header_pos, columns = find_header(raw)
    header_row = raw.rows[header_pos][0]
    status_col = columns.get(COL_STATUS)
    link_col = columns.get(COL_LINK)
    if status_col is None:
        return src

    all_jobs = load_jobs(
        src,
        only_todo=False,
        include_blank=True,
        include_review=True,
        include_failed=True,
        include_completed=True,
    )
    jobs_by_id: dict[str, Job] = {j.id: j for j in all_jobs}

    # 1. Corrige URLs diretas mapeadas em _URL_FALLBACKS
    for j in all_jobs:
        comp_clean = (j.company or "").strip().lower()
        tit_clean = (j.title or "").strip().lower()
        for (f_comp, f_tit), f_url in _URL_FALLBACKS.items():
            if f_comp in comp_clean and (not f_tit or f_tit in tit_clean):
                j.url = f_url
                break

    # 2. Carrega melhores resultados conhecidos de results.jsonl
    best_results: dict[str, ApplicationResult] = {}
    if results_jsonl_path:
        r_path = Path(results_jsonl_path)
        if r_path.exists():
            for line in r_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    res = ApplicationResult.model_validate(data)
                except Exception:
                    continue
                prev = best_results.get(res.job_id)
                if prev is None:
                    best_results[res.job_id] = res
                else:
                    # Preserva submissão real (submitted=True e COMPLETED) sobre dry-run ou erro posterior,
                    # ou atualiza com o mais recente se for progresso superior
                    prev_live = prev.submitted and not prev.dry_run and prev.status == TaskStatus.COMPLETED
                    new_live = res.submitted and not res.dry_run and res.status == TaskStatus.COMPLETED
                    if new_live or not prev_live:
                        if prev.status == TaskStatus.COMPLETED and res.status == TaskStatus.NEEDS_REVIEW and not new_live:
                            continue
                        best_results[res.job_id] = res

    workbook = _load_as_workbook(src)
    sheet = _select_jobs_worksheet(workbook)
    reason_col, processed_col = _ensure_extra_columns(sheet, header_row, columns)
    default_ts = datetime.now().strftime("%d/%m/%Y %H:%M")

    for j in all_jobs:
        if link_col is not None and j.url and j.url.startswith(("http://", "https://")):
            link_cell = sheet.cell(row=j.row_index, column=link_col + 1)
            link_cell.value = j.url
            link_cell.hyperlink = j.url

        res = best_results.get(j.id)
        if res is not None:
            is_live_submitted = res.submitted and not res.dry_run and res.status == TaskStatus.COMPLETED
            if not overwrite_todo and normalize_text(j.sheet_status or "") == "a fazer" and not is_live_submitted:
                continue
            sheet.cell(row=j.row_index, column=status_col + 1, value=status_label(res))
            sheet.cell(row=j.row_index, column=reason_col, value=res.reason or "")
            proc_ts = default_ts
            if res.completed_at:
                try:
                    dt = datetime.fromisoformat(res.completed_at.replace("Z", "+00:00")).astimezone()
                    proc_ts = dt.strftime("%d/%m/%Y %H:%M")
                except Exception:
                    pass
            sheet.cell(row=j.row_index, column=processed_col, value=proc_ts)

    workbook.save(src)
    for extra in extra_out_paths:
        extra_p = Path(extra)
        if extra_p.resolve() != src.resolve():
            extra_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, extra_p)
    return src

