"""Testes unitários do modo gerenciado via Supabase Auth + Edge Function llm-proxy."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from config.settings import Settings
from core.cloud_auth import CloudAuthError, SupabaseSessionManager
from core.matcher import LLMOutputError, Matcher
from web import app as web_app
from web import config_manager


def _make_settings(tmp_path: Path, mode: str = "managed") -> Settings:
    return Settings(
        _env_file=None,
        LLM_API_KEY="managed-via-supabase",
        LLM_BASE_URL="https://openrouter.ai/api/v1",
        LLM_MODEL="google/gemini-3.8-flash",
        DRIVE_FOLDER_ID="folder-1",
        LLM_MODE=mode,  # type: ignore[arg-type]
        SUPABASE_URL="https://proj.supabase.co",
        SUPABASE_ANON_KEY="anon-public-key",
        SUPABASE_AUTH_FILE=tmp_path / "supabase_session.json",
        LLM_MAX_RETRIES=2,
        BACKOFF_BASE_S=0.01,
    )


@pytest.mark.asyncio
async def test_session_manager_login_refresh_and_quota(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/auth/v1/token" and "grant_type=password" in str(request.url.query):
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-initial",
                    "refresh_token": "ref-1",
                    "expires_in": 3600,
                    "user": {"id": "user-123", "email": "dev@example.com"},
                },
            )
        if request.url.path == "/auth/v1/token" and "grant_type=refresh_token" in str(request.url.query):
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-refreshed",
                    "refresh_token": "ref-2",
                    "expires_in": 3600,
                    "user": {"id": "user-123", "email": "dev@example.com"},
                },
            )
        if request.url.path == "/functions/v1/llm-proxy/quota":
            assert request.headers.get("Authorization") == "Bearer tok-refreshed"
            return httpx.Response(
                200,
                json={
                    "plan": "pro_cloud",
                    "subscription_status": "active",
                    "used_pct": 12.5,
                    "remaining_pct": 87.5,
                    "cycle_reset_at": "2026-12-14T00:00:00Z",
                    "is_blocked": False,
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    settings = _make_settings(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        mgr = SupabaseSessionManager(settings, client=client)
        login_res = await mgr.login("dev@example.com", "secret123")
        assert login_res["authenticated"] is True
        assert login_res["email"] == "dev@example.com"
        assert settings.SUPABASE_AUTH_FILE.exists()

        # Simula token expirado para forçar refresh automático antes da chamada de cota
        saved = json.loads(settings.SUPABASE_AUTH_FILE.read_text(encoding="utf-8"))
        saved["expires_at"] = time.time() - 10
        settings.SUPABASE_AUTH_FILE.write_text(json.dumps(saved), encoding="utf-8")

        quota = await mgr.get_quota_status()
        assert quota["plan"] == "pro_cloud"
        assert quota["used_pct"] == 12.5
        assert quota["remaining_pct"] == 87.5
        assert quota["is_blocked"] is False


@pytest.mark.asyncio
async def test_matcher_managed_mode_401_refresh_and_quota_headers(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/auth/v1/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-jwt",
                    "refresh_token": "ref-new",
                    "expires_in": 3600,
                    "user": {"id": "u1", "email": "u@example.com"},
                },
            )
        if request.url.path == "/functions/v1/llm-proxy/chat/completions":
            attempts += 1
            auth = request.headers.get("Authorization")
            if attempts == 1:
                assert auth == "Bearer stale-jwt"
                return httpx.Response(401, json={"error": {"message": "token expired"}})
            assert auth == "Bearer fresh-jwt"
            assert request.headers.get("apikey") == "anon-public-key"
            req_body = json.loads(request.content.decode())
            assert req_body["model"] == "google/gemini-3.8-flash"
            return httpx.Response(
                200,
                headers={
                    "X-Quota-Used-Pct": "23.4",
                    "X-Quota-Remaining-Pct": "76.6",
                    "X-Quota-Reset-At": "2026-12-14T00:00:00Z",
                },
                json={
                    "choices": [{"message": {"content": '{"answer":"sim"}'}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                },
            )
        return httpx.Response(404)

    settings = _make_settings(tmp_path, mode="managed")
    settings.SUPABASE_AUTH_FILE.write_text(
        json.dumps(
            {
                "access_token": "stale-jwt",
                "refresh_token": "ref-1",
                "expires_at": time.time() + 3600,
                "user_id": "u1",
                "email": "u@example.com",
            }
        ),
        encoding="utf-8",
    )

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        matcher = Matcher(settings, client=client, sleep=no_sleep)
        reply = await matcher._post([{"role": "user", "content": "ping"}])
        assert reply == '{"answer":"sim"}'
        assert attempts == 2
        quota = matcher.get_last_quota_status()
        assert quota is not None
        assert quota["used_pct"] == 23.4
        assert quota["remaining_pct"] == 76.6


@pytest.mark.asyncio
async def test_matcher_managed_mode_402_quota_exceeded_stops_immediately(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            402,
            headers={"X-Quota-Used-Pct": "100", "X-Quota-Remaining-Pct": "0"},
            json={"error": {"code": "quota_exceeded", "message": "Limite do ciclo atingido."}},
        )

    settings = _make_settings(tmp_path, mode="managed")
    settings.SUPABASE_AUTH_FILE.write_text(
        json.dumps(
            {
                "access_token": "valid-jwt",
                "refresh_token": "ref-1",
                "expires_at": time.time() + 3600,
                "user_id": "u1",
                "email": "u@example.com",
            }
        ),
        encoding="utf-8",
    )

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        matcher = Matcher(settings, client=client, sleep=no_sleep)
        with pytest.raises(LLMOutputError, match="Limite do ciclo atingido"):
            await matcher._post([{"role": "user", "content": "ping"}])
        assert calls == 1
        assert matcher.get_last_quota_status() == {
            "used_pct": 100.0,
            "remaining_pct": 0.0,
            "cycle_reset_at": None,
        }


def test_web_cloud_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=gemini-2.5-flash\nDRIVE_FOLDER_ID=local\n", encoding="utf-8")
    monkeypatch.setattr(config_manager, "ENV_FILE_PATH", env_file)
    monkeypatch.setattr(web_app, "load_raw_env", config_manager.load_raw_env)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/token" and "grant_type=password" in str(request.url.query):
            body = json.loads(request.content.decode())
            if body.get("password") == "wrong":
                return httpx.Response(400, json={"error_description": "Invalid login credentials"})
            return httpx.Response(
                200,
                json={
                    "access_token": "jwt-ok",
                    "refresh_token": "ref-ok",
                    "expires_in": 3600,
                    "user": {"id": "u-99", "email": body["email"]},
                },
            )
        if request.url.path == "/functions/v1/llm-proxy/quota":
            return httpx.Response(
                200,
                json={
                    "plan": "free_trial",
                    "subscription_status": "trialing",
                    "used_pct": 40.0,
                    "remaining_pct": 60.0,
                    "cycle_reset_at": "2026-12-14T00:00:00Z",
                    "is_blocked": False,
                },
            )
        if request.url.path == "/auth/v1/logout":
            return httpx.Response(204)
        return httpx.Response(404)

    settings = _make_settings(tmp_path, mode="byok")
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mgr = SupabaseSessionManager(settings, client=async_client)
    monkeypatch.setattr(web_app, "_get_cloud_manager", lambda: (settings, mgr))

    client = TestClient(web_app.app)

    status_before = client.get("/api/cloud/status").json()
    assert status_before["authenticated"] is False

    bad_login = client.post("/api/cloud/login", json={"email": "a@b.com", "password": "wrong"})
    assert bad_login.status_code == 400
    assert "Invalid login credentials" in bad_login.json()["detail"]

    ok_login = client.post("/api/cloud/login", json={"email": "a@b.com", "password": "correct"})
    assert ok_login.status_code == 200
    login_data: dict[str, Any] = ok_login.json()
    assert login_data["authenticated"] is True
    assert login_data["quota"]["used_pct"] == 40.0

    mode_res = client.post("/api/cloud/mode", json={"mode": "managed"})
    assert mode_res.status_code == 200
    assert mode_res.json()["mode"] == "managed"
    assert "LLM_MODE=managed" in env_file.read_text(encoding="utf-8")

    logout_res = client.post("/api/cloud/logout")
    assert logout_res.status_code == 200
    assert logout_res.json()["authenticated"] is False


def test_supabase_migration_and_edge_function_present() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    sql_path = repo_root / "supabase" / "migrations" / "0006_llm_proxy_budget_quota.sql"
    fn_path = repo_root / "supabase" / "functions" / "llm-proxy" / "index.ts"

    sql = sql_path.read_text(encoding="utf-8")
    assert "llm_budget_usd" in sql
    assert "check_and_reserve_llm_quota" in sql
    assert "record_llm_usage" in sql
    assert "get_my_llm_quota" in sql

    ts = fn_path.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" in ts
    assert "X-Quota-Used-Pct" in ts
    for model_id in (
        "deepseek/deepseek-v4.1-flash",
        "openai/gpt-6-luna",
        "anthropic/claude-haiku-5.5",
        "google/gemini-3.8-flash",
        "google/gemini-3.5-flash-lite",
        "qwen/qwen3.8-flash",
        "qwen/qwen3.7-flash",
        "minimax/minimax-m3.1-flash",
        "minimax/minimax-m3",
    ):
        assert model_id in ts


@pytest.mark.asyncio
async def test_session_manager_requires_login_when_unauthenticated(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, mode="managed")
    mgr = SupabaseSessionManager(settings)
    with pytest.raises(CloudAuthError):
        await mgr.get_valid_access_token()


@pytest.mark.asyncio
async def test_supabase_session_sync_fetch_and_update_jobs(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, mode="managed")
    settings.SUPABASE_AUTH_FILE.write_text(
        json.dumps(
            {
                "access_token": "jwt-valid",
                "refresh_token": "rt-valid",
                "expires_at": time.time() + 3600,
                "user_id": "u-1",
                "email": "user@example.com",
            }
        ),
        encoding="utf-8",
    )

    inserted_payloads: list[dict[str, Any]] = []
    patch_payloads: list[dict[str, Any]] = []
    remote_rows: list[dict[str, Any]] = [
        {
            "id": "sb-1",
            "company": "Acme",
            "title": "Python Dev",
            "job_url": "https://boards.greenhouse.io/acme/1",
            "external_job_id": "ext-1",
            "status": "PENDING",
            "fit_score": 88,
            "dry_run": True,
            "submitted_live": False,
            "source_platform": "greenhouse",
            "metadata": {"local_id": "gh-1", "matched_skills": ["Python"]},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer jwt-valid"
        path = request.url.path
        if path == "/rest/v1/workspace_members":
            return httpx.Response(200, json=[{"workspace_id": "ws-1"}])
        if path == "/rest/v1/candidate_profiles":
            return httpx.Response(200, json=[{"id": "prof-1"}])
        if path == "/rest/v1/job_applications":
            if request.method == "GET":
                if "id,job_url,external_job_id" in str(request.url.query):
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "id": r["id"],
                                "job_url": r["job_url"],
                                "external_job_id": r["external_job_id"],
                            }
                            for r in remote_rows
                        ],
                    )
                return httpx.Response(200, json=remote_rows)
            if request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                inserted_payloads.extend(body)
                for idx, row in enumerate(body, start=2):
                    created = dict(row)
                    created["id"] = f"sb-{idx}"
                    remote_rows.append(created)
                return httpx.Response(201, json=body)
            if request.method == "PATCH":
                body = json.loads(request.content.decode("utf-8"))
                patch_payloads.append(body)
                return httpx.Response(204)
        return httpx.Response(404)

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mgr = SupabaseSessionManager(settings, client=async_client)

    sync_res = await mgr.sync_jobs_to_supabase(
        [
            {
                "id": "gh-1",
                "company": "Acme",
                "title": "Python Dev",
                "job_url": "https://boards.greenhouse.io/acme/1/",
                "external_job_id": "ext-1",
            },
            {
                "id": "lv-2",
                "company": "Beta",
                "title": "Backend Engineer",
                "job_url": "https://jobs.lever.co/beta/2",
                "fit_score": 92,
                "source_platform": "lever",
            },
        ]
    )
    assert sync_res["inserted"] == 1
    assert sync_res["skipped"] == 1
    assert len(inserted_payloads) == 1
    assert inserted_payloads[0]["workspace_id"] == "ws-1"
    assert inserted_payloads[0]["profile_id"] == "prof-1"
    assert inserted_payloads[0]["company"] == "Beta"

    fetched = await mgr.fetch_supabase_jobs(limit=50)
    assert len(fetched) == 2
    assert fetched[0]["supabase_id"] == "sb-1"
    assert fetched[0]["sheet_status"] == "A Fazer"

    ok = await mgr.update_supabase_job_result(
        "sb-2",
        "https://jobs.lever.co/beta/2",
        {
            "status": "COMPLETED",
            "dry_run": False,
            "submitted_live": True,
            "duration_seconds": 4.2,
            "tokens_consumed": 320,
            "resume_label": "cv_beta.pdf",
        },
    )
    assert ok is True
    assert patch_payloads[0]["status"] == "COMPLETED"
    assert patch_payloads[0]["submitted_live"] is True
    assert "applied_at" in patch_payloads[0]


def test_web_api_discover_and_cloud_sync_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue_file = tmp_path / "discovered_jobs.json"
    env_file = tmp_path / ".env"
    env_file.write_text("DRIVE_FOLDER_ID=none\n", encoding="utf-8")
    monkeypatch.setattr(config_manager, "ENV_FILE_PATH", env_file)
    monkeypatch.setenv("DRIVE_FOLDER_ID", "none")

    settings = Settings(
        _env_file=None,
        LLM_API_KEY="k",
        LLM_BASE_URL="https://openrouter.ai/api/v1",
        LLM_MODEL="deepseek/deepseek-v4.1-flash",
        DRIVE_FOLDER_ID="none",
        SUPABASE_URL="https://proj.supabase.co",
        SUPABASE_ANON_KEY="anon-key",
        SUPABASE_AUTH_FILE=tmp_path / "supabase_session.json",
        DISCOVERED_JOBS_FILE=queue_file,
        RESULTS_FILE=tmp_path / "results.jsonl",
        SHEET_RESULT_FILE=tmp_path / "planilha.xlsx",
    )
    settings.SUPABASE_AUTH_FILE.write_text(
        json.dumps(
            {
                "access_token": "jwt-ok",
                "refresh_token": "rt-ok",
                "expires_at": time.time() + 3600,
                "user_id": "u-1",
                "email": "dev@example.com",
            }
        ),
        encoding="utf-8",
    )

    remote_rows: list[dict[str, Any]] = [
        {
            "id": "sb-remote-only",
            "company": "RemoteCo",
            "title": "Platform Engineer",
            "job_url": "https://jobs.ashbyhq.com/remoteco/77",
            "external_job_id": "ash-77",
            "status": "PENDING",
            "fit_score": 91,
            "dry_run": True,
            "submitted_live": False,
            "source_platform": "ashby",
            "metadata": {},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/workspace_members":
            return httpx.Response(200, json=[{"workspace_id": "ws-1"}])
        if path == "/rest/v1/candidate_profiles":
            return httpx.Response(200, json=[{"id": "prof-1"}])
        if path == "/rest/v1/job_applications":
            if request.method == "GET":
                if "id,job_url,external_job_id" in str(request.url.query):
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "id": r["id"],
                                "job_url": r["job_url"],
                                "external_job_id": r["external_job_id"],
                            }
                            for r in remote_rows
                        ],
                    )
                return httpx.Response(200, json=remote_rows)
            if request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                for idx, row in enumerate(body, start=10):
                    r = dict(row)
                    r["id"] = f"sb-{idx}"
                    remote_rows.append(r)
                return httpx.Response(201, json=body)
        return httpx.Response(404)

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mgr = SupabaseSessionManager(settings, client=async_client)

    monkeypatch.setattr(web_app, "load_settings", lambda: settings)
    monkeypatch.setattr(web_app, "_get_cloud_manager", lambda: (settings, mgr))
    monkeypatch.setattr(web_app, "get_jobs_cache", lambda: [])
    monkeypatch.setattr("web.data_provider.load_settings", lambda: settings)
    monkeypatch.setattr("web.data_provider.refresh_sheet_cache", lambda **kwargs: tmp_path / "missing.xlsx")

    async def fake_search_jobs(**kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "nu-99",
                "company": "Nubank",
                "title": "Senior Python Engineer",
                "job_url": "https://boards.greenhouse.io/nu/99",
                "ats": "Greenhouse",
                "fit_score": 95,
                "source_platform": "greenhouse",
            }
        ]

    monkeypatch.setattr("core.job_search.search_jobs", fake_search_jobs)

    client = TestClient(web_app.app)
    disc_res = client.post(
        "/api/jobs/discover",
        json={"query": "python", "location": "Brasil", "sources": ["open_feeds"]},
    )
    assert disc_res.status_code == 200
    disc_data = disc_res.json()
    assert disc_data["total_found"] == 1
    assert disc_data["supabase_sync"] == {"inserted": 1, "skipped": 0, "remote_total": 2}

    sync_res = client.post("/api/cloud/sync-jobs")
    assert sync_res.status_code == 200
    sync_data = sync_res.json()
    assert sync_data["success"] is True
    assert sync_data["pulled"] == 1
    assert sync_data["remote_total"] == 2

    jobs_res = client.get("/api/jobs")
    assert jobs_res.status_code == 200
    jobs_data = jobs_res.json()
    assert jobs_data["supabase_synced"] is True
    urls = {j["url"] for j in jobs_data["jobs"]}
    assert "https://boards.greenhouse.io/nu/99" in urls
    assert "https://jobs.ashbyhq.com/remoteco/77" in urls


def test_config_manager_saves_discovery_tailor_and_supabase_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=k\nLLM_BASE_URL=http://x\nLLM_MODEL=m\nDRIVE_FOLDER_ID=none\n", encoding="utf-8")
    monkeypatch.setattr(config_manager, "ENV_FILE_PATH", env_file)
    orig_env = dict(os.environ)
    try:
        for k in (
            "SUPABASE_SYNC_ENABLED",
            "TAILOR_RESUME_PER_JOB",
            "GENERATED_RESUMES_DIR",
            "DISCOVERED_JOBS_FILE",
            "TARGET_GREENHOUSE_BOARDS",
            "TARGET_LEVER_BOARDS",
            "TARGET_ASHBY_BOARDS",
            "LINKEDIN_DAILY_LIMIT",
            "LINKEDIN_SEARCH_LOCATION",
        ):
            monkeypatch.delenv(k, raising=False)

        saved = config_manager.save_ui_config(
            {
                "cloud": {"supabase_sync_enabled": False},
                "execution": {
                    "tailor_resume_per_job": False,
                    "generated_resumes_dir": "./out/pdfs",
                },
                "discovery": {
                    "discovered_jobs_file": "./out/disc.json",
                    "linkedin_search_location": "Remoto BR",
                    "linkedin_daily_limit": 25,
                    "target_greenhouse_boards": "nubank, stripe",
                    "target_lever_boards": ["netflix"],
                    "target_ashby_boards": "openai",
                },
            }
        )

        assert saved["cloud"]["supabase_sync_enabled"] is False
        assert saved["execution"]["tailor_resume_per_job"] is False
        assert saved["execution"]["generated_resumes_dir"] == "./out/pdfs"
        assert saved["discovery"]["discovered_jobs_file"] == "./out/disc.json"
        assert saved["discovery"]["linkedin_search_location"] == "Remoto BR"
        assert saved["discovery"]["linkedin_daily_limit"] == 25
        assert saved["discovery"]["target_greenhouse_boards"] == ["nubank", "stripe"]
        assert saved["discovery"]["target_lever_boards"] == ["netflix"]
        assert saved["discovery"]["target_ashby_boards"] == ["openai"]

        parsed_settings = Settings(_env_file=env_file)
        assert parsed_settings.SUPABASE_SYNC_ENABLED is False
        assert parsed_settings.TAILOR_RESUME_PER_JOB is False
        assert parsed_settings.TARGET_GREENHOUSE_BOARDS == ["nubank", "stripe"]
        assert parsed_settings.TARGET_LEVER_BOARDS == ["netflix"]
        assert parsed_settings.LINKEDIN_DAILY_LIMIT == 25
    finally:
        os.environ.clear()
        os.environ.update(orig_env)
