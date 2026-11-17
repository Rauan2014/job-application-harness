"""Testes de integração para BrowserManager: isolamento, bloqueio de rotas e screenshots."""

from __future__ import annotations

import pytest

from automation.browser import BrowserManager
from tests.integration.conftest import MockServerInfo

pytestmark = pytest.mark.integration


async def test_browser_page_lifecycle_and_screenshot(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    """Verifica que nova página abre, navega e salva screenshot com sucesso."""
    async with browser_manager.new_page() as page:
        response = await page.goto(mock_server.lever_url, wait_until="domcontentloaded")
        assert response is not None
        assert response.status == 200

        title = await page.title()
        assert "Lever" in title

        screenshot_path = await browser_manager.save_screenshot(page, "test_lever_screen")
        assert screenshot_path.exists()
        assert screenshot_path.stat().st_size > 0
        screenshot_path.unlink(missing_ok=True)


async def test_browser_blocks_analytics_and_images(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    """Verifica se URLs de rastreamento e analytics foram bloqueadas pelo roteamento."""
    aborted_requests: list[str] = []

    async with browser_manager.new_page() as page:
        page.on("requestfailed", lambda req: aborted_requests.append(req.url))

        await page.goto(mock_server.lever_url, wait_until="domcontentloaded")
        # Espera breve para disparos assíncronos do DOM
        await page.wait_for_timeout(300)

        has_blocked_analytics = any(
            "google-analytics.com" in url or "googletagmanager.com" in url
            for url in aborted_requests
        )
        assert has_blocked_analytics, f"Nenhuma requisição de analytics foi abortada. Aborted: {aborted_requests}"


async def test_browser_context_isolation(
    browser_manager: BrowserManager,
    mock_server: MockServerInfo,
) -> None:
    """Garante que contextos sucessivos são completamente isolados (sem compartilhamento de storage)."""
    async with browser_manager.new_page() as page1:
        await page1.goto(mock_server.lever_url)
        await page1.evaluate("localStorage.setItem('auth_token', 'secret123')")
        val1 = await page1.evaluate("localStorage.getItem('auth_token')")
        assert val1 == "secret123"

    async with browser_manager.new_page() as page2:
        await page2.goto(mock_server.lever_url)
        val2 = await page2.evaluate("localStorage.getItem('auth_token')")
        assert val2 is None
