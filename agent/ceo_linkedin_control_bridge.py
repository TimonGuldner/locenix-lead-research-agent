from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CEO_LATEST = Path('results/ceo_latest.json')
CEO_STATE = Path('results/ceo_state.json')
LINKEDIN_CONTROL_URL = 'https://raw.githubusercontent.com/TimonGuldner/browser-agent/main/results/linkedin_control_state.json'


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def load_url(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'locenix-ceo-linkedin-control'})
        with urllib.request.urlopen(req, timeout=20) as r:
            value = json.loads(r.read().decode('utf-8'))
            return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main() -> None:
    latest = load(CEO_LATEST)
    state = load(CEO_STATE)
    control = load_url(LINKEDIN_CONTROL_URL)
    now = datetime.now(timezone.utc).isoformat()

    if not control:
        control = {
            'control_status': 'UNKNOWN',
            'ceo_attention_required': True,
            'corrective_action': 'LinkedIn control evidence is unavailable; require Agent 8 to report target/actual/gap/action evidence.',
        }

    departments = latest.setdefault('departments', {})
    linkedin = departments.setdefault('linkedin', {})
    linkedin['execution_control'] = {
        'status': control.get('control_status', 'UNKNOWN'),
        'targets': control.get('targets') or {},
        'actual': control.get('actual') or {},
        'gaps': control.get('gaps') or {},
        'created_jobs': control.get('created_jobs') or [],
        'active_roles': control.get('active_roles') or [],
        'corrective_action': control.get('corrective_action'),
        'ceo_attention_required': bool(control.get('ceo_attention_required')),
        'last_control_at': control.get('generated_at', 'UNKNOWN'),
    }
    latest['linkedin_management_control'] = linkedin['execution_control']
    latest['linkedin_manager_accountability_rule'] = 'Agent 0 checks whether Agent 8 converted targets into executed work and funnel movement, not merely whether workflows are healthy.'

    control_status = str(control.get('control_status', 'UNKNOWN')).upper()
    needs_attention = bool(control.get('ceo_attention_required')) or control_status in {'ATTENTION', 'BLOCKED', 'UNKNOWN'}
    if needs_attention:
        existing_type = str((latest.get('priority_1') or {}).get('type') or '').upper()
        if existing_type not in {'STABILITY', 'HUMAN_REVIEW'}:
            action = control.get('corrective_action') or 'Require Agent 8 to close the largest LinkedIn funnel gap and report execution evidence.'
            latest['company_status'] = 'BLOCKED' if control_status == 'BLOCKED' else 'ATTENTION'
            latest['manager_directive'] = action
            latest['priority_1'] = {
                'rank': 1,
                'type': 'DEPARTMENT_ACCOUNTABILITY',
                'owner': 'Agent 8',
                'action': action,
                'reason': f'LinkedIn execution control status is {control_status}.',
            }

    state.update({
        'linkedin_control_status': control_status,
        'linkedin_targets': control.get('targets') or {},
        'linkedin_actual': control.get('actual') or {},
        'linkedin_gaps': control.get('gaps') or {},
        'linkedin_active_roles': control.get('active_roles') or [],
        'linkedin_created_jobs': control.get('created_jobs') or [],
        'linkedin_ceo_attention_required': needs_attention,
        'linkedin_control_last_run_at': control.get('generated_at', 'UNKNOWN'),
        'linkedin_control_checked_by_ceo_at': now,
    })
    if needs_attention:
        state['manager_directive'] = latest.get('manager_directive')
        if control_status == 'BLOCKED':
            state['company_status'] = 'BLOCKED'
        elif str(state.get('company_status', '')).upper() != 'BLOCKED':
            state['company_status'] = 'ATTENTION'

    save(CEO_LATEST, latest)
    save(CEO_STATE, state)
    print(json.dumps({'linkedin_control_status': control_status, 'ceo_attention_required': needs_attention}, ensure_ascii=False))


if __name__ == '__main__':
    main()
