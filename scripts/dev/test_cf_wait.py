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
            await page.goto("https://br.indeed.com/viewjob?jk=5247886b1987b2f8", wait_until="domcontentloaded", timeout=30000)
            for s in range(15):
                await page.wait_for_timeout(1000)
                t = await page.title()
                print(f"Second {s+1}: title='{t}'", flush=True)
                if "just a moment" not in t.lower() and "cloudflare" not in t.lower():
                    print("PASSED CLOUDFLARE TURNSTILE!", flush=True)
                    break

if __name__ == "__main__":
    asyncio.run(test())
