"""Sincronização Drive → cache local (PDFs e Google Docs exportados como PDF).

A biblioteca do Google é síncrona: toda chamada passa por `asyncio.to_thread` e a API pública é `async`.
O `service` (recurso `drive v3` do googleapiclient) é injetável para testes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from core.models import DriveAccessError

log = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
GDOC_MIME = "application/vnd.google-apps.document"
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]
INDEX_FILE = ".drive_index.json"


class DriveFile(BaseModel):
    """Metadados de um arquivo da pasta."""

    id: str
    name: str
    mimeType: str  # noqa: N815 - nome do campo na API do Drive
    md5Checksum: str | None = None  # noqa: N815
    modifiedTime: str = ""  # noqa: N815


class DriveService(Protocol):
    """Subconjunto do recurso `drive v3` usado aqui."""

    def files(self) -> Any: ...


def get_credentials(
    service_account_file: Path | None = None,
    oauth_client_file: Path | None = None,
    token_file: Path | None = None,
    interactive: bool = True,
) -> Any:
    """Obtém credenciais válidas do Google (service account ou OAuth)."""
    sa_path = service_account_file or Path("./service_account.json")
    if sa_path.exists():
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            str(sa_path), scopes=SCOPES
        )
    oauth_path = (
        oauth_client_file
        if (oauth_client_file is not None and oauth_client_file.exists())
        else Path("./client_secret.json")
    )
    if oauth_path.exists():
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = token_file or Path("./token.json")
        creds = None
        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except Exception:
                creds = None

        if not creds or not creds.valid or not creds.has_scopes(SCOPES):
            if creds and creds.expired and creds.refresh_token:
                try:
                    with contextlib.suppress(Exception):
                        import truststore
                        truststore.inject_into_ssl()
                    import requests
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    session = requests.Session()
                    session.verify = False
                    creds.refresh(Request(session=session))
                except Exception:
                    creds = None

            if not creds or not creds.valid or not creds.has_scopes(SCOPES):
                if not interactive:
                    raise DriveAccessError("token OAuth ausente/expirado; rode scripts/auth_google.py")
                flow = InstalledAppFlow.from_client_secrets_file(str(oauth_path), SCOPES)
                creds = flow.run_local_server(port=0)

            token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds
    else:
        raise DriveAccessError(
            "credenciais ausentes: defina GOOGLE_SERVICE_ACCOUNT_FILE (service_account.json) "
            "ou um cliente OAuth (client_secret.json)"
        )


def build_service(
    service_account_file: Path,
    oauth_client_file: Path | None = None,
    token_file: Path | None = None,
    interactive: bool = True,
) -> Any:
    """Cria o serviço do Drive: service account (principal) ou OAuth local (fallback)."""
    from googleapiclient.discovery import build

    creds = get_credentials(
        service_account_file,
        oauth_client_file,
        token_file=token_file,
        interactive=interactive,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def build_sheets_service(
    service_account_file: Path,
    oauth_client_file: Path | None = None,
    token_file: Path | None = None,
    interactive: bool = True,
) -> Any:
    """Cria o serviço do Google Sheets (v4): service account ou OAuth."""
    from googleapiclient.discovery import build

    creds = get_credentials(
        service_account_file,
        oauth_client_file,
        token_file=token_file,
        interactive=interactive,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _http_status(exc: BaseException) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - comparação com md5Checksum do Drive, não segurança
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(file: DriveFile) -> str:
    safe = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in file.name).strip()
    if file.mimeType == GDOC_MIME and not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return safe


class DriveClient:
    """Lista e sincroniza os currículos da pasta do Drive para o cache local."""

    def __init__(self, service: Any, folder_id: str = "", cache_dir: Path | None = None) -> None:
        self._service = service
        self._folder_id = folder_id
        self._cache_dir = cache_dir or Path("./cache/resumes")

    @classmethod
    def from_settings(cls, settings: Any) -> DriveClient:
        """Cria DriveClient a partir de uma instância de Settings."""
        service_file = getattr(settings, "GOOGLE_SERVICE_ACCOUNT_FILE", Path("./service_account.json"))
        oauth_file = getattr(settings, "GOOGLE_OAUTH_CLIENT_FILE", None) or Path("./client_secret.json")
        token_file = getattr(settings, "GOOGLE_TOKEN_FILE", None) or Path("./token.json")
        service = build_service(
            service_file,
            oauth_file if oauth_file.exists() else None,
            token_file=token_file,
            interactive=False,
        )
        folder_id = getattr(settings, "DRIVE_FOLDER_ID", "")
        cache_dir = getattr(settings, "CACHE_DIR", Path("./cache/resumes"))
        return cls(service=service, folder_id=folder_id, cache_dir=cache_dir)

    async def list_files(self, folder_id: str | None = None) -> list[DriveFile]:
        """PDFs e Google Docs da pasta (não lixeira)."""
        return await asyncio.to_thread(self._list_sync, folder_id or self._folder_id)

    def _list_sync(self, folder_id: str) -> list[DriveFile]:
        query = (
            f"'{folder_id}' in parents and trashed = false and "
            f"(mimeType = '{PDF_MIME}' or mimeType = '{GDOC_MIME}')"
        )
        files: list[DriveFile] = []
        token: str | None = None
        while True:
            try:
                response = (
                    self._service.files()
                    .list(
                        q=query,
                        fields="nextPageToken, files(id, name, mimeType, md5Checksum, modifiedTime)",
                        pageToken=token,
                        pageSize=200,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc, "listar a pasta") from exc
            files.extend(DriveFile(**item) for item in response.get("files", []))
            token = response.get("nextPageToken")
            if not token:
                return files

    @staticmethod
    def _translate(exc: BaseException, action: str) -> Exception:
        status = _http_status(exc)
        if status in (401, 403, 404):
            return DriveAccessError(f"sem acesso ao Drive ao {action} (HTTP {status})")
        if isinstance(exc, DriveAccessError):
            return exc
        return DriveAccessError(f"falha ao {action}: {type(exc).__name__}")

    def _read_index(self) -> dict[str, dict[str, str]]:
        path = self._cache_dir / INDEX_FILE
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_index(self, index: dict[str, dict[str, str]]) -> None:
        path = self._cache_dir / INDEX_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _needs_download(self, file: DriveFile, target: Path, entry: dict[str, str] | None) -> bool:
        if not target.exists():
            return True
        if file.mimeType == GDOC_MIME:
            return entry is None or entry.get("modifiedTime") != file.modifiedTime
        if not file.md5Checksum:
            return True
        return _md5(target) != file.md5Checksum

    def _fetch_bytes(self, file: DriveFile) -> bytes:
        files = self._service.files()
        if file.mimeType == GDOC_MIME:
            request = files.export_media(fileId=file.id, mimeType=PDF_MIME)
        else:
            request = files.get_media(fileId=file.id, supportsAllDrives=True)
        data = request.execute()
        if not isinstance(data, (bytes, bytearray)):
            raise DriveAccessError(f"conteúdo inesperado para {file.name}")
        return bytes(data)

    def _download_atomic(self, file: DriveFile, target: Path) -> None:
        tmp = target.with_name(target.name + ".tmp")
        try:
            with tmp.open("wb") as handle:
                handle.write(self._fetch_bytes(file))
            os.replace(tmp, target)
        except Exception as exc:
            if tmp.exists():
                tmp.unlink()
            raise self._translate(exc, f"baixar '{file.name}'") from exc

    async def sync_resumes(self) -> dict[str, Path]:
        """Sincroniza a pasta; retorna nome do arquivo no Drive → caminho local."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        files = await self.list_files()
        if not files:
            log.warning("pasta do Drive vazia ou sem PDFs/Google Docs: %s", self._folder_id)
            return {}
        index = self._read_index()
        out: dict[str, Path] = {}
        for file in files:
            target = self._cache_dir / _local_name(file)
            if self._needs_download(file, target, index.get(file.id)):
                log.info("baixando currículo do Drive: %s", file.name)
                await asyncio.to_thread(self._download_atomic, file, target)
            index[file.id] = {"name": file.name, "modifiedTime": file.modifiedTime}
            out[file.name] = target
        self._write_index(index)
        return out
