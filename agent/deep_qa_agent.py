import asyncio
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "tasks" / "qa_task.json"
LEADS_PATH = ROOT / "results" / "deterministic_leads.json"
RESULTS_PATH = ROOT / "results" / "deep_qa_results.json"
STATE_PATH = ROOT / "results" / "deep_qa_state.json"
LATEST_PATH = ROOT / "results" / "deep_qa_latest.json"

BOOKING_MARKERS = ["termin buchen", "online buchen", "booking", "doctolib", "treatwell", "calendly"]
SOCIAL_MARKERS = ["instagram", "facebook", "linkedin"]
MAPS_POST_MARKERS = ["updates", "neuigkeiten", "beiträge", "posts"]
PHOTO_MARKERS = ["fotos", "photos"]
CLOSED_MARKERS = ["dauerhaft geschlossen", "permanently closed"]


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def identity(lead):
    return "|".join([
        (lead.get("company_name") or "").strip().lower(),
        (lead.get("city") or "").strip().lower(),
        (lead.get("website") or "").strip().lower(),
    ])


def bool_marker(text, markers):
    low = (text or "").lower()
    return any(m in low for m in markers)


async def accept_consent(page):
    for text in ["Alle akzeptieren", "Accept all", "Ich stimme zu", "Zustimmen"]:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count():
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass


async def inspect_maps(page, lead):
    out = {
        "maps_accessible": False,
        "maps_profile_closed": False,
        "maps_text_observed": False,
        "posts_signal": "UNKNOWN",
        "photos_signal": "UNKNOWN",
        "booking_signal": "UNKNOWN",
        "website_link_observed": False,
        "phone_observed": False,
        "address_observed": False,
        "maps_evidence": [],
    }
    url = lead.get("google_maps_url")
    if not url:
        return out
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await accept_consent(page)
        await page.wait_for_timeout(2200)
        text = (await page.locator("body").inner_text(timeout=5000)).lower()
        out["maps_accessible"] = True
        out["maps_text_observed"] = bool(text.strip())
        out["maps_profile_closed"] = bool_marker(text, CLOSED_MARKERS)
        out["posts_signal"] = "OBSERVED" if bool_marker(text, MAPS_POST_MARKERS) else "NOT_OBSERVED"
        out["photos_signal"] = "OBSERVED" if bool_marker(text, PHOTO_MARKERS) else "NOT_OBSERVED"
        out["booking_signal"] = "OBSERVED" if bool_marker(text, BOOKING_MARKERS) else "NOT_OBSERVED"
        out["website_link_observed"] = await page.locator('a[data-item-id="authority"]').count() > 0
        out["phone_observed"] = await page.locator('button[data-item-id^="phone"]').count() > 0
        out["address_observed"] = await page.locator('button[data-item-id="address"]').count() > 0
        if out["maps_profile_closed"]:
            out["maps_evidence"].append("Google Maps profile appears permanently closed.")
        if out["website_link_observed"]:
            out["maps_evidence"].append("Website link observed on Google Maps profile.")
        if out["phone_observed"]:
            out["maps_evidence"].append("Phone action observed on Google Maps profile.")
        if out["booking_signal"] == "OBSERVED":
            out["maps_evidence"].append("Booking/appointment wording observed in current Maps page text.")
    except Exception as exc:
        out["maps_error"] = type(exc).__name__
    return out


async def inspect_website(page, lead):
    out = {
        "website_accessible": False,
        "website_final_url": None,
        "booking_link": None,
        "social_links": [],
        "contact_page_observed": False,
        "website_service_matches": [],
        "website_evidence": [],
    }
    url = lead.get("website")
    if not url:
        return out
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=35000)
        await page.wait_for_timeout(1200)
        out["website_accessible"] = response is None or response.status < 400
        out["website_final_url"] = page.url
        text = (await page.locator("body").inner_text(timeout=5000)).lower()
        anchors = page.locator("a[href]")
        for i in range(min(await anchors.count(), 250)):
            a = anchors.nth(i)
            href = (await a.get_attribute("href")) or ""
            label = ((await a.inner_text()) or "").strip().lower()
            combined = f"{href} {label}".lower()
            if not out["booking_link"] and bool_marker(combined, BOOKING_MARKERS):
                out["booking_link"] = urljoin(page.url, href)
            if any(m in combined for m in SOCIAL_MARKERS):
                absolute = urljoin(page.url, href)
                if absolute not in out["social_links"]:
                    out["social_links"].append(absolute)
            if "kontakt" in combined or "contact" in combined:
                out["contact_page_observed"] = True
        services = [s for s in lead.get("website_services", []) if s and s.lower() in text]
        out["website_service_matches"] = services
        if out["booking_link"]:
            out["website_evidence"].append("Online booking/appointment link observed on website.")
        if out["social_links"]:
            out["website_evidence"].append(f"Active social-link footprint observed ({len(out['social_links'])} link(s)).")
        if out["contact_page_observed"]:
            out["website_evidence"].append("Contact page/link observed on website.")
        if services:
            out["website_evidence"].append("Website services re-observed: " + ", ".join(services[:5]))
    except Exception as exc:
        out["website_error"] = type(exc).__name__
    return out


def final_assessment(lead, maps, website):
    blockers = []
    verified = []
    caution = []

    if not maps.get("maps_accessible"):
        blockers.append("Maps profile could not be verified in this run.")
    if maps.get("maps_profile_closed"):
        blockers.append("Maps profile appears permanently closed.")
    if lead.get("google_review_count") is None:
        caution.append("Google review count is missing; never interpret this as zero.")
    if not website.get("website_accessible"):
        caution.append("Website could not be verified as accessible in this run.")

    review_count = lead.get("google_review_count")
    median = lead.get("competitor_review_median")
    if isinstance(review_count, int) and isinstance(median, int) and median > review_count:
        gap = median - review_count
        if gap >= 30:
            verified.append({"type": "review_gap", "proof": f"Verified source data: {review_count} reviews vs. comparison median {median}; gap {gap}."})
    if website.get("booking_link"):
        verified.append({"type": "digital_maturity_booking", "proof": "Online booking/appointment link observed on website."})
    if len(website.get("social_links", [])) >= 1:
        verified.append({"type": "digital_maturity_social", "proof": "Business website links to at least one social profile."})
    if maps.get("phone_observed") and maps.get("website_link_observed"):
        verified.append({"type": "profile_completeness_core", "proof": "Current Maps page exposes both phone and website actions."})

    score = int(lead.get("total_score") or 0)
    purchase = int(lead.get("purchase_likelihood_score") or 0)
    qa_score = min(100, score + min(8, len(verified) * 2) + (3 if purchase >= 8 else 0) - len(blockers) * 30)

    if blockers:
        decision = "REJECT_OR_MANUAL_REVIEW"
    elif qa_score >= 90 and len(verified) >= 2 and website.get("website_accessible"):
        decision = "FINAL_A"
    elif qa_score >= 82 and len(verified) >= 1:
        decision = "FINAL_B"
    else:
        decision = "MANUAL_REVIEW"

    sales_proofs = [v["proof"] for v in verified if v["type"] in {"review_gap", "digital_maturity_booking", "digital_maturity_social"}]
    primary_pitch = sales_proofs[0] if sales_proofs else None
    return {
        "deep_qa_decision": decision,
        "deep_qa_score": qa_score,
        "deep_qa_verified_signals": verified,
        "deep_qa_blockers": blockers,
        "deep_qa_cautions": caution,
        "primary_sales_angle_verified": primary_pitch,
        "proof": sales_proofs[:3],
        "airtable_ready": decision == "FINAL_A" and bool(primary_pitch),
    }


async def main():
    task = load_json(TASK_PATH, {})
    if not task.get("enabled", False):
        print("Deep QA disabled")
        return

    leads = load_json(LEADS_PATH, [])
    prior = load_json(RESULTS_PATH, [])
    state = load_json(STATE_PATH, {"processed_ids": [], "runs": 0})
    processed = set(state.get("processed_ids", []))
    eligible_classes = set(task.get("eligible_classes", ["A_LEAD", "B_LEAD"]))
    min_score = int(task.get("min_total_score", 80))

    def class_is_eligible(lead):
        lead_class = lead.get("lead_class")
        if lead_class:
            return lead_class in eligible_classes
        return int(lead.get("total_score") or 0) >= min_score

    candidates = [
        x for x in leads
        if identity(x) not in processed
        and class_is_eligible(x)
        and (not task.get("require_maps_url", True) or x.get("google_maps_url"))
    ]
    candidates.sort(key=lambda x: (x.get("lead_class") == "A_LEAD", int(x.get("total_score") or 0)), reverse=True)
    batch = candidates[:int(task.get("batch_size", 5))]

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        maps_page = await browser.new_page(locale="de-DE", viewport={"width": 1440, "height": 1000})
        web_page = await browser.new_page(locale="de-DE", viewport={"width": 1440, "height": 1000})
        for lead in batch:
            maps = await inspect_maps(maps_page, lead)
            website = await inspect_website(web_page, lead)
            assessment = final_assessment(lead, maps, website)
            result = {
                "lead_id": identity(lead),
                "company_name": lead.get("company_name"),
                "industry": lead.get("industry"),
                "city": lead.get("city"),
                "source_total_score": lead.get("total_score"),
                "source_lead_class": lead.get("lead_class"),
                "google_maps_url": lead.get("google_maps_url"),
                "website": lead.get("website"),
                "maps_check": maps,
                "website_check": website,
                **assessment,
                "qa_date": date.today().isoformat(),
                "qa_agent": "LOCENIX_DEEP_QA_AGENT_V1",
                "outreach_status": "NOT_CONTACTED",
            }
            results.append(result)
            processed.add(identity(lead))
        await browser.close()

    merged = {x.get("lead_id"): x for x in prior if x.get("lead_id")}
    for r in results:
        merged[r["lead_id"]] = r
    all_results = sorted(merged.values(), key=lambda x: int(x.get("deep_qa_score") or 0), reverse=True)
    state = {
        "runs": int(state.get("runs", 0)) + 1,
        "processed_ids": sorted(processed),
        "processed_count": len(processed),
        "remaining_eligible": max(0, len(candidates) - len(batch)),
        "last_batch_count": len(results),
        "final_a_total": sum(1 for x in all_results if x.get("deep_qa_decision") == "FINAL_A"),
        "final_b_total": sum(1 for x in all_results if x.get("deep_qa_decision") == "FINAL_B"),
    }
    save_json(RESULTS_PATH, all_results)
    save_json(STATE_PATH, state)
    save_json(LATEST_PATH, {"results": results, "state": state})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())