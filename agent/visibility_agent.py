import asyncio
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "tasks" / "visibility_task.json"
QA_PATH = ROOT / "results" / "deep_qa_results.json"
RESULTS_PATH = ROOT / "results" / "visibility_results.json"
STATE_PATH = ROOT / "results" / "visibility_state.json"
LATEST_PATH = ROOT / "results" / "visibility_latest.json"
ALLOWED_EMAIL_STATUSES = {"PUBLICLY_OBSERVED", "VERIFIED"}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


async def accept_consent(page):
    for text in ["Alle akzeptieren", "Accept all", "Ich stimme zu", "Zustimmen"]:
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count():
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(700)
                return
        except Exception:
            pass


async def search_maps(page, query, max_results):
    url = f"https://www.google.com/maps/search/{quote(query)}?hl=de"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await accept_consent(page)
    await page.wait_for_timeout(2800)
    anchors = page.locator('a[href*="/maps/place/"]')
    rows = []
    seen = set()
    for i in range(min(await anchors.count(), max_results)):
        a = anchors.nth(i)
        href = await a.get_attribute("href")
        name = (await a.get_attribute("aria-label")) or (await a.inner_text())
        if not href or not name or href in seen:
            continue
        seen.add(href)
        rows.append({"position": len(rows) + 1, "name": name.strip(), "maps_url": href})
    return rows


def candidate_queries(qa, task):
    industry = (qa.get("industry") or "").strip()
    city = (qa.get("city") or "").strip()
    queries = []
    if industry and city:
        queries.append(f"{industry} {city}")
        if task.get("include_nearby_intent_query", True):
            queries.append(f"{industry} in {city}")
    return queries[: int(task.get("queries_per_lead", 2))]


def match_target(company_name, rows):
    target = norm(company_name)
    if not target:
        return None
    for row in rows:
        candidate = norm(row.get("name"))
        if candidate == target or (len(target) >= 8 and (target in candidate or candidate in target)):
            return row
    return None


async def main():
    task = load_json(TASK_PATH, {})
    if not task.get("enabled", False):
        print("Visibility agent disabled")
        return

    qa_results = load_json(QA_PATH, [])
    prior = load_json(RESULTS_PATH, [])
    state = load_json(STATE_PATH, {"processed_ids": [], "runs": 0})
    processed = set(state.get("processed_ids", []))
    decisions = set(task.get("eligible_decisions", ["FINAL_A", "FINAL_B"]))

    email_gate_rejected = [
        x for x in qa_results
        if x.get("deep_qa_decision") in decisions
        and not (x.get("email") and x.get("email_verification_status") in ALLOWED_EMAIL_STATUSES)
    ]
    candidates = [
        x for x in qa_results
        if x.get("lead_id") not in processed
        and x.get("deep_qa_decision") in decisions
        and x.get("email")
        and x.get("email_verification_status") in ALLOWED_EMAIL_STATUSES
        and x.get("company_name")
        and x.get("city")
        and x.get("industry")
    ]
    candidates.sort(key=lambda x: int(x.get("deep_qa_score") or 0), reverse=True)
    batch = candidates[: int(task.get("batch_size", 5))]
    max_results = int(task.get("max_results_per_query", 20))

    new_results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = await browser.new_page(locale="de-DE", viewport={"width": 1440, "height": 1000})
        for qa in batch:
            tests = []
            for query in candidate_queries(qa, task):
                try:
                    rows = await search_maps(page, query, max_results)
                    target = match_target(qa.get("company_name"), rows)
                    if target:
                        ahead = [r.get("name") for r in rows if r.get("position", 999) < target.get("position", 999)][:5]
                        tests.append({
                            "query": query,
                            "status": "OBSERVED",
                            "observed_position": target.get("position"),
                            "results_checked": len(rows),
                            "competitors_ahead": ahead,
                            "evidence": f"Business observed at position {target.get('position')} among the first {len(rows)} Maps results returned in this run."
                        })
                    else:
                        tests.append({
                            "query": query,
                            "status": "NOT_OBSERVED_IN_CHECKED_RESULTS",
                            "observed_position": None,
                            "results_checked": len(rows),
                            "competitors_ahead": [r.get("name") for r in rows[:5]],
                            "evidence": f"Business was not observed among the first {len(rows)} Maps results returned in this run. This is not a universal ranking claim."
                        })
                except Exception as exc:
                    tests.append({"query": query, "status": "ERROR", "error": type(exc).__name__})

            observed_positions = [t["observed_position"] for t in tests if isinstance(t.get("observed_position"), int)]
            weak_visibility = any(t.get("status") == "NOT_OBSERVED_IN_CHECKED_RESULTS" for t in tests) or any(p > 5 for p in observed_positions)
            visibility_score = 0
            if tests:
                if any(t.get("status") == "NOT_OBSERVED_IN_CHECKED_RESULTS" for t in tests):
                    visibility_score = 10
                elif observed_positions:
                    best = min(observed_positions)
                    visibility_score = 8 if best > 10 else 6 if best > 5 else 3 if best > 3 else 1

            result = {
                "lead_id": qa.get("lead_id"),
                "company_name": qa.get("company_name"),
                "industry": qa.get("industry"),
                "city": qa.get("city"),
                "email": qa.get("email"),
                "email_verification_status": qa.get("email_verification_status"),
                "deep_qa_decision": qa.get("deep_qa_decision"),
                "deep_qa_score": qa.get("deep_qa_score"),
                "visibility_tests": tests,
                "weak_visibility_observed": weak_visibility,
                "visibility_opportunity_score": visibility_score,
                "visibility_evidence": [t.get("evidence") for t in tests if t.get("evidence")],
                "visibility_date": date.today().isoformat(),
                "visibility_agent": "LOCENIX_MAPS_VISIBILITY_AGENT_V2_EMAIL_GATE",
                "claim_scope": "Observed search-result positions only; no universal or persistent ranking claim.",
                "outreach_status": "NOT_CONTACTED"
            }
            new_results.append(result)
            processed.add(qa.get("lead_id"))
        await browser.close()

    merged = {x.get("lead_id"): x for x in prior if x.get("lead_id")}
    for item in new_results:
        merged[item["lead_id"]] = item
    all_results = sorted(merged.values(), key=lambda x: (int(x.get("visibility_opportunity_score") or 0), int(x.get("deep_qa_score") or 0)), reverse=True)
    state = {
        "runs": int(state.get("runs", 0)) + 1,
        "processed_ids": sorted(processed),
        "processed_count": len(processed),
        "last_batch_count": len(new_results),
        "remaining_eligible": max(0, len(candidates) - len(batch)),
        "weak_visibility_total": sum(1 for x in all_results if x.get("weak_visibility_observed")),
        "email_gate_rejected_count": len(email_gate_rejected),
        "downstream_email_gate_violations": 0,
        "email_gate_rule": "NO_RESEARCHED_PUBLIC_EMAIL_NO_VISIBILITY_ANALYSIS"
    }
    save_json(RESULTS_PATH, all_results)
    save_json(STATE_PATH, state)
    save_json(LATEST_PATH, {"results": new_results, "state": state})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
