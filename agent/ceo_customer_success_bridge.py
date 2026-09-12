import json
from pathlib import Path

CS = Path('results/customer_success_state.json')
REPORT = Path('results/ceo_latest.json')
STATE = Path('results/ceo_state.json')


def load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    cs = load(CS)
    if not cs:
        return
    report = load(REPORT)
    state = load(STATE)

    report.setdefault('departments', {})['customer_success'] = {
        'manager': 'AGENT_10_CUSTOMER_SUCCESS_DEPARTMENT_HEAD',
        'reports_to': 'AGENT_0_CEO',
        'subagents': ['AGENT_11_TRIAL_CONVERSION', 'AGENT_12_ONBOARDING', 'AGENT_13_RETENTION_SUCCESS'],
        'status': cs.get('department_status', 'UNKNOWN'),
        'active_trials': cs.get('active_trials', 'UNKNOWN'),
        'activated_trials': cs.get('activated_trials', 'UNKNOWN'),
        'paid_customers': cs.get('paid_customers', 'UNKNOWN'),
        'trial_to_paid_rate': cs.get('trial_to_paid_rate', 'UNKNOWN'),
        'customers_at_risk': cs.get('customers_at_risk', 'UNKNOWN'),
        'payment_issues': cs.get('payment_issues', 'UNKNOWN'),
        'technical_blockers': cs.get('technical_blockers', 'UNKNOWN'),
        'human_reviews_required': cs.get('human_reviews_required', 'UNKNOWN'),
        'biggest_bottleneck': cs.get('biggest_bottleneck', 'UNKNOWN'),
        'recommended_next_action': cs.get('recommended_next_action', 'UNKNOWN'),
        'last_run_at': cs.get('last_run_at', 'UNKNOWN'),
    }

    report.setdefault('funnel_metrics', {})['customer_success_active_trials'] = cs.get('active_trials', 'UNKNOWN')
    report['funnel_metrics']['customer_success_paid_customers'] = cs.get('paid_customers', 'UNKNOWN')
    report['funnel_metrics']['customer_success_at_risk'] = cs.get('customers_at_risk', 'UNKNOWN')

    if isinstance(cs.get('human_reviews_required'), int) and cs.get('human_reviews_required') > 0:
        report['human_decision_required'] = True

    state.update({
        'customer_success_department_status': cs.get('department_status', 'UNKNOWN'),
        'customer_success_active_trials': cs.get('active_trials', 'UNKNOWN'),
        'customer_success_paid_customers': cs.get('paid_customers', 'UNKNOWN'),
        'customer_success_customers_at_risk': cs.get('customers_at_risk', 'UNKNOWN'),
        'customer_success_human_reviews_required': cs.get('human_reviews_required', 'UNKNOWN'),
        'customer_success_biggest_bottleneck': cs.get('biggest_bottleneck', 'UNKNOWN'),
        'customer_success_last_run_at': cs.get('last_run_at', 'UNKNOWN'),
    })

    save(REPORT, report)
    save(STATE, state)


if __name__ == '__main__':
    main()
