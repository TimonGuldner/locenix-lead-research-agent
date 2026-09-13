from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TASK = Path('tasks/email_sender_task.json')
STATE = Path('results/email_sender_state.json')
LATEST = Path('results/email_sender_latest.json')

AIRTABLE_BASE_ID = os.getenv('AIRTABLE_BASE_ID', 'appuPKnVyLsbWbxMR').strip()
AIRTABLE_TABLE_ID = os.getenv('AIRTABLE_TABLE_ID', 'tblF4ghkYFzkeQwsT').strip()
AIRTABLE_TOKEN = os.getenv('AIRTABLE_TOKEN', '').strip()
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '').strip()
EMAIL_FROM = os.getenv('EMAIL_FROM', '').strip()
EMAIL_REPLY_TO = os.getenv('EMAIL_REPLY_TO', '').strip()

EXCLUDED_PROFESSION_TERMS = (
    'rechtsanwalt', 'rechtsanwälte', 'rechtsanwaelte', 'anwalt', 'anwältin', 'anwaeltin',
    'anwaltskanzlei', 'kanzlei', 'notar', 'notariat', 'patentanwalt',
    'steuerberater', 'steuerberatung', 'wirtschaftsprüfer', 'wirtschaftspruefer'
)


def load_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def excluded_profession(fields: dict[str, Any]) -> bool:
    haystack = ' '.join(str(fields.get(k) or '') for k in ('Company', 'Industry', 'Website')).lower()
    return any(term in haystack for term in EXCLUDED_PROFESSION_TERMS)


def request_json(url: str, method: str = 'GET', headers: dict[str, str] | None = None, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'HTTP {exc.code}: {detail}') from exc


def airtable_list(max_records: int = 100) -> list[dict[str, Any]]:
    if not AIRTABLE_TOKEN:
        raise RuntimeError('AIRTABLE_TOKEN missing')
    records: list[dict[str, Any]] = []
    offset = ''
    while len(records) < max_records:
        params = {'pageSize': '100'}
        if offset:
            params['offset'] = offset
        url = f'https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}?{urllib.parse.urlencode(params)}'
        payload = request_json(url, headers={'Authorization': f'Bearer {AIRTABLE_TOKEN}', 'User-Agent': 'locenix-email-sender'})
        records.extend(payload.get('records') or [])
        offset = str(payload.get('offset') or '')
        if not offset:
            break
    return records[:max_records]


def airtable_update(record_id: str, fields: dict[str, Any]) -> None:
    url = f'https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}'
    request_json(
        url,
        method='PATCH',
        headers={
            'Authorization': f'Bearer {AIRTABLE_TOKEN}',
            'Content-Type': 'application/json',
            'User-Agent': 'locenix-email-sender',
        },
        body={'fields': fields, 'typecast': True},
    )


def send_resend(to_email: str, subject: str, text: str) -> str:
    if not RESEND_API_KEY:
        raise RuntimeError('RESEND_API_KEY missing')
    if not EMAIL_FROM:
        raise RuntimeError('EMAIL_FROM missing')
    payload: dict[str, Any] = {'from': EMAIL_FROM, 'to': [to_email], 'subject': subject, 'text': text}
    if EMAIL_REPLY_TO:
        payload['reply_to'] = [EMAIL_REPLY_TO]
    result = request_json(
        'https://api.resend.com/emails', method='POST',
        headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json', 'User-Agent': 'locenix-email-sender'},
        body=payload,
    )
    message_id = str(result.get('id') or '').strip()
    if not message_id:
        raise RuntimeError(f'Resend returned no message id: {result}')
    return message_id


def sent_today_from_records(records: list[dict[str, Any]], berlin_day: str) -> int:
    count = 0
    for rec in records:
        f = rec.get('fields') or {}
        if str(f.get('Email Send Status') or '') != 'SENT':
            continue
        raw = str(f.get('Email Sent At') or '').strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.astimezone(ZoneInfo('Europe/Berlin')).date().isoformat() == berlin_day:
                count += 1
        except Exception:
            continue
    return count


def main() -> None:
    cfg = load_json(TASK, {})
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    berlin_day = now_dt.astimezone(ZoneInfo('Europe/Berlin')).date().isoformat()
    dry_run = os.getenv('EMAIL_DRY_RUN', 'true').strip().lower() not in {'0', 'false', 'no'}
    if not cfg.get('enabled', False):
        print(json.dumps({'status': 'disabled'})); return

    allowed_bases = set(cfg.get('allowed_legal_bases') or [])
    max_per_run = int(cfg.get('max_emails_per_run') or 5)
    daily_target = int(cfg.get('daily_target') or max_per_run)
    required_send_status = str(cfg.get('required_send_status') or 'READY_TO_SEND')
    required_draft_status = str(cfg.get('required_draft_status') or 'APPROVED')

    records = airtable_list(int(cfg.get('scan_limit') or 200))
    sent_today_before = sent_today_from_records(records, berlin_day)
    remaining_daily_capacity = max(0, daily_target - sent_today_before)
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for rec in records:
        f = rec.get('fields') or {}
        reasons = []
        if excluded_profession(f): reasons.append('EXCLUDED_PROFESSION')
        if not f.get('Email'): reasons.append('NO_EMAIL')
        if not f.get('Outreach Subject') or not f.get('Outreach Draft'): reasons.append('NO_DRAFT')
        if str(f.get('Draft Status') or '') != required_draft_status: reasons.append('DRAFT_NOT_READY')
        if not bool(f.get('Email Send Approved')): reasons.append('NOT_APPROVED')
        legal_basis = str(f.get('Email Legal Basis') or '')
        if legal_basis not in allowed_bases: reasons.append('LEGAL_BASIS_NOT_ALLOWED')
        if str(f.get('Email Send Status') or '') != required_send_status: reasons.append('SEND_STATUS_NOT_READY')
        if str(f.get('Email Send Status') or '') == 'SENT' or f.get('Email Message ID'): reasons.append('ALREADY_SENT')
        if reasons:
            skipped.append({'record_id': rec.get('id'), 'company': f.get('Company'), 'reasons': reasons}); continue
        candidates.append(rec)

    sent = []; failed = []
    run_limit = min(max_per_run, remaining_daily_capacity) if not dry_run else min(max_per_run, max(remaining_daily_capacity, 1))
    for rec in candidates[:run_limit]:
        f = rec.get('fields') or {}
        item = {'record_id': rec.get('id'), 'company': f.get('Company'), 'to': f.get('Email'), 'subject': f.get('Outreach Subject')}
        if dry_run:
            item['status'] = 'DRY_RUN_READY'; sent.append(item); continue
        try:
            message_id = send_resend(str(f['Email']), str(f['Outreach Subject']), str(f['Outreach Draft']))
            airtable_update(rec['id'], {'Email Send Status': 'SENT', 'Email Message ID': message_id, 'Email Sent At': now, 'Email Send Error': '', 'Outreach Status': 'CONTACTED'})
            item.update({'status': 'SENT', 'message_id': message_id}); sent.append(item)
        except Exception as exc:
            err = str(exc)
            try: airtable_update(rec['id'], {'Email Send Status': 'FAILED', 'Email Send Error': err[:5000]})
            except Exception: pass
            item.update({'status': 'FAILED', 'error': err}); failed.append(item)

    sent_this_run = sum(1 for x in sent if x.get('status') == 'SENT')
    sent_today = sent_today_before + sent_this_run
    state = {
        'agent': 'LOCENIX_EMAIL_SENDER', 'mode': 'DRY_RUN' if dry_run else 'LIVE_SEND', 'provider': 'resend',
        'day': berlin_day,
        'daily_target': daily_target,
        'sent_today': sent_today,
        'daily_gap': max(0, daily_target - sent_today),
        'scanned': len(records), 'eligible': len(candidates), 'processed': len(sent) + len(failed),
        'sent_count': sent_this_run,
        'dry_run_ready_count': sum(1 for x in sent if x.get('status') == 'DRY_RUN_READY'),
        'failed_count': len(failed), 'skipped_count': len(skipped), 'last_run_at': now,
        'guardrails': {
            'explicit_airtable_approval_required': True,
            'documented_legal_basis_required': True,
            'duplicate_send_blocked': True,
            'excluded_professions_blocked': True,
            'excluded_professions': list(EXCLUDED_PROFESSION_TERMS),
            'max_emails_per_run': max_per_run,
            'hard_daily_target': daily_target,
            'provider_confirmed_sends_only': True,
        },
    }
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps({'state': state, 'processed': sent, 'failed': failed, 'skipped': skipped[:50]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
