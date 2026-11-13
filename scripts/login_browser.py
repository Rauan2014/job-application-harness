"""Script interativo para realizar login uma única vez no navegador e salvar a sessão (cookies e tokens).

Uso:
    python scripts/login_browser.py
    python scripts/login_browser.py --portal linkedin --auto-save
    python scripts/login_browser.py --portal gupy --auto-save
    python scripts/login_browser.py --url https://br.indeed.com
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings
from web.cookie_manager import PORTAL_CONFIGS, merge_storage_states

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("login_browser")


def _infer_portal(url: str, explicit_portal: str | None) -> str:
    if explicit_portal and explicit_portal.lower() in PORTAL_CONFIGS:
        return explicit_portal.lower()
    lower = (url or "").lower()
    if "linkedin.com" in lower:
        return "linkedin"
    if "gupy.io" in lower:
        return "gupy"
    if "indeed.com" in lower:
        return "indeed"
    return "custom"


def _is_portal_authenticated(
    portal: str,
    current_url: str,
    state: dict[str, Any],
) -> bool:
    """Detecta de forma conservadora se o login do portal foi concluído."""
    cookies = state.get("cookies") or []
    origins = state.get("origins") or []
    lower_url = (current_url or "").lower()

    if portal == "linkedin":
        has_li_at = any(
            str(c.get("name")) == "li_at" and bool(c.get("value"))
            for c in cookies
            if "linkedin.com" in str(c.get("domain", "")).lower()
        )
        not_on_login_form = all(
            seg not in lower_url for seg in ("/login", "/uas/login", "/checkpoint")
        )
        return has_li_at and not_on_login_form

    if portal == "gupy":
        if "gupy.io" not in lower_url:
            return False
        on_auth_screen = any(seg in lower_url for seg in ("/login", "auth.gupy.io", "login.gupy.io", "/signin"))
        if on_auth_screen:
            return False
        gupy_origins = [
            o for o in origins if "gupy.io" in str(o.get("origin", "")).lower() and o.get("localStorage")
        ]
        has_auth_storage = any(
            any(
                kw in str(item.get("name", "")).lower()
                for kw in ("token", "auth", "user", "candidate", "session", "keycloak", "oidc")
            )
            for org in gupy_origins
            for item in (org.get("localStorage") or [])
        )
        gupy_cookies = [c for c in cookies if "gupy.io" in str(c.get("domain", "")).lower()]
        return has_auth_storage or (
            len(gupy_cookies) >= 2 and ("/candidates" in lower_url or "/vagas" in lower_url)
        )

    if portal == "indeed":
        return any(
            str(c.get("name")) in ("SOCK", "PPID") and bool(c.get("value"))
            for c in cookies
            if "indeed.com" in str(c.get("domain", "")).lower()
        )

    return False


def _save_merged_state(out_file: Path, captured_state: dict[str, Any]) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] | None = None
    if out_file.exists():
        try:
            existing = json.loads(out_file.read_text(encoding="utf-8"))
        except Exception:
            existing = None
    merged = merge_storage_states(existing, captured_state)
    out_file.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")


async def _wait_and_capture(
    context: Any,
    page: Any,
    portal: str,
    out_file: Path,
    auto_save: bool,
) -> None:
    if not auto_save:
        await asyncio.to_thread(input, "\n>> Pressione [ENTER] APÓS CONCLUIR O LOGIN no navegador...")
        captured = await context.storage_state()
        _save_merged_state(out_file, captured)
        return

    print(f" Aguardando conclusão do login ({portal}) automaticamente (ou fechamento da janela)...")
    last_state: dict[str, Any] | None = None
    max_wait_s = 300
    elapsed = 0.0

    while elapsed < max_wait_s:
        if page.is_closed():
            break
        try:
            current_state = await context.storage_state()
            last_state = current_state
            current_url = page.url or ""
            if _is_portal_authenticated(portal, current_url, current_state):
                # Pequeno intervalo para garantir persistência de cookies pós-redirect
                await asyncio.sleep(2.0)
                if not page.is_closed():
                    last_state = await context.storage_state()
                _save_merged_state(out_file, last_state)
                print(f"\n[OK] Autenticação em '{portal}' detectada e salva automaticamente!")
                return
        except Exception:
            break
        await asyncio.sleep(1.0)
        elapsed += 1.0

    if last_state and (last_state.get("cookies") or last_state.get("origins")):
        _save_merged_state(out_file, last_state)
        print(f"\n[OK] Estado de sessão capturado salvo em: {out_file}")


async def perform_login(
    url: str,
    use_camoufox: bool,
    portal: str | None = None,
    auto_save: bool = False,
) -> None:
    settings = load_settings()
    out_file = settings.STORAGE_STATE_PATH
    out_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_portal = _infer_portal(url, portal)

    print("\n" + "=" * 70)
    print("                ASSISTENTE DE LOGIN DO HARNESS")
    print("=" * 70)
    print(f" Portal alvo:       {resolved_portal}")
    print(f" Destino inicial:   {url}")
    print(f" Arquivo de sessão: {out_file.resolve()}")
    print(f" Motor navegador:   {'Camoufox (anti-detect)' if use_camoufox else 'Chromium'}")
    print("-" * 70)
    print(" O navegador abrirá visivelmente na sua tela agora.")
    print(" 1. Faça login normalmente na sua conta.")
    if auto_save:
        print(" 2. Assim que o login concluir (ou você fechar a janela), a sessão será salva.")
    else:
        print(" 2. Quando o login for concluído, volte a este terminal.")
        print(" 3. Pressione ENTER para salvar os cookies e a sessão permanente.")
    print("=" * 70 + "\n")

    if use_camoufox:
        from camoufox.async_api import AsyncCamoufox

        async with AsyncCamoufox(headless=False) as browser:
            browser_or_ctx: Any = browser
            kwargs: dict[str, Any] = {}
            if out_file.exists():
                kwargs["storage_state"] = str(out_file.resolve())
            context = (
                await browser_or_ctx.new_context(**kwargs)
                if hasattr(browser_or_ctx, "new_context")
                else browser_or_ctx
            )
            page = await context.new_page()
            await page.goto(url)
            await _wait_and_capture(context, page, resolved_portal, out_file, auto_save)
            print(f"\n[OK] Sessão salva em: {out_file}\n")
            if hasattr(browser_or_ctx, "new_context"):
                with contextlib.suppress(Exception):
                    await context.close()
    else:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            kwargs = {}
            if out_file.exists():
                kwargs["storage_state"] = str(out_file.resolve())
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await page.goto(url)
            await _wait_and_capture(context, page, resolved_portal, out_file, auto_save)
            print(f"\n[OK] Sessão salva em: {out_file}\n")
            with contextlib.suppress(Exception):
                await context.close()
            with contextlib.suppress(Exception):
                await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Abre navegador visual para autenticação manual e salva cookies.")
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="URL inicial para fazer login.",
    )
    parser.add_argument(
        "--portal",
        type=str,
        default=None,
        help="Portal alvo (linkedin, gupy, indeed, custom).",
    )
    parser.add_argument(
        "--auto-save",
        action="store_true",
        default=False,
        help="Detecta conclusão do login automaticamente sem exigir ENTER no terminal.",
    )
    parser.add_argument(
        "--chromium",
        action="store_true",
        default=False,
        help="Usa Chromium em vez do Camoufox.",
    )
    args = parser.parse_args()
    portal_key = (args.portal or "").strip().lower()
    target_url = args.url or PORTAL_CONFIGS.get(portal_key, {}).get("login_url") or "https://br.indeed.com"
    asyncio.run(
        perform_login(
            url=target_url,
            use_camoufox=not args.chromium,
            portal=args.portal,
            auto_save=args.auto_save,
        )
    )


if __name__ == "__main__":
    main()
