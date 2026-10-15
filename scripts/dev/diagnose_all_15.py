import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from automation.registry import get_handler
from config.settings import load_settings
from storage.sheet_loader import load_jobs


async def main():
    settings = load_settings()
    jobs = load_jobs(Path("cache/test_online.xlsx"), only_todo=False, include_blank=True, include_review=True)
    print(f"Total jobs to inspect: {len(jobs)}")
    async with BrowserManager(settings) as bm:
        for idx, job in enumerate(jobs):
            print(f"\n=======================================================")
            print(f"[{idx+1}/{len(jobs)}] {job.company} — {job.title}")
            print(f"URL: {job.url}")
            print(f"Status in sheet: {job.sheet_status}")
            async with bm.new_page() as page:
                try:
                    await page.goto(job.url, timeout=25000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2500)
                    cur_url = page.url
                    title = await page.title()
                    print(f"Landed URL: {cur_url}")
                    print(f"Page Title: {title}")
                    h = get_handler(job)
                    print(f"Handler: {h.name if h else 'None'}")
                    # Check for links to ATS
                    outbound = await page.evaluate(
                        """() => {
                        const links = Array.from(document.querySelectorAll('a')).map(a => ({
                            text: a.innerText.trim(),
                            href: a.href
                        }));
                        return links.filter(l => 
                            l.href.includes('lever.co') || 
                            l.href.includes('greenhouse.io') || 
                            l.href.includes('workable.com') ||
                            l.href.includes('smartapply.indeed.com') ||
                            l.href.includes('solides.com') ||
                            l.href.includes('gupy.io') ||
                            l.text.toLowerCase().includes('candidat') ||
                            l.text.toLowerCase().includes('apply')
                        ).slice(0, 5);
                    }"""
                    )
                    print(f"Outbound links: {outbound}")
                except Exception as e:
                    print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
