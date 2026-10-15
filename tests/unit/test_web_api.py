"""Testes unitários e de integração para a API do painel Web."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from web.app import app
from web.config_manager import mask_secret
from web.cookie_manager import clear_cookies, get_cookies_info, save_cookies
from web.runner import global_runner


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_mask_secret():
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("short") == "••••••••"
    dummy = "mock_secret_key_1234567890_abcdefghijklmnopqrstuvwxyz"
    assert mask_secret(dummy).startswith("mock_s")
    assert mask_secret(dummy).endswith("wxyz")


def test_get_status_endpoint(client: TestClient):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "is_running" in data
    assert "total_jobs" in data
    assert "completed_jobs" in data
    assert "run_tokens" in data


def test_get_metrics_endpoint(client: TestClient):
    response = client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "total_processed" in data
    assert "completed" in data
    assert "submitted_live" in data
    assert "tokens" in data
    assert "time" in data
    assert "top_resumes" in data


def test_get_results_endpoint(client: TestClient):
    response = client.get("/api/results?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "results" in data
    assert isinstance(data["results"], list)


def test_get_jobs_endpoint(client: TestClient):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "jobs" in data


def test_get_config_endpoint(client: TestClient):
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "llm" in data
    assert "sheet" in data
    assert "execution" in data
    assert "api_key_masked" in data["llm"]


def test_cookies_workflow(client: TestClient, tmp_path: Path, monkeypatch):
    test_storage = tmp_path / "test_storage_state.json"
    monkeypatch.setattr("web.cookie_manager.get_storage_state_path", lambda: test_storage)

    # 1. Inicialmente sem cookies
    res = client.get("/api/cookies")
    assert res.status_code == 200
    data = res.json()
    assert data["has_session"] is False

    # 2. Importa cookies de exemplo
    cookies_payload = [
        {"name": "session_id", "value": "xyz123", "domain": ".indeed.com", "path": "/"},
        {"name": "gupy_auth", "value": "abc987", "domain": "gupy.io", "path": "/"},
    ]
    res_post = client.post("/api/cookies", json={"data": cookies_payload})
    assert res_post.status_code == 200
    post_data = res_post.json()
    assert post_data["has_session"] is True
    assert post_data["cookie_count"] == 2
    assert "Indeed" in post_data["known_portals"]
    assert "Gupy" in post_data["known_portals"]

    # 3. Limpa cookies
    res_del = client.delete("/api/cookies")
    assert res_del.status_code == 200
    del_data = res_del.json()
    assert del_data["has_session"] is False


def test_screenshot_not_found(client: TestClient):
    response = client.get("/api/screenshots/inexistente_12345.png")
    assert response.status_code == 404


def test_index_serves_html(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Candidata IA" in response.text


def test_results_history_deduplicates_retries(tmp_path: Path, monkeypatch):
    from config.settings import Settings
    from web.data_provider import get_results_history

    fake_results = tmp_path / "results.jsonl"
    lines = [
        json.dumps({"job_id": "sierra-agent-engineer", "status": "NEEDS_REVIEW", "dry_run": True, "submitted": False}),
        json.dumps({"job_id": "sierra-agent-engineer", "status": "COMPLETED", "dry_run": True, "submitted": False}),
    ]
    fake_results.write_text("\n".join(lines), encoding="utf-8")

    fake_settings = Settings(RESULTS_FILE=fake_results)
    monkeypatch.setattr("web.data_provider.load_settings", lambda: fake_settings)
    monkeypatch.setattr("web.data_provider.get_jobs_cache", lambda: [])

    history = get_results_history(limit=50)
    assert len(history) == 1
    assert history[0]["job_id"] == "sierra-agent-engineer"
    assert history[0]["status"] == "COMPLETED"
    assert history[0]["attempts"] == 2


def test_sheet_url_with_gid_and_multitab_xlsx(tmp_path: Path):
    from openpyxl import Workbook
    from storage.sheet_loader import extract_sheet_gid, extract_spreadsheet_id, is_google_sheet_source, load_jobs

    url = "https://docs.google.com/spreadsheets/d/1UGptyVWtXkTH-tC8Qfd1Z5nkJJSPEJY4-YhXipylSVc/edit?gid=1044397220#gid=1044397220"
    assert is_google_sheet_source(url) is True
    assert extract_spreadsheet_id(url) == "1UGptyVWtXkTH-tC8Qfd1Z5nkJJSPEJY4-YhXipylSVc"
    assert extract_sheet_gid(url) == "1044397220"

    wb = Workbook()
    cover = wb.active
    assert cover is not None
    cover.title = "Capa"
    cover.append(["Resumo", "Total"])
    cover.append(["Vagas", 1])

    vagas_tab = wb.create_sheet("Vagas IA")
    vagas_tab.append(["Data", "Empresa", "Cargo", "Modalidade", "Link da Vaga", "Status da Candidatura"])
    vagas_tab.append(["12/10/2026", "PagBank", "AI Engineer (Agents)", "Remoto", "https://example.com/job", "A Fazer"])

    xlsx_path = tmp_path / "multitab.xlsx"
    wb.save(xlsx_path)

    jobs = load_jobs(xlsx_path, only_todo=True)
    assert len(jobs) == 1
    assert jobs[0].company == "PagBank"
    assert jobs[0].title == "AI Engineer (Agents)"


def test_results_history_includes_pending_sheet_jobs(tmp_path: Path, monkeypatch):
    from config.settings import Settings
    from web.data_provider import get_results_history

    fake_results = tmp_path / "results.jsonl"
    fake_results.write_text("", encoding="utf-8")

    fake_settings = Settings(RESULTS_FILE=fake_results)
    monkeypatch.setattr("web.data_provider.load_settings", lambda: fake_settings)
    monkeypatch.setattr(
        "web.data_provider.get_jobs_cache",
        lambda: [
            {
                "id": "pagbank-ai-engineer-agents",
                "row_index": 2,
                "date": "12/10/2026",
                "company": "PagBank",
                "title": "AI Engineer (Agents)",
                "modality": "Remoto",
                "url": "https://example.com/pagbank",
                "ats": "Indeed",
                "fit_score": 92,
                "resume_label": "Currículo Otimizado - PagBank.pdf",
                "sheet_status": "A Fazer",
                "language": "pt",
            }
        ],
    )

    history = get_results_history(limit=50)
    assert len(history) == 1
    assert history[0]["job_id"] == "pagbank-ai-engineer-agents"
    assert history[0]["status"] == "PENDING"
    assert history[0]["display_status"] == "Na Fila (A Fazer)"

