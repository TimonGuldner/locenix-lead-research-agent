import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks/current_task.json"
LEADS = ROOT / "results/deterministic_leads.json"
LATEST = ROOT / "results/sales_intelligence_latest.json"

CHAIN_MARKERS = ["gmbh & co", "holding", "franchise", "filiale", "standorte deutschland", "locations germany"]
BOOKING_MARKERS = ["termin", "booking", "online buchen", "doctolib", "treatwell", "calendly", "termin buchen"]
MARKETING_MARKERS = ["instagram", "facebook", "linkedin", "newsletter", "blog", "aktuelles"]


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def domain(url):
    try:
        return urlparse(url or "").netloc.lower().replace("www.", "")
    except Exception:
        return ""


def confidence(lead):
    evidence = 0
    evidence += 1 if lead.get("google_review_count") is not None else 0
    evidence += 1 if lead.get("competitor_review_median") else 0
    evidence += 1 if lead.get("website") else 0
    evidence += 1 if lead.get("address") else 0
    evidence += 1 if lead.get("verified_signal_count", 0) >= 2 else 0
    return "HIGH" if evidence >= 5 else "MEDIUM" if evidence >= 3 else "LOW"


def contact_quality(lead):
    score = 0
    reasons = []
    if lead.get("public_business_email"):
        score += 5; reasons.append("public business email")
    if lead.get("phone"):
        score += 2; reasons.append("phone")
    if lead.get("contact_form_url"):
        score += 1; reasons.append("contact form")
    if lead.get("website"):
        score += 2; reasons.append("working website candidate")
    return min(10, score), reasons


def purchase_score(lead):
    score = 0
    reasons = []
    if lead.get("website"):
        score += 3; reasons.append("eigene Website")
    if lead.get("public_business_email") or lead.get("contact_form_url"):
        score += 2; reasons.append("öffentlicher Geschäftskontakt")
    if lead.get("digital_readiness_score", 0) >= 8:
        score += 2; reasons.append("hohe digitale Reife")
    if lead.get("google_review_count", 0) >= 20:
        score += 1; reasons.append("etablierter Betrieb")
    if lead.get("review_gap", 0) >= 50:
        score += 2; reasons.append("klarer Wettbewerbsdruck")
    return min(10, score), reasons


def is_obvious_chain(lead):
    hay = " ".join([lead.get("company_name") or "", lead.get("website") or ""]).lower()
    return any(x in hay for x in CHAIN_MARKERS)


def process():
    task = load(TASK, {})
    leads = load(LEADS, [])
    policy = task.get("qualification_policy", {})
    max_bucket = int(policy.get("max_a_leads_per_city_category", 8))
    bucket_counts = Counter()
    accepted_a = 0
    rejected_chain = 0

    # Existing list order reflects score order; rank is the observed Maps search position captured during discovery when available.
    for lead in leads:
        pscore, preasons = purchase_score(lead)
        cscore, creasons = contact_quality(lead)
        conf = confidence(lead)
        chain = is_obvious_chain(lead)
        bucket = (lead.get("city"), lead.get("industry"))

        lead["purchase_likelihood_score"] = pscore
        lead["purchase_likelihood_reasons"] = preasons
        lead["contact_quality_score"] = cscore
        lead["contact_quality_reasons"] = creasons
        lead["signal_confidence"] = conf
        lead["obvious_chain_candidate"] = chain
        lead["why_now"] = (
            f"Lokaler Wettbewerbsdruck: Review-Gap {lead.get('review_gap', 0)}; "
            f"Kaufwahrscheinlichkeit {pscore}/10; Kontaktqualität {cscore}/10."
        )
        lead["airtable_ready"] = False

        if chain and policy.get("reject_obvious_chains", True):
            lead["lead_class"] = "REJECT"
            lead["qa_gate"] = "CHAIN_REVIEW"
            rejected_chain += 1
            continue

        if lead.get("lead_class") == "A_LEAD":
            if pscore < 6 or cscore < 4 or conf == "LOW":
                lead["lead_class"] = "B_LEAD"
                lead["qa_gate"] = "REVIEW"
            elif bucket_counts[bucket] >= max_bucket:
                lead["lead_class"] = "B_LEAD"
                lead["qa_gate"] = "DIVERSITY_LIMIT"
            else:
                bucket_counts[bucket] += 1
                accepted_a += 1
                lead["qa_gate"] = "PASS_PRE_AIRTABLE_QA"

        # Never auto-sync: final ChatGPT QA must verify GBP detail claims first.
        lead["airtable_ready"] = False
        lead["final_qa_required"] = True

    LEADS.write_text(json.dumps(leads, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "processed": len(leads),
        "a_leads_after_sales_intelligence": accepted_a,
        "chain_candidates_rejected": rejected_chain,
        "airtable_auto_sync": False,
        "final_qa_required": True,
    }
    LATEST.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    process()
