import asyncio
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "tasks" / "current_task.json"
RESULTS_DIR = ROOT / "results"
LEADS_PATH = RESULTS_DIR / "deterministic_leads.json"
STATE_PATH = RESULTS_DIR / "deterministic_state.json"
LATEST_PATH = RESULTS_DIR / "deterministic_latest.json"

EMAIL_RE = re.compile(r"(?<![\w.-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
RATING_RE = re.compile(r"\b([1-5][\.,]\d)\b")
REVIEWS_RE = re.compile(r"(?:\(|\b)([0-9][0-9\.]{0,6})(?:\)|\s+(?:Rezensionen|Bewertungen|reviews))", re.I)

SERVICE_KEYWORDS = {
    "Physiotherapie": ["lymphdrainage", "cmd", "krankengymnastik", "manuelle therapie", "sportphysio", "massage", "atemtherapie"],
    "Osteopathie": ["osteopathie", "craniosacral", "viszeral", "parietal"],
    "Podologie": ["podologie", "medizinische fusspflege", "nagelkorrektur", "orthonyxie"],
    "Kosmetikstudio": ["microneedling", "gesichtsbehandlung", "aquafacial", "hydrafacial", "permanent make-up", "wimpern", "augenbrauen"],
    "Beauty Aesthetik": ["botox", "hyaluron", "microneedling", "laser", "hautbehandlung", "aesthetik"],
    "Friseur": ["balayage", "straehnen", "coloration", "haarschnitt", "extensions", "brautfrisur"],
    "Barbershop": ["bart", "rasur", "haarschnitt", "fade", "konturen"],
    "Zahnarzt": ["implantate", "prophylaxe", "bleaching", "invisalign", "parodontologie", "wurzelbehandlung"],
    "Tierarzt": ["impfung", "chirurgie", "zahnsanierung", "ultraschall", "labor"],
    "Autowerkstatt": ["inspektion", "tuev", "reifen", "bremsen", "klima", "diagnose"],
    "Sanitaer Heizung": ["heizung", "waermepumpe", "sanitaer", "bad", "wartung", "notdienst"],
    "Elektriker": ["elektroinstallation", "photovoltaik", "wallbox", "smart home", "notdienst"],
    "Dachdecker": ["dachsanierung", "flachdach", "dachfenster", "abdichtung", "photovoltaik"],
    "Gebaeudereinigung": ["buero", "fenster", "unterhaltsreinigung", "grundreinigung", "treppenhaus"],
    "Immobilienmakler": ["verkauf", "vermietung", "bewertung", "kapitalanlage", "immobilienbewertung"],
}


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def norm(s):
    return re.sub(r"\W+", "", (s or "").lower())


def parse_number(text):
    if not text:
        return None
    m = REVIEWS_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except Exception:
        return None


def parse_rating(text):
    if not text:
        return None
    m = RATING_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def domain_of(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


async def extract_website_details(url, category):
    if not url:
        return {"email": None, "email_source_url": None, "services": [], "contact_form_url": None, "notes": "No website found"}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; LOCENIXResearch/1.0; +https://locenix.com)"}
    timeout = httpx.Timeout(12.0, connect=8.0)
    out = {"email": None, "email_source_url": None, "services": [], "contact_form_url": None, "notes": ""}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text, flags=re.I)
            plain = re.sub(r"<[^>]+>", " ", text)
            plain = re.sub(r"\s+", " ", plain).lower()
            emails = [e for e in EMAIL_RE.findall(r.text) if not any(x in e.lower() for x in ["example.com", "sentry.io", "wixpress.com"])]
            if emails:
                out["email"] = emails[0]
                out["email_source_url"] = str(r.url)
            kws = SERVICE_KEYWORDS.get(category, [])
            out["services"] = [kw for kw in kws if kw in plain][:8]
            links = re.findall(r'href=["\']([^"\']+)["\']', r.text, flags=re.I)
            for href in links:
                low = href.lower()
                if "kontakt" in low or "contact" in low:
                    if href.startswith("http"):
                        out["contact_form_url"] = href
                    elif href.startswith("/"):
                        p = urlparse(str(r.url))
                        out["contact_form_url"] = f"{p.scheme}://{p.netloc}{href}"
                    break
            if not out["email"]:
                for suffix in ["/impressum", "/kontakt", "/contact"]:
                    try:
                        rr = await client.get(str(r.url).rstrip("/") + suffix)
                        emails = EMAIL_RE.findall(rr.text)
                        if emails:
                            out["email"] = emails[0]
                            out["email_source_url"] = str(rr.url)
                            break
                    except Exception:
                        pass
    except Exception as exc:
        out["notes"] = f"Website fetch failed: {type(exc).__name__}"
    return out


async def search_maps(page, query, max_results):
    url = f"https://www.google.com/maps/search/{quote(query)}?hl=de"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(3500)
    for text in ["Alle akzeptieren", "Accept all", "Ich stimme zu", "Zustimmen"]:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count():
                await btn.first.click(timeout=2500)
                await page.wait_for_timeout(1500)
                break
        except Exception:
            pass
    anchors = page.locator('a[href*="/maps/place/"]')
    count = min(await anchors.count(), max_results)
    rows = []
    for i in range(count):
        a = anchors.nth(i)
        href = await a.get_attribute("href")
        name = (await a.get_attribute("aria-label")) or (await a.inner_text())
        text = ""
        try:
            text = await a.locator("xpath=..",).inner_text(timeout=1500)
        except Exception:
            pass
        if href and name:
            rows.append({"name": name.strip(), "maps_url": href, "card_text": text})
    # preserve order, dedupe URLs
    seen, out = set(), []
    for row in rows:
        if row["maps_url"] in seen:
            continue
        seen.add(row["maps_url"])
        out.append(row)
    return out


async def enrich_place(page, row):
    data = {"address": None, "website": None, "phone": None, "rating": parse_rating(row.get("card_text")), "reviews": parse_number(row.get("card_text"))}
    try:
        await page.goto(row["maps_url"], wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(2500)
        try:
            rating_el = page.locator("div.F7nice").first
            txt = await rating_el.inner_text(timeout=1500)
            data["rating"] = data["rating"] or parse_rating(txt)
            data["reviews"] = data["reviews"] or parse_number(txt)
        except Exception:
            pass
        for sel, key in [
            ('button[data-item-id="address"]', "address"),
            ('button[data-item-id^="phone"]', "phone"),
            ('a[data-item-id="authority"]', "website"),
        ]:
            try:
                el = page.locator(sel).first
                if await el.count():
                    if key == "website":
                        data[key] = await el.get_attribute("href")
                    else:
                        data[key] = (await el.get_attribute("aria-label")) or (await el.inner_text())
                        if data[key] and ":" in data[key]:
                            data[key] = data[key].split(":", 1)[1].strip()
            except Exception:
                pass
    except Exception:
        pass
    return data


def score_lead(rating, reviews, competitor_reviews, website, services):
    reviews = reviews or 0
    comp_med = 0
    vals = sorted([x for x in competitor_reviews if isinstance(x, int) and x >= 0])
    if vals:
        comp_med = vals[len(vals)//2]
    review_gap = max(0, comp_med - reviews)
    report = 8
    if review_gap >= 150: report += 12
    elif review_gap >= 75: report += 9
    elif review_gap >= 30: report += 6
    if reviews < 30: report += 6
    elif reviews < 80: report += 4
    if services: report += min(7, len(services) * 2)
    report = min(35, report)
    visible = 8 + (4 if review_gap >= 50 else 0) + (3 if services else 0)
    maps_importance = 14
    customer_value = 8
    pressure = 5 + (5 if comp_med >= 100 else 3 if comp_med >= 50 else 1)
    digital = 4 + (6 if website else 0)
    saas = 5 if website else 3
    total = report + min(15, visible) + maps_importance + customer_value + min(10, pressure) + min(10, digital) + saas
    return {
        "report_potential_score": report,
        "visible_value_score": min(15, visible),
        "maps_importance_score": maps_importance,
        "customer_value_score": customer_value,
        "competitive_pressure_score": min(10, pressure),
        "digital_readiness_score": min(10, digital),
        "saas_fit_score": saas,
        "total_score": min(100, total),
        "competitor_review_median": comp_med,
        "review_gap": review_gap,
    }


def grade(score):
    if score >= 90: return "A+"
    if score >= 85: return "A"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    return "C"


async def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    task = load_json(TASK_PATH, {})
    if not task.get("enabled"):
        print("Task disabled")
        return
    leads = load_json(LEADS_PATH, [])
    state = load_json(STATE_PATH, {"query_cursor": 0, "runs": 0})
    existing = {(norm(x.get("company_name")), domain_of(x.get("website")), norm(x.get("address"))) for x in leads}
    target = int(task.get("target_leads", 200))
    if len(leads) >= target:
        print(f"Target reached: {len(leads)}")
        return

    cities = task["cities"]
    cats = task["categories"]
    all_queries = [(city, cat) for city in cities for cat in cats]
    cursor = int(state.get("query_cursor", 0))
    qpr = int(task.get("queries_per_run", 3))
    max_results = int(task.get("max_results_per_query", 8))
    min_score = int(task.get("min_score", 70))
    accepted = []
    inspected = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = await browser.new_page(locale="de-DE", viewport={"width": 1440, "height": 1000})
        for offset in range(qpr):
            city, cat = all_queries[(cursor + offset) % len(all_queries)]
            query = f"{cat} {city}"
            try:
                rows = await search_maps(page, query, max_results)
            except Exception:
                rows = []
            comp_reviews = [parse_number(r.get("card_text")) for r in rows]
            for row in rows:
                if len(leads) + len(accepted) >= target:
                    break
                inspected += 1
                details = await enrich_place(page, row)
                website_info = await extract_website_details(details.get("website"), cat)
                key = (norm(row.get("name")), domain_of(details.get("website")), norm(details.get("address")))
                if key in existing:
                    continue
                scoring = score_lead(details.get("rating"), details.get("reviews"), comp_reviews, details.get("website"), website_info.get("services"))
                if scoring["total_score"] < min_score:
                    continue
                weaknesses = []
                if scoring["review_gap"] >= 30:
                    weaknesses.append(f"Deutlich weniger Bewertungen als mehrere sichtbare Wettbewerber; Abstand etwa {scoring['review_gap']} Bewertungen zum lokalen Vergleichswert.")
                if (details.get("reviews") or 0) < 50:
                    weaknesses.append("Relativ geringe Bewertungsanzahl für einen lokal stark suchgetriebenen Dienstleister.")
                if website_info.get("services"):
                    weaknesses.append("Die Website nennt mehrere konkrete Leistungen, die im ersten Google-Maps-Eindruck nicht gleichwertig sichtbar waren; Service-Gap sollte im LOCENIX Check geprüft werden.")
                if not weaknesses:
                    weaknesses.append("Google-Präsenz bietet im Wettbewerbsvergleich sichtbares Optimierungspotenzial; Detailprüfung empfohlen.")
                lead = {
                    "company_name": row["name"], "industry": cat, "city": city, "address": details.get("address"),
                    "website": details.get("website"), "google_maps_url": row.get("maps_url"), "phone": details.get("phone"),
                    "public_business_email": website_info.get("email"), "email_source_url": website_info.get("email_source_url"),
                    "contact_form_url": website_info.get("contact_form_url"),
                    "contact_basis": "PUBLIC_BUSINESS_EMAIL_ONLY" if website_info.get("email") else ("CONTACT_FORM" if website_info.get("contact_form_url") else "UNKNOWN"),
                    "google_rating": details.get("rating"), "google_review_count": details.get("reviews"), "main_category": cat,
                    "website_services": website_info.get("services", []), "google_visible_services": [],
                    "service_gap": website_info.get("services", []),
                    "competitor_1": rows[0]["name"] if rows and rows[0]["name"] != row["name"] else (rows[1]["name"] if len(rows) > 1 else None),
                    "competitor_1_reviews": next((parse_number(r.get("card_text")) for r in rows if r["name"] != row["name"] and parse_number(r.get("card_text")) is not None), None),
                    "competitor_2": next((r["name"] for r in rows[1:] if r["name"] != row["name"] and r["name"] != (rows[0]["name"] if rows else "")), None),
                    "competitor_2_reviews": next((parse_number(r.get("card_text")) for r in rows[1:] if r["name"] != row["name"] and parse_number(r.get("card_text")) is not None), None),
                    "weakness_1": weaknesses[0] if len(weaknesses) > 0 else None,
                    "weakness_2": weaknesses[1] if len(weaknesses) > 1 else None,
                    "weakness_3": weaknesses[2] if len(weaknesses) > 2 else None,
                    **scoring,
                    "grade": grade(scoring["total_score"]),
                    "best_outreach_angle": weaknesses[0] if weaknesses else None,
                    "second_outreach_angle": weaknesses[1] if len(weaknesses) > 1 else None,
                    "research_notes": f"Deterministic no-LLM research. Query: {query}. Website note: {website_info.get('notes') or 'ok'}. Final QA required in ChatGPT.",
                    "research_date": date.today().isoformat(), "research_status": "PARTIAL", "qa_status": "PENDING", "outreach_status": "NOT_CONTACTED"
                }
                existing.add(key)
                accepted.append(lead)
                if len(accepted) >= int(task.get("batch_size", 20)):
                    break
            if len(accepted) >= int(task.get("batch_size", 20)):
                break
        await browser.close()

    leads.extend(accepted)
    leads = sorted(leads, key=lambda x: x.get("total_score", 0), reverse=True)[:target]
    state.update({"query_cursor": cursor + qpr, "runs": int(state.get("runs", 0)) + 1, "lead_count": len(leads), "target_leads": target, "last_accepted": len(accepted), "last_inspected": inspected, "complete": len(leads) >= target})
    save_json(LEADS_PATH, leads)
    save_json(STATE_PATH, state)
    save_json(LATEST_PATH, {"accepted": accepted, "accepted_count": len(accepted), "inspected": inspected, "state": state})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
