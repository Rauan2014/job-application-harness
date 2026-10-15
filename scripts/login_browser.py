"""Script interativo para realizar login uma única vez no navegador e salvar a sessão (cookies e tokens).

Uso:
    python scripts/login_browser.py
    python scripts/login_browser.py --url https://br.indeed.com
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("login_browser")


async def perform_login(url: str, use_camoufox: bool) -> None:
    settings = load_settings()
    out_file = settings.STORAGE_STATE_PATH
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("                ASSISTENTE DE LOGIN DO HARNESS")
    print("=" * 70)
    print(f" Destino inicial:  {url}")
    print(f" Arquivo de sessão: {out_file.resolve()}")
    print(f" Motor navegador:   {'Camoufox (anti-detect)' if use_camoufox else 'Chromium'}")
    print("-" * 70)
    print(" O navegador abrirá visivelmente na sua tela agora.")
    print(" 1. Faça login normalmente na sua conta (Indeed, Google, etc.).")
    print(" 2. Quando o login for concluído, volte a este terminal.")
    print(" 3. Pressione ENTER para salvar os cookies e a sessão permanente.")
    print("=" * 70 + "\n")

    if use_camoufox:
        from camoufox.async_api import AsyncCamoufox

        async with AsyncCamoufox(headless=False) as browser:
            # Se já existir uma sessão salva prévia, carrega para manter outros cookies
            kwargs = {}
            if out_file.exists():
                kwargs["storage_state"] = str(out_file.resolve())
            context = await browser.new_context(**kwargs)
            page = await context.new_page()
            await page.goto(url)

            # Espera interação do usuário no terminal
            await asyncio.to_thread(input, "\n>> Pressione [ENTER] APÓS CONCLUIR O LOGIN no navegador...")

            # Salva o storage_state atualizado (cookies, localStorage, sessão)
            await context.storage_state(path=str(out_file.resolve()))
            print(f"\n[OK] Sessão e cookies salvos com sucesso em: {out_file.resolve()}")
            print("Agora o 'ia_candidata' estará logado automaticamente em todas as execuções!\n")
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

            await asyncio.to_thread(input, "\n>> Pressione [ENTER] APÓS CONCLUIR O LOGIN no navegador...")

            await context.storage_state(path=str(out_file.resolve()))
            print(f"\n[OK] Sessão e cookies salvos com sucesso em: {out_file.resolve()}")
            print("Agora o 'ia_candidata' estará logado automaticamente em todas as execuções!\n")
            await context.close()
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Abre navegador visual para autenticação manual e salva cookies.")
    parser.add_argument(
        "--url",
        type=str,
        default="https://br.indeed.com",
        help="URL inicial para fazer login (padrão: https://br.indeed.com)",
    )
    parser.add_argument(
        "--chromium",
        action="store_true",
        default=False,
        help="Usa Chromium em vez do Camoufox.",
    )
    args = parser.parse_args()
    asyncio.run(perform_login(url=args.url, use_camoufox=not args.chromium))


if __name__ == "__main__":
    main()
