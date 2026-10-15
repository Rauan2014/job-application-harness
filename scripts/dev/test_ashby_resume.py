import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings

async def test():
    settings = load_settings()
    async with BrowserManager(settings) as bm:
        url = "https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0/application"
        async with bm.new_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            res = await page.evaluate("""() => {
                const els = document.querySelectorAll('*[name*=\"_systemfield\"], *[id*=\"_systemfield\"]');
                return Array.from(els).map(el => ({
                    tagName: el.tagName,
                    type: el.type,
                    id: el.id,
                    name: el.name,
                    required: el.required,
                    ariaRequired: el.getAttribute('aria-required'),
                    outerHTML: el.outerHTML.slice(0, 200)
                }));
            }""")
            print("Systemfield elements:", res)

if __name__ == "__main__":
    asyncio.run(test())
