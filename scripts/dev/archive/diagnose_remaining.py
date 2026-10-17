import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings
from storage.sheet_loader import load_jobs

async def main():
    settings = load_settings()
    jobs = load_jobs("cache/vagas_sheets_latest.xlsx", only_todo=False, include_blank=True, include_review=True, include_failed=True)
    # Filter only those not already Pronta (dry-run)
    targets = [j for j in jobs if "pronta" not in (j.sheet_status or "").lower()]
    print(f"Total targets to diagnose: {len(targets)}")

    async with BrowserManager(settings) as bm:
        for idx, job in enumerate(targets, 1):
            print(f"\n==================================================")
            print(f"[{idx}/{len(targets)}] {job.company} - {job.title}")
            print(f"Original URL: {job.url}")
            async with bm.new_page() as page:
                try:
                    await page.goto(job.url, timeout=25000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                    cur_url = page.url
                    title = await page.title()
                    print(f"Landed URL: {cur_url}")
                    print(f"Page Title: {title}")
                    
                    data = await page.evaluate("""() => {
                        const links = Array.from(document.querySelectorAll('a, button')).map(el => ({
                            tag: el.tagName,
                            text: (el.innerText || '').trim().replace(/\\s+/g, ' '),
                            href: el.href || el.getAttribute('href') || '',
                            dataUrl: el.getAttribute('data-url') || el.getAttribute('data-apply-url') || ''
                        }));
                        const fileInputs = Array.from(document.querySelectorAll('input[type="file"]')).map(i => i.id || i.name);
                        const emailInputs = Array.from(document.querySelectorAll('input[type="email"], input[id*="email"], input[name*="email"]')).map(i => i.id || i.name);
                        return { links: links.filter(l => l.text.toLowerCase().includes('apply') || l.text.toLowerCase().includes('candidat') || l.href.includes('apply') || l.href.includes('job')).slice(0, 8), fileInputs, emailInputs };
                    }""")
                    print(f"File inputs: {data['fileInputs']}")
                    print(f"Email inputs: {data['emailInputs']}")
                    print(f"Key links/buttons: {data['links']}")
                except Exception as e:
                    print(f"Error on {job.company}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
