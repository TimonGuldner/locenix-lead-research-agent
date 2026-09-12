from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/outreach_task.json')
QUEUE = Path('results/sales_queue.json')
OUT = Path('results/outreach_drafts.json')
LATEST = Path('results/outreach_drafts_latest.json')
STATE = Path('results/outreach_draft_state.json')


def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def compact_proof(lead: dict) -> str:
    proofs = [lead.get('proof_1'), lead.get('proof_2'), lead.get('proof_3')]
    clean = [p.strip() for p in proofs if isinstance(p, str) and p.strip()]
    if not clean:
        return ''
    return clean[0]


def human_fact(proof: str) -> str:
    text = proof.replace('Verified source data: ', '').strip().rstrip('.')
    m = re.fullmatch(r'(\d+) reviews vs\. comparison median (\d+); gap (\d+)', text, flags=re.I)
    if m:
        own, median, gap = m.groups()
        return f"euer Profil hat aktuell {own} Bewertungen; der Vergleichswert der geprüften Wettbewerber liegt bei {median} Bewertungen"
    return text


def draft_message(lead: dict, task: dict) -> str:
    proof = compact_proof(lead)
    city = lead.get('city') or 'eurer Region'
    if proof:
        fact = human_fact(proof)
        return (
            f"Hi, ich habe mir euer Google-Maps-Profil kurz angesehen. Dabei ist mir aufgefallen, dass {fact}. "
            f"Da könnte noch Potenzial für mehr lokale Sichtbarkeit in {city} liegen. "
            f"Wenn du möchtest, erstelle ich dir kostenlos einen kurzen Local Visibility Check und schicke dir die wichtigsten Hebel."
        )
    return (
        f"Hi, ich habe mir euer Google-Maps-Profil kurz angesehen und ein paar mögliche Hebel für die lokale Sichtbarkeit in {city} gefunden. "
        f"Wenn du möchtest, erstelle ich dir kostenlos einen kurzen Local Visibility Check und schicke dir die wichtigsten Punkte."
    )


def main() -> None:
    task = load(TASK, {})
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return

    queue = load(QUEUE, [])
    previous = load(OUT, [])
    previous_by_id = {x.get('lead_id'): x for x in previous if x.get('lead_id')}

    eligible_decisions = set(task.get('eligible_decisions') or ['FINAL_A'])
    eligible_sync = set(task.get('eligible_sync_statuses') or ['SYNCED_CREATED', 'SYNCED_UPDATED'])
    eligible_outreach = set(task.get('eligible_outreach_statuses') or ['NOT_CONTACTED'])
    min_priority = int(task.get('min_priority_score') or 90)
    limit = int(task.get('max_drafts_per_run') or 10)

    candidates = []
    for lead in queue:
        if lead.get('deep_qa_decision') not in eligible_decisions:
            continue
        if lead.get('airtable_sync_status') not in eligible_sync:
            continue
        if lead.get('outreach_status') not in eligible_outreach:
            continue
        if int(lead.get('priority_score') or 0) < min_priority:
            continue
        candidates.append(lead)

    candidates.sort(key=lambda x: int(x.get('priority_score') or 0), reverse=True)
    now = datetime.now(timezone.utc).isoformat()
    created_or_refreshed = []

    for lead in candidates[:limit]:
        lead_id = lead.get('lead_id')
        if not lead_id:
            continue
        item = {
            'lead_id': lead_id,
            'airtable_record_id': lead.get('airtable_record_id'),
            'company_name': lead.get('company_name'),
            'industry': lead.get('industry'),
            'city': lead.get('city'),
            'website': lead.get('website'),
            'google_maps_url': lead.get('google_maps_url'),
            'priority_score': lead.get('priority_score'),
            'deep_qa_decision': lead.get('deep_qa_decision'),
            'verified_basis': compact_proof(lead),
            'draft_channel': 'LINKEDIN_OR_MANUAL_LEGAL_CHANNEL',
            'draft_message': draft_message(lead, task),
            'draft_status': 'READY_FOR_HUMAN_REVIEW',
            'send_status': 'NOT_SENT',
            'automatic_send_allowed': False,
            'generated_at': now,
            'agent': 'LOCENIX_OUTREACH_DRAFT_AGENT_V1',
            'compliance_note': 'Draft only. No message sent, no contact form submitted, no login performed.'
        }
        previous_by_id[lead_id] = item
        created_or_refreshed.append(item)

    merged = list(previous_by_id.values())
    merged.sort(key=lambda x: int(x.get('priority_score') or 0), reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    LATEST.write_text(json.dumps(created_or_refreshed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state = {
        'mode': 'DRAFT_ONLY',
        'eligible_count': len(candidates),
        'drafts_total': len(merged),
        'drafts_this_run': len(created_or_refreshed),
        'sent_count': 0,
        'last_run_at': now
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
