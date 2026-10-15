import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings

async def test():
    settings = load_settings()
    async with BrowserManager(settings) as bm:
        url = "https://kognit.vagas.solides.com.br/vaga/929436"
        async with bm.new_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            btn = page.locator("button:has-text('Quero me candidatar')").first
            if await btn.count() > 0:
                print("Clicking Quero me candidatar on Solides...")
                await btn.click()
                await page.wait_for_timeout(3000)
                print("New URL:", page.url)
                print("New Title:", await page.title())
                inputs = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('input, textarea, select')).map(i => ({
                        tag: i.tagName, type: i.type, id: i.id, name: i.name
                    })).slice(0, 15);
                }""")
                print("Inputs on Solides after click:", inputs)

if __name__ == "__main__":
    asyncio.run(test())
