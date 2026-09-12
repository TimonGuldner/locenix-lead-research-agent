from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/department_head_task.json')
REPORT = Path('results/department_head_latest.json')
STATE = Path('results/department_head_state.json')
DEPARTMENTS_DIR = Path('tasks/departments')

STATE_FILES = {
    'lead_research': Path('results/deterministic_state.json'),
    'deep_qa': Path('results/deep_qa_state.json'),
    'visibility': Path('results/visibility_state.json'),
    'sales_queue': Path('results/sales_queue_state.json'),
    'outreach_controller': Path('results/outreach_controller_state.json'),
}


def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def load_departments() -> list[dict]:
    if not DEPARTMENTS_DIR.exists():
        return []
    items = []
    for path in sorted(DEPARTMENTS_DIR.glob('*.json')):
        data = load(path, {})
        if not isinstance(data, dict):
            continue
        data['_file'] = str(path)
        items.append(data)
    return items


def summarize(states: dict, departments: list[dict]) -> dict:
    proposed = [d for d in departments if str(d.get('status', '')).upper() == 'PROPOSED']
    return {
        'lead_count': int(states['lead_research'].get('lead_count') or 0),
        'lead_a': int(states['lead_research'].get('a_leads') or 0),
        'lead_b': int(states['lead_research'].get('b_leads') or 0),
        'qa_remaining': int(states['deep_qa'].get('remaining_eligible') or 0),
        'qa_final_a': int(states['deep_qa'].get('final_a_total') or 0),
        'qa_final_b': int(states['deep_qa'].get('final_b_total') or 0),
        'visibility_remaining': int(states['visibility'].get('remaining_eligible') or 0),
        'weak_visibility': int(states['visibility'].get('weak_visibility_total') or 0),
        'sales_queue_count': int(states['sales_queue'].get('queue_count') or 0),
        'airtable_ready': int(states['sales_queue'].get('airtable_ready_count') or 0),
        'airtable_errors': states['sales_queue'].get('airtable_errors') or [],
        'drafts_eligible': int(states['outreach_controller'].get('eligible_count') or 0),
        'drafts_stored': int(states['outreach_controller'].get('stored_count') or 0),
        'draft_errors': int(states['outreach_controller'].get('error_count') or 0),
        'sent_count': int(states['outreach_controller'].get('sent_count') or 0),
        'departments_total': len(departments),
        'departments_proposed': len(proposed),
        'proposed_department_names': [d.get('name') for d in proposed if d.get('name')][:10],
    }


def decide(metrics: dict) -> dict:
    if metrics['airtable_errors']:
        return {'status': 'BLOCKED', 'next_agent': None, 'reason': 'Airtable sync has errors; do not push more downstream work until fixed.'}
    if metrics['draft_errors'] > 0:
        return {'status': 'ACTION_REQUIRED', 'next_agent': 'outreach_controller', 'reason': 'Outreach draft storage has errors.'}
    if metrics['departments_proposed'] > 0:
        names = ', '.join(metrics['proposed_department_names'][:3])
        return {'status': 'MANAGEMENT_REVIEW', 'next_agent': None, 'reason': f"{metrics['departments_proposed']} neue Abteilung(en) warten auf Management-Prüfung: {names}."}
    if metrics['qa_remaining'] > 0:
        return {'status': 'BACKLOG', 'next_agent': 'deep_qa', 'reason': f"Deep QA has {metrics['qa_remaining']} eligible leads waiting."}
    if metrics['visibility_remaining'] > 0:
        return {'status': 'BACKLOG', 'next_agent': 'visibility', 'reason': f"Visibility has {metrics['visibility_remaining']} eligible leads waiting."}
    if metrics['airtable_ready'] < metrics['qa_final_a']:
        return {'status': 'BACKLOG', 'next_agent': 'sales_queue', 'reason': 'Qualified FINAL_A leads have not all reached the Airtable sales queue.'}
    if metrics['drafts_stored'] < metrics['airtable_ready']:
        return {'status': 'BACKLOG', 'next_agent': 'outreach_controller', 'reason': 'Airtable-ready leads are missing stored outreach drafts.'}
    return {'status': 'HEALTHY', 'next_agent': 'lead_research', 'reason': 'Downstream pipeline is clear; add new qualified leads.'}


def dispatch(workflow_file: str) -> tuple[bool, str]:
    repo = os.environ.get('GITHUB_REPOSITORY')
    token = os.environ.get('GITHUB_TOKEN')
    if not repo or not token:
        return False, 'Missing GITHUB_REPOSITORY or GITHUB_TOKEN.'
    url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches'
    data = json.dumps({'ref': 'main'}).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201, 204), f'HTTP {resp.status}'
    except Exception as exc:
        return False, str(exc)


def main() -> None:
    task = load(TASK, {})
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return

    states = {name: load(path, {}) for name, path in STATE_FILES.items()}
    departments = load_departments()
    metrics = summarize(states, departments)
    decision = decide(metrics)

    dispatched = False
    dispatch_detail = 'not requested'
    next_agent = decision.get('next_agent')
    workflows = task.get('workflows') or {}

    if task.get('auto_dispatch') and next_agent:
        workflow_file = workflows.get(next_agent)
        if workflow_file:
            dispatched, dispatch_detail = dispatch(workflow_file)
        else:
            dispatch_detail = 'No approved workflow mapping for next agent.'

    now = datetime.now(timezone.utc).isoformat()
    report = {
        'manager': 'LOCENIX_GROWTH_MANAGER_V2',
        'mode': task.get('mode', 'SUPERVISE_ONLY'),
        'generated_at': now,
        'department_status': decision['status'],
        'metrics': metrics,
        'departments': departments,
        'next_agent': next_agent,
        'reason': decision['reason'],
        'dispatch_attempted': bool(task.get('auto_dispatch') and next_agent),
        'dispatch_success': dispatched,
        'dispatch_detail': dispatch_detail,
        'guardrails': {
            'outreach_send_allowed': False,
            'contact_form_submit_allowed': False,
            'unknown_workflow_dispatch_allowed': False,
            'max_child_workflows_this_run': 1,
            'new_departments_start_as_proposed': True,
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    STATE.write_text(json.dumps({
        'last_run_at': now,
        'department_status': decision['status'],
        'next_agent': next_agent,
        'reason': decision['reason'],
        'departments_total': metrics['departments_total'],
        'departments_proposed': metrics['departments_proposed'],
        'dispatch_success': dispatched,
        'dispatch_detail': dispatch_detail,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
