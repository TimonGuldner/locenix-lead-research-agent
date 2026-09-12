from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCOUT = Path('results/intent_scout_results.json')
OUT = Path('results/intent_qa_results.json')
STATE = Path('results/intent_qa_state.json')
LATEST = Path('results/intent_qa_latest.json')
TASK = Path('tasks/intent_department_task.json')

FIELD = {
    'company': 'fldzzPqhSvYyWWMTb',
    'lead_id': 'fld1Hub7WnDzd6xoR',
    'priority': 'fld1aZCTLGqfZegS3',
    'next_action': 'fldH14W5sKsE4iFA2',
    'notes': 'fld37ikFjqYK3Zjc9',
    'intent_source': 'fldugCRnpO7Sh4Z8F',
    'intent_url': 'fldYpBedpdGoCCWXP',
    'intent_signal': 'fldzNrNZtbx59qINT',
    'intent_tier': 'flduwOJl4hk6CDnUB',
    'intent_score': 'fldiHg1WlwQDLDqOx',
    'intent_detected': 'fldff5YICLecU8x82',
    'intent_response_status': 'fld4434NOR0yVgdtB',
    'intent_conversation_status': 'fldczIdDCPfmt9UTW',
    'intent_assigned_agent': 'fldq8Gawn0pX34oIo',
    'department': 'flddXQhaFhq1Q8G3b',
    'intent_dnc': 'fldAfQGhGAUfrV7S2',
}

HOT = ('suche', 'gesucht', 'auftrag', 'projekt', 'freelancer', 'angebot', 'ausschreibung', 'hiring', 'stellenangebot')
WARM = ('hilfe', 'problem', 'ranking', 'sichtbarkeit', 'optimierung', 'google maps', 'google business', 'unternehmensprofil', 'local seo')
REJECT = ('kurs', 'seminar', 'lexikon', 'definition', 'agentur bietet', 'dienstleistung anbieten')
EXCLUDED = ('rechtsanwalt', 'anwalt', 'kanzlei', 'notar', 'notariat', 'patentanwalt', 'steuerberater', 'steuerberatung', 'wirtschaftspruefer', 'wirtschaftsprüfer')


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def api(method: str, url: str, token: str, data=None):
    body = None if data is None else json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode('utf-8')
        return json.loads(raw) if raw else {}


def list_existing(base: str, table: str, token: str) -> list[dict]:
    out = []
    offset = None
    while True:
        qs = {'pageSize': 100}
        if offset:
            qs['offset'] = offset
        url = f'https://api.airtable.com/v0/{base}/{table}?' + urllib.parse.urlencode(qs)
        data = api('GET', url, token)
        out.extend(data.get('records', []))
        offset = data.get('offset')
        if not offset:
            return out


def classify(item: dict) -> tuple[str, int, str]:
    text = f"{item.get('title','')} {item.get('snippet','')}".lower()
    if any(x in text for x in EXCLUDED) or any(x in text for x in REJECT):
        return 'REJECT', 0, 'Excluded profession or low-fit informational/provider content.'
    hot_hits = sum(1 for x in HOT if x in text)
    warm_hits = sum(1 for x in WARM if x in text)
    if hot_hits >= 1 and warm_hits >= 1:
        return 'HOT', 100, 'Active project/search signal plus LOCENIX-relevant local visibility terms.'
    if warm_hits >= 2:
        return 'WARM', 95, 'Clear LOCENIX-relevant problem/optimization signal without explicit purchase language.'
    if warm_hits >= 1:
        return 'WATCH', 75, 'Relevant topic signal but purchase intent is not yet strong enough.'
    return 'REJECT', 0, 'No sufficient LOCENIX intent evidence.'


def main() -> None:
    task = load(TASK, {})
    scout = load(SCOUT, {'signals': []})
    previous = load(OUT, {'items': []})
    done = {x.get('signal_id'): x for x in previous.get('items', []) if x.get('signal_id')}
    now = datetime.now(timezone.utc).isoformat()

    base = os.environ.get('AIRTABLE_BASE_ID') or task.get('airtable_base_id')
    table = os.environ.get('AIRTABLE_TABLE_ID') or task.get('airtable_table_id')
    token = os.environ.get('AIRTABLE_TOKEN')
    existing_records = []
    existing_by_url = {}
    if token and base and table:
        try:
            existing_records = list_existing(base, table, token)
            for r in existing_records:
                u = (r.get('fields') or {}).get(FIELD['intent_url'])
                if u:
                    existing_by_url[u] = r
        except Exception:
            existing_records = []

    created = updated = sync_errors = 0
    for item in scout.get('signals', []):
        sid = item.get('signal_id')
        if not sid or sid in done:
            continue
        tier, score, reason = classify(item)
        record = {
            **item,
            'tier': tier,
            'intent_score': score,
            'qa_reason': reason,
            'qa_at': now,
            'airtable_record_id': None,
        }
        if tier != 'REJECT' and token and base and table:
            fields = {
                FIELD['company']: ('INTENT: ' + (item.get('title') or item.get('source') or sid))[:250],
                FIELD['lead_id']: sid,
                FIELD['priority']: score,
                FIELD['next_action']: 'INTENT_RESPONSE_DRAFT' if tier in {'HOT','WARM'} else 'MONITOR_INTENT',
                FIELD['notes']: f"Agent 15B: {reason}",
                FIELD['intent_source']: item.get('source') or '',
                FIELD['intent_url']: item.get('source_url') or '',
                FIELD['intent_signal']: ((item.get('title') or '') + '\n' + (item.get('snippet') or '')).strip()[:10000],
                FIELD['intent_tier']: tier,
                FIELD['intent_score']: score,
                FIELD['intent_detected']: item.get('detected_at') or now,
                FIELD['intent_response_status']: 'NOT_PREPARED',
                FIELD['intent_conversation_status']: 'NONE',
                FIELD['intent_assigned_agent']: 'AGENT_15D_INTENT_RESPONSE' if tier in {'HOT','WARM'} else 'AGENT_15A_INTENT_SCOUT',
                FIELD['department']: 'intent_opportunity_acquisition',
                FIELD['intent_dnc']: False,
            }
            try:
                existing = existing_by_url.get(item.get('source_url'))
                if existing:
                    rid = existing['id']
                    api('PATCH', f'https://api.airtable.com/v0/{base}/{table}/{rid}', token, {'fields': fields})
                    updated += 1
                else:
                    response = api('POST', f'https://api.airtable.com/v0/{base}/{table}', token, {'fields': fields})
                    rid = response.get('id')
                    created += 1
                    if rid:
                        existing_by_url[item.get('source_url')] = {'id': rid, 'fields': fields}
                record['airtable_record_id'] = rid
            except Exception as exc:
                sync_errors += 1
                record['airtable_error'] = str(exc)[:500]
        done[sid] = record

    items = list(done.values())
    counts = {k: sum(1 for x in items if x.get('tier') == k) for k in ('HOT','WARM','WATCH','REJECT')}
    pending = sum(1 for x in scout.get('signals', []) if x.get('signal_id') not in done)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'generated_at': now, 'items': items}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state = {
        'agent': 'AGENT_15B_INTENT_QA', 'last_run_at': now, 'pending': pending,
        'hot': counts['HOT'], 'warm': counts['WARM'], 'watch': counts['WATCH'], 'rejected': counts['REJECT'],
        'airtable_created': created, 'airtable_updated': updated, 'errors': sync_errors,
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    LATEST.write_text(json.dumps({'state': state, 'qualified': [x for x in items if x.get('tier') in {'HOT','WARM'}][-25:]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
