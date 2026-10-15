"""Gerenciador assíncrono do Playwright com isolamento de contexto e bloqueio de rotas."""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)

from config.settings import Settings

logger = logging.getLogger(__name__)

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})


class BrowserManager:
    """Gerencia ciclo de vida do navegador, contextos descartáveis e interceptação de rede."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._camoufox_cm: Any | None = None
        self._is_camoufox: bool = False

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    @property
    def is_camoufox(self) -> bool:
        return self._is_camoufox

    async def start(self) -> BrowserManager:
        """Inicia Playwright e navegador (Camoufox anti-detect ou Chromium com fallback)."""
        if self._browser is not None and self._browser.is_connected():
            return self

        # 1. Se configurado, tenta inicializar Camoufox (anti-detect de alto nível em C++/Gecko)
        if self.settings.USE_CAMOUFOX:
            try:
                from camoufox.async_api import AsyncCamoufox

                logger.info("Inicializando motor anti-detect Camoufox...")
                self._camoufox_cm = AsyncCamoufox(
                    headless=self.settings.HEADLESS,
                    humanize=True,
                )
                self._browser = cast(Browser, await self._camoufox_cm.__aenter__())
                self._is_camoufox = True
                logger.info("Navegador Camoufox (anti-detect) iniciado com sucesso.")
                return self
            except Exception as exc:
                logger.warning(
                    "Camoufox não pôde ser iniciado (%s). Realizando fallback para Chromium...",
                    exc,
                )
                self._camoufox_cm = None
                self._is_camoufox = False

        # 2. Inicialização padrão do Playwright Chromium (com fallback de canais)
        self._playwright = await async_playwright().start()

        channels_to_try: list[str | None] = [None, "chrome", "msedge"]
        last_error: Exception | None = None

        launch_args = [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-extensions",
            "--disable-background-networking",
        ]

        for channel in channels_to_try:
            try:
                if channel is None:
                    self._browser = await self._playwright.chromium.launch(
                        headless=self.settings.HEADLESS,
                        args=launch_args,
                    )
                else:
                    self._browser = await self._playwright.chromium.launch(
                        channel=channel,
                        headless=self.settings.HEADLESS,
                        args=launch_args,
                    )
                self._is_camoufox = False
                logger.info("Navegador Chromium iniciado com sucesso (canal=%s)", channel)
                return self
            except Exception as exc:
                last_error = exc
                logger.debug("Falha ao iniciar navegador com canal %s: %s", channel, exc)

        await self.stop()
        raise RuntimeError(
            f"Não foi possível iniciar nenhum navegador Chromium/Chrome/Edge: {last_error}"
        ) from last_error

    async def stop(self) -> None:
        """Fecha navegador e desliga processo do Playwright/Camoufox."""
        if self._camoufox_cm is not None:
            try:
                await self._camoufox_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("Erro ao fechar Camoufox: %s", exc)
            finally:
                self._camoufox_cm = None
                self._browser = None
                self._is_camoufox = False

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.debug("Erro ao fechar navegador: %s", exc)
            finally:
                self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.debug("Erro ao desligar Playwright: %s", exc)
            finally:
                self._playwright = None

    async def __aenter__(self) -> BrowserManager:
        return await self.start()

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.stop()

    def _should_block_url(self, url: str) -> bool:
        """Verifica se URL corresponde a algum padrão bloqueado."""
        url_lower = url.lower()
        for pattern in self.settings.BLOCKED_URL_PATTERNS:
            pattern_lower = pattern.lower()
            if fnmatch.fnmatch(url_lower, pattern_lower):
                return True
            clean_part = pattern_lower.replace("*", "").strip()
            if clean_part and clean_part in url_lower:
                return True
        return False

    async def _setup_routing(self, page: Page) -> None:
        """Intercepta e bloqueia requisições a imagens, fontes, mídias e analytics."""

        async def route_handler(route: Route) -> None:
            request = route.request
            resource_type = request.resource_type
            url = request.url

            # No Chromium padrão bloqueamos imagens/fontes para otimizar desempenho.
            # No Camoufox, permitimos para não falhar testes de Canvas do Cloudflare Turnstile.
            if not self._is_camoufox and resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort("blockedbyclient")
                return

            if self._should_block_url(url):
                await route.abort("blockedbyclient")
                return

            await route.continue_()

        await page.route("**/*", route_handler)

    @asynccontextmanager
    async def new_page(self) -> AsyncIterator[Page]:
        """Cria um contexto limpo e página descartável com timeouts e bloqueios configurados."""
        if not self.is_running or self._browser is None:
            raise RuntimeError("BrowserManager não foi iniciado ou navegador desconectou.")

        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 800},
            "accept_downloads": False,
        }
        if not self._is_camoufox:
            context_kwargs["user_agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )

        if self.settings.STORAGE_STATE_PATH and self.settings.STORAGE_STATE_PATH.exists():
            context_kwargs["storage_state"] = str(self.settings.STORAGE_STATE_PATH.resolve())

        context: BrowserContext = await self._browser.new_context(**context_kwargs)
        page: Page = await context.new_page()
        page.set_default_timeout(self.settings.ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(self.settings.NAV_TIMEOUT_MS)

        await self._setup_routing(page)

        try:
            yield page
        finally:
            try:
                await page.close()
            except Exception as exc:
                logger.debug("Erro ao fechar página: %s", exc)
            try:
                await context.close()
            except Exception as exc:
                logger.debug("Erro ao fechar contexto: %s", exc)

    async def save_screenshot(self, page: Page, name_prefix: str) -> Path:
        """Salva screenshot da página no diretório de artefatos."""
        out_dir = self.settings.SCREENSHOT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{name_prefix}_{uuid4().hex[:8]}.png"
        target_path = out_dir / filename
        await page.screenshot(path=str(target_path), full_page=True)
        return target_path
