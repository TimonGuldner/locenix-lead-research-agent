from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK = Path('tasks/email_conversation_task.json')
STATE = Path('results/email_conversation_state.json')
LATEST = Path('results/email_conversation_latest.json')

AIRTABLE_BASE_ID = os.getenv('AIRTABLE_BASE_ID', 'appuPKnVyLsbWbxMR').strip()
AIRTABLE_TABLE_ID = os.getenv('AIRTABLE_TABLE_ID', 'tblF4ghkYFzkeQwsT').strip()
AIRTABLE_TOKEN = os.getenv('AIRTABLE_TOKEN', '').strip()
RESEND_INBOX_API_KEY = os.getenv('RESEND_INBOX_API_KEY', '').strip()
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '').strip()
EMAIL_FROM = os.getenv('EMAIL_FROM', 'Timon Guldner <hello@locenix.com>').strip() or 'Timon Guldner <hello@locenix.com>'
EMAIL_REPLY_TO = os.getenv('EMAIL_REPLY_TO', 'hello@locenix.com').strip() or 'hello@locenix.com'
AUTO_REPLY_ENV = os.getenv('EMAIL_AUTO_REPLY', 'false').strip().lower() in {'1', 'true', 'yes'}

SIGNATURE_TEXT = (
    'Viele Grüße\n\n'
    'Timon Guldner\n'
    'LOCENIX\n'
    'Local SEO & Google Business Profile\n'
    'hello@locenix.com\n'
    'https://locenix.com'
)


def load_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


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


def auth_headers(token: str, user_agent: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'User-Agent': user_agent}


def airtable_list(max_records: int = 500) -> list[dict[str, Any]]:
    if not AIRTABLE_TOKEN:
        raise RuntimeError('AIRTABLE_TOKEN missing')
    out, offset = [], ''
    while len(out) < max_records:
        params = {'pageSize': '100'}
        if offset:
            params['offset'] = offset
        url = f'https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}?{urllib.parse.urlencode(params)}'
        payload = request_json(url, headers=auth_headers(AIRTABLE_TOKEN, 'locenix-email-conversation'))
        out.extend(payload.get('records') or [])
        offset = str(payload.get('offset') or '')
        if not offset:
            break
    return out[:max_records]


def airtable_update(record_id: str, fields: dict[str, Any]) -> None:
    url = f'https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}/{record_id}'
    request_json(url, method='PATCH', headers=auth_headers(AIRTABLE_TOKEN, 'locenix-email-conversation'), body={'fields': fields, 'typecast': True})


def list_received(limit: int) -> list[dict[str, Any]]:
    if not RESEND_INBOX_API_KEY:
        raise RuntimeError('RESEND_INBOX_API_KEY missing')
    url = f'https://api.resend.com/emails/receiving?limit={max(1, min(limit, 100))}'
    return request_json(url, headers=auth_headers(RESEND_INBOX_API_KEY, 'locenix-email-conversation')).get('data') or []


def get_received(email_id: str) -> dict[str, Any]:
    return request_json(
        f'https://api.resend.com/emails/receiving/{urllib.parse.quote(email_id)}',
        headers=auth_headers(RESEND_INBOX_API_KEY, 'locenix-email-conversation'),
    )


def official_html(text: str) -> str:
    safe = html.escape(text)
    blocks = ''.join(
        f'<p style="margin:0 0 14px 0;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#1f2937;">{part.replace(chr(10), "<br>")}</p>'
        for part in safe.split('\n\n')
        if part.strip()
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge"></head>'
        '<body style="margin:0;padding:0;background-color:#ffffff;">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="left" bgcolor="#ffffff" style="background-color:#ffffff;padding-top:24px;padding-right:24px;padding-bottom:24px;padding-left:24px;">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;"><tr><td style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;">'
        + blocks +
        '</td></tr></table></td></tr></table></body></html>'
    )


def send_reply(to_email: str, subject: str, text: str) -> str:
    token = RESEND_API_KEY or RESEND_INBOX_API_KEY
    if not token:
        raise RuntimeError('No Resend sending key available')
    payload: dict[str, Any] = {
        'from': EMAIL_FROM,
        'to': [to_email],
        'subject': subject,
        'text': text,
        'html': official_html(text),
        'reply_to': [EMAIL_REPLY_TO],
    }
    result = request_json('https://api.resend.com/emails', method='POST', headers=auth_headers(token, 'locenix-email-conversation'), body=payload)
    message_id = str(result.get('id') or '').strip()
    if not message_id:
        raise RuntimeError(f'Resend returned no id: {result}')
    return message_id


def extract_email(value: str) -> str:
    m = re.search(r'<([^>]+@[^>]+)>', value or '')
    if m:
        return m.group(1).strip().lower()
    m = re.search(r'([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})', value or '', re.I)
    return m.group(1).lower() if m else ''


def classify(subject: str, text: str) -> tuple[str, str]:
    s = f'{subject}\n{text}'.lower()
    if any(x in s for x in ['unsubscribe', 'abbestellen', 'nicht mehr schreiben', 'keine weiteren mails']): return 'UNSUBSCRIBE', 'NONE'
    if any(x in s for x in ['kein interesse', 'nicht interessiert', 'nein danke', 'no interest']): return 'NEGATIVE', 'NONE'
    if any(x in s for x in ['automatische antwort', 'abwesen', 'urlaub', 'out of office', 'automatic reply']): return 'OUT_OF_OFFICE', 'NONE'
    if any(x in s for x in ['trial', 'testen', 'testzugang', 'test account']): return 'TRIAL_INTEREST', 'HIGH'
    if any(x in s for x in ['visibility check', 'sichtbarkeitscheck', 'check schicken', 'auswertung']): return 'VISIBILITY_CHECK_INTEREST', 'HIGH'
    if any(x in s for x in ['preis', 'kosten', 'monat', 'jahrespreis', 'rabatt']): return 'PRICE_QUESTION', 'MEDIUM'
    if '?' in s or any(x in s for x in ['wie funktioniert', 'kann locenix', 'technisch', 'google profil']): return 'QUESTION', 'MEDIUM'
    if any(x in s for x in ['interessant', 'gerne', 'ja bitte', 'klingt gut', 'mehr infos']): return 'POSITIVE_INTEREST', 'MEDIUM'
    if any(x in s for x in ['später', 'nächsten monat', 'aktuell nicht', 'momentan nicht']): return 'NOT_NOW', 'LOW'
    return 'UNKNOWN', 'UNKNOWN'


def with_signature(body: str) -> str:
    return f'{body.strip()}\n\n{SIGNATURE_TEXT}' if body.strip() else ''


def make_draft(category: str, inbound_text: str) -> str:
    bodies = {
        'TRIAL_INTEREST': 'Hallo,\n\nvielen Dank für die Rückmeldung. Gerne können wir den nächsten Schritt Richtung Testzugang machen. Ich schaue kurz, was für euren aktuellen Stand am sinnvollsten ist, und schicke euch die passenden nächsten Schritte.',
        'VISIBILITY_CHECK_INTEREST': 'Hallo,\n\nsehr gerne. Der kostenlose Local Visibility Check zeigt kompakt, wie euer Google-Unternehmensprofil aktuell aufgestellt ist und wo konkretes Potenzial liegt. Ich bereite die wichtigsten Punkte für euch vor; danach könnt ihr in Ruhe entscheiden, ob LOCENIX für euch interessant ist.',
        'POSITIVE_INTEREST': 'Hallo,\n\nvielen Dank für eure Rückmeldung. Gerne schaue ich mir das genauer an. Als ersten Schritt kann ich euch kostenlos einen kurzen Local Visibility Check erstellen, damit wir konkret sehen, wo aktuell Potenzial liegt.',
        'NEEDS_MORE_INFORMATION': 'Hallo,\n\nvielen Dank für eure Rückmeldung. Gerne gebe ich euch mehr Informationen. Am sinnvollsten ist meist ein kurzer Blick auf den aktuellen Google-Unternehmensprofil-Stand; dafür kann ich euch kostenlos einen Local Visibility Check erstellen.',
        'QUESTION': 'Hallo,\n\nvielen Dank für die Frage. Ich schaue mir das gern konkret für euren Fall an. Wenn ihr möchtet, kann ich euren aktuellen Google-Unternehmensprofil-Stand kurz prüfen und euch die wichtigsten Punkte verständlich zusammenfassen.',
        'TECHNICAL_QUESTION': 'Hallo,\n\nvielen Dank für die technische Frage. Ich schaue mir das gern konkret an und antworte euch so, dass klar wird, was LOCENIX in eurem Fall tatsächlich leisten kann.',
        'NOT_NOW': 'Hallo,\n\ndanke für die Rückmeldung. Kein Problem, dann melde ich mich dazu jetzt nicht weiter. Wenn das Thema später wieder aktuell wird, könnt ihr euch jederzeit gerne melden.',
    }
    return with_signature(bodies.get(category, ''))


def main() -> None:
    cfg = load_json(TASK, {})
    now = datetime.now(timezone.utc).isoformat()
    if not cfg.get('enabled', False):
        print(json.dumps({'status': 'disabled'}))
        return

    auto_reply = bool(cfg.get('auto_reply_enabled', False)) and AUTO_REPLY_ENV
    safe_categories = set(cfg.get('safe_auto_reply_categories') or [])
    human_categories = set(cfg.get('human_review_categories') or [])
    no_reply_categories = set(cfg.get('no_reply_categories') or [])
    stop_categories = set(cfg.get('stop_categories') or [])

    leads = airtable_list()
    by_email, processed_ids = {}, set()
    for rec in leads:
        f = rec.get('fields') or {}
        email_addr = str(f.get('Email') or '').strip().lower()
        if email_addr:
            by_email[email_addr] = rec
        mid = str(f.get('Last Inbound Message ID') or '').strip()
        if mid:
            processed_ids.add(mid)

    received = list_received(int(cfg.get('poll_limit') or 50))
    handled, unmatched, duplicate, errors = [], [], [], []

    for meta in sorted(received, key=lambda x: str(x.get('created_at') or '')):
        provider_id = str(meta.get('id') or '')
        message_id = str(meta.get('message_id') or provider_id).strip()
        if not message_id or message_id in processed_ids:
            duplicate.append(message_id or provider_id)
            continue
        try:
            full = get_received(provider_id)
            sender = extract_email(str(full.get('from') or meta.get('from') or ''))
            rec = by_email.get(sender)
            if not rec:
                unmatched.append({'message_id': message_id, 'from': sender, 'subject': full.get('subject') or meta.get('subject')})
                continue
            f = rec.get('fields') or {}
            text = str(full.get('text') or '')
            if not text:
                text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(full.get('html') or ''))).strip()
            subject = str(full.get('subject') or meta.get('subject') or '')
            category, intent = classify(subject, text)
            dnc = bool(f.get('Email Do Not Contact')) or category in stop_categories
            draft = '' if category in no_reply_categories or category in stop_categories else make_draft(category, text)
            reply_status = 'NO_REPLY_NEEDED' if category in no_reply_categories or category in stop_categories else 'DRAFTED'
            if category in human_categories:
                reply_status = 'HUMAN_REVIEW_REQUIRED'
            elif draft and category in safe_categories and not dnc:
                reply_status = 'AUTO_REPLY_READY' if auto_reply else 'WAITING_APPROVAL'

            update_fields: dict[str, Any] = {
                'Last Inbound Message ID': message_id,
                'Last Inbound At': full.get('created_at') or meta.get('created_at') or now,
                'Last Inbound Subject': subject,
                'Last Inbound Body': text[:10000],
                'Email Reply Classification': category,
                'Email Sales Intent': intent,
                'Email Agent Reply Draft': draft,
                'Email Agent Reply Status': reply_status,
            }
            if dnc:
                update_fields['Email Do Not Contact'] = True
            airtable_update(rec['id'], update_fields)

            result = {'record_id': rec['id'], 'company': f.get('Company'), 'from': sender, 'message_id': message_id, 'classification': category, 'sales_intent': intent, 'reply_status': reply_status}
            if auto_reply and reply_status == 'AUTO_REPLY_READY' and draft and not dnc:
                reply_subject = subject if subject.lower().startswith('re:') else f'Re: {subject}'
                sent_id = send_reply(sender, reply_subject, draft)
                airtable_update(rec['id'], {'Email Agent Reply Status': 'SENT'})
                result['reply_status'] = 'SENT'
                result['sent_message_id'] = sent_id
            handled.append(result)
            processed_ids.add(message_id)
        except Exception as exc:
            errors.append({'provider_id': provider_id, 'message_id': message_id, 'error': str(exc)})

    state = {
        'agent': 'AGENT_9_EMAIL_CONVERSATION_AGENT',
        'reports_to': 'AGENT_7_GROWTH_MANAGER',
        'inbox': cfg.get('inbox_address', 'hello@locenix.com'),
        'sender': EMAIL_FROM,
        'reply_to': EMAIL_REPLY_TO,
        'auto_reply_enabled': auto_reply,
        'received_scanned': len(received),
        'handled': len(handled),
        'unmatched': len(unmatched),
        'duplicates_skipped': len(duplicate),
        'errors': len(errors),
        'positive_replies': sum(1 for x in handled if x['classification'] in {'POSITIVE_INTEREST', 'VISIBILITY_CHECK_INTEREST', 'TRIAL_INTEREST'}),
        'trial_interest': sum(1 for x in handled if x['classification'] == 'TRIAL_INTEREST'),
        'human_reviews_required': sum(1 for x in handled if x['reply_status'] == 'HUMAN_REVIEW_REQUIRED'),
        'last_run_at': now,
    }
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps({'state': state, 'handled': handled, 'unmatched': unmatched, 'duplicates': duplicate, 'errors': errors}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
