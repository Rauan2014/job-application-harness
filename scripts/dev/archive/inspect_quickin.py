import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings

async def test():
    settings = load_settings()
    async with BrowserManager(settings) as bm:
        url = "https://jobs.quickin.io/sinqia/apply?job_id=6ac3e23e8759e00013ac4841"
        async with bm.new_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            data = await page.evaluate("""() => {
                const files = Array.from(document.querySelectorAll('input[type="file"]')).map(i => ({
                    id: i.id, name: i.name, class: i.className
                }));
                const emails = Array.from(document.querySelectorAll('input[type="email"], input[id*="email"], input[name*="email"]')).map(i => ({
                    id: i.id, name: i.name
                }));
                const allInputs = Array.from(document.querySelectorAll('input, select, textarea')).map(i => ({
                    id: i.id, name: i.name, type: i.type, placeholder: i.placeholder, label: i.labels ? (i.labels[0] ? i.labels[0].innerText : '') : ''
                }));
                return { files, emails, allInputs };
            }""")
            print("Files:", data["files"])
            print("Emails:", data["emails"])
            print("Sample inputs:", data["allInputs"][:15])

if __name__ == "__main__":
    asyncio.run(test())
