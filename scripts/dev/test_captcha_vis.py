import asyncio

from automation.browser import BrowserManager
from config.settings import load_settings


async def main():
    s = load_settings()
    mgr = BrowserManager(s)
    await mgr.start()
    try:
        async with mgr.new_page() as p:
            await p.goto('https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd/apply')
            await p.wait_for_timeout(2000)
            iframes = p.locator('iframe[src*="hcaptcha"], iframe[src*="recaptcha"]')
            cnt = await iframes.count()
            print('Total captcha iframes:', cnt)
            for i in range(cnt):
                fr = iframes.nth(i)
                src = await fr.get_attribute('src')
                is_vis = await fr.is_visible()
                box = await fr.bounding_box()
                print(f'Iframe {i}: visible={is_vis}, box={box}, src={src[:60]}...')
    finally:
        await mgr.stop()

if __name__ == "__main__":
    asyncio.run(main())
