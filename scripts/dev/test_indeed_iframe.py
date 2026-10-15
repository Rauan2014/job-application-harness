import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings

async def test():
    settings = load_settings()
    async with BrowserManager(settings) as bm:
        async with bm.new_page() as page:
            await page.goto("https://br.indeed.com/q-desenvolvedor-ia-l-s%C3%A3o-paulo,-sp-vagas.html?vjk=07d8ef0b1c75ef31", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            print("SERP Title:", await page.title())
            btn = page.locator("button:has-text('Candidatura')").first
            if await btn.count() > 0:
                print("Clicking Candidatura...")
                await btn.click()
                await page.wait_for_timeout(4000)
                for f in page.frames:
                    print("Frame URL:", f.url)
                    try:
                        f_btns = await f.evaluate("() => Array.from(document.querySelectorAll('button, input')).map(e => e.innerText || e.value || e.placeholder || e.id)")
                        print("Frame elements sample:", f_btns[:10])
                    except Exception as e:
                        print("Frame eval err:", e)

if __name__ == "__main__":
    asyncio.run(test())
