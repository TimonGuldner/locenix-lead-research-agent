from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
QA_RESULTS = ROOT / "results" / "deep_qa_results.json"
QA_STATE = ROOT / "results" / "deep_qa_state.json"
VIS_RESULTS = ROOT / "results" / "visibility_results.json"
VIS_STATE = ROOT / "results" / "visibility_state.json"
STATE = ROOT / "results" / "hard_email_gate_state.json"
LATEST = ROOT / "results" / "hard_email_gate_latest.json"

AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appuPKnVyLsbWbxMR").strip()
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID", "tblF4ghkYFzkeQwsT").strip()
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN", "").strip()
AIRTABLE_API = "https://api.airtable.com/v0"
ALLOWED_EMAIL_STATUSES = {"PUBLICLY_OBSERVED", "VERIFIED"}
QUALIFIED_DECISIONS = {"FINAL_A", "FINAL_B"}


def load(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def email_ready(item: dict[str, Any]) -> bool:
    return bool(
        str(item.get("email") or "").strip()
        and str(item.get("email_verification_status") or "").upper() in ALLOWED_EMAIL_STATUSES
    )


def airtable_request(method: str, path: str, payload: dict | None = None) -> dict:
    if not AIRTABLE_TOKEN:
        raise RuntimeError("AIRTABLE_TOKEN missing")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{AIRTABLE_API}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {AIRTABLE_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "locenix-hard-email-gate",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Airtable HTTP {exc.code}: {detail}") from exc


def find_airtable_record(lead_id: str) -> dict | None:
    formula = "{Lead ID}=" + json.dumps(str(lead_id))
    query = urllib.parse.urlencode({"filterByFormula": formula, "maxRecords": 1})
    records = airtable_request("GET", f"?{query}").get("records") or []
    return records[0] if records else None


def quarantine_airtable(excluded: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {"matched": 0, "updated": 0, "already_safe": 0, "errors": []}
    if not AIRTABLE_TOKEN:
        stats["errors"].append("AIRTABLE_TOKEN missing; legacy Airtable quarantine was not checked")
        return stats

    for qa in excluded:
        lead_id = str(qa.get("lead_id") or "").strip()
        if not lead_id:
            continue
        try:
            record = find_airtable_record(lead_id)
            if not record:
                continue
            stats["matched"] += 1
            fields = record.get("fields") or {}
            current_status = str(fields.get("Email Send Status") or "")
            already_safe = (
                not bool(fields.get("Airtable Ready"))
                and not bool(fields.get("Email Send Approved"))
                and current_status not in {"READY_TO_SEND", "SENT"}
            )
            if current_status == "SENT":
                # Never rewrite historical provider-confirmed sends. Flag for review instead.
                stats["errors"].append(f"{lead_id}: historical SENT record has no currently verified public email; not mutated")
                continue
            if already_safe:
                stats["already_safe"] += 1
                continue

            note = "Hard email gate: excluded from Maps downstream/outbound until a researched public business email is verified."
            old_notes = str(fields.get("Notes") or "").strip()
            notes = old_notes if note in old_notes else (old_notes + "\n" + note).strip()
            patch = {
                "Airtable Ready": False,
                "Email Send Approved": False,
                "Email Send Status": "NOT_READY",
                "Next Action": "EMAIL_MISSING_RESEARCH_REQUIRED",
                "Outreach Status": "NOT_CONTACTED",
                "Notes": notes,
            }
            airtable_request("PATCH", f"/{record['id']}", {"fields": patch, "typecast": True})
            stats["updated"] += 1
        except Exception as exc:
            stats["errors"].append(f"{lead_id}: {exc}")
    return stats


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    qa_results = load(QA_RESULTS, [])
    qa_state = load(QA_STATE, {})
    visibility = load(VIS_RESULTS, [])
    visibility_state = load(VIS_STATE, {})

    qualified = [x for x in qa_results if x.get("deep_qa_decision") in QUALIFIED_DECISIONS]
    excluded = [x for x in qualified if not email_ready(x)]
    allowed_ids = {
        str(x.get("lead_id"))
        for x in qualified
        if email_ready(x) and x.get("lead_id")
    }

    # Visibility is a downstream department. Purge any historical row that no longer
    # satisfies the public-email gate, not only newly generated rows.
    filtered_visibility = [
        row for row in visibility
        if str(row.get("lead_id") or "") in allowed_ids
        and str(row.get("email") or "").strip()
        and str(row.get("email_verification_status") or "").upper() in ALLOWED_EMAIL_STATUSES
    ]
    purged_visibility = len(visibility) - len(filtered_visibility)
    if filtered_visibility != visibility:
        save(VIS_RESULTS, filtered_visibility)

    processed_ids = [
        lead_id for lead_id in (visibility_state.get("processed_ids") or [])
        if str(lead_id) in allowed_ids
    ]
    visibility_state.update({
        "processed_ids": sorted(set(processed_ids)),
        "processed_count": len(set(processed_ids)),
        "weak_visibility_total": sum(1 for x in filtered_visibility if x.get("weak_visibility_observed")),
        "email_gate_rejected_count": len(excluded),
        "downstream_email_gate_violations": 0,
        "legacy_visibility_rows_purged": int(visibility_state.get("legacy_visibility_rows_purged") or 0) + max(0, purged_visibility),
        "email_gate_rule": "NO_RESEARCHED_PUBLIC_EMAIL_NO_VISIBILITY_ANALYSIS",
    })
    save(VIS_STATE, visibility_state)

    # Keep QA truth explicit: these may have scored well, but they are not downstream-eligible.
    qa_state.update({
        "email_gate_qualified_total": len(qualified) - len(excluded),
        "email_missing_excluded_total": len(excluded),
        "downstream_eligible_total": len(allowed_ids),
        "email_gate_rule": "FINAL_A_OR_B_REQUIRES_RESEARCHED_PUBLIC_EMAIL_FOR_ANY_DOWNSTREAM_HANDOFF",
    })
    save(QA_STATE, qa_state)

    airtable = quarantine_airtable(excluded)
    hard_violations = sum(
        1 for row in filtered_visibility
        if not (
            str(row.get("email") or "").strip()
            and str(row.get("email_verification_status") or "").upper() in ALLOWED_EMAIL_STATUSES
        )
    )

    state = {
        "agent": "LOCENIX_HARD_EMAIL_GATE_ENFORCER",
        "last_run_at": now,
        "qualified_qa_total": len(qualified),
        "downstream_email_qualified_total": len(allowed_ids),
        "email_missing_excluded_total": len(excluded),
        "visibility_rows_before": len(visibility),
        "visibility_rows_after": len(filtered_visibility),
        "visibility_rows_purged_this_run": max(0, purged_visibility),
        "downstream_email_gate_violations": hard_violations,
        "airtable_quarantine": airtable,
        "status": "BLOCKED" if hard_violations or airtable["errors"] else "OK",
        "rule": "NO VERIFIED PUBLIC BUSINESS EMAIL = NO VISIBILITY, SALES QUEUE OR OUTBOUND-READY HANDOFF",
    }
    save(STATE, state)
    save(LATEST, {"state": state, "excluded_lead_ids": [x.get("lead_id") for x in excluded]})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
