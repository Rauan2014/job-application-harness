import asyncio
import sys

from automation.browser import BrowserManager
from config.settings import load_settings


async def check():
    s = load_settings()
    bm = BrowserManager(s)
    await bm.start()
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://jobs.lever.co/ciandt/56f9af4b-0b7c-4f99-be77-e51a2859abdd/apply"
    async with bm.new_page() as page:
        await page.goto(target_url)
        cards = await page.locator(".application-question").all()
        for idx, card in enumerate(cards):
            has_lbl = await card.locator("label, .application-label").count() > 0
            lbl = await card.locator("label, .application-label").first.text_content() if has_lbl else "NO LABEL"
            inps = [await x.get_attribute("name") for x in await card.locator("input, select, textarea").all()]
            clean_lbl = lbl.encode("ascii", "replace").decode()
            print(f"CARD {idx}: label={clean_lbl!r}, inputs={inps}")

        print("\n--- ALL REQUIRED INPUTS ---")
        reqs = await page.locator("input[required], select[required], textarea[required]").all()
        for r in reqs:
            name = await r.get_attribute("name") or await r.get_attribute("id") or "unnamed"
            val = await r.input_value() if await r.get_attribute("type") != "file" else "file"
            print(f"REQUIRED: name={name}, val={val!r}")

    await bm.stop()

if __name__ == "__main__":
    asyncio.run(check())
