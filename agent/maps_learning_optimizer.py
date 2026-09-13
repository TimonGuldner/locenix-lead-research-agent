from __future__ import annotations

import json
import math
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks" / "maps_learning_task.json"
RESEARCH = ROOT / "results" / "deterministic_leads.json"
QA = ROOT / "results" / "deep_qa_results.json"
VIS = ROOT / "results" / "visibility_results.json"
CONFIG = ROOT / "results" / "maps_learning_config.json"
STATE = ROOT / "results" / "maps_learning_state.json"
LATEST = ROOT / "results" / "maps_learning_latest.json"


def load(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def identity(lead: dict[str, Any]) -> str:
    return "|".join([
        str(lead.get("company_name") or "").strip().lower(),
        str(lead.get("city") or "").strip().lower(),
        str(lead.get("website") or "").strip().lower(),
    ])


def as_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "active", "started", "paid"}


def fetch_airtable(base: str, table: str, token: str) -> list[dict[str, Any]]:
    if not token or not base or not table:
        return []
    rows: list[dict[str, Any]] = []
    offset = ""
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        url = f"https://api.airtable.com/v0/{base}/{table}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "locenix-maps-learning/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
        rows.extend(payload.get("records") or [])
        offset = str(payload.get("offset") or "")
        if not offset:
            return rows


def merge_airtable_by_lead_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in records:
        f = row.get("fields") or {}
        lead_id = str(f.get("Lead ID") or "").strip()
        if not lead_id or lead_id.startswith("intent-") or lead_id.startswith("TEST_"):
            continue
        current = merged.setdefault(lead_id, {})
        for key, value in f.items():
            if value not in (None, "", [], False):
                current[key] = value
        if f.get("Email Do Not Contact") is True:
            current["Email Do Not Contact"] = True
    return merged


def outcome_reward(lead: dict[str, Any], qa: dict[str, Any], vis: dict[str, Any], air: dict[str, Any]) -> float:
    reward = 0.0

    lead_class = str(lead.get("lead_class") or "")
    if lead_class == "A_LEAD":
        reward += 0.75
    elif lead_class == "B_LEAD":
        reward += 0.35

    decision = str(qa.get("deep_qa_decision") or air.get("Deep QA Decision") or "")
    if decision == "FINAL_A":
        reward += 2.5
    elif decision == "FINAL_B":
        reward += 1.5
    elif decision == "MANUAL_REVIEW":
        reward -= 0.5
    elif decision == "REJECT_OR_MANUAL_REVIEW":
        reward -= 2.0

    if qa.get("email") or air.get("Email"):
        reward += 0.75
    if qa.get("airtable_ready") or truthy(air.get("Airtable Ready")):
        reward += 0.5

    vis_score = vis.get("visibility_opportunity_score")
    if not isinstance(vis_score, (int, float)):
        vis_score = air.get("Visibility Opportunity Score")
    if isinstance(vis_score, (int, float)):
        if vis_score >= 8:
            reward += 2.0
        elif vis_score >= 6:
            reward += 1.4
        elif vis_score >= 3:
            reward += 0.6
    if vis.get("weak_visibility_observed"):
        reward += 0.5

    send_status = as_text(air.get("Email Send Status")).upper()
    if send_status == "SENT":
        reward += 1.5
    elif send_status == "READY_TO_SEND":
        reward += 0.15

    reply = as_text(air.get("Email Reply Classification")).upper()
    sales_intent = as_text(air.get("Email Sales Intent")).upper()
    if any(x in reply for x in ("INTEREST", "POSITIVE", "VISIBILITY_CHECK", "MEETING", "TRIAL")):
        reward += 6.0
    elif "QUESTION" in reply:
        reward += 3.0
    elif any(x in reply for x in ("NEGATIVE", "NOT_INTERESTED")):
        reward -= 3.0
    elif any(x in reply for x in ("UNSUBSCRIBE", "DO_NOT_CONTACT")):
        reward -= 6.0

    if sales_intent == "HIGH":
        reward += 3.0
    elif sales_intent == "MEDIUM":
        reward += 1.5

    if truthy(air.get("Trial")) or truthy(air.get("Trial Active")) or str(air.get("Trial Status") or "").upper() in {"ACTIVE", "STARTED"}:
        reward += 10.0
    if truthy(air.get("Paid")) or truthy(air.get("Paid Customer")) or (isinstance(air.get("MRR"), (int, float)) and air.get("MRR") > 0):
        reward += 15.0

    if truthy(air.get("Email Do Not Contact")):
        reward = min(reward, -6.0)
    return reward


def bucket_rating(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return "rating_unknown"
    if x >= 4.8:
        return "rating_4.8_plus"
    if x >= 4.5:
        return "rating_4.5_4.79"
    if x >= 4.0:
        return "rating_4.0_4.49"
    return "rating_under_4.0"


def bucket_reviews(value: Any) -> str:
    try:
        x = int(value)
    except Exception:
        return "reviews_unknown"
    if x < 30:
        return "reviews_0_29"
    if x < 80:
        return "reviews_30_79"
    if x < 200:
        return "reviews_80_199"
    return "reviews_200_plus"


def bucket_gap(value: Any) -> str:
    try:
        x = int(value)
    except Exception:
        return "gap_unknown"
    if x >= 150:
        return "gap_150_plus"
    if x >= 75:
        return "gap_75_149"
    if x >= 30:
        return "gap_30_74"
    return "gap_under_30"


def accumulate(bucket: dict[str, dict[str, float]], key: str, reward: float) -> None:
    if not key:
        return
    row = bucket.setdefault(key, {"samples": 0.0, "reward": 0.0, "positive": 0.0, "negative": 0.0})
    row["samples"] += 1
    row["reward"] += reward
    if reward >= 3:
        row["positive"] += 1
    if reward < 0:
        row["negative"] += 1


def finalize(bucket: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for key, row in bucket.items():
        samples = int(row["samples"])
        avg = row["reward"] / max(1, samples)
        out[key] = {
            "samples": samples,
            "reward": round(row["reward"], 3),
            "avg_reward": round(avg, 3),
            "positive_rate": round(row["positive"] / max(1, samples), 3),
            "negative_rate": round(row["negative"] / max(1, samples), 3),
        }
    return out


def fresh_weight(avg_reward: float, samples: int, min_boost: int, min_down: int, low: float, high: float) -> float:
    if samples < min_boost:
        return 1.0
    confidence = min(1.0, samples / 12.0)
    signal = math.tanh(avg_reward / 5.0)
    candidate = 1.0 + 0.4 * confidence * signal
    if candidate < 1.0 and samples < min_down:
        candidate = 1.0
    return max(low, min(high, candidate))


def smooth(previous: float, fresh: float, samples: int, low: float, high: float) -> float:
    alpha = min(0.40, 0.12 + samples * 0.025)
    return round(max(low, min(high, previous * (1 - alpha) + fresh * alpha)), 3)


def main() -> None:
    cfg = load(TASK, {})
    if not cfg.get("enabled", False):
        print(json.dumps({"status": "disabled"}))
        return

    now = datetime.now(timezone.utc).isoformat()
    previous = load(CONFIG, {})
    research = load(RESEARCH, [])
    qa_rows = load(QA, [])
    vis_rows = load(VIS, [])
    qa_by_id = {str(x.get("lead_id")): x for x in qa_rows if x.get("lead_id")}
    vis_by_id = {str(x.get("lead_id")): x for x in vis_rows if x.get("lead_id")}

    base = os.environ.get("AIRTABLE_BASE_ID") or cfg.get("airtable_base_id")
    table = os.environ.get("AIRTABLE_TABLE_ID") or cfg.get("airtable_table_id")
    token = os.environ.get("AIRTABLE_TOKEN", "")
    errors: list[str] = []
    try:
        air_by_id = merge_airtable_by_lead_id(fetch_airtable(str(base), str(table), token))
    except Exception as exc:
        air_by_id = {}
        errors.append(f"airtable:{type(exc).__name__}")

    industries_raw: dict[str, dict[str, float]] = {}
    cities_raw: dict[str, dict[str, float]] = {}
    pairs_raw: dict[str, dict[str, float]] = {}
    features_raw: dict[str, dict[str, float]] = {}
    evaluated = 0
    downstream = 0

    for lead in research:
        lid = identity(lead)
        qa = qa_by_id.get(lid, {})
        vis = vis_by_id.get(lid, {})
        air = air_by_id.get(lid, {})
        reward = outcome_reward(lead, qa, vis, air)
        evaluated += 1
        if qa or vis or air:
            downstream += 1
        industry = str(lead.get("industry") or lead.get("main_category") or "").strip()
        city = str(lead.get("city") or "").strip()
        accumulate(industries_raw, industry, reward)
        accumulate(cities_raw, city, reward)
        accumulate(pairs_raw, f"{industry}|{city}" if industry and city else "", reward)
        for feature in (
            bucket_rating(lead.get("google_rating")),
            bucket_reviews(lead.get("google_review_count")),
            bucket_gap(lead.get("review_gap")),
            "website_ready" if lead.get("website") else "website_missing",
            "email_observed" if (qa.get("email") or air.get("Email")) else "email_missing",
        ):
            accumulate(features_raw, feature, reward)

    industry_stats = finalize(industries_raw)
    city_stats = finalize(cities_raw)
    pair_stats = finalize(pairs_raw)
    feature_stats = finalize(features_raw)

    min_boost = int(cfg.get("minimum_samples_before_boost", 2))
    min_down = int(cfg.get("minimum_samples_before_downweight", 5))
    low = float(cfg.get("weight_min", 0.70))
    high = float(cfg.get("weight_max", 1.40))

    prev_i = previous.get("industry_weights") or {}
    prev_c = previous.get("city_weights") or {}
    industry_weights: dict[str, float] = {}
    city_weights: dict[str, float] = {}

    for key, stats in industry_stats.items():
        fresh = fresh_weight(float(stats["avg_reward"]), int(stats["samples"]), min_boost, min_down, low, high)
        industry_weights[key] = smooth(float(prev_i.get(key, 1.0)), fresh, int(stats["samples"]), low, high)
    for key, stats in city_stats.items():
        fresh = fresh_weight(float(stats["avg_reward"]), int(stats["samples"]), min_boost, min_down, low, high)
        city_weights[key] = smooth(float(prev_c.get(key, 1.0)), fresh, int(stats["samples"]), low, high)

    # Preserve unseen options already known from previous learning at neutral/bounded weights.
    for key, value in prev_i.items():
        industry_weights.setdefault(key, float(value))
    for key, value in prev_c.items():
        city_weights.setdefault(key, float(value))

    recommended_industries = sorted(industry_weights, key=lambda x: (-industry_weights[x], -int(industry_stats.get(x, {}).get("samples", 0)), x))
    recommended_cities = sorted(city_weights, key=lambda x: (-city_weights[x], -int(city_stats.get(x, {}).get("samples", 0)), x))

    run_count = int(previous.get("learning_run_count") or 0) + 1
    learning = {
        "agent": "AGENT_4L_MAPS_RESEARCH_LEARNING_OPTIMIZER",
        "generated_at": now,
        "learning_run_count": run_count,
        "mode": "SAFE_OUTCOME_LEARNING",
        "principle": "Optimize Google Maps research toward downstream sales quality, not raw lead count; never rewrite code or relax safety/legal guardrails.",
        "exploration_share": float(cfg.get("exploration_share", 0.15)),
        "minimum_samples_before_downweight": min_down,
        "minimum_samples_before_boost": min_boost,
        "industry_weights": industry_weights,
        "city_weights": city_weights,
        "recommended_industries": recommended_industries[:15],
        "recommended_cities": recommended_cities[:20],
        "industry_stats": industry_stats,
        "city_stats": city_stats,
        "pair_stats": pair_stats,
        "feature_stats": feature_stats,
        "guardrails": cfg.get("rules", {}),
    }
    state = {
        "agent": "AGENT_4L_MAPS_RESEARCH_LEARNING_OPTIMIZER",
        "last_run_at": now,
        "status": "OK" if not errors else "PARTIAL",
        "learning_run_count": run_count,
        "research_leads_evaluated": evaluated,
        "leads_with_downstream_evidence": downstream,
        "airtable_leads_deduped": len(air_by_id),
        "industries_learned": len(industry_stats),
        "cities_learned": len(city_stats),
        "pairs_learned": len(pair_stats),
        "recommended_industry": recommended_industries[0] if recommended_industries else None,
        "recommended_city": recommended_cities[0] if recommended_cities else None,
        "exploration_share": learning["exploration_share"],
        "errors": len(errors),
        "error_details": errors,
    }

    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(learning, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LATEST.write_text(json.dumps({"state": state, "learning": learning}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
