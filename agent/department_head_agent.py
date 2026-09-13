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
AGENTS_DIR = Path('tasks/agents')

STATE_FILES = {
    'lead_research': Path('results/deterministic_state.json'),
    'maps_learning': Path('results/maps_learning_state.json'),
    'deep_qa': Path('results/deep_qa_state.json'),
    'visibility': Path('results/visibility_state.json'),
    'sales_queue': Path('results/sales_queue_state.json'),
    'outreach_controller': Path('results/outreach_controller_state.json'),
    'email_sender': Path('results/email_sender_state.json'),
    'email_conversation': Path('results/email_conversation_state.json'),
}


def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def load_proposals(directory: Path, kind: str) -> list[dict]:
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.glob('*.json')):
        data = load(path, {})
        if isinstance(data, dict):
            data['_file'] = str(path)
            data['_kind'] = kind
            items.append(data)
    return items


def summarize(states: dict, departments: list[dict], agents: list[dict], task: dict) -> dict:
    proposed_departments = [d for d in departments if str(d.get('status', '')).upper() == 'PROPOSED']
    proposed_agents = [a for a in agents if str(a.get('status', '')).upper() == 'PROPOSED']
    email = states.get('email_conversation') or {}
    sender = states.get('email_sender') or {}
    learning = states.get('maps_learning') or {}
    research = states.get('lead_research') or {}
    qa = states.get('deep_qa') or {}
    sales = states.get('sales_queue') or {}
    eligible_research = int(research.get('a_leads') or 0) + int(research.get('b_leads') or 0)
    qa_processed = int(qa.get('processed_count') or 0)
    daily_targets = task.get('daily_targets') or {}
    email_target = int(daily_targets.get('approved_emails_sent') or 5)
    email_sent_today = int(sender.get('sent_today') or 0)
    return {
        'lead_count': int(research.get('lead_count') or 0),
        'lead_a': int(research.get('a_leads') or 0),
        'lead_b': int(research.get('b_leads') or 0),
        'eligible_research_count': eligible_research,
        'qa_processed_count': qa_processed,
        'research_unqa': max(0, eligible_research - qa_processed),
        'maps_learning_available': bool(learning),
        'maps_learning_status': learning.get('status', 'UNKNOWN'),
        'maps_learning_runs': int(learning.get('learning_run_count') or 0),
        'maps_learning_evaluated': int(learning.get('research_leads_evaluated') or 0),
        'maps_learning_downstream_evidence': int(learning.get('leads_with_downstream_evidence') or 0),
        'maps_learning_recommended_industry': learning.get('recommended_industry'),
        'maps_learning_recommended_city': learning.get('recommended_city'),
        'maps_learning_errors': int(learning.get('errors') or 0),
        'qa_remaining': int(qa.get('remaining_eligible') or 0),
        'qa_final_a': int(qa.get('final_a_total') or 0),
        'qa_final_b': int(qa.get('final_b_total') or 0),
        'qa_email_ready': int(qa.get('email_ready_total') or 0),
        'qa_email_missing': int(qa.get('email_missing_total') or 0),
        'visibility_remaining': int(states['visibility'].get('remaining_eligible') or 0),
        'visibility_email_gate_rejected': int(states['visibility'].get('email_gate_rejected_count') or 0),
        'weak_visibility': int(states['visibility'].get('weak_visibility_total') or 0),
        'sales_queue_count': int(sales.get('queue_count') or 0),
        'airtable_ready': int(sales.get('airtable_ready_count') or 0),
        'airtable_errors': sales.get('airtable_errors') or [],
        'sales_hard_email_gate_rejected': int(sales.get('hard_email_gate_rejected_count') or 0),
        'downstream_email_gate_violations': int(sales.get('downstream_email_gate_violations') or 0),
        'drafts_eligible': int(states['outreach_controller'].get('eligible_count') or 0),
        'drafts_stored': int(states['outreach_controller'].get('stored_count') or 0),
        'draft_errors': int(states['outreach_controller'].get('error_count') or 0),
        'sent_count': int(states['outreach_controller'].get('sent_count') or 0),
        'email_sender_available': bool(sender),
        'email_sender_mode': sender.get('mode', 'UNKNOWN'),
        'email_sender_eligible': int(sender.get('eligible') or 0),
        'email_sender_failed': int(sender.get('failed_count') or 0),
        'email_daily_target': email_target,
        'email_sent_today': email_sent_today,
        'email_daily_gap': max(0, email_target - email_sent_today),
        'email_agent_available': bool(email),
        'email_auto_reply_enabled': email.get('auto_reply_enabled', 'UNKNOWN'),
        'email_received_scanned': int(email.get('received_scanned') or 0),
        'email_handled': int(email.get('handled') or 0),
        'email_unmatched': int(email.get('unmatched') or 0),
        'email_errors': int(email.get('errors') or 0),
        'email_positive_replies': int(email.get('positive_replies') or 0),
        'email_trial_interest': int(email.get('trial_interest') or 0),
        'email_human_reviews_required': int(email.get('human_reviews_required') or 0),
        'email_last_run_at': email.get('last_run_at', 'UNKNOWN'),
        'departments_total': len(departments),
        'departments_proposed': len(proposed_departments),
        'agents_total': len(agents),
        'agents_proposed': len(proposed_agents),
        'proposed_names': [x.get('name') for x in proposed_departments + proposed_agents if x.get('name')][:10],
    }


def decide(metrics: dict) -> dict:
    if metrics['airtable_errors']:
        return {'status': 'BLOCKED', 'next_agent': None, 'reason': 'Airtable sync has errors; downstream work is paused.'}
    if metrics['downstream_email_gate_violations'] > 0:
        return {'status': 'BLOCKED', 'next_agent': 'sales_queue', 'reason': f"Hard email gate violated by {metrics['downstream_email_gate_violations']} downstream Maps lead(s). Rebuild Sales Queue immediately."}
    if metrics['email_errors'] > 0:
        return {'status': 'ACTION_REQUIRED', 'next_agent': None, 'reason': f"Agent 9 email channel has {metrics['email_errors']} processing errors and requires inspection."}
    if metrics['email_sender_failed'] > 0:
        return {'status': 'ACTION_REQUIRED', 'next_agent': 'email_sender', 'reason': f"Email sender has {metrics['email_sender_failed']} failed provider send(s); retry only after record-level eligibility remains valid."}
    if metrics['email_human_reviews_required'] > 0:
        return {'status': 'MANAGEMENT_REVIEW', 'next_agent': None, 'reason': f"Agent 9 has {metrics['email_human_reviews_required']} email conversation(s) waiting for human review."}
    if metrics['draft_errors'] > 0:
        return {'status': 'ACTION_REQUIRED', 'next_agent': 'outreach_controller', 'reason': 'Outreach draft storage has errors.'}
    proposed = metrics['departments_proposed'] + metrics['agents_proposed']
    if proposed > 0:
        names = ', '.join(metrics['proposed_names'][:4])
        return {'status': 'MANAGEMENT_REVIEW', 'next_agent': None, 'reason': f"{proposed} neue Agent-/Abteilungs-Vorschläge warten auf Prüfung: {names}."}

    # Keep new Maps leads moving through QA because email research happens there.
    if metrics['research_unqa'] > 0:
        return {'status': 'BACKLOG', 'next_agent': 'deep_qa', 'reason': f"{metrics['research_unqa']} A/B research lead(s) have not yet passed Deep QA/email research."}
    if metrics['qa_remaining'] > 0:
        return {'status': 'BACKLOG', 'next_agent': 'deep_qa', 'reason': f"Deep QA has {metrics['qa_remaining']} eligible leads waiting."}
    if metrics['visibility_remaining'] > 0:
        return {'status': 'BACKLOG', 'next_agent': 'visibility', 'reason': f"Visibility has {metrics['visibility_remaining']} email-qualified leads waiting."}
    if metrics['airtable_ready'] < metrics['qa_email_ready']:
        return {'status': 'BACKLOG', 'next_agent': 'sales_queue', 'reason': 'Email-qualified FINAL_A leads have not all reached Airtable Sales Queue.'}
    if metrics['drafts_stored'] < metrics['airtable_ready']:
        return {'status': 'BACKLOG', 'next_agent': 'outreach_controller', 'reason': 'Airtable-ready email-qualified leads are missing outreach drafts.'}

    if metrics['email_daily_gap'] > 0 and metrics['airtable_ready'] > 0:
        return {
            'status': 'DAILY_TARGET_GAP',
            'next_agent': 'email_sender',
            'reason': f"Approved email daily target is behind: {metrics['email_sent_today']}/{metrics['email_daily_target']} provider-confirmed sends today."
        }

    return {'status': 'HEALTHY', 'next_agent': 'lead_research', 'reason': 'Daily approved-email target is met and downstream pipeline is clear; add new qualified leads using Agent 4L learned Maps priorities.'}


def dispatch(workflow_file: str, inputs: dict | None = None) -> tuple[bool, str]:
    repo = os.environ.get('GITHUB_REPOSITORY')
    token = os.environ.get('GITHUB_TOKEN')
    if not repo or not token:
        return False, 'Missing GITHUB_REPOSITORY or GITHUB_TOKEN.'
    url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches'
    payload = {'ref': 'main'}
    if inputs:
        payload['inputs'] = inputs
    data = json.dumps(payload).encode('utf-8')
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
    departments = load_proposals(DEPARTMENTS_DIR, 'department')
    agents = load_proposals(AGENTS_DIR, 'agent')
    metrics = summarize(states, departments, agents, task)
    decision = decide(metrics)

    dispatched = False
    dispatch_detail = 'not requested'
    next_agent = decision.get('next_agent')
    workflows = task.get('workflows') or {}
    if task.get('auto_dispatch') and next_agent:
        workflow_file = workflows.get(next_agent)
        if workflow_file:
            inputs = {'live_send': True} if next_agent == 'email_sender' else None
            dispatched, dispatch_detail = dispatch(workflow_file, inputs)
        else:
            dispatch_detail = 'No approved workflow mapping for next agent.'

    now = datetime.now(timezone.utc).isoformat()
    report = {
        'manager': 'LOCENIX_GROWTH_MANAGER_V4_DAILY_OUTBOUND_ACCOUNTABILITY',
        'mode': task.get('mode', 'SUPERVISE_ONLY'),
        'generated_at': now,
        'department_status': decision['status'],
        'metrics': metrics,
        'daily_accountability': {
            'approved_email_target': metrics['email_daily_target'],
            'approved_email_actual': metrics['email_sent_today'],
            'approved_email_gap': metrics['email_daily_gap'],
            'maps_downstream_email_gate_target': 0,
            'maps_downstream_email_gate_actual': metrics['downstream_email_gate_violations'],
            'research_missing_email_rejected_from_sales': metrics['sales_hard_email_gate_rejected'],
            'rule': 'TARGET -> ACTUAL -> GAP -> ACTION -> RE-MEASURE. Missing-email Maps leads never enter downstream sales/outreach.'
        },
        'learning': {
            'agent': 'AGENT_4L_MAPS_RESEARCH_LEARNING_OPTIMIZER',
            'available': metrics['maps_learning_available'],
            'status': metrics['maps_learning_status'],
            'learning_runs': metrics['maps_learning_runs'],
            'research_leads_evaluated': metrics['maps_learning_evaluated'],
            'leads_with_downstream_evidence': metrics['maps_learning_downstream_evidence'],
            'recommended_industry': metrics['maps_learning_recommended_industry'],
            'recommended_city': metrics['maps_learning_recommended_city'],
            'errors': metrics['maps_learning_errors'],
        },
        'channels': {
            'email': {
                'sender': 'LOCENIX_EMAIL_SENDER',
                'conversation_agent': 'AGENT_9_EMAIL_CONVERSATION_AGENT',
                'daily_target': metrics['email_daily_target'],
                'sent_today': metrics['email_sent_today'],
                'daily_gap': metrics['email_daily_gap'],
                'sender_mode': metrics['email_sender_mode'],
                'sender_eligible': metrics['email_sender_eligible'],
                'auto_reply_enabled': metrics['email_auto_reply_enabled'],
                'positive_replies': metrics['email_positive_replies'],
                'trial_interest': metrics['email_trial_interest'],
                'human_reviews_required': metrics['email_human_reviews_required'],
                'errors': metrics['email_errors'],
                'last_run_at': metrics['email_last_run_at'],
            }
        },
        'departments': departments,
        'agent_proposals': agents,
        'next_agent': next_agent,
        'reason': decision['reason'],
        'dispatch_attempted': bool(task.get('auto_dispatch') and next_agent),
        'dispatch_success': dispatched,
        'dispatch_detail': dispatch_detail,
        'guardrails': {
            'unapproved_outreach_send_allowed': False,
            'approved_email_sender_dispatch_allowed': True,
            'email_sender_record_level_approval_required': True,
            'documented_legal_basis_required': True,
            'maps_missing_email_downstream_allowed': False,
            'contact_form_submit_allowed': False,
            'unknown_workflow_dispatch_allowed': False,
            'max_child_workflows_this_run': 1,
            'new_agents_and_departments_start_as_proposed': True,
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
        'agents_total': metrics['agents_total'],
        'agents_proposed': metrics['agents_proposed'],
        'dispatch_success': dispatched,
        'dispatch_detail': dispatch_detail,
        'research_unqa': metrics['research_unqa'],
        'maps_learning_available': metrics['maps_learning_available'],
        'maps_learning_status': metrics['maps_learning_status'],
        'maps_learning_runs': metrics['maps_learning_runs'],
        'maps_learning_recommended_industry': metrics['maps_learning_recommended_industry'],
        'maps_learning_recommended_city': metrics['maps_learning_recommended_city'],
        'maps_learning_errors': metrics['maps_learning_errors'],
        'maps_downstream_email_gate_violations': metrics['downstream_email_gate_violations'],
        'sales_hard_email_gate_rejected': metrics['sales_hard_email_gate_rejected'],
        'email_daily_target': metrics['email_daily_target'],
        'email_sent_today': metrics['email_sent_today'],
        'email_daily_gap': metrics['email_daily_gap'],
        'email_agent_available': metrics['email_agent_available'],
        'email_positive_replies': metrics['email_positive_replies'],
        'email_trial_interest': metrics['email_trial_interest'],
        'email_human_reviews_required': metrics['email_human_reviews_required'],
        'email_errors': metrics['email_errors'],
        'email_last_run_at': metrics['email_last_run_at'],
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
