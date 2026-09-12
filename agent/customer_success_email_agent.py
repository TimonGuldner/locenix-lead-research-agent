from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / 'tasks' / 'agents' / 'agent_14_customer_success_email.json'
CS_TASK = ROOT / 'tasks' / 'customer_success_task.json'
STATE = ROOT / 'results' / 'agent_14_customer_success_email_state.json'
LATEST = ROOT / 'results' / 'agent_14_customer_success_email_latest.json'


def load(path, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def airtable_records(base_id, table_id, token):
    url = f'https://api.airtable.com/v0/{base_id}/{table_id}?pageSize=100'
    out = []
    while url:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
        out.extend(data.get('records', []))
        offset = data.get('offset')
        url = f'https://api.airtable.com/v0/{base_id}/{table_id}?pageSize=100&offset={urllib.parse.quote(offset)}' if offset else None
    return out


def airtable_patch(base_id, table_id, record_id, fields, token):
    url = f'https://api.airtable.com/v0/{base_id}/{table_id}/{record_id}'
    body = json.dumps({'fields': fields}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='PATCH', headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def send_resend(api_key, sender, reply_to, recipient, subject, text, html):
    payload = {
        'from': sender,
        'to': [recipient],
        'reply_to': reply_to,
        'subject': subject,
        'text': text,
        'html': html,
    }
    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=json.dumps(payload).encode('utf-8'),
        method='POST',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None


def greeting(name):
    first = (name or '').strip().split(' ')[0] if (name or '').strip() else ''
    return f'Hallo {first},' if first else 'Hallo,'


def signature_text():
    return '\n\nViele Grüße\n\nTimon Guldner\nLOCENIX\nLocal SEO & Google Business Profile\nhello@locenix.com\nhttps://locenix.com'


def signature_html():
    return '<p>Viele Grüße</p><p>Timon Guldner<br>LOCENIX<br>Local SEO &amp; Google Business Profile<br>hello@locenix.com<br>https://locenix.com</p>'


def compose(email_type, customer, trial=None, onboarding=None):
    name = customer.get('Contact Name')
    hi = greeting(name)
    company = customer.get('Company') or ''

    if email_type == 'TRIAL_EXPIRING':
        days = (trial or {}).get('Trial Days Remaining')
        day_text = 'bald' if days is None else (f'in {int(days)} Tag' if int(days) == 1 else f'in {int(days)} Tagen')
        subject = 'Dein LOCENIX Testzeitraum läuft bald aus'
        body = (
            f'{hi}\n\n'
            f'dein LOCENIX Testzeitraum läuft {day_text} aus. '
            'Wenn du noch etwas testen möchtest oder irgendwo festhängst, antworte einfach auf diese E-Mail. '
            'Ich helfe dir gern beim nächsten sinnvollen Schritt.'
        )
    elif email_type == 'GBP_CONNECTION_HELP':
        subject = 'Kurze Hilfe beim Verbinden deines Google Profils'
        body = (
            f'{hi}\n\n'
            'dein Google Business Profile ist in LOCENIX noch nicht verbunden. '
            'Sobald die Verbindung steht, kann LOCENIX dein Profil analysieren und konkrete Verbesserungen anzeigen. '
            'Wenn es beim Verbinden hakt, antworte kurz auf diese E-Mail und sag mir, an welcher Stelle du festhängst.'
        )
    elif email_type == 'ONBOARDING_BLOCKER':
        blocker = ((onboarding or {}).get('Blocker') or '').strip()
        subject = 'Kann ich dir beim LOCENIX Setup helfen?'
        detail = f' Im Setup ist aktuell folgender Punkt offen: {blocker}' if blocker else ''
        body = (
            f'{hi}\n\n'
            'ich sehe, dass dein LOCENIX Setup noch nicht ganz abgeschlossen ist.' + detail + ' '
            'Wenn du möchtest, antworte einfach kurz – dann schauen wir uns genau diesen Schritt an.'
        )
    else:  # FIRST_VALUE_NUDGE
        subject = 'Hol dir den ersten konkreten Nutzen aus LOCENIX'
        body = (
            f'{hi}\n\n'
            'dein Test läuft bereits, aber der erste vollständige Analyse- bzw. Optimierungsschritt ist noch offen. '
            'Am schnellsten kommst du zum ersten sichtbaren Nutzen, wenn dein Google Profil verbunden ist und du anschließend die erste Analyse startest. '
            'Wenn du irgendwo hängenbleibst, antworte einfach auf diese E-Mail.'
        )

    text = body + signature_text()
    html_body = ''.join(f'<p>{p}</p>' for p in body.split('\n\n')) + signature_html()
    return subject, text, html_body


def choose_candidate(customer, trial, onboarding, risks):
    # Never auto-message when human review or explicit do-not-contact is present.
    if customer.get('CS Do Not Contact') or customer.get('Human Review'):
        return None, 'HUMAN_REVIEW_OR_DNC'
    for risk in risks:
        if risk.get('Customer ID') == customer.get('Customer ID') and (risk.get('Human Review') or risk.get('Status') == 'HUMAN_REVIEW_REQUIRED'):
            return None, 'HUMAN_REVIEW_REQUIRED'

    if trial and trial.get('Trial Outcome') == 'ACTIVE':
        days = trial.get('Trial Days Remaining')
        if isinstance(days, (int, float)) and days <= 2:
            return 'TRIAL_EXPIRING', None
        if onboarding and not onboarding.get('GBP Connected'):
            return 'GBP_CONNECTION_HELP', None
        if onboarding and ((onboarding.get('Blocker Type') not in (None, '', 'NONE')) or bool(onboarding.get('Blocker'))):
            return 'ONBOARDING_BLOCKER', None
        if not trial.get('First Value Reached'):
            return 'FIRST_VALUE_NUDGE', None
    return None, 'NO_SAFE_EMAIL_NEEDED'


def main():
    task = load(TASK)
    cs_task = load(CS_TASK)
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return

    airtable_token = os.getenv('AIRTABLE_TOKEN', '').strip()
    resend_key = (os.getenv('RESEND_API_KEY') or '').strip()
    if not airtable_token:
        raise SystemExit('AIRTABLE_TOKEN missing')
    if not resend_key:
        raise SystemExit('RESEND_API_KEY missing')

    base = cs_task['airtable_base_id']
    tables = cs_task['tables']
    customers_raw = airtable_records(base, tables['customers'], airtable_token)
    trials_raw = airtable_records(base, tables['trials'], airtable_token)
    onboarding_raw = airtable_records(base, tables['onboarding'], airtable_token)
    risks_raw = airtable_records(base, tables['risks'], airtable_token)

    trials = [r.get('fields', {}) for r in trials_raw]
    onboarding = [r.get('fields', {}) for r in onboarding_raw]
    risks = [r.get('fields', {}) for r in risks_raw]
    trial_by_customer = {x.get('Customer ID'): x for x in trials if x.get('Customer ID')}
    onboarding_by_customer = {x.get('Customer ID'): x for x in onboarding if x.get('Customer ID')}

    now = datetime.now(timezone.utc)
    max_emails = int(task.get('max_emails_per_run') or 5)
    cooldown = timedelta(hours=int(task.get('cooldown_hours') or 24))
    sent = 0
    skipped = 0
    failed = 0
    human_review = 0
    actions = []

    for rec in customers_raw:
        if sent >= max_emails:
            break
        customer = rec.get('fields', {})
        customer_id = customer.get('Customer ID')
        email = (customer.get('Email') or '').strip()
        if not customer_id or not email:
            skipped += 1
            continue

        trial = trial_by_customer.get(customer_id)
        onb = onboarding_by_customer.get(customer_id)
        email_type, reason = choose_candidate(customer, trial, onb, risks)

        if reason in {'HUMAN_REVIEW_OR_DNC', 'HUMAN_REVIEW_REQUIRED'}:
            human_review += 1
            try:
                airtable_patch(base, tables['customers'], rec['id'], {'CS Email Status': 'HUMAN_REVIEW'}, airtable_token)
            except Exception:
                pass
            continue
        if not email_type:
            skipped += 1
            continue

        last_type = customer.get('CS Last Email Type')
        last_status = customer.get('CS Email Status')
        last_at = parse_dt(customer.get('CS Last Email At'))
        if last_type == email_type and last_status == 'SENT':
            skipped += 1
            continue
        if last_at and now - last_at < cooldown:
            skipped += 1
            continue

        subject, text, html = compose(email_type, customer, trial, onb)
        try:
            result = send_resend(
                resend_key,
                task.get('sender', 'Timon Guldner <hello@locenix.com>'),
                task.get('reply_to', 'hello@locenix.com'),
                email,
                subject,
                text,
                html,
            )
            message_id = result.get('id')
            airtable_patch(base, tables['customers'], rec['id'], {
                'CS Email Status': 'SENT',
                'CS Last Email Type': email_type,
                'CS Last Email At': now.isoformat(),
                'CS Email Message ID': message_id or '',
                'CS Email Error': '',
            }, airtable_token)
            sent += 1
            actions.append({'customer_id': customer_id, 'email_type': email_type, 'message_id': message_id, 'status': 'SENT'})
        except Exception as exc:
            failed += 1
            err = f'{type(exc).__name__}: {exc}'[:1000]
            try:
                airtable_patch(base, tables['customers'], rec['id'], {
                    'CS Email Status': 'FAILED',
                    'CS Last Email Type': email_type,
                    'CS Email Error': err,
                }, airtable_token)
            except Exception:
                pass
            actions.append({'customer_id': customer_id, 'email_type': email_type, 'status': 'FAILED', 'error': err})

    state = {
        'agent': 'AGENT_14_CUSTOMER_SUCCESS_EMAIL',
        'reports_to': 'AGENT_10_CUSTOMER_SUCCESS_DEPARTMENT_HEAD',
        'sender': task.get('sender', 'Timon Guldner <hello@locenix.com>'),
        'sent_count': sent,
        'skipped_count': skipped,
        'failed_count': failed,
        'human_reviews_required': human_review,
        'status': 'ATTENTION' if failed or human_review else 'HEALTHY',
        'last_run_at': now.isoformat(),
    }
    save(STATE, state)
    save(LATEST, {'state': state, 'actions': actions})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
