import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "tasks" / "sales_queue_task.json"
QA_PATH = ROOT / "results" / "deep_qa_results.json"
VIS_PATH = ROOT / "results" / "visibility_results.json"
RESULTS_PATH = ROOT / "results" / "sales_queue.json"
STATE_PATH = ROOT / "results" / "sales_queue_state.json"
LATEST_PATH = ROOT / "results" / "sales_queue_latest.json"


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def priority_for(qa, vis):
    qa_score = int(qa.get("deep_qa_score") or 0)
    score = qa_score
    if qa.get("deep_qa_decision") == "FINAL_A":
        score += 6
    elif qa.get("deep_qa_decision") == "FINAL_B":
        score += 2
    if qa.get("airtable_ready"):
        score += 3
    if vis:
        score += min(8, int(vis.get("visibility_opportunity_score") or 0))
        if vis.get("weak_visibility_observed"):
            score += 3
    if qa.get("primary_sales_angle_verified"):
        score += 2
    if qa.get("deep_qa_blockers"):
        score -= 25
    return max(0, min(100, score))


def build_why_now(qa, vis):
    reasons = []
    for proof in qa.get("proof", [])[:2]:
        if proof:
            reasons.append(proof)
    if vis and vis.get("weak_visibility_observed"):
        evidence = vis.get("visibility_evidence") or []
        if evidence:
            reasons.append(evidence[0])
        else:
            reasons.append("Weak visibility was observed in at least one current Maps search test.")
    return reasons[:3]


def next_action(priority, qa, vis):
    if qa.get("deep_qa_decision") == "FINAL_A" and priority >= 90:
        return "PRIORITY_SALES_REVIEW"
    if qa.get("deep_qa_decision") in {"FINAL_A", "FINAL_B"} and priority >= 82:
        return "SALES_REVIEW"
    if not vis:
        return "WAIT_FOR_VISIBILITY_CHECK"
    return "HOLD_OR_MANUAL_REVIEW"


def main():
    task = load_json(TASK_PATH, {})
    if not task.get("enabled", False):
        print("Sales queue agent disabled")
        return

    qa_results = load_json(QA_PATH, [])
    visibility = load_json(VIS_PATH, [])
    vis_by_id = {x.get("lead_id"): x for x in visibility if x.get("lead_id")}
    decisions = set(task.get("eligible_decisions", ["FINAL_A", "FINAL_B"]))
    min_priority = int(task.get("min_priority", 80))

    queue = []
    for qa in qa_results:
        if qa.get("deep_qa_decision") not in decisions:
            continue
        lead_id = qa.get("lead_id")
        vis = vis_by_id.get(lead_id)
        priority = priority_for(qa, vis)
        why_now = build_why_now(qa, vis)
        action = next_action(priority, qa, vis)
        proofs = []
        for item in qa.get("proof", []):
            if item and item not in proofs:
                proofs.append(item)
        if vis:
            for item in vis.get("visibility_evidence", []):
                if item and item not in proofs:
                    proofs.append(item)

        airtable_ready = bool(
            qa.get("airtable_ready")
            and qa.get("deep_qa_decision") == "FINAL_A"
            and priority >= int(task.get("airtable_ready_min_priority", 88))
            and qa.get("primary_sales_angle_verified")
        )

        queue.append({
            "lead_id": lead_id,
            "company_name": qa.get("company_name"),
            "industry": qa.get("industry"),
            "city": qa.get("city"),
            "website": qa.get("website"),
            "google_maps_url": qa.get("google_maps_url"),
            "deep_qa_decision": qa.get("deep_qa_decision"),
            "deep_qa_score": qa.get("deep_qa_score"),
            "visibility_checked": bool(vis),
            "weak_visibility_observed": vis.get("weak_visibility_observed") if vis else None,
            "visibility_opportunity_score": vis.get("visibility_opportunity_score") if vis else None,
            "priority_score": priority,
            "sales_tier": "A" if priority >= 90 else "B" if priority >= 82 else "C",
            "primary_pitch": qa.get("primary_sales_angle_verified"),
            "why_now": why_now,
            "proof_1": proofs[0] if len(proofs) > 0 else None,
            "proof_2": proofs[1] if len(proofs) > 1 else None,
            "proof_3": proofs[2] if len(proofs) > 2 else None,
            "next_action": action,
            "airtable_ready": airtable_ready,
            "airtable_sync_status": "READY_NOT_SYNCED" if airtable_ready else "NOT_READY",
            "outreach_status": "NOT_CONTACTED",
            "queue_date": date.today().isoformat(),
            "queue_agent": "LOCENIX_SALES_QUEUE_AGENT_V1",
            "compliance_note": "Research and prioritization only. No automatic outreach."
        })

    queue.sort(key=lambda x: int(x.get("priority_score") or 0), reverse=True)
    if task.get("keep_only_min_priority", False):
        queue = [x for x in queue if int(x.get("priority_score") or 0) >= min_priority]

    state = {
        "queue_count": len(queue),
        "airtable_ready_count": sum(1 for x in queue if x.get("airtable_ready")),
        "priority_a_count": sum(1 for x in queue if x.get("sales_tier") == "A"),
        "priority_b_count": sum(1 for x in queue if x.get("sales_tier") == "B"),
        "visibility_pending_count": sum(1 for x in queue if not x.get("visibility_checked")),
        "last_run_date": date.today().isoformat()
    }
    save_json(RESULTS_PATH, queue)
    save_json(STATE_PATH, state)
    save_json(LATEST_PATH, {"top": queue[: int(task.get("latest_top_n", 25))], "state": state})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
