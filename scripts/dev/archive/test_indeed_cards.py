import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings


async def test():
    s = load_settings()
    async with BrowserManager(s) as bm:
        async with bm.new_page() as page:
            url = "https://br.indeed.com/q-engenheiro-de-agentes-de-ia-l-remoto-vagas.html"
            print("Navigating to", url)
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            cards = await page.evaluate(
                """() => {
                const els = document.querySelectorAll('div.job_seen_beacon, [data-jk]');
                return Array.from(els).map(el => {
                    const t = el.querySelector('h2 a, a.jcs-JobTitle');
                    const c = el.querySelector('[data-testid="company-name"], span.companyName');
                    return {
                        title: t ? t.innerText.trim() : '',
                        company: c ? c.innerText.trim() : '',
                        jk: el.getAttribute('data-jk') || (t ? t.getAttribute('data-jk') : '')
                    };
                });
            }"""
            )
            print(f"Total cards found: {len(cards)}")
            for c in cards:
                print(c)


if __name__ == "__main__":
    asyncio.run(test())
