"""Leitura da planilha "Central de Vagas e Candidaturas" (.xlsx/.csv) para `list[Job]`."""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from core.models import Job, SheetFormatError

log = logging.getLogger(__name__)

COL_DATE = "Data"
COL_COMPANY = "Empresa"
COL_TITLE = "Cargo"
COL_MODALITY = "Modalidade"
COL_LINK = "Link da Vaga"
COL_RESUME = "Currículo Otimizado"
COL_FIT = "Fit Score"
COL_BASE = "Base Utilizada"
COL_STACK = "Stack Principal"
COL_STATUS = "Status da Candidatura"
COL_URL_EXTRA = "URL da Vaga"

STATUS_TODO = "a fazer"
STATUS_REVIEW = "revisar"
_ERROR_VALUES = {"#error!", "#ref!", "#n/a", "#value!", "#name?"}
_EN_PREFIX = "resume optimized"
_SHEET_URL_RE = re.compile(r"/(?:spreadsheets|file)/d/([a-zA-Z0-9-_]+)")
_SHEET_ID_RE = re.compile(r"^[a-zA-Z0-9-_]{20,}$")
_SHEET_GID_RE = re.compile(r"[?&#]gid=(\d+)")


def is_google_sheet_source(source: str | Path) -> bool:
    """Verifica se a fonte informada é uma URL ou ID do Google Sheets."""
    s = str(source).strip()
    if _SHEET_URL_RE.search(s) is not None:
        return True
    return bool(not Path(s).exists() and _SHEET_ID_RE.match(s) is not None)


def extract_spreadsheet_id(source: str | Path) -> str | None:
    """Extrai o ID alfanumérico da planilha a partir de uma URL ou ID bruto."""
    s = str(source).strip()
    match = _SHEET_URL_RE.search(s)
    if match:
        return match.group(1)
    if _SHEET_ID_RE.match(s):
        return s
    return None


def extract_sheet_gid(source: str | Path) -> str | None:
    """Extrai o gid numérico da aba a partir de uma URL do Google Sheets, se presente."""
    match = _SHEET_GID_RE.search(str(source).strip())
    return match.group(1) if match else None


def download_google_sheet(
    url_or_id: str | Path,
    dest_path: Path | None = None,
    drive_service: Any = None,
    service_account_file: Path | None = None,
    oauth_client_file: Path | None = None,
    token_file: Path | None = None,
) -> Path:
    """Baixa a planilha do Google Sheets e salva como .xlsx local.

    1. Tenta download direto HTTP da URL de exportação pública.
    2. Se falhar e drive_service estiver disponível, usa Google Drive API export_media.
    """
    sheet_id = extract_spreadsheet_id(url_or_id)
    if not sheet_id:
        raise SheetFormatError(f"URL ou ID do Google Sheets inválido: '{url_or_id}'")

    target = dest_path or Path("./cache/vagas_sheets_latest.xlsx")
    target.parent.mkdir(parents=True, exist_ok=True)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"

    # 1. Download direto via HTTP (para planilhas com acesso de leitura com link) com retry
    try:
        try:
            import truststore
            truststore.inject_into_ssl()
        except Exception:
            pass
        import httpx
        import time

        for attempt in range(3):
            try:
                with httpx.Client(follow_redirects=True, timeout=30.0, verify=False) as client:
                    resp = client.get(export_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                    if resp.status_code == 200 and resp.content.startswith(b"PK\x03\x04"):
                        target.write_bytes(resp.content)
                        log.info("Planilha do Google Sheets baixada com sucesso (%d bytes): %s", len(resp.content), target)
                        return target
                    elif resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(1.0 * (2 ** attempt))
                        continue
                    break
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt < 2:
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                break
    except Exception as exc:
        log.debug("Download direto da planilha falhou (%s), tentando fallback com Google Drive API...", exc)

    # 2. Fallback via Google Drive API autenticada (se a planilha for privada)
    if drive_service is None:
        try:
            from storage.drive_client import build_service

            sa_file = service_account_file or Path("./service_account.json")
            oa_file = oauth_client_file or Path("./client_secret.json")
            if sa_file.exists() or oa_file.exists():
                drive_service = build_service(
                    service_account_file=sa_file,
                    oauth_client_file=oa_file if oa_file.exists() else None,
                    token_file=token_file,
                    interactive=False,
                )
        except Exception as exc:
            log.debug("Inicialização automática do drive_service falhou: %s", exc)

    if drive_service is not None:
        try:
            from storage.drive_client import _execute_api_call_with_retry

            req = drive_service.files().export_media(
                fileId=sheet_id,
                mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            data = _execute_api_call_with_retry(req.execute, f"exportar planilha '{sheet_id}' via Drive API")
            if isinstance(data, (bytes, bytearray)) and bytes(data).startswith(b"PK\x03\x04"):
                target.write_bytes(bytes(data))
                log.info("Planilha do Google Sheets exportada via Google Drive API: %s", target)
                return target
        except Exception as exc:
            log.warning("Export via Google Drive API falhou: %s", exc)

    raise SheetFormatError(
        f"Não foi possível baixar a planilha do Google Sheets (ID: {sheet_id}).\n"
        "Verifique se o compartilhamento está definido como 'Qualquer pessoa com o link' (Leitor) "
        "para download direto sem login, ou configure credenciais no client_secret.json / service_account.json."
    )


def normalize_text(value: str) -> str:
    """Minúsculas, sem acentos e com espaços colapsados."""
    decomposed = unicodedata.normalize("NFKD", value)
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", no_accents).strip().lower()


@dataclass
class RawCell:
    """Valor de célula + destino do hiperlink (só .xlsx)."""

    value: Any
    hyperlink: str | None = None

    @property
    def text(self) -> str:
        if self.value is None:
            return ""
        if isinstance(self.value, datetime):
            return self.value.strftime("%d/%m/%Y")
        if isinstance(self.value, date):
            return self.value.strftime("%d/%m/%Y")
        return str(self.value).strip()


@dataclass
class RawSheet:
    """Planilha crua: linhas de células com número da linha (1-based)."""

    rows: list[tuple[int, list[RawCell]]]


def read_raw(path: Path) -> RawSheet:
    """Lê .xlsx (com hiperlinks) ou .csv para uma estrutura comum."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _read_xlsx(path)
    if suffix == ".csv":
        return _read_csv(path)
    raise SheetFormatError(f"extensão não suportada: {path.suffix}")


def _sheet_has_job_headers(sheet: Any) -> bool:
    """Verifica se uma aba do workbook contém o cabeçalho esperado ('Empresa' e 'Cargo')."""
    if sheet is None:
        return False
    comp_norm = normalize_text(COL_COMPANY)
    title_norm = normalize_text(COL_TITLE)
    for row in sheet.iter_rows(max_row=25, values_only=True):
        norms = {normalize_text(str(val)) for val in row if val is not None}
        if comp_norm in norms and title_norm in norms:
            return True
    return False


def _read_xlsx(path: Path) -> RawSheet:
    workbook_values = load_workbook(path, data_only=True)
    workbook_formulas = load_workbook(path, data_only=False)
    sheet_values = workbook_values.active
    sheet_formulas = workbook_formulas.active

    if not _sheet_has_job_headers(sheet_values):
        for name in workbook_values.sheetnames:
            candidate = workbook_values[name]
            if _sheet_has_job_headers(candidate):
                sheet_values = candidate
                sheet_formulas = workbook_formulas[name] if name in workbook_formulas.sheetnames else None
                break

    if sheet_values is None:
        raise SheetFormatError("planilha sem aba ativa")

    re_hyperlink = re.compile(r'=HYPERLINK\("([^"]+)"(?:,\s*"([^"]*)")?\)', re.IGNORECASE)

    rows: list[tuple[int, list[RawCell]]] = []
    for r_idx, row in enumerate(sheet_values.iter_rows(values_only=False), start=1):
        cells: list[RawCell] = []
        for c_idx, cell in enumerate(row, start=1):
            target = cell.hyperlink.target if cell.hyperlink is not None else None
            value = cell.value

            if sheet_formulas is not None:
                cell_formula = sheet_formulas.cell(row=r_idx, column=c_idx).value
                if isinstance(cell_formula, str) and cell_formula.upper().startswith("=HYPERLINK"):
                    m = re_hyperlink.search(cell_formula)
                    if m:
                        target = m.group(1)
                        if value is None or str(value).startswith("="):
                            value = m.group(2) if m.group(2) is not None else m.group(1)

            if isinstance(value, float) and "%" in str(cell.number_format):
                value = f"{round(value * 100)}%"
            cells.append(RawCell(value=value, hyperlink=target))
        rows.append((row[0].row if row else len(rows) + 1, cells))

    workbook_values.close()
    workbook_formulas.close()
    return RawSheet(rows=rows)


def _read_csv(path: Path) -> RawSheet:
    raw_text = path.read_bytes().decode("utf-8-sig")
    sample = raw_text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
    rows = [
        (index, [RawCell(value=value) for value in record])
        for index, record in enumerate(reader, start=1)
    ]
    return RawSheet(rows=rows)


def find_header(raw: RawSheet) -> tuple[int, dict[str, int]]:
    """Localiza o cabeçalho (linha com `Empresa` e `Cargo`). Retorna (posição na lista, coluna→índice)."""
    for position, (_, cells) in enumerate(raw.rows):
        names = {normalize_text(cell.text): idx for idx, cell in enumerate(cells) if cell.text}
        if normalize_text(COL_COMPANY) in names and normalize_text(COL_TITLE) in names:
            columns: dict[str, int] = {}
            for idx, cell in enumerate(cells):
                if cell.text:
                    columns[cell.text.strip()] = idx
            return position, _canonical_columns(columns)
    raise SheetFormatError("cabeçalho com 'Empresa' e 'Cargo' não encontrado")


def _canonical_columns(columns: dict[str, int]) -> dict[str, int]:
    known = [
        COL_DATE, COL_COMPANY, COL_TITLE, COL_MODALITY, COL_LINK, COL_RESUME,
        COL_FIT, COL_BASE, COL_STACK, COL_STATUS, COL_URL_EXTRA,
    ]
    by_norm = {normalize_text(name): name for name in known}
    out: dict[str, int] = {}
    for header, idx in columns.items():
        canonical = by_norm.get(normalize_text(header), header)
        out[canonical] = idx
    return out


def is_error_value(text: str) -> bool:
    """Valores de erro de fórmula exportados pela planilha."""
    return normalize_text(text) in _ERROR_VALUES


def parse_fit_score(text: str) -> int | None:
    """'95%' → 95; inválido → None."""
    cleaned = text.replace("%", "").replace(",", ".").strip()
    if not cleaned or is_error_value(text):
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if 0 < number <= 1 and "%" not in text and "." in cleaned:
        number *= 100
    value = int(round(number))
    return value if 0 <= value <= 100 else None


def split_list(text: str, sep: str) -> list[str]:
    """Separa e limpa listas (`Base Utilizada` por ' / ', `Stack Principal` por ',')."""
    if not text or is_error_value(text):
        return []
    return [part.strip() for part in text.split(sep) if part.strip()]


def detect_language(resume_label: str | None, base_used: list[str]) -> str:
    """'en' se o rótulo começa com 'Resume Optimized'; sem rótulo, usa 'Base Inglês'."""
    if resume_label:
        return "en" if normalize_text(resume_label).startswith(_EN_PREFIX) else "pt"
    if any(normalize_text(base) == "base ingles" for base in base_used):
        return "en"
    return "pt"


def _valid_url(text: str | None) -> str | None:
    if not text:
        return None
    candidate = text.strip()
    if is_error_value(candidate):
        return None
    if candidate.lower().startswith(("http://", "https://")):
        return candidate
    return None


def slugify(text: str) -> str:
    """Slug ASCII estável."""
    return re.sub(r"[^a-z0-9]+", "-", normalize_text(text)).strip("-") or "vaga"


def _cell(cells: list[RawCell], columns: dict[str, int], name: str) -> RawCell:
    idx = columns.get(name)
    if idx is None or idx >= len(cells):
        return RawCell(value=None)
    return cells[idx]


def _row_to_job(row_number: int, cells: list[RawCell], columns: dict[str, int]) -> Job | None:
    company = _cell(cells, columns, COL_COMPANY).text
    title = _cell(cells, columns, COL_TITLE).text
    if not company and not title:
        return None
    link_cell = _cell(cells, columns, COL_LINK)
    url = (
        _valid_url(_cell(cells, columns, COL_URL_EXTRA).text)
        or _valid_url(link_cell.hyperlink)
        or _valid_url(link_cell.text)
    )
    resume_text = _cell(cells, columns, COL_RESUME).text
    resume_label = None if (not resume_text or is_error_value(resume_text)) else resume_text
    base_used = split_list(_cell(cells, columns, COL_BASE).text, " / ")
    return Job(
        id="",
        row_index=row_number,
        date=_cell(cells, columns, COL_DATE).text,
        company=company,
        title=title,
        modality=_cell(cells, columns, COL_MODALITY).text,
        url=url,
        fit_score=parse_fit_score(_cell(cells, columns, COL_FIT).text),
        resume_label=resume_label,
        base_used=base_used,
        stack=split_list(_cell(cells, columns, COL_STACK).text, ","),
        sheet_status=_cell(cells, columns, COL_STATUS).text,
        language=detect_language(resume_label, base_used),  # type: ignore[arg-type]
    )


def load_jobs(
    path: Path | str,
    only_todo: bool = True,
    include_blank: bool = False,
    include_review: bool = False,
    include_failed: bool = False,
    include_completed: bool = False,
    drive_service: Any = None,
    service_account_file: Path | None = None,
    oauth_client_file: Path | None = None,
    token_file: Path | None = None,
) -> list[Job]:
    """Carrega as vagas da planilha (.xlsx/.csv local ou URL/ID do Google Sheets).

    Por padrão, só as com status 'A Fazer' (ou vazias se include_blank=True, revisar se include_review=True, falhou se include_failed=True).
    """
    if is_google_sheet_source(path):
        source = download_google_sheet(
            path,
            drive_service=drive_service,
            service_account_file=service_account_file,
            oauth_client_file=oauth_client_file,
            token_file=token_file,
        )
    else:
        source = Path(path)
    raw = read_raw(source)
    header_pos, columns = find_header(raw)
    jobs: list[Job] = []
    seen: dict[str, int] = {}
    for row_number, cells in raw.rows[header_pos + 1:]:
        if not any(cell.text for cell in cells):
            continue
        try:
            job = _row_to_job(row_number, cells, columns)
        except Exception as exc:  # noqa: BLE001 - uma linha ruim nunca derruba as demais
            log.warning("linha %d ignorada: %s", row_number, exc)
            continue
        if job is None:
            continue
        norm_status = normalize_text(job.sheet_status)
        if only_todo:
            allowed = {STATUS_TODO, "pronta (dry-run)", "pronta (dry run)", "pronta"}
            if include_review:
                allowed.add(STATUS_REVIEW)
            if include_blank:
                allowed.add("")
            if include_failed:
                allowed.add("falhou")
            if include_completed:
                allowed.update({"candidatado", "aplicado", "ja candidatado", "ja aplicado"})
            if norm_status not in allowed:
                continue
        elif not include_completed and norm_status in ("candidatado", "aplicado", "ja candidatado", "ja aplicado"):
            continue
        base_id = slugify(f"{job.company}-{job.title}")
        seen[base_id] = seen.get(base_id, 0) + 1
        job.id = base_id if seen[base_id] == 1 else f"{base_id}-{seen[base_id]}"
        if job.url is None:
            log.warning("vaga %s sem link válido na planilha", job.id)
        jobs.append(job)
    return jobs
