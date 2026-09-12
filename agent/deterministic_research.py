import asyncio
import json
import re
from datetime import date
from pathlib import Path
from statistics import median
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
    base = {"email": None, "email_source_url": None, "services": [], "contact_form_url": None, "notes": "", "website_text_ok": False}
    if not url:
        base["notes"] = "No website found"
        return base
    headers = {"User-Agent": "Mozilla/5.0 (compatible; LOCENIXResearch/1.1; +https://locenix.com)"}
    timeout = httpx.Timeout(12.0, connect=8.0)
    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
            base["website_text_ok"] = r.status_code < 400 and len(r.text) > 500
            stripped = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text, flags=re.I)
            plain = re.sub(r"<[^>]+>", " ", stripped)
            plain = re.sub(r"\s+", " ", plain).lower()
            emails = [e for e in EMAIL_RE.findall(r.text) if not any(x in e.lower() for x in ["example.com", "sentry.io", "wixpress.com"])]
            if emails:
                base["email"] = emails[0]
                base["email_source_url"] = str(r.url)
            base["services"] = [kw for kw in SERVICE_KEYWORDS.get(category, []) if kw in plain][:8]
            links = re.findall(r'href=["\']([^"\']+)["\']', r.text, flags=re.I)
            for href in links:
                low = href.lower()
                if "kontakt" in low or "contact" in low:
                    if href.startswith("http"):
                        base["contact_form_url"] = href
                    elif href.startswith("/"):
                        p = urlparse(str(r.url))
                        base["contact_form_url"] = f"{p.scheme}://{p.netloc}{href}"
                    break
            if not base["email"]:
                for suffix in ["/impressum", "/kontakt", "/contact"]:
                    try:
                        rr = await client.get(str(r.url).rstrip("/") + suffix)
                        found = EMAIL_RE.findall(rr.text)
                        if found:
                            base["email"] = found[0]
                            base["email_source_url"] = str(rr.url)
                            break
                    except Exception:
                        pass
    except Exception as exc:
        base["notes"] = f"Website fetch failed: {type(exc).__name__}"
    return base


async def search_maps(page, query, max_results):
    await page.goto(f"https://www.google.com/maps/search/{quote(query)}?hl=de", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(3200)
    for text in ["Alle akzeptieren", "Accept all", "Ich stimme zu", "Zustimmen"]:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count():
                await btn.first.click(timeout=2500)
                await page.wait_for_timeout(1200)
                break
        except Exception:
            pass
    anchors = page.locator('a[href*="/maps/place/"]')
    rows = []
    for i in range(min(await anchors.count(), max_results)):
        a = anchors.nth(i)
        href = await a.get_attribute("href")
        name = (await a.get_attribute("aria-label")) or (await a.inner_text())
        text = ""
        try:
            text = await a.locator("xpath=..").inner_text(timeout=1500)
        except Exception:
            pass
        if href and name:
            rows.append({"name": name.strip(), "maps_url": href, "card_text": text})
    seen, out = set(), []
    for row in rows:
        if row["maps_url"] not in seen:
            seen.add(row["maps_url"])
            out.append(row)
    return out


async def enrich_place(page, row, category):
    data = {"address": None, "website": None, "phone": None, "rating": parse_rating(row.get("card_text")), "reviews": parse_number(row.get("card_text")), "maps_text": ""}
    try:
        await page.goto(row["maps_url"], wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(2200)
        try:
            data["maps_text"] = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            pass
        try:
            txt = await page.locator("div.F7nice").first.inner_text(timeout=1500)
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


def competitor_stats(row, rows):
    comps = []
    for r in rows:
        if r.get("maps_url") == row.get("maps_url") or r.get("name") == row.get("name"):
            continue
        rv = parse_number(r.get("card_text"))
        if rv is not None:
            comps.append((r.get("name"), rv))
    vals = [rv for _, rv in comps]
    med = int(round(median(vals))) if vals else 0
    comps_sorted = sorted(comps, key=lambda x: x[1], reverse=True)
    return med, comps_sorted[:2]


def build_signals(rating, reviews, comp_median, website_info, maps_text):
    reviews = reviews or 0
    rating = rating or 0
    review_gap = max(0, comp_median - reviews)
    verified = []
    soft = []
    if comp_median and review_gap >= 30:
        verified.append({"type": "review_gap", "proof": f"{reviews} eigene Bewertungen vs. lokaler Wettbewerbsmedian {comp_median}; Gap {review_gap}."})
    if reviews < 50:
        verified.append({"type": "low_review_volume", "proof": f"Nur {reviews} Google-Bewertungen."})
    if rating and rating < 4.8:
        verified.append({"type": "rating_headroom", "proof": f"Google-Bewertung {rating:.1f}; sichtbares Reputationspotenzial."})

    website_services = website_info.get("services", [])
    not_observed = [s for s in website_services if s.lower() not in (maps_text or "")]
    if not_observed:
        soft.append({"type": "service_visibility_candidate", "proof": "Auf Website gefunden, aber im ersten Google-Maps-Ansichts-Text nicht beobachtet: " + ", ".join(not_observed[:5]), "verification": "REQUIRES_FINAL_QA"})

    return verified, soft, review_gap


def score_lead(rating, reviews, comp_median, website, website_info, verified, soft):
    reviews = reviews or 0
    review_gap = max(0, comp_median - reviews)
    report = 8
    if review_gap >= 150: report += 12
    elif review_gap >= 75: report += 9
    elif review_gap >= 30: report += 6
    if reviews < 30: report += 6
    elif reviews < 80: report += 4
    report += min(5, max(0, len(verified) - 1) * 2)
    report = min(35, report)

    visible = min(15, 7 + (5 if review_gap >= 50 else 2 if review_gap >= 30 else 0) + (2 if soft else 0))
    maps_importance = 14
    customer_value = 8
    pressure = min(10, 5 + (5 if comp_median >= 100 else 3 if comp_median >= 50 else 1))
    digital = 3 + (4 if website else 0) + (2 if website_info.get("website_text_ok") else 0) + (1 if website_info.get("email") or website_info.get("contact_form_url") else 0)
    saas = 5 if website and website_info.get("website_text_ok") else 3
    total = min(100, report + visible + maps_importance + customer_value + pressure + min(10, digital) + saas)
    return {
        "report_potential_score": report,
        "visible_value_score": visible,
        "maps_importance_score": maps_importance,
        "customer_value_score": customer_value,
        "competitive_pressure_score": pressure,
        "digital_readiness_score": min(10, digital),
        "saas_fit_score": saas,
        "total_score": total,
        "competitor_review_median": comp_median,
        "review_gap": review_gap,
    }


def qualification(score, verified, website_info, min_score):
    hard_count = len(verified)
    digital_ready = bool(website_info.get("website_text_ok"))
    if score >= 85 and hard_count >= 2 and digital_ready:
        return "A_LEAD", "PASS"
    if score >= min_score and hard_count >= 1:
        return "B_LEAD", "REVIEW"
    return "REJECT", "FAIL"


def grade(score):
    if score >= 90: return "A+"
    if score >= 85: return "A"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    return "C"


def pitch_from(verified, soft):
    proofs = [s["proof"] for s in verified]
    if soft:
        proofs.append(soft[0]["proof"])
    primary = proofs[0] if proofs else None
    return {
        "primary_pitch": primary,
        "proof": proofs[:3],
        "expected_locenix_value": "LOCENIX Check: Google-Unternehmensprofil gegen lokale Wettbewerber prüfen und belegbare Optimierungshebel priorisieren." if proofs else None,
    }


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
    cities, cats = task["cities"], task["categories"]
    all_queries = [(city, cat) for city in cities for cat in cats]
    cursor = int(state.get("query_cursor", 0))
    qpr = int(task.get("queries_per_run", 3))
    max_results = int(task.get("max_results_per_query", 8))
    min_score = int(task.get("min_score", 80))
    accepted, inspected, rejected = [], 0, 0

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

            for row in rows:
                if len(leads) + len(accepted) >= target:
                    break
                inspected += 1
                details = await enrich_place(page, row, cat)
                website_info = await extract_website_details(details.get("website"), cat)
                key = (norm(row.get("name")), domain_of(details.get("website")), norm(details.get("address")))
                if key in existing:
                    continue

                comp_median, top_comps = competitor_stats(row, rows)
                verified, soft, _ = build_signals(details.get("rating"), details.get("reviews"), comp_median, website_info, details.get("maps_text"))
                scoring = score_lead(details.get("rating"), details.get("reviews"), comp_median, details.get("website"), website_info, verified, soft)
                lead_class, qa_gate = qualification(scoring["total_score"], verified, website_info, min_score)
                if lead_class == "REJECT":
                    rejected += 1
                    continue

                pitch = pitch_from(verified, soft)
                lead = {
                    "company_name": row["name"], "industry": cat, "city": city, "address": details.get("address"),
                    "website": details.get("website"), "google_maps_url": row.get("maps_url"), "phone": details.get("phone"),
                    "public_business_email": website_info.get("email"), "email_source_url": website_info.get("email_source_url"),
                    "contact_form_url": website_info.get("contact_form_url"),
                    "contact_basis": "PUBLIC_BUSINESS_EMAIL_ONLY" if website_info.get("email") else ("CONTACT_FORM" if website_info.get("contact_form_url") else "UNKNOWN"),
                    "google_rating": details.get("rating"), "google_review_count": details.get("reviews"), "main_category": cat,
                    "website_services": website_info.get("services", []),
                    "google_visible_services": [],
                    "service_gap": [],
                    "service_visibility_candidates": [s["proof"] for s in soft if s.get("type") == "service_visibility_candidate"],
                    "service_gap_status": "UNVERIFIED" if soft else "NOT_DETECTED",
                    "competitor_1": top_comps[0][0] if len(top_comps) > 0 else None,
                    "competitor_1_reviews": top_comps[0][1] if len(top_comps) > 0 else None,
                    "competitor_2": top_comps[1][0] if len(top_comps) > 1 else None,
                    "competitor_2_reviews": top_comps[1][1] if len(top_comps) > 1 else None,
                    "verified_optimization_signals": verified,
                    "soft_optimization_signals": soft,
                    "verified_signal_count": len(verified),
                    **scoring,
                    "grade": grade(scoring["total_score"]),
                    "lead_class": lead_class,
                    "qa_gate": qa_gate,
                    **pitch,
                    "best_outreach_angle": pitch.get("primary_pitch"),
                    "second_outreach_angle": pitch.get("proof", [None, None])[1] if len(pitch.get("proof", [])) > 1 else None,
                    "research_notes": f"Deterministic research v2. Query: {query}. Website note: {website_info.get('notes') or 'ok'}. Service-gap claims remain unverified until final QA.",
                    "research_date": date.today().isoformat(),
                    "research_status": "QUALIFIED" if lead_class == "A_LEAD" else "PARTIAL",
                    "qa_status": "AUTO_PASS_NEEDS_FINAL_QA" if lead_class == "A_LEAD" else "PENDING",
                    "outreach_status": "NOT_CONTACTED"
                }
                existing.add(key)
                accepted.append(lead)
                if len(accepted) >= int(task.get("batch_size", 20)):
                    break
            if len(accepted) >= int(task.get("batch_size", 20)):
                break
        await browser.close()

    leads.extend(accepted)
    leads = sorted(leads, key=lambda x: (x.get("lead_class") == "A_LEAD", x.get("total_score", 0)), reverse=True)[:target]
    state.update({
        "query_cursor": cursor + qpr,
        "runs": int(state.get("runs", 0)) + 1,
        "lead_count": len(leads),
        "target_leads": target,
        "last_accepted": len(accepted),
        "last_rejected": rejected,
        "last_inspected": inspected,
        "a_leads": sum(1 for x in leads if x.get("lead_class") == "A_LEAD"),
        "b_leads": sum(1 for x in leads if x.get("lead_class") == "B_LEAD"),
        "complete": len(leads) >= target,
    })
    save_json(LEADS_PATH, leads)
    save_json(STATE_PATH, state)
    save_json(LATEST_PATH, {"accepted": accepted, "accepted_count": len(accepted), "rejected_count": rejected, "inspected": inspected, "state": state})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
