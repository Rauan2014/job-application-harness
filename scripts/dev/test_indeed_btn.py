import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings

async def test():
    settings = load_settings()
    async with BrowserManager(settings) as bm:
        url = "https://br.indeed.com/q-desenvolvedor-ia-l-s%C3%A3o-paulo,-sp-vagas.html?vjk=07d8ef0b1c75ef31"
        async with bm.new_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            btn = page.locator("button:has-text('Candidatura')").first
            # Register popup handler
            popup_future = asyncio.get_event_loop().create_future()
            page.on("popup", lambda p: popup_future.set_result(p) if not popup_future.done() else None)
            
            await btn.click()
            await page.wait_for_timeout(3000)
            
            # Check if popup was triggered
            if popup_future.done():
                popup = popup_future.result()
                print("POPUP OPENED:", popup.url)
            else:
                print("No popup, checking iframes...")
                iframes = [f.url for f in page.frames]
                print("Frames:", iframes)
                modals = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('dialog, [role="dialog"], iframe')).map(e => ({
                        tag: e.tagName,
                        id: e.id,
                        class: e.className,
                        src: e.src || ''
                    }));
                }""")
                print("Modals/iframes:", modals)

if __name__ == "__main__":
    asyncio.run(test())
