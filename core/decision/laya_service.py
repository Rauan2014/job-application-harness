"""Gerenciamento de ciclo de vida e inicialização automática do sidecar Laya."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


async def is_laya_healthy(base_url: str, timeout_s: float = 2.0) -> bool:
    """Verifica se o servidor Laya está respondendo em /health."""
    url = f"{base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return bool(data.get("ok", True))
    except Exception:
        return False
    return False


def spawn_laya_process(port: int = 8787, host: str = "127.0.0.1") -> subprocess.Popen[bytes] | None:
    """Dispara o processo laya-mcp serve em segundo plano."""
    venv_bin = Path(sys.executable).parent
    laya_exe = venv_bin / ("laya-mcp.exe" if sys.platform == "win32" else "laya-mcp")

    if laya_exe.exists():
        cmd = [str(laya_exe), "serve", "--host", host, "--port", str(port), "--model", "multilingual"]
    elif shutil.which("laya-mcp"):
        cmd = ["laya-mcp", "serve", "--host", host, "--port", str(port), "--model", "multilingual"]
    else:
        cmd = [sys.executable, "-m", "laya_mcp.server", "--host", host, "--port", str(port), "--model", "multilingual"]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        log.info("Iniciando processo laya-mcp serve na porta %d...", port)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return proc
    except Exception as exc:
        log.debug("Não foi possível iniciar laya-mcp automaticamente: %s", exc)
        return None


async def ensure_laya_running(
    base_url: str = "http://127.0.0.1:8787",
    auto_start: bool = True,
    wait_timeout_s: float = 30.0,
) -> tuple[bool, subprocess.Popen[bytes] | None]:
    """Garante que o sidecar Laya esteja acessível.
    Se não estiver ativo e auto_start=True, tenta iniciá-lo automaticamente.
    """
    if await is_laya_healthy(base_url, timeout_s=1.5):
        log.info("Sidecar Laya já está ativo e respondendo em %s.", base_url)
        return True, None

    if not auto_start:
        return False, None

    # Tenta extrair a porta da URL
    port = 8787
    if ":" in base_url.split("//")[-1]:
        try:
            port = int(base_url.split("//")[-1].split(":")[-1].split("/")[0])
        except ValueError:
            port = 8787

    proc = spawn_laya_process(port=port)
    if proc is None:
        return False, None

    deadline = time.monotonic() + wait_timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.warning("Processo laya-mcp encerrou prematuramente com código %s.", proc.returncode)
            return False, None
        if await is_laya_healthy(base_url, timeout_s=1.0):
            log.info("Sidecar Laya inicializado e saudável com sucesso em %s.", base_url)
            return True, proc
        await asyncio.sleep(0.5)

    log.warning("Tempo limite (%0.1fs) esgotado aguardando inicialização do Laya.", wait_timeout_s)
    import contextlib

    with contextlib.suppress(Exception):
        proc.terminate()
    return False, None
