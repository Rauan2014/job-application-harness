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
    """Retorna configuração formatada para consumo seguro pela UI."""
    raw = load_raw_env()
    try:
        settings = load_settings()
        api_key_secret = settings.LLM_API_KEY.get_secret_value()
    except Exception:
        settings = None
        api_key_secret = raw.get("LLM_API_KEY", "")

    return {
        "llm": {
            "api_key": api_key_secret,
            "api_key_masked": mask_secret(api_key_secret),
            "is_key_set": bool(api_key_secret and api_key_secret != "your_api_key_here"),
            "base_url": raw.get("LLM_BASE_URL", getattr(settings, "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")),
            "model": raw.get("LLM_MODEL", getattr(settings, "LLM_MODEL", "gemini-2.5-flash")),
            "timeout_s": int(raw.get("LLM_TIMEOUT_S", getattr(settings, "LLM_TIMEOUT_S", 60))),
            "no_llm": raw.get("NO_LLM", "false").lower() in ("true", "1", "yes"),
        },
        "sheet": {
            "google_sheet_url": raw.get("GOOGLE_SHEET_URL", getattr(settings, "GOOGLE_SHEET_URL", "") or ""),
            "include_blank_status": raw.get("INCLUDE_BLANK_STATUS", "true").lower() in ("true", "1", "yes"),
            "drive_folder_id": raw.get("DRIVE_FOLDER_ID", getattr(settings, "DRIVE_FOLDER_ID", "") or ""),
            "cache_dir": raw.get("CACHE_DIR", str(getattr(settings, "CACHE_DIR", "./cache/resumes"))),
        },
        "execution": {
            "dry_run": raw.get("DRY_RUN", "true").lower() in ("true", "1", "yes"),
            "autonomous_mode": raw.get("AUTONOMOUS_MODE", "true").lower() in ("true", "1", "yes"),
            "max_concurrency": int(raw.get("MAX_CONCURRENCY", getattr(settings, "MAX_CONCURRENCY", 3))),
            "use_camoufox": raw.get("USE_CAMOUFOX", "true").lower() in ("true", "1", "yes"),
            "headless": raw.get("HEADLESS", "true").lower() in ("true", "1", "yes"),
            "min_fit_score": int(raw.get("MIN_FIT_SCORE", getattr(settings, "MIN_FIT_SCORE", 70))),
        },
    }


def save_ui_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Salva alterações nos valores do .env preservando estrutura existente."""
    current_lines: list[str] = []
    if ENV_FILE_PATH.exists():
        current_lines = ENV_FILE_PATH.read_text(encoding="utf-8").splitlines()

    mapping: dict[str, str] = {}
    
    # Flatten updates
    if "llm" in updates:
        llm = updates["llm"]
        if "api_key" in llm and llm["api_key"] and "•••" not in llm["api_key"]:
            mapping["LLM_API_KEY"] = str(llm["api_key"]).strip()
        if "base_url" in llm:
            mapping["LLM_BASE_URL"] = str(llm["base_url"]).strip()
        if "model" in llm:
            mapping["LLM_MODEL"] = str(llm["model"]).strip()
        if "no_llm" in llm:
            mapping["NO_LLM"] = "true" if llm["no_llm"] else "false"

    if "sheet" in updates:
        sh = updates["sheet"]
        if "google_sheet_url" in sh:
            mapping["GOOGLE_SHEET_URL"] = str(sh["google_sheet_url"]).strip()
        if "include_blank_status" in sh:
            mapping["INCLUDE_BLANK_STATUS"] = "true" if sh["include_blank_status"] else "false"
        if "drive_folder_id" in sh:
            mapping["DRIVE_FOLDER_ID"] = str(sh["drive_folder_id"]).strip()
        if "cache_dir" in sh:
            mapping["CACHE_DIR"] = str(sh["cache_dir"]).strip()

    if "execution" in updates:
        exc = updates["execution"]
        if "dry_run" in exc:
            mapping["DRY_RUN"] = "true" if exc["dry_run"] else "false"
        if "autonomous_mode" in exc:
            mapping["AUTONOMOUS_MODE"] = "true" if exc["autonomous_mode"] else "false"
        if "max_concurrency" in exc:
            mapping["MAX_CONCURRENCY"] = str(int(exc["max_concurrency"]))
        if "use_camoufox" in exc:
            mapping["USE_CAMOUFOX"] = "true" if exc["use_camoufox"] else "false"
        if "headless" in exc:
            mapping["HEADLESS"] = "true" if exc["headless"] else "false"
        if "min_fit_score" in exc:
            mapping["MIN_FIT_SCORE"] = str(int(exc["min_fit_score"]))

    # Update or add lines in .env
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
    
    # Reload OS env for current process
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
