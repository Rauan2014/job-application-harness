import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings
from storage.sheet_loader import load_jobs
from automation.registry import get_handler

async def main():
    settings = load_settings()
    jobs = load_jobs(Path('cache/test_online.xlsx'), only_todo=False, include_blank=True, include_review=True)
    async with BrowserManager(settings) as bm:
        for idx, job in enumerate(jobs[:5]):
            print(f"\n--- [{idx+1}/{len(jobs)}] {job.company} | {job.title} ---")
            print(f"Original URL: {job.url}")
            async with bm.new_page() as page:
                try:
                    await page.goto(job.url, timeout=25000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                    final_url = page.url
                    title = await page.title()
                    print(f"Final URL: {final_url}")
                    print(f"Title: {title}")
                    handler = get_handler(job)
                    print(f"Initial Handler: {handler.name if handler else 'None'}")
                except Exception as e:
                    print(f"Error navigating: {e}")

if __name__ == "__main__":
    asyncio.run(main())
