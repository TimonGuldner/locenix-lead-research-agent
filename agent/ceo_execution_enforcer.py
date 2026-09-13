from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'results' / 'ceo_execution_state.json'
GROWTH_STATE = ROOT / 'results' / 'department_head_state.json'
CS_STATE = ROOT / 'results' / 'customer_success_state.json'
EMAIL_STATE = ROOT / 'results' / 'email_sender_state.json'
CEO_STATE = ROOT / 'results' / 'ceo_latest.json'
LINKEDIN_URL = 'https://raw.githubusercontent.com/TimonGuldner/browser-agent/main/results/linkedin_control_state.json'

LINKEDIN_TARGETS = {
    'qualified_leads_found': 10,
    'engagement_actions': 3,
    'connection_requests': 5,
    'direct_messages': 3,
}
EMAIL_TARGET = 5


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def load_url(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'LOCENIX-CEO-Agent-0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return {}


def dispatch(workflow_file: str, repo: str | None = None) -> tuple[bool, str]:
    target_repo = (repo or os.environ.get('GITHUB_REPOSITORY', '')).strip()
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if not target_repo or not token:
        return False, 'missing GitHub runtime credentials'
    url = f'https://api.github.com/repos/{target_repo}/actions/workflows/{workflow_file}/dispatches'
    req = urllib.request.Request(url, data=json.dumps({'ref': 'main'}).encode('utf-8'), method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201, 204), f'HTTP {resp.status}'
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def age_minutes(value: object) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60)
    except Exception:
        return None


def linkedin_snapshot() -> dict:
    raw = load_url(LINKEDIN_URL)
    control = raw.get('execution_control') if isinstance(raw.get('execution_control'), dict) else raw
    actual = control.get('actual', {}) if isinstance(control, dict) else {}
    gaps = {k: max(0, target - int(actual.get(k, 0) or 0)) for k, target in LINKEDIN_TARGETS.items()}
    return {'actual': {k: int(actual.get(k, 0) or 0) for k in LINKEDIN_TARGETS}, 'gaps': gaps, 'complete': not any(gaps.values())}


def email_snapshot() -> dict:
    raw = load(EMAIL_STATE)
    candidates = [raw.get('sent_today'), raw.get('provider_confirmed_sends_today'), raw.get('sent')]
    actual = next((int(x) for x in candidates if isinstance(x, (int, float))), 0)
    return {'actual': actual, 'target': EMAIL_TARGET, 'gap': max(0, EMAIL_TARGET - actual), 'complete': actual >= EMAIL_TARGET}


def maps_gate_snapshot() -> dict:
    ceo = load(CEO_STATE)
    accountability = ceo.get('daily_outbound_accountability', {})
    gate = accountability.get('maps_email_gate', {}) if isinstance(accountability, dict) else {}
    violations = int(gate.get('violations_actual', gate.get('downstream_email_gate_violations', 0)) or 0)
    return {'violations': violations, 'complete': violations == 0, 'rule': 'NO_RESEARCHED_PUBLIC_BUSINESS_EMAIL_NO_DOWNSTREAM_HANDOFF'}


def main() -> None:
    berlin = datetime.now(ZoneInfo('Europe/Berlin'))
    if not (8 <= berlin.hour < 20):
        result = {'status': 'outside_business_hours', 'checked_at': datetime.now(timezone.utc).isoformat()}
        STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result))
        return

    before = {'linkedin': linkedin_snapshot(), 'email': email_snapshot(), 'maps_gate': maps_gate_snapshot()}
    actions = []

    # Agent 0 only accepts confirmed daily outcomes. Open gaps cause immediate manager correction work.
    if not before['email']['complete'] or not before['maps_gate']['complete']:
        ok, detail = dispatch('department-head.yml')
        actions.append({'department': 'growth', 'manager': 'AGENT_7', 'reason': 'EMAIL_OR_MAPS_DAILY_GAP', 'workflow': 'department-head.yml', 'dispatched': ok, 'detail': detail})

    if not before['linkedin']['complete']:
        ok, detail = dispatch('linkedin-department-head.yml', repo='TimonGuldner/browser-agent')
        actions.append({'department': 'linkedin', 'manager': 'AGENT_8', 'reason': 'LINKEDIN_DAILY_TARGET_GAP', 'workflow': 'linkedin-department-head.yml', 'dispatched': ok, 'detail': detail})

    # Customer Success remains supervised, but outbound quota closure is the CEO's highest operating priority.
    ok, detail = dispatch('customer-success.yml')
    actions.append({'department': 'customer_success', 'manager': 'AGENT_10', 'reason': 'LIFECYCLE_REVIEW', 'workflow': 'customer-success.yml', 'dispatched': ok, 'detail': detail})

    # A short second measurement proves whether execution evidence moved. A future CEO cycle repeats correction if not.
    time.sleep(5)
    after = {'linkedin': linkedin_snapshot(), 'email': email_snapshot(), 'maps_gate': maps_gate_snapshot()}
    open_gaps = {
        'linkedin': after['linkedin']['gaps'],
        'email': after['email']['gap'],
        'maps_gate_violations': after['maps_gate']['violations'],
    }
    quotas_complete = after['linkedin']['complete'] and after['email']['complete'] and after['maps_gate']['complete']

    result = {
        'status': 'TARGETS_MET' if quotas_complete else 'ATTENTION',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'priority': 'HIGHEST_DAILY_OUTBOUND_TARGET_ENFORCEMENT',
        'rule': 'ONLY_CONFIRMED_RESULTS_COUNT_QUEUED_RUNNING_OR_GREEN_WORKFLOWS_DO_NOT_COUNT',
        'control_loop': 'MEASURE -> GAP -> DISPATCH/RECOVERY -> RE-MEASURE -> REPEAT_NEXT_CEO_CYCLE_UNTIL_ZERO_GAP',
        'before': before,
        'after_remeasurement': after,
        'open_gaps': open_gaps,
        'actions': actions,
        'deadline_rule': 'Before daily deadline, every remaining gap stays ATTENTION and must trigger corrective work on every CEO cycle; after deadline a nonzero gap is a failed daily objective unless a real external blocker is documented.',
        'maps_rule': 'No researched public business email = no Visibility, Sales Queue or outreach-ready handoff. Public email alone does not authorize sending; approval and documented legal basis remain required.',
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
