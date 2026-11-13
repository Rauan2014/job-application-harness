"""Gerenciador de cookies e sessões de navegador (storage_state.json)."""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import load_settings

logger = logging.getLogger(__name__)

PORTAL_CONFIGS: dict[str, dict[str, Any]] = {
    "linkedin": {
        "label": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "domain_match": "linkedin.com",
        "session_cookies": ("li_at",),
    },
    "gupy": {
        "label": "Gupy",
        "login_url": "https://portal.gupy.io/",
        "domain_match": "gupy.io",
        "session_cookies": ("_gupy_session", "gupy_session", "access_token", "id_token"),
    },
    "indeed": {
        "label": "Indeed",
        "login_url": "https://br.indeed.com",
        "domain_match": "indeed.com",
        "session_cookies": ("SOCK", "PPID", "CTK"),
    },
}


def get_storage_state_path() -> Path:
    """Retorna o caminho canônico do arquivo de storage_state."""
    try:
        settings = load_settings()
        return settings.STORAGE_STATE_PATH
    except Exception:
        return Path("./cache/storage_state.json")


def _is_cookie_unexpired(cookie: dict[str, Any], now_ts: float) -> bool:
    expires = cookie.get("expires", -1)
    if expires in (None, -1, 0):
        return True
    try:
        return float(expires) > now_ts
    except (TypeError, ValueError):
        return True


def compute_portal_statuses(
    raw_cookies: list[dict[str, Any]],
    origins: list[dict[str, Any]],
    now_ts: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Calcula status de conexão para LinkedIn, Gupy e Indeed a partir do storage_state."""
    ts = now_ts if now_ts is not None else time.time()
    statuses: dict[str, dict[str, Any]] = {}

    for portal_key, cfg in PORTAL_CONFIGS.items():
        domain_match = cfg["domain_match"]
        session_cookie_names = set(cfg["session_cookies"])

        portal_cookies = [
            c
            for c in raw_cookies
            if domain_match in str(c.get("domain", "")).lower() and _is_cookie_unexpired(c, ts)
        ]
        portal_origins = [
            o
            for o in origins
            if domain_match in str(o.get("origin", "")).lower() and bool(o.get("localStorage"))
        ]

        has_named_session = any(str(c.get("name", "")) in session_cookie_names for c in portal_cookies)

        if portal_key == "linkedin":
            connected = has_named_session
        elif portal_key == "gupy":
            has_auth_storage = any(
                any(
                    tok_kw in str(item.get("name", "")).lower()
                    for tok_kw in ("token", "auth", "user", "candidate", "session", "keycloak", "oidc")
                )
                for org in portal_origins
                for item in (org.get("localStorage") or [])
            )
            connected = has_named_session or has_auth_storage or len(portal_cookies) >= 2
        else:
            connected = has_named_session or len(portal_cookies) >= 2

        expires_ts_list = [
            float(c["expires"])
            for c in portal_cookies
            if c.get("expires") not in (None, -1, 0) and str(c.get("name", "")) in session_cookie_names
        ]
        expires_hint: str | None = None
        if expires_ts_list:
            expires_hint = datetime.fromtimestamp(max(expires_ts_list)).strftime("%d/%m/%Y")

        statuses[portal_key] = {
            "portal": portal_key,
            "label": cfg["label"],
            "connected": connected,
            "cookie_count": len(portal_cookies),
            "origins_count": len(portal_origins),
            "expires_hint": expires_hint,
        }

    return statuses


def merge_storage_states(
    existing_state: dict[str, Any] | None,
    new_state: dict[str, Any],
) -> dict[str, Any]:
    """Mescla cookies e origins preservando sessões prévias de outros portais."""
    base_cookies = list((existing_state or {}).get("cookies") or [])
    base_origins = list((existing_state or {}).get("origins") or [])

    cookie_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for c in base_cookies:
        if isinstance(c, dict) and c.get("name"):
            key = (str(c.get("name", "")), str(c.get("domain", "")), str(c.get("path", "/")))
            cookie_map[key] = c
    for c in new_state.get("cookies") or []:
        if isinstance(c, dict) and c.get("name"):
            key = (str(c.get("name", "")), str(c.get("domain", "")), str(c.get("path", "/")))
            cookie_map[key] = c

    origin_map: dict[str, dict[str, Any]] = {}
    for o in base_origins:
        if isinstance(o, dict) and o.get("origin"):
            origin_map[str(o["origin"])] = o
    for o in new_state.get("origins") or []:
        if isinstance(o, dict) and o.get("origin"):
            origin_map[str(o["origin"])] = o

    return {
        "cookies": list(cookie_map.values()),
        "origins": list(origin_map.values()),
    }


def get_cookies_info() -> dict[str, Any]:
    """Lê e resume os cookies e contas autenticadas no storage_state."""
    path = get_storage_state_path()
    empty_portals = compute_portal_statuses([], [])
    if not path.exists():
        return {
            "has_session": False,
            "path": str(path.resolve()),
            "last_modified": None,
            "cookie_count": 0,
            "domains": [],
            "known_portals": [],
            "portals": empty_portals,
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
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "secure": bool(c.get("secure", False)),
                    "sameSite": c.get("sameSite", "Lax"),
                }
            )

        known_portals: list[str] = []
        for d in sorted(domains_set):
            dl = d.lower()
            if "linkedin" in dl:
                known_portals.append("LinkedIn")
            elif "indeed" in dl:
                known_portals.append("Indeed")
            elif "gupy" in dl:
                known_portals.append("Gupy")
            elif "greenhouse" in dl:
                known_portals.append("Greenhouse")
            elif "lever" in dl:
                known_portals.append("Lever")
            elif "google" in dl:
                known_portals.append("Google")
            elif "workday" in dl:
                known_portals.append("Workday")

        known_portals = sorted(set(known_portals))
        portals = compute_portal_statuses(raw_cookies, origins)

        return {
            "has_session": len(raw_cookies) > 0 or len(origins) > 0,
            "path": str(path.resolve()),
            "last_modified": last_mod,
            "cookie_count": len(raw_cookies),
            "domains": sorted(domains_set),
            "known_portals": known_portals,
            "portals": portals,
            "origins_count": len(origins),
            "cookies": formatted_cookies[:100],
        }
    except Exception as exc:
        logger.warning("Falha ao ler storage_state.json: %s", exc)
        return {
            "has_session": False,
            "path": str(path.resolve()),
            "error": f"Erro ao ler arquivo: {exc}",
            "cookie_count": 0,
            "domains": [],
            "known_portals": [],
            "portals": empty_portals,
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

    if isinstance(raw_data, dict) and "cookies" in raw_data:
        storage_state["cookies"] = raw_data.get("cookies", [])
        storage_state["origins"] = raw_data.get("origins", [])
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

    existing: dict[str, Any] | None = None
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = None

    merged = merge_storage_states(existing, storage_state)
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    import os

    if os.name == "posix":
        with contextlib.suppress(Exception):
            os.chmod(path, 0o600)
    return get_cookies_info()


def clear_cookies() -> dict[str, Any]:
    """Apaga a sessão salva de cookies."""
    path = get_storage_state_path()
    if path.exists():
        path.unlink(missing_ok=True)
    return get_cookies_info()


def _spawn_login_subprocess(
    target_url: str,
    portal_key: str,
    auto_save: bool,
    use_camoufox: bool,
) -> subprocess.Popen[bytes]:
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "login_browser.py"
    python_exe = sys.executable

    cmd = [python_exe, str(script_path), "--url", target_url]
    if portal_key:
        cmd.extend(["--portal", portal_key])
    if auto_save:
        cmd.append("--auto-save")
    if not use_camoufox:
        cmd.append("--chromium")

    return subprocess.Popen(
        cmd,
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
    )


async def launch_visual_login(
    url: str | None = None,
    use_camoufox: bool = True,
    portal: str | None = None,
    auto_save: bool = True,
) -> dict[str, Any]:
    """Abre uma janela de navegador visível para o usuário se autenticar."""
    portal_key = (portal or "").strip().lower()
    if portal_key in PORTAL_CONFIGS and not url:
        url = PORTAL_CONFIGS[portal_key]["login_url"]
    target_url = url or "https://br.indeed.com"

    try:
        proc = _spawn_login_subprocess(target_url, portal_key, auto_save, use_camoufox)
        return {
            "success": True,
            "portal": portal_key or "custom",
            "url": target_url,
            "auto_save": auto_save,
            "message": (
                "Janela do navegador aberta! Faça login normalmente; a sessão será detectada e salva automaticamente."
                if auto_save
                else "Janela do navegador aberta para login! Acesse sua conta e pressione ENTER no terminal auxiliar."
            ),
            "pid": proc.pid,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Não foi possível abrir o navegador: {exc}",
        }
