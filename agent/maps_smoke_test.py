import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "maps_smoke_test.json"


async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "query": "Physiotherapie Troisdorf",
        "success": False,
        "listing_name": None,
        "listing_url": None,
        "page_title": None,
        "note": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="de-DE", viewport={"width": 1440, "height": 1000})
        try:
            await page.goto("https://www.google.com/maps", wait_until="domcontentloaded", timeout=45000)

            # Handle the common Google consent dialog when present.
            for text in ["Alle akzeptieren", "Accept all", "Ich stimme zu"]:
                try:
                    btn = page.get_by_role("button", name=text)
                    if await btn.count():
                        await btn.first.click(timeout=3000)
                        break
                except Exception:
                    pass

            search = page.locator("input#searchboxinput")
            await search.wait_for(timeout=20000)
            await search.fill(result["query"])
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            # Open the first actual Maps place result, not an ad/control link.
            links = page.locator('a[href*="/maps/place/"]')
            count = await links.count()
            if count == 0:
                result["note"] = "No /maps/place/ result link found; Google may have served a changed or blocked layout."
            else:
                first = links.first
                name = (await first.get_attribute("aria-label")) or (await first.inner_text())
                href = await first.get_attribute("href")
                result["listing_name"] = (name or "").strip() or None
                result["listing_url"] = href
                await first.click()
                await page.wait_for_timeout(4000)
                result["page_title"] = await page.title()
                result["listing_url"] = page.url
                result["success"] = "/maps/place/" in page.url
                result["note"] = "Opened the first visible Google Maps business result in read-only mode."
        except Exception as exc:
            result["note"] = f"Smoke test error: {type(exc).__name__}: {exc}"
        finally:
            await browser.close()

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
