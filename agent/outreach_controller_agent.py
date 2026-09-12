from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/outreach_controller_task.json')
QUEUE = Path('results/sales_queue.json')
LATEST = Path('results/outreach_controller_latest.json')
STATE = Path('results/outreach_controller_state.json')


def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def clean_fact(text: str | None) -> str:
    if not text:
        return ''
    s = str(text).strip().replace('Verified source data: ', '')

    m = re.search(r'(\d+) reviews vs\. comparison median (\d+); gap (\d+)', s, flags=re.I)
    if m:
        own, median, gap = m.groups()
        return (
            f'{own} Google-Bewertungen, während vergleichbare lokale Anbieter im Median bei {median} liegen; '
            f'das entspricht einer Differenz von {gap} Bewertungen'
        )

    m = re.search(r'Business observed at position (\d+) among the first (\d+) Maps results', s, flags=re.I)
    if m:
        pos, checked = m.groups()
        return f'das Profil wurde bei der geprüften Google-Maps-Suche auf Position {pos} unter den ersten {checked} Ergebnissen beobachtet'

    if 'not observed among the first' in s.lower():
        m = re.search(r'first (\d+) Maps results', s, flags=re.I)
        checked = m.group(1) if m else 'geprüften'
        return f'das Profil wurde bei der geprüften Google-Maps-Suche unter den ersten {checked} Ergebnissen nicht beobachtet'

    if 'online booking/appointment link observed on website' in s.lower():
        return 'auf der Website ist bereits eine Online-Terminbuchung vorhanden'

    if 'business website links to at least one social profile' in s.lower():
        return 'die Website ist bereits mit mindestens einem Social-Media-Profil verknüpft'

    return s.rstrip('.')


def compose(lead: dict) -> tuple[str, str]:
    city = lead.get('city') or 'deiner Region'
    proof1 = clean_fact(lead.get('proof_1'))
    proof2 = clean_fact(lead.get('proof_2'))
    weak = bool(lead.get('weak_visibility_observed'))

    subject = f'Kurzer Google-Maps-Check für {city}'

    facts = [x for x in [proof1, proof2] if x]
    if facts:
        observed = facts[0].rstrip('.') + '.'
        if len(facts) > 1 and facts[1] != facts[0]:
            observed += f' Außerdem {facts[1]}.'
    else:
        observed = 'Bei einem kurzen Check sind mir ein paar mögliche Hebel für die lokale Sichtbarkeit aufgefallen.'

    visibility_sentence = ''
    if weak and 'position' not in observed.lower() and 'nicht beobachtet' not in observed.lower():
        visibility_sentence = ' Zusätzlich deutet der aktuelle Maps-Check auf ungenutztes Sichtbarkeitspotenzial hin.'

    body = (
        'Hallo,\n\n'
        'ich habe mir euer Google-Unternehmensprofil und die lokale Sichtbarkeit kurz angesehen. '
        f'Dabei ist mir aufgefallen: {observed}{visibility_sentence}\n\n'
        f'Ich kann euch gern kostenlos einen kurzen Local Visibility Check für {city} erstellen und die wichtigsten Hebel kompakt zusammenfassen. '
        'Wenn das interessant ist, schicke ich euch die Auswertung gern zu.\n\n'
        'Viele Grüße\nTimon'
    )
    return subject, body


def airtable_update(record_id: str, fields: dict) -> None:
    token = os.environ['AIRTABLE_TOKEN']
    base_id = os.environ['AIRTABLE_BASE_ID']
    table_id = os.environ['AIRTABLE_TABLE_ID']
    url = f'https://api.airtable.com/v0/{base_id}/{table_id}/{record_id}'
    payload = json.dumps({'fields': fields, 'typecast': True}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, method='PATCH')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status >= 300:
                raise RuntimeError(f'Airtable HTTP {resp.status}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Airtable API error {e.code}: {detail}') from e


def main() -> None:
    task = load(TASK, {})
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return

    queue = load(QUEUE, [])
    eligible_decisions = set(task.get('eligible_decisions') or ['FINAL_A'])
    eligible_sync = set(task.get('eligible_sync_statuses') or ['SYNCED_CREATED', 'SYNCED_UPDATED'])
    eligible_outreach = set(task.get('eligible_outreach_statuses') or ['NOT_CONTACTED'])
    min_priority = int(task.get('min_priority_score') or 90)
    limit = int(task.get('max_drafts_per_run') or 20)

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
        if not lead.get('airtable_record_id'):
            continue
        candidates.append(lead)

    candidates.sort(key=lambda x: int(x.get('priority_score') or 0), reverse=True)
    now = datetime.now(timezone.utc).isoformat()
    results = []
    errors = []

    field_cfg = task.get('airtable_fields') or {}
    subject_field = field_cfg.get('subject', 'Outreach Subject')
    body_field = field_cfg.get('body', 'Outreach Draft')
    status_field = field_cfg.get('status', 'Draft Status')

    for lead in candidates[:limit]:
        subject, body = compose(lead)
        try:
            airtable_update(
                lead['airtable_record_id'],
                {
                    subject_field: subject,
                    body_field: body,
                    status_field: 'READY_FOR_REVIEW',
                },
            )
            results.append({
                'lead_id': lead.get('lead_id'),
                'airtable_record_id': lead.get('airtable_record_id'),
                'company_name': lead.get('company_name'),
                'subject': subject,
                'draft': body,
                'draft_status': 'READY_FOR_REVIEW',
                'send_status': 'NOT_SENT',
                'generated_at': now,
            })
        except Exception as exc:
            errors.append(f"{lead.get('lead_id')}: {exc}")

    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state = {
        'mode': 'DRAFT_AND_STORE_ONLY',
        'eligible_count': len(candidates),
        'stored_count': len(results),
        'error_count': len(errors),
        'errors': errors,
        'sent_count': 0,
        'last_run_at': now,
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
