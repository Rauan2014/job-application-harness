"""Autenticação Supabase e consulta de cota para o modo LLM gerenciado."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from config.settings import Settings

log = logging.getLogger("ia_candidata.cloud_auth")


class CloudAuthError(Exception):
    """Erro de autenticação ou comunicação com a conta gerenciada."""

    def __init__(self, message: str, status_code: int = 401, code: str = "cloud_auth_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SupabaseSessionManager:
    """Gerencia login, renovação de JWT e consulta de consumo no Supabase."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def supabase_url(self) -> str:
        return self._settings.SUPABASE_URL.rstrip("/")

    @property
    def anon_key(self) -> str:
        return self._settings.SUPABASE_ANON_KEY.strip()

    @property
    def proxy_base_url(self) -> str:
        return f"{self.supabase_url}/functions/v1/llm-proxy"

    @property
    def auth_file(self) -> Path:
        return self._settings.SUPABASE_AUTH_FILE

    def _ensure_configured(self) -> None:
        if not self.supabase_url or not self.anon_key:
            raise CloudAuthError(
                "SUPABASE_URL e SUPABASE_ANON_KEY precisam estar configurados.",
                status_code=500,
                code="supabase_not_configured",
            )

    def _base_headers(self, access_token: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any = None,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, json=json_body, timeout=20.0)
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.request(method, url, headers=headers, json=json_body)

    async def _rest_request(
        self,
        method: str,
        path_and_query: str,
        *,
        json_body: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Executa requisição autenticada no PostgREST (/rest/v1) com refresh automático em 401."""
        self._ensure_configured()
        url = f"{self.supabase_url}/rest/v1/{path_and_query.lstrip('/')}"
        token = await self.get_valid_access_token()
        headers = self._base_headers(token)
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = await self._request(method, url, headers=headers, json_body=json_body)
            if resp.status_code == 401:
                token = await self.get_valid_access_token(force_refresh=True)
                headers = self._base_headers(token)
                if extra_headers:
                    headers.update(extra_headers)
                resp = await self._request(method, url, headers=headers, json_body=json_body)
            return resp
        except httpx.HTTPError as exc:
            raise CloudAuthError(f"Erro de comunicação com o Supabase: {exc}", status_code=502) from exc

    def load_session(self) -> dict[str, Any] | None:
        """Carrega a sessão salva localmente em cache/supabase_session.json."""
        if not self.auth_file.exists():
            return None
        try:
            raw = self.auth_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        return data

    def _save_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        expires_in = float(payload.get("expires_in") or 3600)
        expires_at = float(payload.get("expires_at") or (time.time() + expires_in))
        user_raw = payload.get("user")
        user: dict[str, Any] = user_raw if isinstance(user_raw, dict) else {}
        session_data: dict[str, Any] = {
            "access_token": str(payload.get("access_token") or ""),
            "refresh_token": str(payload.get("refresh_token") or ""),
            "expires_at": expires_at,
            "user_id": str(user.get("id") or payload.get("user_id") or ""),
            "email": str(user.get("email") or payload.get("email") or ""),
        }
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.auth_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.auth_file)
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(self.auth_file, 0o600)
        return session_data

    def clear_session(self) -> None:
        """Remove a sessão autenticada salva localmente."""
        with contextlib.suppress(OSError):
            if self.auth_file.exists():
                self.auth_file.unlink()

    @staticmethod
    def _extract_error_message(response: httpx.Response, fallback: str) -> str:
        try:
            data = response.json()
        except ValueError:
            return fallback
        if not isinstance(data, dict):
            return fallback
        if isinstance(data.get("error"), dict):
            msg = data["error"].get("message")
            if msg:
                return str(msg)
        for key in ("error_description", "msg", "message", "error"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return fallback

    async def signup(self, email: str, password: str) -> dict[str, Any]:
        """Cria conta no Supabase Auth e salva sessão se já houver JWT."""
        self._ensure_configured()
        url = f"{self.supabase_url}/auth/v1/signup"
        try:
            resp = await self._request(
                "POST",
                url,
                headers=self._base_headers(),
                json_body={"email": email.strip(), "password": password},
            )
        except httpx.HTTPError as exc:
            raise CloudAuthError(f"Falha de rede ao criar conta: {exc}", status_code=502) from exc

        if resp.status_code >= 400:
            msg = self._extract_error_message(resp, "Não foi possível criar sua conta.")
            raise CloudAuthError(msg, status_code=resp.status_code, code="signup_failed")

        data = resp.json()
        session_obj = data.get("session") if isinstance(data.get("session"), dict) else data
        if isinstance(session_obj, dict) and session_obj.get("access_token"):
            merged = dict(session_obj)
            if "user" not in merged and isinstance(data.get("user"), dict):
                merged["user"] = data["user"]
            saved = self._save_session(merged)
            return {
                "authenticated": True,
                "confirmation_required": False,
                "email": saved["email"] or email.strip(),
                "user_id": saved["user_id"],
            }

        return {
            "authenticated": False,
            "confirmation_required": True,
            "email": email.strip(),
            "user_id": str((data.get("user") or {}).get("id") or data.get("id") or ""),
        }

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Autentica com e-mail e senha no Supabase Auth."""
        self._ensure_configured()
        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        try:
            resp = await self._request(
                "POST",
                url,
                headers=self._base_headers(),
                json_body={"email": email.strip(), "password": password},
            )
        except httpx.HTTPError as exc:
            raise CloudAuthError(f"Falha de rede ao entrar: {exc}", status_code=502) from exc

        if resp.status_code >= 400:
            msg = self._extract_error_message(resp, "E-mail ou senha inválidos.")
            raise CloudAuthError(msg, status_code=resp.status_code, code="login_failed")

        saved = self._save_session(resp.json())
        return {
            "authenticated": True,
            "email": saved["email"],
            "user_id": saved["user_id"],
            "expires_at": saved["expires_at"],
        }

    async def refresh_session(self, refresh_token: str | None = None) -> dict[str, Any]:
        """Renova o JWT usando o refresh_token do Supabase."""
        self._ensure_configured()
        rt = refresh_token
        if not rt:
            current = self.load_session()
            rt = str((current or {}).get("refresh_token") or "")
        if not rt:
            self.clear_session()
            raise CloudAuthError(
                "Sessão expirada. Faça login novamente na conta gerenciada.",
                status_code=401,
                code="refresh_token_missing",
            )

        url = f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token"
        try:
            resp = await self._request(
                "POST",
                url,
                headers=self._base_headers(),
                json_body={"refresh_token": rt},
            )
        except httpx.HTTPError as exc:
            raise CloudAuthError(f"Erro ao renovar sessão: {exc}", status_code=502) from exc

        if resp.status_code >= 400:
            if resp.status_code in (400, 401, 403):
                self.clear_session()
            msg = self._extract_error_message(resp, "Sessão expirada. Faça login novamente.")
            raise CloudAuthError(msg, status_code=401, code="refresh_failed")

        return self._save_session(resp.json())

    async def logout(self) -> None:
        """Encerra a sessão no servidor (best-effort) e remove o cache local."""
        current = self.load_session()
        if current and current.get("access_token") and self.supabase_url and self.anon_key:
            with contextlib.suppress(httpx.HTTPError):
                await self._request(
                    "POST",
                    f"{self.supabase_url}/auth/v1/logout",
                    headers=self._base_headers(str(current["access_token"])),
                )
        self.clear_session()

    async def get_valid_access_token(self, force_refresh: bool = False) -> str:
        """Retorna um JWT válido, renovando-o automaticamente quando próximo do vencimento."""
        current = self.load_session()
        if not current:
            raise CloudAuthError(
                "Conta gerenciada não conectada. Faça login em Configurações > Modo de IA.",
                status_code=401,
                code="not_authenticated",
            )
        expires_at = float(current.get("expires_at") or 0)
        if force_refresh or time.time() >= (expires_at - 60):
            refreshed = await self.refresh_session(str(current.get("refresh_token") or ""))
            return str(refreshed["access_token"])
        return str(current["access_token"])

    async def get_quota_status(self) -> dict[str, Any]:
        """Consulta o percentual de cota consumido no proxy llm-proxy."""
        self._ensure_configured()
        url = f"{self.proxy_base_url}/quota"
        token = await self.get_valid_access_token()
        try:
            resp = await self._request("GET", url, headers=self._base_headers(token))
            if resp.status_code == 401:
                token = await self.get_valid_access_token(force_refresh=True)
                resp = await self._request("GET", url, headers=self._base_headers(token))
        except httpx.HTTPError as exc:
            raise CloudAuthError(f"Erro ao consultar consumo na nuvem: {exc}", status_code=502) from exc

        if resp.status_code >= 400:
            msg = self._extract_error_message(resp, "Não foi possível consultar sua cota.")
            raise CloudAuthError(msg, status_code=resp.status_code, code="quota_fetch_failed")

        data = resp.json()
        return {
            "plan": data.get("plan", "free_trial"),
            "subscription_status": data.get("subscription_status", "inactive"),
            "used_pct": float(data.get("used_pct", 0.0)),
            "remaining_pct": float(data.get("remaining_pct", 100.0)),
            "cycle_reset_at": data.get("cycle_reset_at"),
            "is_blocked": bool(data.get("is_blocked", False)),
            "reason": data.get("reason"),
        }

    async def list_allowed_models(self) -> list[dict[str, str]]:
        """Lista modelos rápidos habilitados no modo gerenciado."""
        self._ensure_configured()
        url = f"{self.proxy_base_url}/models"
        try:
            resp = await self._request("GET", url, headers=self._base_headers())
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        payload = resp.json()
        models = payload.get("data")
        return models if isinstance(models, list) else []

    @staticmethod
    def _sheet_status_to_task(sheet_status: str | None) -> tuple[str, bool, bool]:
        norm = str(sheet_status or "").strip().lower()
        if "candidatad" in norm or "aplicad" in norm:
            return "COMPLETED", False, True
        if "pronta" in norm or "dry" in norm:
            return "COMPLETED", True, False
        if "revis" in norm:
            return "NEEDS_REVIEW", True, False
        if "falh" in norm:
            return "FAILED", True, False
        if "ignor" in norm or "skip" in norm:
            return "SKIPPED", True, False
        return "PENDING", True, False

    @staticmethod
    def _task_status_to_sheet(task_status: str | None, submitted_live: bool = False) -> str:
        st = str(task_status or "PENDING").strip().upper()
        if st == "COMPLETED":
            return "Candidatado" if submitted_live else "Pronta (Dry-Run)"
        if st == "NEEDS_REVIEW":
            return "Revisar"
        if st == "FAILED":
            return "Falhou"
        if st == "SKIPPED":
            return "Ignorada"
        return "A Fazer"

    async def get_workspace_context(self) -> dict[str, str]:
        """Obtém workspace_id e profile_id do usuário autenticado via RLS."""
        ws_resp = await self._rest_request(
            "GET",
            "workspace_members?select=workspace_id&order=created_at.asc&limit=1",
        )
        if ws_resp.status_code >= 400:
            msg = self._extract_error_message(ws_resp, "Não foi possível localizar o workspace no Supabase.")
            raise CloudAuthError(msg, status_code=ws_resp.status_code, code="workspace_fetch_failed")
        ws_rows = ws_resp.json()
        if not isinstance(ws_rows, list) or not ws_rows or not ws_rows[0].get("workspace_id"):
            raise CloudAuthError(
                "Nenhum workspace encontrado para esta conta no Supabase.",
                status_code=404,
                code="workspace_missing",
            )
        workspace_id = str(ws_rows[0]["workspace_id"])

        prof_resp = await self._rest_request(
            "GET",
            f"candidate_profiles?workspace_id=eq.{quote(workspace_id, safe='')}&select=id&order=created_at.asc&limit=1",
        )
        if prof_resp.status_code >= 400:
            msg = self._extract_error_message(prof_resp, "Não foi possível localizar o perfil no Supabase.")
            raise CloudAuthError(msg, status_code=prof_resp.status_code, code="profile_fetch_failed")
        prof_rows = prof_resp.json()
        if not isinstance(prof_rows, list) or not prof_rows or not prof_rows[0].get("id"):
            raise CloudAuthError(
                "Perfil de candidato não encontrado para este workspace no Supabase.",
                status_code=404,
                code="profile_missing",
            )
        return {"workspace_id": workspace_id, "profile_id": str(prof_rows[0]["id"])}

    async def sync_jobs_to_supabase(self, jobs: list[dict[str, Any]]) -> dict[str, int]:
        """Envia vagas novas para public.job_applications com deduplicação por URL/external_id."""
        ctx = await self.get_workspace_context()
        ws_id = ctx["workspace_id"]
        prof_id = ctx["profile_id"]

        existing_resp = await self._rest_request(
            "GET",
            f"job_applications?workspace_id=eq.{quote(ws_id, safe='')}&select=id,job_url,external_job_id&limit=1000",
        )
        if existing_resp.status_code >= 400:
            msg = self._extract_error_message(existing_resp, "Falha ao consultar vagas existentes no Supabase.")
            raise CloudAuthError(msg, status_code=existing_resp.status_code, code="supabase_jobs_read_failed")

        existing_rows = existing_resp.json() if isinstance(existing_resp.json(), list) else []
        seen_urls = {
            str(r.get("job_url") or "").strip().rstrip("/").lower()
            for r in existing_rows
            if r.get("job_url")
        }
        seen_ext_ids = {
            str(r.get("external_job_id") or "").strip()
            for r in existing_rows
            if r.get("external_job_id")
        }

        to_insert: list[dict[str, Any]] = []
        skipped = 0
        for job in jobs:
            job_url = str(job.get("job_url") or job.get("url") or "").strip()
            if not job_url:
                skipped += 1
                continue
            norm_url = job_url.rstrip("/").lower()
            ext_id = str(job.get("external_job_id") or job.get("id") or "").strip()
            if norm_url in seen_urls or (ext_id and ext_id in seen_ext_ids):
                skipped += 1
                continue

            seen_urls.add(norm_url)
            if ext_id:
                seen_ext_ids.add(ext_id)

            task_status, dry_run, submitted_live = self._sheet_status_to_task(job.get("sheet_status"))
            fit_raw = job.get("fit_score")
            fit_val: int | None = None
            if fit_raw is not None and str(fit_raw).strip() != "":
                with contextlib.suppress(TypeError, ValueError):
                    fit_val = int(float(fit_raw))

            row: dict[str, Any] = {
                "workspace_id": ws_id,
                "profile_id": prof_id,
                "company": str(job.get("company") or "Empresa")[:200],
                "title": str(job.get("title") or "Cargo")[:250],
                "job_url": job_url,
                "status": task_status,
                "dry_run": dry_run,
                "submitted_live": submitted_live,
                "ats": str(job.get("ats") or "") or None,
                "fit_score": fit_val,
                "modality": str(job.get("modality") or "") or None,
                "resume_label": str(job.get("resume_label") or "") or None,
                "base_used": job.get("base_used") or None,
                "stack": job.get("stack") or None,
                "job_date": str(job.get("job_date") or job.get("date") or "") or None,
                "description": str(job.get("description") or "")[:15000] or None,
                "location": str(job.get("location") or "") or None,
                "salary": str(job.get("salary") or "") or None,
                "seniority": str(job.get("seniority") or "") or None,
                "source_platform": str(job.get("source_platform") or "radar") or None,
                "external_job_id": ext_id or None,
                "notes": str(job.get("notes") or "") or None,
                "metadata": {
                    "local_id": str(job.get("id") or ext_id or ""),
                    "tailored_resume_path": job.get("tailored_resume_path"),
                    "matched_skills": job.get("matched_skills") or [],
                },
            }
            to_insert.append({k: v for k, v in row.items() if v is not None})

        if to_insert:
            ins_resp = await self._rest_request(
                "POST",
                "job_applications",
                json_body=to_insert,
                extra_headers={"Prefer": "return=representation"},
            )
            if ins_resp.status_code >= 400:
                msg = self._extract_error_message(ins_resp, "Falha ao salvar vagas no Supabase.")
                raise CloudAuthError(msg, status_code=ins_resp.status_code, code="supabase_jobs_insert_failed")

        return {
            "inserted": len(to_insert),
            "skipped": skipped,
            "remote_total": len(existing_rows) + len(to_insert),
        }

    async def fetch_supabase_jobs(self, status_filter: str | None = None, limit: int = 150) -> list[dict[str, Any]]:
        """Busca vagas salvas em public.job_applications do usuário autenticado."""
        ctx = await self.get_workspace_context()
        ws_id = ctx["workspace_id"]
        query = (
            f"job_applications?workspace_id=eq.{quote(ws_id, safe='')}"
            f"&select=id,company,title,job_url,ats,status,fit_score,dry_run,submitted_live,"
            f"review_reason,duration_seconds,tokens_consumed,modality,resume_label,base_used,stack,"
            f"job_date,description,location,salary,seniority,source_platform,external_job_id,"
            f"notes,metadata,created_at,updated_at,applied_at"
            f"&order=created_at.desc&limit={max(1, min(int(limit), 500))}"
        )
        if status_filter:
            query += f"&status=eq.{quote(str(status_filter).upper(), safe='')}"

        resp = await self._rest_request("GET", query)
        if resp.status_code >= 400:
            msg = self._extract_error_message(resp, "Falha ao buscar vagas no Supabase.")
            raise CloudAuthError(msg, status_code=resp.status_code, code="supabase_jobs_fetch_failed")

        rows = resp.json()
        if not isinstance(rows, list):
            return []

        mapped: list[dict[str, Any]] = []
        for row in rows:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            supabase_id = str(row.get("id") or "")
            local_id = str(meta.get("local_id") or row.get("external_job_id") or f"sb-{supabase_id}")
            tailored_path = meta.get("tailored_resume_path")
            tailored_url = (
                f"/api/resumes/generated/{Path(str(tailored_path)).name}"
                if tailored_path and Path(str(tailored_path)).name
                else None
            )
            submitted_live = bool(row.get("submitted_live"))
            mapped.append(
                {
                    "id": local_id,
                    "supabase_id": supabase_id,
                    "company": row.get("company") or "Empresa",
                    "title": row.get("title") or "Cargo",
                    "url": row.get("job_url"),
                    "job_url": row.get("job_url"),
                    "ats": row.get("ats") or "Supabase",
                    "status": row.get("status") or "PENDING",
                    "sheet_status": self._task_status_to_sheet(row.get("status"), submitted_live),
                    "fit_score": row.get("fit_score"),
                    "dry_run": bool(row.get("dry_run", True)),
                    "submitted_live": submitted_live,
                    "review_reason": row.get("review_reason"),
                    "duration_seconds": row.get("duration_seconds"),
                    "tokens_consumed": row.get("tokens_consumed") or 0,
                    "modality": row.get("modality") or "100% Remoto",
                    "resume_label": row.get("resume_label"),
                    "base_used": row.get("base_used") or [],
                    "stack": row.get("stack") or [],
                    "job_date": row.get("job_date") or "",
                    "date": row.get("job_date") or "",
                    "description": row.get("description"),
                    "location": row.get("location"),
                    "salary": row.get("salary"),
                    "seniority": row.get("seniority"),
                    "source_platform": row.get("source_platform") or "supabase",
                    "external_job_id": row.get("external_job_id"),
                    "notes": row.get("notes"),
                    "tailored_resume_path": tailored_path,
                    "tailored_resume_url": tailored_url,
                    "matched_skills": meta.get("matched_skills") or [],
                    "created_at": row.get("created_at"),
                }
            )
        return mapped

    async def update_supabase_job_result(
        self,
        supabase_job_id: str | None,
        job_url: str | None,
        result_fields: dict[str, Any],
    ) -> bool:
        """Atualiza o status e resultado de uma vaga em public.job_applications."""
        ctx = await self.get_workspace_context()
        ws_id = ctx["workspace_id"]
        target_id = str(supabase_job_id or "").strip()

        if not target_id and job_url:
            clean_url = str(job_url).strip()
            lookup_q = (
                f"job_applications?workspace_id=eq.{quote(ws_id, safe='')}"
                f"&job_url=eq.{quote(clean_url, safe='')}&select=id&limit=1"
            )
            lookup = await self._rest_request("GET", lookup_q)
            if lookup.status_code < 400 and isinstance(lookup.json(), list) and lookup.json():
                target_id = str(lookup.json()[0].get("id") or "")

        if not target_id:
            return False

        patch_payload: dict[str, Any] = {
            "status": str(result_fields.get("status") or "COMPLETED"),
            "dry_run": bool(result_fields.get("dry_run", True)),
            "submitted_live": bool(result_fields.get("submitted_live", False)),
            "review_reason": result_fields.get("review_reason"),
            "duration_seconds": result_fields.get("duration_seconds"),
            "tokens_consumed": int(result_fields.get("tokens_consumed") or 0),
            "resume_label": result_fields.get("resume_label"),
        }
        if patch_payload["submitted_live"]:
            patch_payload["applied_at"] = datetime.now(UTC).isoformat()
        clean_patch = {k: v for k, v in patch_payload.items() if v is not None}

        resp = await self._rest_request(
            "PATCH",
            f"job_applications?workspace_id=eq.{quote(ws_id, safe='')}&id=eq.{quote(target_id, safe='')}",
            json_body=clean_patch,
        )
        return resp.status_code < 400
