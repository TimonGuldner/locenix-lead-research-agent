from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / 'tasks' / 'customer_success_task.json'
STATE = ROOT / 'results' / 'customer_success_state.json'
LATEST = ROOT / 'results' / 'customer_success_latest.json'
A11 = ROOT / 'results' / 'agent_11_trial_conversion_state.json'
A12 = ROOT / 'results' / 'agent_12_onboarding_state.json'
A13 = ROOT / 'results' / 'agent_13_retention_state.json'
A14 = ROOT / 'results' / 'agent_14_customer_success_email_state.json'


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


def fields(records):
    return [r.get('fields', {}) for r in records]


def main():
    task = load(TASK)
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return

    token = os.getenv('AIRTABLE_TOKEN', '').strip()
    if not token:
        raise SystemExit('AIRTABLE_TOKEN missing')

    base = task['airtable_base_id']
    tables = task['tables']
    now = datetime.now(timezone.utc).isoformat()

    errors = []
    raw = {}
    for key, table_id in tables.items():
        try:
            raw[key] = airtable_records(base, table_id, token)
        except Exception as exc:
            raw[key] = []
            errors.append(f'{key}:{type(exc).__name__}:{exc}')

    customers = fields(raw['customers'])
    trials = fields(raw['trials'])
    onboarding = fields(raw['onboarding'])
    risks = fields(raw['risks'])

    active_trials = [x for x in trials if x.get('Trial Outcome') == 'ACTIVE']
    converted_trials = [x for x in trials if x.get('Trial Outcome') == 'CONVERTED']
    activated_trials = [x for x in active_trials if x.get('Trial Activated')]
    expiring_trials = [x for x in active_trials if isinstance(x.get('Trial Days Remaining'), (int, float)) and x.get('Trial Days Remaining') <= 2]
    onboarding_blocked = [x for x in onboarding if x.get('Blocker Type') not in (None, '', 'NONE') or bool(x.get('Blocker'))]
    no_first_value = [x for x in active_trials if not x.get('First Value Reached')]
    paid = [x for x in customers if x.get('Status') == 'ACTIVE' and x.get('Payment Status') == 'ACTIVE']
    at_risk = [x for x in customers if x.get('Status') in {'AT_RISK', 'CANCEL_REQUESTED', 'PAST_DUE'} or x.get('Customer Health') in {'AT_RISK', 'BLOCKED'} or x.get('Churn Risk') == 'HIGH']
    payment_issues = [x for x in customers if x.get('Status') == 'PAST_DUE' or x.get('Payment Status') == 'PAST_DUE']
    open_risks = [x for x in risks if x.get('Status') in {'OPEN', 'IN_PROGRESS', 'HUMAN_REVIEW_REQUIRED'}]
    human_reviews = [x for x in customers if x.get('Human Review')] + [x for x in trials if x.get('Human Review')] + [x for x in risks if x.get('Human Review') or x.get('Status') == 'HUMAN_REVIEW_REQUIRED']
    a14 = load(A14, {})

    if errors:
        status, bottleneck, owner, action = 'BLOCKED', 'AIRTABLE_ACCESS_OR_SYNC', 'AGENT_10', 'Resolve Airtable access/sync before Customer Success automation continues.'
    elif a14 and int(a14.get('failed_count') or 0) > 0:
        status, bottleneck, owner, action = 'ATTENTION', 'CUSTOMER_SUCCESS_EMAIL_ERRORS', 'AGENT_14', 'Resolve Customer Success email delivery errors before further automated communication.'
    elif a14 and int(a14.get('human_reviews_required') or 0) > 0:
        status, bottleneck, owner, action = 'ATTENTION', 'CUSTOMER_SUCCESS_EMAIL_HUMAN_REVIEW', 'AGENT_10', 'Review Customer Success conversations that require a human decision.'
    elif any(x.get('Status') == 'CANCEL_REQUESTED' for x in customers):
        status, bottleneck, owner, action = 'ATTENTION', 'CANCEL_RISK', 'AGENT_13', 'Prioritize customers with cancellation requests and require human review before commitments.'
    elif payment_issues:
        status, bottleneck, owner, action = 'ATTENTION', 'PAYMENT_PROBLEM', 'AGENT_13', 'Prioritize payment issues for active customers.'
    elif at_risk:
        status, bottleneck, owner, action = 'ATTENTION', 'CUSTOMERS_AT_RISK', 'AGENT_13', 'Work the highest-risk paying customer before new trial activity.'
    elif expiring_trials:
        status, bottleneck, owner, action = 'ATTENTION', 'TRIAL_EXPIRING', 'AGENT_11', 'Prioritize active trials with two days or less remaining.'
    elif onboarding_blocked:
        status, bottleneck, owner, action = 'ATTENTION', 'ONBOARDING_BLOCKER', 'AGENT_12', 'Resolve onboarding blockers, starting with GBP connection and technical blockers.'
    elif no_first_value:
        status, bottleneck, owner, action = 'ATTENTION', 'NO_FIRST_VALUE', 'AGENT_12', 'Move active trials to first value before adding more conversion activity.'
    else:
        status, bottleneck, owner, action = 'HEALTHY', 'NONE', 'AGENT_11', 'Monitor for new qualified trials and conversion opportunities.'

    denom = len(active_trials) + len(converted_trials)
    conversion_rate = round((len(converted_trials) / denom) * 100, 1) if denom else None

    a11 = {
        'agent': 'AGENT_11_TRIAL_CONVERSION', 'reports_to': 'AGENT_10_CUSTOMER_SUCCESS_DEPARTMENT_HEAD',
        'active_trials': len(active_trials), 'activated_trials': len(activated_trials), 'expiring_trials': len(expiring_trials),
        'converted_trials': len(converted_trials), 'status': 'ACTION_REQUIRED' if owner == 'AGENT_11' and status != 'HEALTHY' else 'HEALTHY', 'last_run_at': now,
    }
    a12 = {
        'agent': 'AGENT_12_ONBOARDING', 'reports_to': 'AGENT_10_CUSTOMER_SUCCESS_DEPARTMENT_HEAD',
        'onboarding_records': len(onboarding), 'blocked': len(onboarding_blocked), 'trials_without_first_value': len(no_first_value),
        'status': 'ACTION_REQUIRED' if owner == 'AGENT_12' and status != 'HEALTHY' else 'HEALTHY', 'last_run_at': now,
    }
    a13 = {
        'agent': 'AGENT_13_RETENTION_SUCCESS', 'reports_to': 'AGENT_10_CUSTOMER_SUCCESS_DEPARTMENT_HEAD',
        'paid_customers': len(paid), 'customers_at_risk': len(at_risk), 'payment_issues': len(payment_issues), 'open_risks': len(open_risks),
        'status': 'ACTION_REQUIRED' if owner == 'AGENT_13' and status != 'HEALTHY' else 'HEALTHY', 'last_run_at': now,
    }

    state = {
        'agent': 'AGENT_10_CUSTOMER_SUCCESS_DEPARTMENT_HEAD',
        'reports_to': 'AGENT_0_CEO',
        'department': 'CUSTOMER_SUCCESS',
        'department_status': status,
        'airtable_base_id': base,
        'active_trials': len(active_trials),
        'activated_trials': len(activated_trials),
        'paid_customers': len(paid),
        'trial_to_paid_rate': conversion_rate,
        'customers_at_risk': len(at_risk),
        'payment_issues': len(payment_issues),
        'technical_blockers': len(onboarding_blocked),
        'human_reviews_required': len(human_reviews) + int(a14.get('human_reviews_required') or 0),
        'customer_success_email_agent_available': bool(a14),
        'customer_success_email_sent_last_run': a14.get('sent_count', 'UNKNOWN'),
        'customer_success_email_failed_last_run': a14.get('failed_count', 'UNKNOWN'),
        'customer_success_email_last_run_at': a14.get('last_run_at', 'UNKNOWN'),
        'biggest_bottleneck': bottleneck,
        'recommended_next_action': action,
        'next_agent': owner,
        'errors': errors,
        'last_run_at': now,
    }

    save(A11, a11); save(A12, a12); save(A13, a13); save(STATE, state)
    save(LATEST, {'state': state, 'subagents': {'agent_11': a11, 'agent_12': a12, 'agent_13': a13, 'agent_14': a14}})
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
