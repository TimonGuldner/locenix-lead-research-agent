from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

QA = Path('results/intent_qa_results.json')
OUT = Path('results/intent_response_results.json')
STATE = Path('results/intent_response_state.json')
LATEST = Path('results/intent_response_latest.json')
TASK = Path('tasks/intent_department_task.json')

FIELD = {
    'intent_response_status': 'fld4434NOR0yVgdtB',
    'intent_response_draft': 'fldWlHXHqrn9g3TJt',
    'intent_assigned_agent': 'fldq8Gawn0pX34oIo',
    'next_action': 'fldH14W5sKsE4iFA2',
}


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


def draft(item: dict) -> str:
    title = (item.get('title') or '').strip()
    snippet = (item.get('snippet') or '').strip()
    source = item.get('source') or 'der Plattform'
    context = snippet if snippet else title
    context = context[:420].strip()
    return (
        f"Hallo, ich bin über deinen Beitrag auf {source} gestolpert. "
        f"Du beschreibst dort sinngemäß: {context}\n\n"
        "Das klingt nach einem Thema, bei dem Google Business Profile / lokale Sichtbarkeit direkt relevant sein kann. "
        "Wenn du möchtest, kann ich dir kostenlos einen kurzen Local Visibility Check machen und die wichtigsten Hebel kompakt zusammenfassen. "
        "Dann siehst du zuerst, ob überhaupt Potenzial da ist – ohne Verpflichtung."
    )


def main() -> None:
    task = load(TASK, {})
    qa = load(QA, {'items': []})
    previous = load(OUT, {'items': []})
    qualified = {x.get('signal_id'): x for x in qa.get('items', []) if x.get('signal_id') and x.get('tier') in {'HOT', 'WARM'}}
    done = {
        x.get('signal_id'): x for x in previous.get('items', [])
        if x.get('signal_id') in qualified
    }
    now = datetime.now(timezone.utc).isoformat()
    base = os.environ.get('AIRTABLE_BASE_ID') or task.get('airtable_base_id')
    table = os.environ.get('AIRTABLE_TABLE_ID') or task.get('airtable_table_id')
    token = os.environ.get('AIRTABLE_TOKEN')
    prepared = sync_errors = 0

    for sid, item in qualified.items():
        if sid in done:
            continue
        text = draft(item)
        row = {
            'signal_id': sid,
            'airtable_record_id': item.get('airtable_record_id'),
            'tier': item.get('tier'),
            'source': item.get('source'),
            'source_url': item.get('source_url'),
            'draft': text,
            'status': 'DRAFTED',
            'prepared_at': now,
        }
        rid = item.get('airtable_record_id')
        if rid and token and base and table:
            try:
                fields = {
                    FIELD['intent_response_status']: 'DRAFTED',
                    FIELD['intent_response_draft']: text,
                    FIELD['intent_assigned_agent']: 'AGENT_15E_INTENT_CONVERSATION',
                    FIELD['next_action']: 'REVIEW_PLATFORM_RESPONSE',
                }
                api('PATCH', f'https://api.airtable.com/v0/{base}/{table}/{rid}', token, {'fields': fields})
            except Exception as exc:
                sync_errors += 1
                row['airtable_error'] = str(exc)[:500]
        done[sid] = row
        prepared += 1

    items = list(done.values())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'generated_at': now, 'items': items}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state = {
        'agent': 'AGENT_15D_INTENT_RESPONSE',
        'last_run_at': now,
        'prepared': len(items),
        'new_prepared': prepared,
        'pending': max(0, len(qualified) - len(items)),
        'sent': sum(1 for x in items if x.get('status') == 'SENT'),
        'errors': sync_errors,
        'mode': 'DRAFT_ONLY',
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    LATEST.write_text(json.dumps({'state': state, 'latest_drafts': items[-20:]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
