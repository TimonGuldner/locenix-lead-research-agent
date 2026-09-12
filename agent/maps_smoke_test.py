import asyncio
import json
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "maps_smoke_test.json"


async def click_consent(page):
    candidates = [
        ("button", "Alle akzeptieren"),
        ("button", "Accept all"),
        ("button", "Ich stimme zu"),
        ("button", "Zustimmen"),
        ("button", "Alles akzeptieren"),
    ]
    for role, name in candidates:
        try:
            locator = page.get_by_role(role, name=name, exact=False)
            if await locator.count():
                await locator.first.click(timeout=2500)
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            pass

    # Fallback for consent.google.com / alternate markup.
    for selector in [
        'button:has-text("Alle akzeptieren")',
        'button:has-text("Accept all")',
        'form button',
    ]:
        try:
            locator = page.locator(selector)
            if await locator.count():
                await locator.first.click(timeout=2500)
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    return False


async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    query = "Physiotherapie Troisdorf"
    result = {
        "query": query,
        "success": False,
        "listing_name": None,
        "listing_url": None,
        "page_title": None,
        "final_url": None,
        "consent_clicked": False,
        "note": None,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            locale="de-DE",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            # Open the Maps search URL directly so the test does not depend on the search-box DOM.
            search_url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=de"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            result["consent_clicked"] = await click_consent(page)

            # If consent redirected us away, return to the direct Maps search URL.
            if "consent.google" in page.url or "/maps" not in page.url:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                await click_consent(page)

            await page.wait_for_timeout(5000)
            result["final_url"] = page.url

            # Prefer visible place-result links. Google may render these as /maps/place/ URLs.
            links = page.locator('a[href*="/maps/place/"]')
            count = await links.count()
            if count == 0:
                # Fallback: result feed items sometimes expose a link with an aria-label but no stable selector.
                links = page.locator('div[role="feed"] a[href*="/maps/"]')
                count = await links.count()

            if count == 0:
                result["page_title"] = await page.title()
                body_text = (await page.locator("body").inner_text())[:1200]
                result["note"] = (
                    "Google Maps loaded but no business result link was detected. "
                    f"Current URL: {page.url}. Body preview: {body_text}"
                )
            else:
                first = links.first
                name = (await first.get_attribute("aria-label")) or (await first.inner_text())
                href = await first.get_attribute("href")
                result["listing_name"] = (name or "").strip() or None
                result["listing_url"] = href
                await first.click(timeout=10000)
                await page.wait_for_timeout(4000)
                result["page_title"] = await page.title()
                result["listing_url"] = page.url
                result["final_url"] = page.url
                result["success"] = "/maps/place/" in page.url
                result["note"] = "Opened the first visible Google Maps business result in read-only mode."
        except Exception as exc:
            result["final_url"] = page.url
            result["note"] = f"Smoke test error: {type(exc).__name__}: {exc}"
        finally:
            await context.close()
            await browser.close()

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
