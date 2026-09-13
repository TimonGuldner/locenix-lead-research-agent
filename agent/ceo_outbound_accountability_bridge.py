from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CEO_LATEST = Path('results/ceo_latest.json')
CEO_STATE = Path('results/ceo_state.json')
GROWTH_STATE = Path('results/department_head_state.json')
LINKEDIN_CONTROL_URL = 'https://raw.githubusercontent.com/TimonGuldner/browser-agent/main/results/linkedin_control_state.json'


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def load_url(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'locenix-ceo-outbound-accountability'})
        with urllib.request.urlopen(req, timeout=20) as r:
            value = json.loads(r.read().decode('utf-8'))
            return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def expected_for_target(target: int, now_berlin: datetime) -> int:
    target = max(0, int(target))
    if target == 0:
        return 0
    hour = now_berlin.hour + now_berlin.minute / 60
    if hour >= 18:
        return target
    fraction = max(0.0, min(1.0, (hour - 8.0) / 10.0))
    if fraction <= 0:
        return 0
    return min(target, int(math.ceil(target * fraction)))


def main() -> None:
    latest = load(CEO_LATEST)
    state = load(CEO_STATE)
    growth = load(GROWTH_STATE)
    linkedin = load_url(LINKEDIN_CONTROL_URL)
    now_utc = datetime.now(timezone.utc)
    now_berlin = now_utc.astimezone(ZoneInfo('Europe/Berlin'))

    email_target = int(growth.get('email_daily_target') or 5)
    email_actual = int(growth.get('email_sent_today') or 0)
    email_gap = max(0, email_target - email_actual)
    email_expected = expected_for_target(email_target, now_berlin)
    email_pacing_gap = max(0, email_expected - email_actual)

    maps_violations = int(growth.get('maps_downstream_email_gate_violations') or 0)
    linkedin_targets = linkedin.get('targets') or {}
    linkedin_actual = linkedin.get('actual') or {}
    linkedin_gaps = linkedin.get('gaps') or {}
    linkedin_pacing = linkedin.get('pacing_gaps') or {}
    linkedin_status = str(linkedin.get('control_status') or 'UNKNOWN').upper()

    hard_deadline = now_berlin.hour >= 18
    linkedin_gap_total = sum(max(0, int(v or 0)) for v in linkedin_gaps.values()) if linkedin_gaps else 0
    linkedin_pacing_total = sum(max(0, int(v or 0)) for v in linkedin_pacing.values()) if linkedin_pacing else 0

    if maps_violations > 0:
        accountability_status = 'BLOCKED'
        reason = f'Maps downstream email gate has {maps_violations} violation(s). Missing-email leads must be removed from downstream handoff immediately.'
        owner = 'Agent 7'
    elif email_pacing_gap > 0:
        accountability_status = 'ATTENTION'
        reason = f'Approved email outbound is behind pace: {email_actual}/{email_target} sent today; expected by now {email_expected}.'
        owner = 'Agent 7'
    elif linkedin_status in {'ATTENTION', 'BLOCKED', 'UNKNOWN'} or linkedin_pacing_total > 0:
        accountability_status = 'BLOCKED' if linkedin_status == 'BLOCKED' else 'ATTENTION'
        reason = linkedin.get('corrective_action') or f'LinkedIn daily outbound is behind pace; pacing gap total {linkedin_pacing_total}.'
        owner = 'Agent 8'
    elif hard_deadline and (email_gap > 0 or linkedin_gap_total > 0):
        accountability_status = 'ATTENTION'
        reason = f'Hard daily deadline reached with open outbound gaps: email={email_gap}, linkedin={linkedin_gap_total}.'
        owner = 'Agent 7 / Agent 8'
    else:
        accountability_status = 'ON_TRACK' if (email_gap > 0 or linkedin_gap_total > 0) else 'ON_TARGET'
        reason = 'Daily outbound is on pace.' if accountability_status == 'ON_TRACK' else 'All measured daily outbound targets are reached.'
        owner = 'Agent 7 / Agent 8'

    accountability = {
        'generated_at': now_utc.isoformat(),
        'berlin_time': now_berlin.isoformat(),
        'status': accountability_status,
        'hard_deadline_hour': 18,
        'email': {
            'manager': 'AGENT_7_LOCENIX_GROWTH_MANAGER',
            'target': email_target,
            'actual': email_actual,
            'gap': email_gap,
            'expected_by_now': email_expected,
            'pacing_gap': email_pacing_gap,
            'counting_rule': 'Only provider-confirmed sends from records with explicit Airtable approval and allowed legal basis count.'
        },
        'linkedin': {
            'manager': 'AGENT_8_LINKEDIN_DEPARTMENT_HEAD',
            'status': linkedin_status,
            'targets': linkedin_targets,
            'actual': linkedin_actual,
            'gaps': linkedin_gaps,
            'expected_by_now': linkedin.get('expected_by_now') or {},
            'pacing_gaps': linkedin_pacing,
            'corrective_action': linkedin.get('corrective_action'),
        },
        'maps_email_gate': {
            'manager': 'AGENT_7_LOCENIX_GROWTH_MANAGER',
            'violations_target': 0,
            'violations_actual': maps_violations,
            'rule': 'No researched public business email = no Visibility, Sales Queue or outreach-ready handoff.'
        },
        'manager_accountability_rule': 'CEO holds department heads accountable for TARGET -> ACTUAL -> EXPECTED-BY-NOW -> GAP -> ACTION -> RE-MEASURE. Workflow success without target execution is not success.',
        'reason': reason,
        'owner': owner,
    }

    latest['daily_outbound_accountability'] = accountability
    state['daily_outbound_accountability_status'] = accountability_status
    state['daily_email_target'] = email_target
    state['daily_email_actual'] = email_actual
    state['daily_email_gap'] = email_gap
    state['daily_email_expected_by_now'] = email_expected
    state['daily_linkedin_targets'] = linkedin_targets
    state['daily_linkedin_actual'] = linkedin_actual
    state['daily_linkedin_gaps'] = linkedin_gaps
    state['daily_linkedin_pacing_gaps'] = linkedin_pacing
    state['maps_downstream_email_gate_violations'] = maps_violations
    state['outbound_accountability_checked_at'] = now_utc.isoformat()

    if accountability_status in {'ATTENTION', 'BLOCKED'}:
        current = str((latest.get('priority_1') or {}).get('type') or '').upper()
        if current not in {'STABILITY', 'HUMAN_REVIEW'} or accountability_status == 'BLOCKED':
            latest['company_status'] = 'BLOCKED' if accountability_status == 'BLOCKED' else 'ATTENTION'
            latest['manager_directive'] = reason
            latest['priority_1'] = {
                'rank': 1,
                'type': 'DAILY_OUTBOUND_ACCOUNTABILITY',
                'owner': owner,
                'action': reason,
                'reason': 'Hard daily outbound target or Maps email-quality gate is not currently satisfied.'
            }
            state['company_status'] = latest['company_status']
            state['manager_directive'] = reason

    save(CEO_LATEST, latest)
    save(CEO_STATE, state)
    print(json.dumps(accountability, ensure_ascii=False))


if __name__ == '__main__':
    main()
