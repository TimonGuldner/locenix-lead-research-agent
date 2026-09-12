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
    'company': 'fldzzPqhSvYyWWMTb', 'lead_id': 'fld1Hub7WnDzd6xoR',
    'priority': 'fld1aZCTLGqfZegS3', 'next_action': 'fldH14W5sKsE4iFA2',
    'notes': 'fld37ikFjqYK3Zjc9', 'intent_source': 'fldugCRnpO7Sh4Z8F',
    'intent_url': 'fldYpBedpdGoCCWXP', 'intent_signal': 'fldzNrNZtbx59qINT',
    'intent_tier': 'flduwOJl4hk6CDnUB', 'intent_score': 'fldiHg1WlwQDLDqOx',
    'intent_detected': 'fldff5YICLecU8x82', 'intent_response_status': 'fld4434NOR0yVgdtB',
    'intent_conversation_status': 'fldczIdDCPfmt9UTW', 'intent_assigned_agent': 'fldq8Gawn0pX34oIo',
    'department': 'flddXQhaFhq1Q8G3b', 'intent_dnc': 'fldAfQGhGAUfrV7S2',
}

RELEVANCE = ('google business','google business profile','google unternehmensprofil','google maps','local seo','lokale sichtbarkeit','local search','standort')
ACTIVE = ('suche ','gesucht','sucht ','freelancer','projekt','auftrag','ausschreibung','hiring','hire ','looking for','seeking','seeks ','needed','need ','job ','support needed','hilfe gesucht')
INFORMATIONAL = ('what is','was ist','guide','leitfaden','learn how','how to','tutorial','definition','lexikon','course','kurs','ausbildung','webinar','definitive guide','comprehensive guide')
REJECT = ('agentur bietet','dienstleistung anbieten')
EXCLUDED = ('rechtsanwalt','anwalt','kanzlei','notar','notariat','patentanwalt','steuerberater','steuerberatung','wirtschaftspruefer','wirtschaftsprüfer')
JOB_MARKETPLACES = ('upwork.com','freelancermap.de','freelancer.com','peopleperhour.com','contra.com','malt.de','malt.com')


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
    out, offset = [], None
    while True:
        qs = {'pageSize': 100}
        if offset:
            qs['offset'] = offset
        data = api('GET', f'https://api.airtable.com/v0/{base}/{table}?' + urllib.parse.urlencode(qs), token)
        out.extend(data.get('records', []))
        offset = data.get('offset')
        if not offset:
            return out


def marketplace(source: str) -> bool:
    source = (source or '').lower().removeprefix('www.')
    return any(source == d or source.endswith('.' + d) for d in JOB_MARKETPLACES)


def classify(item: dict) -> tuple[str, int, str]:
    text = f"{item.get('title','')} {item.get('snippet','')}".lower()
    source = (item.get('source') or '').lower()
    if source in {'bing.com','google.com','duckduckgo.com'}:
        return 'REJECT', 0, 'Search-engine result URL is not an opportunity source.'
    if any(x in text for x in EXCLUDED) or any(x in text for x in REJECT):
        return 'REJECT', 0, 'Excluded profession or provider-side content.'
    relevant_hits = sum(1 for x in RELEVANCE if x in text)
    active_hits = sum(1 for x in ACTIVE if x in text)
    info_hits = sum(1 for x in INFORMATIONAL if x in text)
    is_marketplace = marketplace(source)
    if relevant_hits == 0:
        return 'REJECT', 0, 'No LOCENIX-relevant local visibility evidence.'
    if info_hits and active_hits == 0:
        return 'REJECT', 0, 'Informational content without an active buyer/problem signal.'
    if active_hits >= 1 and relevant_hits >= 1:
        return 'HOT', 100, 'Active demand/search language plus LOCENIX-relevant local visibility terms.'
    if is_marketplace and relevant_hits >= 1:
        return 'WARM', 95, 'Relevant marketplace/project listing; explicit demand language is weak in extracted text.'
    if relevant_hits >= 2:
        return 'WATCH', 75, 'Relevant topic signal, but active purchase intent is not sufficiently evidenced.'
    return 'REJECT', 0, 'Insufficient purchase/problem intent evidence.'


def main() -> None:
    task = load(TASK, {})
    scout = load(SCOUT, {'signals': []})
    previous = load(OUT, {'items': []})
    done = {x.get('signal_id'): x for x in previous.get('items', []) if x.get('signal_id')}
    current_ids = {x.get('signal_id') for x in scout.get('signals', []) if x.get('signal_id')}
    now = datetime.now(timezone.utc).isoformat()

    base = os.environ.get('AIRTABLE_BASE_ID') or task.get('airtable_base_id')
    table = os.environ.get('AIRTABLE_TABLE_ID') or task.get('airtable_table_id')
    token = os.environ.get('AIRTABLE_TOKEN')
    existing_records, existing_by_url, existing_by_lead = [], {}, {}
    if token and base and table:
        try:
            existing_records = list_existing(base, table, token)
            for r in existing_records:
                fields = r.get('fields') or {}
                if fields.get(FIELD['intent_url']):
                    existing_by_url[fields[FIELD['intent_url']]] = r
                if fields.get(FIELD['lead_id']):
                    existing_by_lead[fields[FIELD['lead_id']]] = r
        except Exception:
            existing_records = []

    created = updated = sync_errors = quarantined = 0

    # Quarantine browser-fallback records removed by the hardened scout.
    for sid, old in list(done.items()):
        if sid in current_ids or not old.get('browser_fallback'):
            continue
        old['tier'] = 'REJECT'
        old['intent_score'] = 0
        old['qa_reason'] = 'Quarantined after hardened browser-fallback validation; prior search result was not a verified opportunity.'
        old['qa_at'] = now
        old['quarantined'] = True
        rid = old.get('airtable_record_id') or (existing_by_lead.get(sid) or {}).get('id')
        if rid and token and base and table:
            try:
                api('PATCH', f'https://api.airtable.com/v0/{base}/{table}/{rid}', token, {'fields': {
                    FIELD['priority']: 0,
                    FIELD['next_action']: 'NONE',
                    FIELD['notes']: 'Agent 15B: Quarantined false positive from earlier browser-search parsing. Do not contact.',
                    FIELD['intent_tier']: 'REJECT',
                    FIELD['intent_score']: 0,
                    FIELD['intent_response_status']: 'SKIPPED',
                    FIELD['intent_conversation_status']: 'DO_NOT_CONTACT',
                    FIELD['intent_assigned_agent']: 'AGENT_15B_INTENT_QA',
                    FIELD['intent_dnc']: True,
                }})
                updated += 1
            except Exception as exc:
                sync_errors += 1
                old['airtable_error'] = str(exc)[:500]
        quarantined += 1

    for item in scout.get('signals', []):
        sid = item.get('signal_id')
        if not sid:
            continue
        # Re-evaluate all browser fallback items every run so tightened rules take effect.
        if sid in done and not item.get('browser_fallback'):
            continue
        tier, score, reason = classify(item)
        record = {**item, 'tier': tier, 'intent_score': score, 'qa_reason': reason, 'qa_at': now, 'airtable_record_id': None}
        existing_old = done.get(sid)
        if existing_old and existing_old.get('airtable_record_id'):
            record['airtable_record_id'] = existing_old['airtable_record_id']

        if token and base and table:
            fields = {
                FIELD['company']: ('INTENT: ' + (item.get('title') or item.get('source') or sid))[:250],
                FIELD['lead_id']: sid,
                FIELD['priority']: score,
                FIELD['next_action']: 'INTENT_RESPONSE_DRAFT' if tier in {'HOT','WARM'} else ('MONITOR_INTENT' if tier == 'WATCH' else 'NONE'),
                FIELD['notes']: f"Agent 15B: {reason}",
                FIELD['intent_source']: item.get('source') or '',
                FIELD['intent_url']: item.get('source_url') or '',
                FIELD['intent_signal']: ((item.get('title') or '') + '\n' + (item.get('snippet') or '')).strip()[:10000],
                FIELD['intent_tier']: tier,
                FIELD['intent_score']: score,
                FIELD['intent_detected']: item.get('detected_at') or now,
                FIELD['intent_response_status']: 'NOT_PREPARED' if tier in {'HOT','WARM'} else 'SKIPPED',
                FIELD['intent_conversation_status']: 'NONE' if tier != 'REJECT' else 'DO_NOT_CONTACT',
                FIELD['intent_assigned_agent']: 'AGENT_15D_INTENT_RESPONSE' if tier in {'HOT','WARM'} else 'AGENT_15B_INTENT_QA',
                FIELD['department']: 'intent_opportunity_acquisition',
                FIELD['intent_dnc']: tier == 'REJECT',
            }
            try:
                existing = existing_by_url.get(item.get('source_url')) or existing_by_lead.get(sid)
                if existing:
                    rid = existing['id']
                    api('PATCH', f'https://api.airtable.com/v0/{base}/{table}/{rid}', token, {'fields': fields})
                    updated += 1
                elif tier != 'REJECT':
                    response = api('POST', f'https://api.airtable.com/v0/{base}/{table}', token, {'fields': fields})
                    rid = response.get('id')
                    created += 1
                    if rid:
                        existing_by_url[item.get('source_url')] = {'id': rid, 'fields': fields}
                        existing_by_lead[sid] = {'id': rid, 'fields': fields}
                else:
                    rid = None
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
        'airtable_created': created, 'airtable_updated': updated, 'quarantined': quarantined, 'errors': sync_errors,
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    LATEST.write_text(json.dumps({'state': state, 'qualified': [x for x in items if x.get('tier') in {'HOT','WARM'}][-25:]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
