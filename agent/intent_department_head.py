from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/intent_department_task.json')
STATE = Path('results/intent_department_state.json')
LATEST = Path('results/intent_department_latest.json')

CHILDREN = {
    'intent_scout': Path('results/intent_scout_state.json'),
    'intent_qa': Path('results/intent_qa_state.json'),
    'platform_access': Path('results/platform_access_state.json'),
    'intent_response': Path('results/intent_response_state.json'),
    'intent_conversation': Path('results/intent_conversation_state.json'),
}


def load(path: Path, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def n(obj: dict, key: str) -> int:
    try:
        return int(obj.get(key) or 0)
    except Exception:
        return 0


def decide(states: dict[str, dict]) -> tuple[str, str, str]:
    for name, state in states.items():
        if n(state, 'errors') or n(state, 'error_count'):
            return 'ATTENTION', name, f'{name} reports errors and should be inspected before scaling.'
        if n(state, 'human_action_required'):
            return 'HUMAN_ACTION_REQUIRED', name, f'{name} requires a manual platform/security action.'

    scout = states['intent_scout']
    qa = states['intent_qa']
    access = states['platform_access']
    response = states['intent_response']
    conversation = states['intent_conversation']

    if n(qa, 'pending') > 0 or n(scout, 'unreviewed') > 0:
        return 'BACKLOG', 'intent_qa', 'Fresh intent signals are waiting for QA/scoring.'
    if n(access, 'pending_registrations') > 0:
        return 'BACKLOG', 'platform_access', 'Approved source accounts are waiting for setup or verification.'
    if n(response, 'pending') > 0 or n(qa, 'hot') + n(qa, 'warm') > n(response, 'prepared'):
        return 'BACKLOG', 'intent_response', 'Qualified intent opportunities are waiting for response preparation.'
    if n(conversation, 'open_conversations') > 0 or n(conversation, 'followups_due') > 0:
        return 'ACTIVE', 'intent_conversation', 'Existing intent conversations need attention before adding volume.'
    return 'HEALTHY', 'intent_scout', 'No downstream backlog observed; find fresh high-intent DACH opportunities.'


def main() -> None:
    task = load(TASK)
    if not task.get('enabled'):
        print(json.dumps({'status': 'disabled'}))
        return

    states = {name: load(path) for name, path in CHILDREN.items()}
    status, next_agent, reason = decide(states)
    now = datetime.now(timezone.utc).isoformat()

    metrics = {
        'signals_found': n(states['intent_scout'], 'signals_found'),
        'hot': n(states['intent_qa'], 'hot'),
        'warm': n(states['intent_qa'], 'warm'),
        'watch': n(states['intent_qa'], 'watch'),
        'rejected': n(states['intent_qa'], 'rejected'),
        'responses_prepared': n(states['intent_response'], 'prepared'),
        'responses_sent': n(states['intent_response'], 'sent'),
        'positive_replies': n(states['intent_conversation'], 'positive'),
        'trials': n(states['intent_conversation'], 'trials'),
        'paid_customers': n(states['intent_conversation'], 'paid_customers'),
        'human_action_required': sum(n(s, 'human_action_required') for s in states.values()),
        'errors': sum(n(s, 'errors') + n(s, 'error_count') for s in states.values()),
    }

    report = {
        'agent': 'AGENT_15_INTENT_OPPORTUNITY_HEAD',
        'parent': 'AGENT_0_LOCENIX_CEO',
        'generated_at': now,
        'department_status': status,
        'next_agent': next_agent,
        'reason': reason,
        'metrics': metrics,
        'children': states,
        'source_of_truth': 'Airtable Leads',
        'registration_email': task.get('registration_email', 'hello@locenix.com'),
        'linkedin_new_account_allowed': False,
        'sales_handoff': 'existing_sales_pipeline',
        'customer_success_handoff': 'existing_customer_success_pipeline',
        'browser_policy': task.get('browser', {}),
        'guardrails': {
            'excluded_professions': task.get('excluded_professions', []),
            'captcha_bypass_allowed': False,
            'security_bypass_allowed': False,
            'new_linkedin_account_allowed': False,
        },
    }

    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    STATE.write_text(json.dumps({
        'last_run_at': now,
        'department_status': status,
        'next_agent': next_agent,
        'reason': reason,
        **metrics,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
