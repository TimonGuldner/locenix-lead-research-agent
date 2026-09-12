from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'results' / 'ceo_execution_state.json'
GROWTH_STATE = ROOT / 'results' / 'department_head_state.json'
CS_STATE = ROOT / 'results' / 'customer_success_state.json'


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def dispatch(workflow_file: str) -> tuple[bool, str]:
    repo = os.environ.get('GITHUB_REPOSITORY', '').strip()
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if not repo or not token:
        return False, 'missing GitHub runtime credentials'
    url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches'
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


def main() -> None:
    berlin = datetime.now(ZoneInfo('Europe/Berlin'))
    if not (8 <= berlin.hour < 20):
        result = {'status': 'outside_business_hours', 'checked_at': datetime.now(timezone.utc).isoformat()}
        STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result))
        return

    growth = load(GROWTH_STATE)
    cs = load(CS_STATE)
    actions = []

    # CEO requires every department head to actively review and execute at least hourly.
    # The Growth Manager then dispatches the highest-value approved child workflow itself.
    ok, detail = dispatch('department-head.yml')
    actions.append({'department': 'growth', 'manager': 'AGENT_7', 'workflow': 'department-head.yml', 'dispatched': ok, 'detail': detail})

    # Customer Success is checked every CEO cycle. Its own manager decides which lifecycle agent needs action.
    ok, detail = dispatch('customer-success.yml')
    actions.append({'department': 'customer_success', 'manager': 'AGENT_10', 'workflow': 'customer-success.yml', 'dispatched': ok, 'detail': detail})

    result = {
        'status': 'HEALTHY' if all(x['dispatched'] for x in actions) else 'ATTENTION',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'rule': 'CEO_ENFORCES_DEPARTMENT_EXECUTION_NOT_JUST_REPORTING',
        'department_reviews': {
            'growth': {
                'last_manager_run_at': growth.get('last_run_at', 'UNKNOWN'),
                'age_minutes_before_dispatch': age_minutes(growth.get('last_run_at')),
                'last_status': growth.get('department_status', 'UNKNOWN'),
                'last_next_agent': growth.get('next_agent', 'UNKNOWN'),
                'last_dispatch_success': growth.get('dispatch_success', 'UNKNOWN'),
            },
            'customer_success': {
                'last_manager_run_at': cs.get('last_run_at', 'UNKNOWN'),
                'age_minutes_before_dispatch': age_minutes(cs.get('last_run_at')),
                'last_status': cs.get('department_status', 'UNKNOWN'),
                'last_next_agent': cs.get('next_agent', 'UNKNOWN'),
                'active_trials': cs.get('active_trials', 'UNKNOWN'),
                'paid_customers': cs.get('paid_customers', 'UNKNOWN'),
            },
        },
        'actions': actions,
        'success_criteria': {
            'growth': 'manager reviews pipeline and dispatches the highest-value approved child workflow',
            'customer_success': 'manager reviews every active trial/customer and runs lifecycle communication/actions when due',
            'linkedin': 'Agent 8 independently enforces hourly daily lead/contact targets in browser-agent and reports to CEO',
        },
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
