"""Gerenciador de configurações (.env) com mascaramento de segredos e teste de conectividade."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from config.settings import Settings, load_settings

logger = logging.getLogger(__name__)

ENV_FILE_PATH = Path(".env")


def mask_secret(value: str | None) -> str:
    """Mascara segredos mostrando apenas prefixo e sufixo para exibição segura."""
    if not value or len(value) <= 8:
        return "••••••••" if value else ""
    return f"{value[:6]}••••••••{value[-4:]}"


def load_raw_env() -> dict[str, str]:
    """Lê todas as variáveis cruas do arquivo .env."""
    if not ENV_FILE_PATH.exists():
        return {}
    
    result: dict[str, str] = {}
    lines = ENV_FILE_PATH.read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            result[key] = val
    return result


def get_ui_config() -> dict[str, Any]:
    """Retorna configuração formatada para consumo seguro pela UI, cobrindo 100% dos valores do .env."""
    import json

    raw = load_raw_env()
    try:
        settings = load_settings()
        api_key_secret = settings.LLM_API_KEY.get_secret_value()
    except Exception:
        settings = None
        api_key_secret = raw.get("LLM_API_KEY", "")

    raw_env_content = ""
    if ENV_FILE_PATH.exists():
        raw_env_content = ENV_FILE_PATH.read_text(encoding="utf-8")

    return {
        "llm": {
            "api_key": api_key_secret,
            "api_key_masked": mask_secret(api_key_secret),
            "is_key_set": bool(api_key_secret and api_key_secret != "your_api_key_here"),
            "base_url": raw.get("LLM_BASE_URL", getattr(settings, "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")),
            "model": raw.get("LLM_MODEL", getattr(settings, "LLM_MODEL", "gemini-2.5-flash")),
            "timeout_s": float(raw.get("LLM_TIMEOUT_S", getattr(settings, "LLM_TIMEOUT_S", 60.0))),
            "max_retries": int(raw.get("LLM_MAX_RETRIES", getattr(settings, "LLM_MAX_RETRIES", 3))),
            "max_input_chars": int(raw.get("LLM_MAX_INPUT_CHARS", getattr(settings, "LLM_MAX_INPUT_CHARS", 12000))),
            "no_llm": raw.get("NO_LLM", "false").lower() in ("true", "1", "yes"),
        },
        "sheet": {
            "google_sheet_url": raw.get("GOOGLE_SHEET_URL", getattr(settings, "GOOGLE_SHEET_URL", "") or ""),
            "include_blank_status": raw.get("INCLUDE_BLANK_STATUS", "true").lower() in ("true", "1", "yes"),
            "drive_folder_id": raw.get("DRIVE_FOLDER_ID", getattr(settings, "DRIVE_FOLDER_ID", "") or ""),
            "cache_dir": raw.get("CACHE_DIR", str(getattr(settings, "CACHE_DIR", "./cache/resumes"))),
            "google_oauth_client_file": raw.get("GOOGLE_OAUTH_CLIENT_FILE", str(getattr(settings, "GOOGLE_OAUTH_CLIENT_FILE", "./client_secret.json"))),
            "google_service_account_file": raw.get("GOOGLE_SERVICE_ACCOUNT_FILE", str(getattr(settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "./service_account.json"))),
            "google_token_file": raw.get("GOOGLE_TOKEN_FILE", str(getattr(settings, "GOOGLE_TOKEN_FILE", "./token.json"))),
        },
        "execution": {
            "dry_run": raw.get("DRY_RUN", "true").lower() in ("true", "1", "yes"),
            "autonomous_mode": raw.get("AUTONOMOUS_MODE", "false").lower() in ("true", "1", "yes"),
            "max_concurrency": int(raw.get("MAX_CONCURRENCY", getattr(settings, "MAX_CONCURRENCY", 3))),
            "use_camoufox": raw.get("USE_CAMOUFOX", "true").lower() in ("true", "1", "yes"),
            "headless": raw.get("HEADLESS", "true").lower() in ("true", "1", "yes"),
            "min_fit_score": int(raw.get("MIN_FIT_SCORE", getattr(settings, "MIN_FIT_SCORE", 70))),
            "fit_gate_enabled": raw.get("FIT_GATE_ENABLED", "true").lower() in ("true", "1", "yes"),
            "resume_match_threshold": float(raw.get("RESUME_MATCH_THRESHOLD", getattr(settings, "RESUME_MATCH_THRESHOLD", 0.9))),
            "task_timeout_s": float(raw.get("TASK_TIMEOUT_S", getattr(settings, "TASK_TIMEOUT_S", 120.0))),
            "nav_timeout_ms": int(raw.get("NAV_TIMEOUT_MS", getattr(settings, "NAV_TIMEOUT_MS", 30000))),
            "action_timeout_ms": int(raw.get("ACTION_TIMEOUT_MS", getattr(settings, "ACTION_TIMEOUT_MS", 10000))),
            "max_retries": int(raw.get("MAX_RETRIES", getattr(settings, "MAX_RETRIES", 2))),
            "backoff_base_s": float(raw.get("BACKOFF_BASE_S", getattr(settings, "BACKOFF_BASE_S", 1.0))),
            "blocked_url_patterns": raw.get("BLOCKED_URL_PATTERNS", json.dumps(getattr(settings, "BLOCKED_URL_PATTERNS", ["*google-analytics.com*", "*googletagmanager.com*", "*doubleclick.net*", "*facebook.net*", "*hotjar.com*", "*segment.io*"]))),
            "storage_state_path": raw.get("STORAGE_STATE_PATH", str(getattr(settings, "STORAGE_STATE_PATH", "./cache/storage_state.json"))),
            "screenshot_dir": raw.get("SCREENSHOT_DIR", str(getattr(settings, "SCREENSHOT_DIR", "./artifacts/screenshots"))),
            "results_file": raw.get("RESULTS_FILE", str(getattr(settings, "RESULTS_FILE", "./artifacts/results.jsonl"))),
            "sheet_result_file": raw.get("SHEET_RESULT_FILE", str(getattr(settings, "SHEET_RESULT_FILE", "./artifacts/planilha_atualizada.xlsx"))),
        },
        "decision": {
            "decision_backend": raw.get("DECISION_BACKEND", getattr(settings, "DECISION_BACKEND", "laya")),
            "laya_url": raw.get("LAYA_URL", getattr(settings, "LAYA_URL", "http://127.0.0.1:8787")),
            "laya_timeout_s": float(raw.get("LAYA_TIMEOUT_S", getattr(settings, "LAYA_TIMEOUT_S", 5.0))),
            "laya_cpu_timeout_factor": float(raw.get("LAYA_CPU_TIMEOUT_FACTOR", getattr(settings, "LAYA_CPU_TIMEOUT_FACTOR", 5.0))),
            "laya_max_failures": int(raw.get("LAYA_MAX_FAILURES", getattr(settings, "LAYA_MAX_FAILURES", 3))),
            "laya_breaker_cooldown_s": float(raw.get("LAYA_BREAKER_COOLDOWN_S", getattr(settings, "LAYA_BREAKER_COOLDOWN_S", 60.0))),
            "laya_yes_threshold": float(raw.get("LAYA_YES_THRESHOLD", getattr(settings, "LAYA_YES_THRESHOLD", 0.8))),
            "laya_no_threshold": float(raw.get("LAYA_NO_THRESHOLD", getattr(settings, "LAYA_NO_THRESHOLD", 0.2))),
            "laya_min_margin": float(raw.get("LAYA_MIN_MARGIN", getattr(settings, "LAYA_MIN_MARGIN", 0.15))),
            "laya_order_swap": raw.get("LAYA_ORDER_SWAP", "true").lower() in ("true", "1", "yes"),
            "laya_state_max_chars": int(raw.get("LAYA_STATE_MAX_CHARS", getattr(settings, "LAYA_STATE_MAX_CHARS", 1500))),
            "laya_plan_above_chars": int(raw.get("LAYA_PLAN_ABOVE_CHARS", getattr(settings, "LAYA_PLAN_ABOVE_CHARS", 600))),
            "laya_dp_modes": raw.get("LAYA_DP_MODES", json.dumps(getattr(settings, "LAYA_DP_MODES", {"DP1": "shadow", "DP2": "shadow", "DP3": "active", "DP4": "active", "DP5": "shadow", "DP6": "active"}))),
            "laya_require_eval_for_active": raw.get("LAYA_REQUIRE_EVAL_FOR_ACTIVE", "true").lower() in ("true", "1", "yes"),
            "laya_eval_file": raw.get("LAYA_EVAL_FILE", str(getattr(settings, "LAYA_EVAL_FILE", "./artifacts/laya_eval.json"))),
            "laya_eval_max_age_days": int(raw.get("LAYA_EVAL_MAX_AGE_DAYS", getattr(settings, "LAYA_EVAL_MAX_AGE_DAYS", 14))),
            "eval_min_examples": int(raw.get("EVAL_MIN_EXAMPLES", getattr(settings, "EVAL_MIN_EXAMPLES", 30))),
            "eval_margin": float(raw.get("EVAL_MARGIN", getattr(settings, "EVAL_MARGIN", 0.10))),
            "eval_min_coverage": float(raw.get("EVAL_MIN_COVERAGE", getattr(settings, "EVAL_MIN_COVERAGE", 0.5))),
            "decision_audit_file": raw.get("DECISION_AUDIT_FILE", str(getattr(settings, "DECISION_AUDIT_FILE", "./artifacts/decisions.jsonl"))),
        },
        "raw_env": raw_env_content,
    }


def save_ui_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Salva alterações nos valores do .env preservando estrutura existente e recarregando o ambiente."""
    import json

    # Se foi enviado o arquivo .env bruto (modo editor direto)
    if "raw_env" in updates and isinstance(updates["raw_env"], str):
        content = updates["raw_env"].replace("\r\n", "\n")
        ENV_FILE_PATH.write_text(content, encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")
        raw = load_raw_env()
        if raw.get("GOOGLE_SHEET_URL"):
            try:
                from web.data_provider import refresh_sheet_cache
                refresh_sheet_cache(sheet_url=raw["GOOGLE_SHEET_URL"], force_download=True)
            except Exception as exc:
                logger.warning("Falha ao sincronizar planilha após salvar .env bruto: %s", exc)
        return get_ui_config()

    current_lines: list[str] = []
    if ENV_FILE_PATH.exists():
        current_lines = ENV_FILE_PATH.read_text(encoding="utf-8").splitlines()

    mapping: dict[str, str] = {}

    # 1. LLM
    if "llm" in updates and isinstance(updates["llm"], dict):
        llm = updates["llm"]
        if "api_key" in llm and llm["api_key"] and "•••" not in llm["api_key"]:
            mapping["LLM_API_KEY"] = str(llm["api_key"]).strip()
        if "base_url" in llm and llm["base_url"] is not None:
            mapping["LLM_BASE_URL"] = str(llm["base_url"]).strip()
        if "model" in llm and llm["model"] is not None:
            mapping["LLM_MODEL"] = str(llm["model"]).strip()
        if "timeout_s" in llm and llm["timeout_s"] is not None:
            mapping["LLM_TIMEOUT_S"] = str(llm["timeout_s"]).strip()
        if "max_retries" in llm and llm["max_retries"] is not None:
            mapping["LLM_MAX_RETRIES"] = str(int(llm["max_retries"]))
        if "max_input_chars" in llm and llm["max_input_chars"] is not None:
            mapping["LLM_MAX_INPUT_CHARS"] = str(int(llm["max_input_chars"]))
        if "no_llm" in llm:
            mapping["NO_LLM"] = "true" if llm["no_llm"] else "false"

    # 2. Planilha & Google Drive
    if "sheet" in updates and isinstance(updates["sheet"], dict):
        sh = updates["sheet"]
        if "google_sheet_url" in sh and sh["google_sheet_url"] is not None:
            mapping["GOOGLE_SHEET_URL"] = str(sh["google_sheet_url"]).strip()
        if "include_blank_status" in sh:
            mapping["INCLUDE_BLANK_STATUS"] = "true" if sh["include_blank_status"] else "false"
        if "drive_folder_id" in sh and sh["drive_folder_id"] is not None:
            mapping["DRIVE_FOLDER_ID"] = str(sh["drive_folder_id"]).strip()
        if "cache_dir" in sh and sh["cache_dir"] is not None:
            mapping["CACHE_DIR"] = str(sh["cache_dir"]).strip()
        if "google_oauth_client_file" in sh and sh["google_oauth_client_file"] is not None:
            mapping["GOOGLE_OAUTH_CLIENT_FILE"] = str(sh["google_oauth_client_file"]).strip()
        if "google_service_account_file" in sh and sh["google_service_account_file"] is not None:
            mapping["GOOGLE_SERVICE_ACCOUNT_FILE"] = str(sh["google_service_account_file"]).strip()
        if "google_token_file" in sh and sh["google_token_file"] is not None:
            mapping["GOOGLE_TOKEN_FILE"] = str(sh["google_token_file"]).strip()

    # 3. Execução, Concorrência, Timeouts & Navegador
    if "execution" in updates and isinstance(updates["execution"], dict):
        exc = updates["execution"]
        if "dry_run" in exc:
            mapping["DRY_RUN"] = "true" if exc["dry_run"] else "false"
        if "autonomous_mode" in exc:
            mapping["AUTONOMOUS_MODE"] = "true" if exc["autonomous_mode"] else "false"
        if "max_concurrency" in exc and exc["max_concurrency"] is not None:
            mapping["MAX_CONCURRENCY"] = str(int(exc["max_concurrency"]))
        if "use_camoufox" in exc:
            mapping["USE_CAMOUFOX"] = "true" if exc["use_camoufox"] else "false"
        if "headless" in exc:
            mapping["HEADLESS"] = "true" if exc["headless"] else "false"
        if "min_fit_score" in exc and exc["min_fit_score"] is not None:
            mapping["MIN_FIT_SCORE"] = str(int(exc["min_fit_score"]))
        if "fit_gate_enabled" in exc:
            mapping["FIT_GATE_ENABLED"] = "true" if exc["fit_gate_enabled"] else "false"
        if "resume_match_threshold" in exc and exc["resume_match_threshold"] is not None:
            mapping["RESUME_MATCH_THRESHOLD"] = str(float(exc["resume_match_threshold"]))
        if "task_timeout_s" in exc and exc["task_timeout_s"] is not None:
            mapping["TASK_TIMEOUT_S"] = str(float(exc["task_timeout_s"]))
        if "nav_timeout_ms" in exc and exc["nav_timeout_ms"] is not None:
            mapping["NAV_TIMEOUT_MS"] = str(int(exc["nav_timeout_ms"]))
        if "action_timeout_ms" in exc and exc["action_timeout_ms"] is not None:
            mapping["ACTION_TIMEOUT_MS"] = str(int(exc["action_timeout_ms"]))
        if "max_retries" in exc and exc["max_retries"] is not None:
            mapping["MAX_RETRIES"] = str(int(exc["max_retries"]))
        if "backoff_base_s" in exc and exc["backoff_base_s"] is not None:
            mapping["BACKOFF_BASE_S"] = str(float(exc["backoff_base_s"]))
        if "blocked_url_patterns" in exc and exc["blocked_url_patterns"] is not None:
            pat = exc["blocked_url_patterns"]
            mapping["BLOCKED_URL_PATTERNS"] = json.dumps(pat) if isinstance(pat, list) else str(pat).strip()
        if "storage_state_path" in exc and exc["storage_state_path"] is not None:
            mapping["STORAGE_STATE_PATH"] = str(exc["storage_state_path"]).strip()
        if "screenshot_dir" in exc and exc["screenshot_dir"] is not None:
            mapping["SCREENSHOT_DIR"] = str(exc["screenshot_dir"]).strip()
        if "results_file" in exc and exc["results_file"] is not None:
            mapping["RESULTS_FILE"] = str(exc["results_file"]).strip()
        if "sheet_result_file" in exc and exc["sheet_result_file"] is not None:
            mapping["SHEET_RESULT_FILE"] = str(exc["sheet_result_file"]).strip()

    # 4. Decisão Calibrada (Laya & Rules)
    if "decision" in updates and isinstance(updates["decision"], dict):
        dec = updates["decision"]
        if "decision_backend" in dec and dec["decision_backend"] is not None:
            mapping["DECISION_BACKEND"] = str(dec["decision_backend"]).strip()
        if "laya_url" in dec and dec["laya_url"] is not None:
            mapping["LAYA_URL"] = str(dec["laya_url"]).strip()
        if "laya_timeout_s" in dec and dec["laya_timeout_s"] is not None:
            mapping["LAYA_TIMEOUT_S"] = str(float(dec["laya_timeout_s"]))
        if "laya_cpu_timeout_factor" in dec and dec["laya_cpu_timeout_factor"] is not None:
            mapping["LAYA_CPU_TIMEOUT_FACTOR"] = str(float(dec["laya_cpu_timeout_factor"]))
        if "laya_max_failures" in dec and dec["laya_max_failures"] is not None:
            mapping["LAYA_MAX_FAILURES"] = str(int(dec["laya_max_failures"]))
        if "laya_breaker_cooldown_s" in dec and dec["laya_breaker_cooldown_s"] is not None:
            mapping["LAYA_BREAKER_COOLDOWN_S"] = str(float(dec["laya_breaker_cooldown_s"]))
        if "laya_yes_threshold" in dec and dec["laya_yes_threshold"] is not None:
            mapping["LAYA_YES_THRESHOLD"] = str(float(dec["laya_yes_threshold"]))
        if "laya_no_threshold" in dec and dec["laya_no_threshold"] is not None:
            mapping["LAYA_NO_THRESHOLD"] = str(float(dec["laya_no_threshold"]))
        if "laya_min_margin" in dec and dec["laya_min_margin"] is not None:
            mapping["LAYA_MIN_MARGIN"] = str(float(dec["laya_min_margin"]))
        if "laya_order_swap" in dec:
            mapping["LAYA_ORDER_SWAP"] = "true" if dec["laya_order_swap"] else "false"
        if "laya_state_max_chars" in dec and dec["laya_state_max_chars"] is not None:
            mapping["LAYA_STATE_MAX_CHARS"] = str(int(dec["laya_state_max_chars"]))
        if "laya_plan_above_chars" in dec and dec["laya_plan_above_chars"] is not None:
            mapping["LAYA_PLAN_ABOVE_CHARS"] = str(int(dec["laya_plan_above_chars"]))
        if "laya_dp_modes" in dec and dec["laya_dp_modes"] is not None:
            dp = dec["laya_dp_modes"]
            mapping["LAYA_DP_MODES"] = json.dumps(dp) if isinstance(dp, dict) else str(dp).strip()
        if "laya_require_eval_for_active" in dec:
            mapping["LAYA_REQUIRE_EVAL_FOR_ACTIVE"] = "true" if dec["laya_require_eval_for_active"] else "false"
        if "laya_eval_file" in dec and dec["laya_eval_file"] is not None:
            mapping["LAYA_EVAL_FILE"] = str(dec["laya_eval_file"]).strip()
        if "laya_eval_max_age_days" in dec and dec["laya_eval_max_age_days"] is not None:
            mapping["LAYA_EVAL_MAX_AGE_DAYS"] = str(int(dec["laya_eval_max_age_days"]))
        if "eval_min_examples" in dec and dec["eval_min_examples"] is not None:
            mapping["EVAL_MIN_EXAMPLES"] = str(int(dec["eval_min_examples"]))
        if "eval_margin" in dec and dec["eval_margin"] is not None:
            mapping["EVAL_MARGIN"] = str(float(dec["eval_margin"]))
        if "eval_min_coverage" in dec and dec["eval_min_coverage"] is not None:
            mapping["EVAL_MIN_COVERAGE"] = str(float(dec["eval_min_coverage"]))
        if "decision_audit_file" in dec and dec["decision_audit_file"] is not None:
            mapping["DECISION_AUDIT_FILE"] = str(dec["decision_audit_file"]).strip()

    # Atualiza ou adiciona linhas no .env preservando comentários e formatação
    updated_keys = set()
    new_lines: list[str] = []

    for line in current_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in mapping:
                new_lines.append(f"{k}={mapping[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, val in mapping.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={val}")

    ENV_FILE_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Atualiza o os.environ do processo em execução
    for k, v in mapping.items():
        os.environ[k] = v

    if mapping.get("GOOGLE_SHEET_URL"):
        try:
            from web.data_provider import refresh_sheet_cache

            refresh_sheet_cache(sheet_url=mapping["GOOGLE_SHEET_URL"], force_download=True)
        except Exception as exc:
            logger.warning("Falha ao sincronizar planilha após salvar configuração: %s", exc)

    return get_ui_config()


async def test_llm_connection(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Testa a conectividade com o modelo LLM fazendo uma chamada rápida."""
    import time

    raw = load_raw_env()
    key = api_key or raw.get("LLM_API_KEY", "")
    url = (base_url or raw.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")).rstrip("/")
    mdl = model or raw.get("LLM_MODEL", "gemini-2.5-flash")

    if not key or key == "your_api_key_here":
        return {
            "success": False,
            "error": "Chave de API não informada ou vazia. Insira sua chave para testar.",
        }

    chat_url = f"{url}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}
    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": "Responda apenas com a palavra OK."},
            {"role": "user", "content": "ping"},
        ],
        "max_tokens": 10,
        "temperature": 0,
    }

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(chat_url, headers=headers, json=payload)
            elapsed_ms = round((time.perf_counter() - t0) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                usage = data.get("usage", {})
                return {
                    "success": True,
                    "message": f"Conexão com a IA estabelecida com sucesso! ({content or 'OK'})",
                    "latency_ms": elapsed_ms,
                    "model": mdl,
                    "usage": usage,
                }
            else:
                return {
                    "success": False,
                    "status_code": resp.status_code,
                    "error": f"Erro retornado pela API ({resp.status_code}): {resp.text[:300]}",
                }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Falha ao conectar na API da IA: {type(exc).__name__}: {exc}",
        }
