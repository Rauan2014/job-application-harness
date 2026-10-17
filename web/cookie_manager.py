"""Gerenciador de cookies e sessões de navegador (storage_state.json)."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from config.settings import load_settings

logger = logging.getLogger(__name__)


def get_storage_state_path() -> Path:
    """Retorna o caminho canônico do arquivo de storage_state."""
    try:
        settings = load_settings()
        return settings.STORAGE_STATE_PATH
    except Exception:
        return Path("./cache/storage_state.json")


def get_cookies_info() -> dict[str, Any]:
    """Lê e resume os cookies e contas autenticadas no storage_state."""
    path = get_storage_state_path()
    if not path.exists():
        return {
            "has_session": False,
            "path": str(path.resolve()),
            "last_modified": None,
            "cookie_count": 0,
            "domains": [],
            "origins_count": 0,
            "cookies": [],
        }

    try:
        stat = path.stat()
        last_mod = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M:%S")
        content = json.loads(path.read_text(encoding="utf-8"))

        raw_cookies = content.get("cookies", [])
        origins = content.get("origins", [])

        domains_set: set[str] = set()
        formatted_cookies: list[dict[str, Any]] = []

        for c in raw_cookies:
            domain = c.get("domain", "")
            if domain:
                domains_set.add(domain.lstrip("."))
            formatted_cookies.append(
                {
                    "name": c.get("name", ""),
                    "domain": domain,
                    "path": c.get("path", "/"),
                    "expires": c.get("expires", -1),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                    "sameSite": c.get("sameSite", "Lax"),
                }
            )

        # Agrupamento amigável de domínios reconhecidos
        known_portals = []
        for d in sorted(domains_set):
            if "indeed" in d:
                known_portals.append("Indeed")
            elif "gupy" in d:
                known_portals.append("Gupy")
            elif "greenhouse" in d:
                known_portals.append("Greenhouse")
            elif "lever" in d:
                known_portals.append("Lever")
            elif "google" in d:
                known_portals.append("Google")
            elif "workday" in d:
                known_portals.append("Workday")

        known_portals = sorted(set(known_portals))

        return {
            "has_session": len(raw_cookies) > 0,
            "path": str(path.resolve()),
            "last_modified": last_mod,
            "cookie_count": len(raw_cookies),
            "domains": sorted(domains_set),
            "known_portals": known_portals,
            "origins_count": len(origins),
            "cookies": formatted_cookies[:100],  # Amostra para exibição
        }
    except Exception as exc:
        logger.warning("Falha ao ler storage_state.json: %s", exc)
        return {
            "has_session": False,
            "path": str(path.resolve()),
            "error": f"Erro ao ler arquivo: {exc}",
            "cookie_count": 0,
            "domains": [],
            "origins_count": 0,
            "cookies": [],
        }


def save_cookies(raw_data: str | list[dict] | dict) -> dict[str, Any]:
    """Importa cookies colados pelo usuário em JSON (formato Playwright ou EditThisCookie)."""
    path = get_storage_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(raw_data, str):
        raw_data = json.loads(raw_data.strip())

    storage_state: dict[str, Any] = {"cookies": [], "origins": []}

    # Caso 1: JSON completo do Playwright {"cookies": [...], "origins": [...]}
    if isinstance(raw_data, dict) and "cookies" in raw_data:
        storage_state["cookies"] = raw_data.get("cookies", [])
        storage_state["origins"] = raw_data.get("origins", [])

    # Caso 2: Lista direta de cookies (ex: EditThisCookie ou exported cookies)
    elif isinstance(raw_data, list):
        converted_cookies = []
        for item in raw_data:
            if not isinstance(item, dict) or "name" not in item:
                continue
            converted_cookies.append(
                {
                    "name": item.get("name"),
                    "value": str(item.get("value", "")),
                    "domain": item.get("domain", ""),
                    "path": item.get("path", "/"),
                    "expires": float(item.get("expirationDate", item.get("expires", -1))),
                    "httpOnly": bool(item.get("httpOnly", False)),
                    "secure": bool(item.get("secure", False)),
                    "sameSite": item.get("sameSite", "Lax"),
                }
            )
        storage_state["cookies"] = converted_cookies

    path.write_text(json.dumps(storage_state, indent=2, ensure_ascii=False), encoding="utf-8")
    import os

    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    return get_cookies_info()


def clear_cookies() -> dict[str, Any]:
    """Apaga a sessão salva de cookies."""
    path = get_storage_state_path()
    if path.exists():
        path.unlink(missing_ok=True)
    return get_cookies_info()


async def launch_visual_login(url: str = "https://br.indeed.com", use_camoufox: bool = True) -> dict[str, Any]:
    """Abre uma janela de navegador visível para o usuário se autenticar."""
    script_path = Path("scripts/login_browser.py").resolve()
    python_exe = sys.executable

    cmd = [python_exe, str(script_path), "--url", url]
    if not use_camoufox:
        cmd.append("--chromium")

    # Inicia como subprocesso não bloqueante
    try:
        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
        return {
            "success": True,
            "message": "Janela do navegador aberta para login! Acesse sua conta e pressione ENTER no terminal auxiliar.",
            "pid": proc.pid,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Não foi possível abrir o navegador: {exc}",
        }
