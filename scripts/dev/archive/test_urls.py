import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings

async def test():
    settings = load_settings()
    async with BrowserManager(settings) as bm:
        for name, url in [
            ('WithProtege', 'https://www.remoterocketship.com/company/withprotege-ai/jobs/ai-engineer-new-verticals-brazil-remote/'),
            ('Jump', 'https://remotar.com.br/job/165249/jump/engenheiro-de-ia'),
            ('Sierra', 'https://jobs.ashbyhq.com/sierra-studio/64d8bf9f-9c84-469c-bff3-e15c541e9bd0'),
            ('CI&T', 'https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd')
        ]:
            async with bm.new_page() as page:
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    await page.wait_for_timeout(2000)
                    title = await page.title()
                    cur_url = page.url
                    links = await page.evaluate('''() => {
                        return Array.from(document.querySelectorAll('a, button')).map(el => ({
                            tag: el.tagName,
                            text: (el.innerText || '').trim().replace(/\\s+/g, ' '),
                            href: el.href || el.getAttribute('href') || ''
                        })).filter(l => l.href.includes('lever') || l.href.includes('ashby') || l.href.includes('greenhouse') || l.href.includes('workable') || l.href.includes('apply') || l.href.includes('candidat') || l.text.toLowerCase().includes('apply') || l.text.toLowerCase().includes('candidat')).slice(0, 5);
                    }''')
                    print(f'{name} -> Landed: {cur_url} | Title: {title}', flush=True)
                    for l in links:
                        print(f'   {l["tag"]}: text="{l["text"][:30]}" href={l["href"]}', flush=True)
                except Exception as e:
                    print(f'{name} error: {e}', flush=True)

if __name__ == '__main__':
    asyncio.run(test())
