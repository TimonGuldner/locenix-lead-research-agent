import asyncio
import csv
import json
import os
import re
from pathlib import Path
from typing import Any

from browser_use import Agent, ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "tasks" / "current_task.json"
PROMPT_PATH = ROOT / "prompts" / "lead_research.md"
RESULTS_DIR = ROOT / "results"
LEADS_PATH = RESULTS_DIR / "leads.json"
CSV_PATH = RESULTS_DIR / "leads.csv"
STATE_PATH = RESULTS_DIR / "state.json"
LATEST_PATH = RESULTS_DIR / "latest_run.json"


def load_json(path: Path, default: Any):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize(value: Any) -> str:
    return re.sub(r"\W+", "", str(value or "").lower())


def dedupe_key(lead: dict[str, Any]) -> str:
    return f"{normalize(lead.get('company_name'))}|{normalize(lead.get('city'))}|{normalize(lead.get('website'))}"


def extract_json_array(text: str) -> list[dict[str, Any]]:
    text = (text or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:
        pass
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def write_csv(leads: list[dict[str, Any]]):
    if not leads:
        CSV_PATH.write_text("", encoding="utf-8")
        return
    preferred = [
        "company_name", "industry", "city", "address", "website", "google_maps_url", "phone",
        "public_business_email", "email_source_url", "contact_form_url", "google_rating", "google_review_count",
        "main_category", "website_services", "google_visible_services", "service_gap", "competitor_1",
        "competitor_1_reviews", "competitor_2", "competitor_2_reviews", "weakness_1", "weakness_2",
        "weakness_3", "report_potential_score", "visible_value_score", "maps_importance_score",
        "customer_value_score", "competitive_pressure_score", "digital_readiness_score", "saas_fit_score",
        "total_score", "best_outreach_angle", "second_outreach_angle", "contact_basis", "research_notes"
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=preferred, extrasaction="ignore")
        writer.writeheader()
        for row in leads:
            clean = {k: (" | ".join(map(str, v)) if isinstance(v, list) else v) for k, v in row.items()}
            writer.writerow(clean)


def select_slice(task: dict[str, Any], state: dict[str, Any]) -> tuple[str, str, int]:
    areas = task["areas"]
    categories = task["categories"]
    cursor = int(state.get("slice_cursor", 0))
    total = len(areas) * len(categories)
    idx = cursor % total
    area = areas[(idx // len(categories)) % len(areas)]
    category = categories[idx % len(categories)]
    return area, category, cursor


async def main():
    task = load_json(TASK_PATH, {})
    RESULTS_DIR.mkdir(exist_ok=True)
    if not task.get("enabled"):
        print("Research task disabled; nothing to do.")
        return
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required as a GitHub Actions secret.")

    leads = load_json(LEADS_PATH, [])
    state = load_json(STATE_PATH, {"slice_cursor": 0, "runs": 0})
    target = int(task.get("target_leads", 100))
    if len(leads) >= target:
        print(f"Target already reached: {len(leads)}/{target}")
        return

    area, category, cursor = select_slice(task, state)
    batch_size = min(int(task.get("batch_size", 8)), target - len(leads))
    min_score = int(task.get("min_score", 70))
    existing = [f"{x.get('company_name','')} — {x.get('city','')}" for x in leads[-120:]]
    base_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    task_prompt = f"""{base_prompt}

CURRENT BATCH
Search area: {area}
Primary category: {category}
Return up to {batch_size} genuinely strong, verified leads with total_score >= {min_score}.
Research broadly enough to compare direct competitors and inspect each candidate's official website where available.
Do not pad the batch with weak leads. Returning fewer is better than inventing or lowering quality.

ALREADY COLLECTED — EXCLUDE THESE:
{json.dumps(existing, ensure_ascii=False)}
"""

    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"))
    agent = Agent(task=task_prompt, llm=llm, use_vision=False)
    history = await agent.run(max_steps=int(task.get("max_steps", 55)))
    raw = history.final_result() or ""
    batch = extract_json_array(raw)

    existing_keys = {dedupe_key(x) for x in leads}
    accepted = []
    for lead in batch:
        if not isinstance(lead, dict):
            continue
        try:
            score = int(float(lead.get("total_score", 0)))
        except Exception:
            score = 0
        if score < min_score:
            continue
        key = dedupe_key(lead)
        if not key or key in existing_keys:
            continue
        existing_keys.add(key)
        accepted.append(lead)

    leads.extend(accepted)
    leads = sorted(leads, key=lambda x: float(x.get("total_score", 0) or 0), reverse=True)[:target]
    state.update({
        "slice_cursor": cursor + 1,
        "runs": int(state.get("runs", 0)) + 1,
        "lead_count": len(leads),
        "target_leads": target,
        "last_area": area,
        "last_category": category,
        "last_accepted": len(accepted),
        "complete": len(leads) >= target,
    })
    save_json(LEADS_PATH, leads)
    write_csv(leads)
    save_json(STATE_PATH, state)
    save_json(LATEST_PATH, {
        "area": area,
        "category": category,
        "accepted": len(accepted),
        "raw_count": len(batch),
        "total_leads": len(leads),
        "success": history.is_successful(),
        "errors": [str(e) for e in history.errors() if e],
        "raw_final_result": raw,
    })
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
