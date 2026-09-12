from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/ceo_task.json')
REPORT = Path('results/ceo_latest.json')
STATE = Path('results/ceo_state.json')
LINKEDIN_DIRECTIVE = Path('results/linkedin_directive.json')
AGENTS_DIR = Path('tasks/agents')
DEPARTMENTS_DIR = Path('tasks/departments')
LINKEDIN_STATE_URL = 'https://raw.githubusercontent.com/TimonGuldner/browser-agent/main/results/linkedin_department_state.json'

SOURCES = {
    'research': Path('results/deterministic_state.json'),
    'qa': Path('results/deep_qa_state.json'),
    'visibility': Path('results/visibility_state.json'),
    'sales': Path('results/sales_queue_state.json'),
    'outreach': Path('results/outreach_controller_state.json'),
    'manager': Path('results/department_head_state.json'),
    'email': Path('results/email_conversation_state.json'),
}


def load(path, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def load_url(url, default=None):
    if default is None:
        default = {}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'locenix-ceo-agent'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return default


def val(obj, key):
    v = obj.get(key)
    return 'UNKNOWN' if v is None else v


def proposed_count(directory: Path):
    if not directory.exists():
        return 0
    n = 0
    for p in directory.glob('*.json'):
        d = load(p)
        if str(d.get('status', '')).upper() == 'PROPOSED':
            n += 1
    return n


def metrics(s):
    li = s.get('linkedin', {})
    em = s.get('email', {})
    return {
        'research_leads': val(s['research'], 'lead_count'),
        'research_a': val(s['research'], 'a_leads'),
        'research_b': val(s['research'], 'b_leads'),
        'qa_remaining': val(s['qa'], 'remaining_eligible'),
        'final_a': val(s['qa'], 'final_a_total'),
        'final_b': val(s['qa'], 'final_b_total'),
        'visibility_remaining': val(s['visibility'], 'remaining_eligible'),
        'weak_visibility': val(s['visibility'], 'weak_visibility_total'),
        'sales_queue': val(s['sales'], 'queue_count'),
        'airtable_ready': val(s['sales'], 'airtable_ready_count'),
        'airtable_errors': s['sales'].get('airtable_errors', 'UNKNOWN'),
        'drafts_stored': val(s['outreach'], 'stored_count'),
        'draft_errors': val(s['outreach'], 'error_count'),
        'contacted': 'UNKNOWN',
        'replies': 'UNKNOWN',
        'trials': 'UNKNOWN',
        'paid_customers': 'UNKNOWN',
        'email_channel_status': 'AVAILABLE' if em else 'UNKNOWN',
        'email_auto_reply_enabled': val(em, 'auto_reply_enabled'),
        'email_received_scanned': val(em, 'received_scanned'),
        'email_handled': val(em, 'handled'),
        'email_unmatched': val(em, 'unmatched'),
        'email_errors': val(em, 'errors'),
        'email_positive_replies': val(em, 'positive_replies'),
        'email_trial_interest': val(em, 'trial_interest'),
        'email_human_reviews_required': val(em, 'human_reviews_required'),
        'email_last_run_at': val(em, 'last_run_at'),
        'linkedin_department_status': val(li, 'department_status'),
        'linkedin_biggest_bottleneck': val(li, 'biggest_bottleneck'),
        'linkedin_leads': val(li, 'leads'),
        'linkedin_qualified_leads': val(li, 'qualified_leads'),
        'linkedin_active_conversations': val(li, 'active_conversations'),
        'linkedin_replies': val(li, 'replies'),
        'linkedin_positive_signals': val(li, 'positive_signals'),
        'linkedin_open_followups': val(li, 'open_followups'),
        'linkedin_overdue_followups': val(li, 'overdue_followups'),
        'linkedin_trials': val(li, 'trials'),
        'linkedin_paid_customers': val(li, 'paid_customers'),
        'linkedin_queue_backlog': val(li, 'queue_backlog'),
        'linkedin_failed_jobs': val(li, 'failed_jobs'),
        'proposed_agents': proposed_count(AGENTS_DIR),
        'proposed_departments': proposed_count(DEPARTMENTS_DIR),
    }


def number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def decide(m, linkedin, email):
    priorities = []
    errors = m['airtable_errors']
    if errors not in ('UNKNOWN', [], None) or (number(m['draft_errors']) or 0) > 0:
        priorities.append({'rank': 1, 'type': 'STABILITY', 'owner': 'Agent 7', 'action': 'Resolve downstream sync/draft errors before increasing pipeline volume.', 'reason': 'Downstream errors can propagate bad state.'})

    email_errors = number(m['email_errors'])
    if email_errors is not None and email_errors > 0:
        priorities.append({'rank': 0, 'type': 'STABILITY', 'owner': 'Agent 9', 'action': 'Resolve email processing errors before scaling the email channel.', 'reason': f'Agent 9 reports {email_errors} email processing errors.'})

    email_reviews = number(m['email_human_reviews_required'])
    if email_reviews is not None and email_reviews > 0:
        priorities.append({'rank': 0, 'type': 'HUMAN_REVIEW', 'owner': 'Agent 9', 'action': f'Review {email_reviews} email conversation(s) that require human decision.', 'reason': 'Email conversations are waiting for a human decision before a safe response.'})

    li_status = str(linkedin.get('department_status', 'UNKNOWN')).upper()
    if li_status == 'BLOCKED' or linkedin.get('ceo_escalation_required') is True:
        priorities.append({
            'rank': 0,
            'type': 'STABILITY',
            'owner': 'Agent 8',
            'action': linkedin.get('priority_1', {}).get('action') or 'Resolve the LinkedIn department blocker and report back to CEO.',
            'reason': linkedin.get('ceo_message') or 'LinkedIn Department Head escalated a blocker.',
        })

    qr = number(m['qa_remaining'])
    vr = number(m['visibility_remaining'])
    ar = number(m['airtable_ready'])
    fa = number(m['final_a'])
    ds = number(m['drafts_stored'])
    if qr and qr > 0:
        priorities.append({'rank': 0, 'type': 'BOTTLENECK', 'owner': 'Agent 7', 'action': f'Prioritize Deep QA until backlog falls below 5; current observed backlog: {qr}.', 'reason': 'Qualified input is waiting for validation.'})
    if vr and vr > 0:
        priorities.append({'rank': 0, 'type': 'BOTTLENECK', 'owner': 'Agent 7', 'action': f'Prioritize Maps Visibility; current observed backlog: {vr}.', 'reason': 'Validated leads are waiting for visibility evidence.'})
    if fa is not None and ar is not None and ar < fa:
        priorities.append({'rank': 0, 'type': 'BOTTLENECK', 'owner': 'Agent 7', 'action': 'Move remaining FINAL_A leads through Sales Queue/Airtable.', 'reason': 'Qualified leads have not all reached sales readiness.'})
    if ar is not None and ds is not None and ds < ar:
        priorities.append({'rank': 0, 'type': 'BOTTLENECK', 'owner': 'Agent 7', 'action': 'Generate and store missing outreach drafts.', 'reason': 'Sales-ready leads are missing prepared outreach.'})

    if li_status == 'ATTENTION' and str(linkedin.get('biggest_bottleneck', '')).upper() == 'EVIDENCE':
        priorities.append({'rank': 0, 'type': 'EVIDENCE', 'owner': 'Agent 8', 'action': 'Connect current LinkedIn operational metrics to the department report so channel performance becomes measurable.', 'reason': 'Agent 8 is healthy but currently lacks observed LinkedIn funnel metrics.'})
    if li_status in {'HEALTHY', 'ATTENTION'} and isinstance(linkedin.get('priority_1'), dict):
        p = linkedin['priority_1']
        if p.get('type') not in {'EVIDENCE'}:
            priorities.append({'rank': 0, 'type': 'CHANNEL', 'owner': 'Agent 8', 'action': p.get('action') or 'Execute the highest-value LinkedIn department priority.', 'reason': f"LinkedIn department priority: {p.get('type', 'UNKNOWN')}"})

    if not priorities:
        priorities.append({'rank': 0, 'type': 'GROWTH', 'owner': 'Agent 7', 'action': 'Keep research active and add high-quality qualified leads while downstream capacity is clear.', 'reason': 'No observed downstream bottleneck.'})

    order = {'STABILITY': 0, 'HUMAN_REVIEW': 1, 'BOTTLENECK': 2, 'EVIDENCE': 3, 'CHANNEL': 3, 'GROWTH': 4}
    priorities.sort(key=lambda x: order.get(x['type'], 5))
    priorities = priorities[:3]
    for i, p in enumerate(priorities, 1):
        p['rank'] = i
    p1 = priorities[0]
    status = 'BLOCKED' if p1['type'] == 'STABILITY' else ('ATTENTION' if p1['type'] in {'HUMAN_REVIEW', 'BOTTLENECK', 'EVIDENCE'} else 'HEALTHY')
    return status, priorities


def write_linkedin_directive(priorities, now):
    target = next((p for p in priorities if p.get('owner') == 'Agent 8'), None)
    if target is None:
        existing = load(LINKEDIN_DIRECTIVE, {})
        if existing:
            existing['status'] = 'COMPLETED' if existing.get('status') == 'IN_PROGRESS' else existing.get('status', 'COMPLETED')
            existing['last_ceo_review_at'] = now
            LINKEDIN_DIRECTIVE.parent.mkdir(parents=True, exist_ok=True)
            LINKEDIN_DIRECTIVE.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return None
    raw = f"linkedin|{target.get('type')}|{target.get('action')}"
    directive_id = 'li-' + hashlib.sha1(raw.encode()).hexdigest()[:12]
    previous = load(LINKEDIN_DIRECTIVE, {})
    created_at = previous.get('created_at') if previous.get('directive_id') == directive_id else now
    directive = {
        'directive_id': directive_id,
        'department': 'linkedin',
        'owner_agent': 'AGENT_8_LINKEDIN_DEPARTMENT_HEAD',
        'objective': target.get('action'),
        'reason': target.get('reason'),
        'priority': target.get('rank'),
        'success_metric': 'Agent 8 reports the bottleneck reduced or resolved using observed Airtable/Supabase evidence.',
        'created_at': created_at,
        'updated_at': now,
        'status': 'QUEUED',
        'external_action_authorized': False,
    }
    LINKEDIN_DIRECTIVE.parent.mkdir(parents=True, exist_ok=True)
    LINKEDIN_DIRECTIVE.write_text(json.dumps(directive, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return directive


def main():
    task = load(TASK)
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return
    s = {k: load(v) for k, v in SOURCES.items()}
    s['linkedin'] = load_url(LINKEDIN_STATE_URL)
    m = metrics(s)
    status, priorities = decide(m, s['linkedin'], s['email'])
    now = datetime.now(timezone.utc).isoformat()
    directive = write_linkedin_directive(priorities, now)
    report = {
        'agent': 'AGENT_0_LOCENIX_CEO',
        'generated_at': now,
        'company_status': status,
        'north_star': task.get('north_star', 'paid_customers'),
        'funnel_metrics': m,
        'departments': {
            'growth': {'manager': 'AGENT_7_LOCENIX_GROWTH_MANAGER', 'health': 'AVAILABLE' if s['manager'] else 'UNKNOWN'},
            'email': {
                'manager': 'AGENT_7_LOCENIX_GROWTH_MANAGER',
                'agent': 'AGENT_9_EMAIL_CONVERSATION_AGENT',
                'health': 'AVAILABLE' if s['email'] else 'UNKNOWN',
                'auto_reply_enabled': val(s['email'], 'auto_reply_enabled'),
                'positive_replies': val(s['email'], 'positive_replies'),
                'trial_interest': val(s['email'], 'trial_interest'),
                'human_reviews_required': val(s['email'], 'human_reviews_required'),
                'errors': val(s['email'], 'errors'),
                'last_run_at': val(s['email'], 'last_run_at'),
            },
            'linkedin': {
                'manager': 'AGENT_8_LINKEDIN_DEPARTMENT_HEAD',
                'health': val(s['linkedin'], 'department_health'),
                'status': val(s['linkedin'], 'department_status'),
                'biggest_bottleneck': val(s['linkedin'], 'biggest_bottleneck'),
                'priority_1': s['linkedin'].get('priority_1'),
                'ceo_escalation_required': s['linkedin'].get('ceo_escalation_required', 'UNKNOWN'),
                'last_run_at': val(s['linkedin'], 'last_run_at'),
            },
        },
        'biggest_bottleneck': priorities[0]['reason'],
        'priority_1': priorities[0],
        'priority_2': priorities[1] if len(priorities) > 1 else None,
        'priority_3': priorities[2] if len(priorities) > 2 else None,
        'manager_directive': priorities[0]['action'],
        'linkedin_directive': directive,
        'agent_health': {k: ('AVAILABLE' if s[k] else 'UNKNOWN') for k in s},
        'opportunities': [],
        'risks': ([priorities[0]['reason']] if status != 'HEALTHY' else []),
        'proposed_agents': m['proposed_agents'],
        'proposed_departments': m['proposed_departments'],
        'human_decision_required': bool(m['proposed_agents'] or m['proposed_departments'] or (number(m['email_human_reviews_required']) or 0) > 0 or s['linkedin'].get('human_decision_required') is True),
        'autonomy': task.get('autonomy', {}),
        'executive_rule': 'DECIDE_DELEGATE_MEASURE_IMPROVE',
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    STATE.write_text(json.dumps({
        'last_run_at': now,
        'company_status': status,
        'north_star': report['north_star'],
        'priority_1': priorities[0]['action'],
        'manager_directive': report['manager_directive'],
        'email_channel_status': m['email_channel_status'],
        'email_positive_replies': m['email_positive_replies'],
        'email_trial_interest': m['email_trial_interest'],
        'email_human_reviews_required': m['email_human_reviews_required'],
        'email_errors': m['email_errors'],
        'email_last_run_at': m['email_last_run_at'],
        'linkedin_department_status': m['linkedin_department_status'],
        'linkedin_biggest_bottleneck': m['linkedin_biggest_bottleneck'],
        'linkedin_directive_id': directive.get('directive_id') if directive else 'NONE',
        'human_decision_required': report['human_decision_required'],
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
