"""Testes unitários para o cliente do Google Drive (drive_client)."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from core.models import DriveAccessError
from storage.drive_client import GDOC_MIME, PDF_MIME, DriveClient


class FakeHttpError(Exception):
    def __init__(self, status: int, message: str = "Error") -> None:
        super().__init__(message)
        self.resp = type("Resp", (), {"status": status})()


class MockDriveRequest:
    def __init__(self, result: Any = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc
        self.executed = False

    def execute(self) -> Any:
        self.executed = True
        if self._exc:
            raise self._exc
        return self._result


class MockFilesResource:
    def __init__(self) -> None:
        self.files_to_list: list[dict[str, Any]] = []
        self.get_media_payloads: dict[str, bytes] = {}
        self.export_media_payloads: dict[str, bytes] = {}
        self.list_exc: Exception | None = None
        self.download_exc: dict[str, Exception] = {}
        self.download_calls: list[str] = []

    def list(self, **kwargs: Any) -> MockDriveRequest:
        if self.list_exc:
            return MockDriveRequest(exc=self.list_exc)
        return MockDriveRequest(result={"files": self.files_to_list})

    def get_media(self, fileId: str, **kwargs: Any) -> MockDriveRequest:
        self.download_calls.append(fileId)
        if fileId in self.download_exc:
            return MockDriveRequest(exc=self.download_exc[fileId])
        return MockDriveRequest(result=self.get_media_payloads.get(fileId, b"dummy-pdf-content"))

    def export_media(self, fileId: str, mimeType: str) -> MockDriveRequest:
        self.download_calls.append(fileId)
        if fileId in self.download_exc:
            return MockDriveRequest(exc=self.download_exc[fileId])
        return MockDriveRequest(result=self.export_media_payloads.get(fileId, b"exported-gdoc-pdf"))


class MockDriveService:
    def __init__(self) -> None:
        self._files = MockFilesResource()

    def files(self) -> MockFilesResource:
        return self._files


@pytest.mark.asyncio
async def test_empty_folder_returns_empty_dict(tmp_path: Path) -> None:
    service = MockDriveService()
    client = DriveClient(service, "empty-folder", tmp_path)
    res = await client.sync_resumes()
    assert res == {}


@pytest.mark.asyncio
async def test_md5_match_skips_download_mismatch_downloads(tmp_path: Path) -> None:
    service = MockDriveService()
    content_a = b"pdf-content-A"
    md5_a = hashlib.md5(content_a).hexdigest()

    # Cria arquivo existente em cache com MD5 batendo
    local_a = tmp_path / "resume_a.pdf"
    local_a.write_bytes(content_a)

    service._files.files_to_list = [
        {"id": "id-a", "name": "resume_a.pdf", "mimeType": PDF_MIME, "md5Checksum": md5_a},
        {"id": "id-b", "name": "resume_b.pdf", "mimeType": PDF_MIME, "md5Checksum": "new-hash"},
    ]
    service._files.get_media_payloads["id-b"] = b"new-pdf-content-B"

    client = DriveClient(service, "folder-123", tmp_path)
    synced = await client.sync_resumes()

    # resume_a já estava em cache com mesmo MD5 -> não baixou
    # resume_b não existia -> baixou
    assert service._files.download_calls == ["id-b"]
    assert "resume_a.pdf" in synced
    assert "resume_b.pdf" in synced
    assert (tmp_path / "resume_b.pdf").read_bytes() == b"new-pdf-content-B"


@pytest.mark.asyncio
async def test_gdoc_exported_and_reexported_on_modified_time(tmp_path: Path) -> None:
    service = MockDriveService()
    service._files.files_to_list = [
        {
            "id": "gdoc-1",
            "name": "Curriculo Google Docs",
            "mimeType": GDOC_MIME,
            "modifiedTime": "2026-10-01T10:00:00Z",
        },
    ]
    service._files.export_media_payloads["gdoc-1"] = b"v1-pdf"

    client = DriveClient(service, "folder-123", tmp_path)

    # Primeira sync: baixa v1
    await client.sync_resumes()
    assert (tmp_path / "Curriculo Google Docs.pdf").read_bytes() == b"v1-pdf"
    assert len(service._files.download_calls) == 1

    # Segunda sync com mesmo modifiedTime: não baixa novamente
    await client.sync_resumes()
    assert len(service._files.download_calls) == 1

    # Terceira sync com modifiedTime alterado: re-exporta
    service._files.files_to_list[0]["modifiedTime"] = "2026-10-02T10:00:00Z"
    service._files.export_media_payloads["gdoc-1"] = b"v2-pdf"
    await client.sync_resumes()
    assert len(service._files.download_calls) == 2
    assert (tmp_path / "Curriculo Google Docs.pdf").read_bytes() == b"v2-pdf"


@pytest.mark.asyncio
async def test_http_403_raises_drive_access_error(tmp_path: Path) -> None:
    service = MockDriveService()
    service._files.list_exc = FakeHttpError(403, "Access Denied")
    client = DriveClient(service, "forbidden-folder", tmp_path)

    with pytest.raises(DriveAccessError, match="HTTP 403"):
        await client.sync_resumes()


@pytest.mark.asyncio
async def test_download_failure_cleans_tmp_file(tmp_path: Path) -> None:
    service = MockDriveService()
    service._files.files_to_list = [
        {"id": "fail-id", "name": "failing.pdf", "mimeType": PDF_MIME, "md5Checksum": "xyz"},
    ]
    service._files.download_exc["fail-id"] = FakeHttpError(500, "Server Error")
    client = DriveClient(service, "folder-123", tmp_path)

    with pytest.raises(DriveAccessError):
        await client.sync_resumes()

    # Nenhum arquivo .tmp deve restar no diretório
    tmps = list(tmp_path.glob("*.tmp"))  # noqa: ASYNC240
    assert len(tmps) == 0


def test_get_credentials_non_interactive_raises_when_no_token(tmp_path: Path) -> None:
    from storage.drive_client import get_credentials

    oauth_file = tmp_path / "client_secret.json"
    oauth_file.write_text('{"installed": {"client_id": "dummy"}}', encoding="utf-8")
    fake_sa = tmp_path / "non_existent_sa.json"
    fake_token = tmp_path / "non_existent_token.json"

    with pytest.raises(DriveAccessError, match="token OAuth ausente/expirado"):
        get_credentials(
            service_account_file=fake_sa,
            oauth_client_file=oauth_file,
            token_file=fake_token,
            interactive=False,
        )
