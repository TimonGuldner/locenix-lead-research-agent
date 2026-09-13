from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

QA_RESULTS = Path('results/intent_qa_results.json')
SCOUT_STATE = Path('results/intent_scout_state.json')
TASK = Path('tasks/intent_department_task.json')
CONFIG = Path('results/intent_learning_config.json')
STATE = Path('results/intent_learning_state.json')
LATEST = Path('results/intent_learning_latest.json')

FIELD = {
    'lead_id': 'fld1Hub7WnDzd6xoR',
    'intent_source': 'fldugCRnpO7Sh4Z8F',
    'intent_url': 'fldYpBedpdGoCCWXP',
    'intent_tier': 'flduwOJl4hk6CDnUB',
    'intent_response_status': 'fld4434NOR0yVgdtB',
    'intent_conversation_status': 'fldczIdDCPfmt9UTW',
    'department': 'flddXQhaFhq1Q8G3b',
}

TIER_REWARD = {'HOT': 4.0, 'WARM': 2.0, 'WATCH': 0.25, 'REJECT': -2.0}
CONVERSATION_REWARD = {
    'POSITIVE': 6.0,
    'INTERESTED': 6.0,
    'QUESTION': 3.0,
    'TRIAL': 10.0,
    'PAID': 20.0,
    'PAID_CUSTOMER': 20.0,
    'NOT_NOW': 0.0,
    'NONE': 0.0,
    'UNKNOWN': 0.0,
    'NEGATIVE': -4.0,
    'UNSUBSCRIBE': -7.0,
    'DO_NOT_CONTACT': -7.0,
}
RESPONSE_REWARD = {'SENT': 0.25, 'REPLIED': 1.0}
PROTECTED_SOURCES = {'upwork.com', 'freelancermap.de', 'freelancer.com', 'malt.de', 'malt.com'}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def api_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def airtable_outcomes(base: str | None, table: str | None, token: str | None) -> dict[str, dict]:
    if not (base and table and token):
        return {}
    result: dict[str, dict] = {}
    offset = None
    while True:
        params = {'pageSize': 100, 'returnFieldsByFieldId': 'true'}
        if offset:
            params['offset'] = offset
        data = api_get(f'https://api.airtable.com/v0/{base}/{table}?' + urllib.parse.urlencode(params), token)
        for rec in data.get('records', []):
            f = rec.get('fields') or {}
            if f.get(FIELD['department']) != 'intent_opportunity_acquisition':
                continue
            lead_id = f.get(FIELD['lead_id'])
            if not lead_id:
                continue
            result[str(lead_id)] = {
                'conversation': str(f.get(FIELD['intent_conversation_status']) or 'NONE').upper(),
                'response': str(f.get(FIELD['intent_response_status']) or '').upper(),
                'source': str(f.get(FIELD['intent_source']) or '').lower(),
                'url': str(f.get(FIELD['intent_url']) or ''),
                'tier': str(f.get(FIELD['intent_tier']) or '').upper(),
            }
        offset = data.get('offset')
        if not offset:
            break
    return result


def bucket():
    return {'samples': 0, 'reward': 0.0, 'hot': 0, 'warm': 0, 'watch': 0, 'reject': 0, 'positive': 0, 'negative': 0}


def add_sample(store: dict, key: str, tier: str, reward: float, conversation: str):
    if not key:
        return
    b = store.setdefault(key, bucket())
    b['samples'] += 1
    b['reward'] += reward
    low = tier.lower()
    if low in b:
        b[low] += 1
    if conversation in {'POSITIVE', 'INTERESTED', 'QUESTION', 'TRIAL', 'PAID', 'PAID_CUSTOMER'}:
        b['positive'] += 1
    if conversation in {'NEGATIVE', 'UNSUBSCRIBE', 'DO_NOT_CONTACT'}:
        b['negative'] += 1


def summarize(store: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for key, b in store.items():
        samples = max(1, int(b['samples']))
        avg = round(float(b['reward']) / samples, 3)
        reject_rate = round(float(b['reject']) / samples, 3)
        qualified_rate = round(float(b['hot'] + b['warm']) / samples, 3)
        out[key] = {**b, 'avg_reward': avg, 'reject_rate': reject_rate, 'qualified_rate': qualified_rate}
    return out


def weight(avg_reward: float) -> float:
    return round(max(0.35, min(2.5, 1.0 + avg_reward / 5.0)), 3)


def provider_policy(previous: dict, scout: dict, run_count: int) -> dict:
    prev = previous.get('provider_policy') or {}
    hits = scout.get('provider_hits') or {}
    failures = defaultdict(int)
    for err in scout.get('query_errors') or []:
        failures[str(err.get('provider') or '')] += 1

    providers = set(prev) | set(hits) | set(failures)
    out = {}
    for provider in providers:
        old = prev.get(provider) or {}
        streak = int(old.get('fail_streak') or 0)
        if failures.get(provider, 0) > 0 and int(hits.get(provider) or 0) == 0:
            streak += 1
        elif int(hits.get(provider) or 0) > 0:
            streak = 0
        cooldown = streak >= 3
        # Even a cooled-down provider gets a probe every sixth learning cycle so recovery is detected.
        probe_due = cooldown and run_count % 6 == 0
        out[provider] = {
            'fail_streak': streak,
            'cooldown': cooldown and not probe_due,
            'probe_due': probe_due,
            'last_errors': int(failures.get(provider, 0)),
            'last_hits': int(hits.get(provider) or 0),
        }
    return out


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    task = load(TASK, {})
    qa = load(QA_RESULTS, {'items': []})
    scout = load(SCOUT_STATE, {})
    previous = load(CONFIG, {})
    run_count = int(previous.get('learning_run_count') or 0) + 1

    base = os.environ.get('AIRTABLE_BASE_ID') or task.get('airtable_base_id')
    table = os.environ.get('AIRTABLE_TABLE_ID') or task.get('airtable_table_id')
    token = os.environ.get('AIRTABLE_TOKEN')
    try:
        outcomes = airtable_outcomes(base, table, token)
        airtable_error = None
    except Exception as exc:
        outcomes = {}
        airtable_error = str(exc)[:300]

    by_query: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    by_provider: dict[str, dict] = {}
    evaluated = 0

    for item in qa.get('items', []):
        # Old parser bugs should not train the learner against a source/query.
        if item.get('quarantined'):
            continue
        tier = str(item.get('tier') or 'REJECT').upper()
        lead_id = str(item.get('signal_id') or '')
        outcome = outcomes.get(lead_id) or {}
        conversation = str(outcome.get('conversation') or 'NONE').upper()
        response = str(outcome.get('response') or '').upper()
        reward = TIER_REWARD.get(tier, 0.0)
        reward += CONVERSATION_REWARD.get(conversation, 0.0)
        reward += RESPONSE_REWARD.get(response, 0.0)

        query = str(item.get('query') or '').strip()
        source = str(item.get('source') or outcome.get('source') or '').lower().removeprefix('www.')
        provider = str(item.get('provider') or '')
        add_sample(by_query, query, tier, reward, conversation)
        add_sample(by_source, source, tier, reward, conversation)
        add_sample(by_provider, provider, tier, reward, conversation)
        evaluated += 1

    qstats = summarize(by_query)
    sstats = summarize(by_source)
    pstats = summarize(by_provider)

    query_weights = {q: weight(v['avg_reward']) for q, v in qstats.items()}
    source_weights = {s: weight(v['avg_reward']) for s, v in sstats.items()}

    paused_queries = sorted(
        q for q, v in qstats.items()
        if v['samples'] >= 5 and v['reject_rate'] >= 0.80 and v['positive'] == 0
    )
    blocked_domains = sorted(
        s for s, v in sstats.items()
        if s not in PROTECTED_SOURCES and v['samples'] >= 5 and v['reject_rate'] >= 0.90 and v['positive'] == 0
    )
    boosted_queries = sorted(
        (q for q, v in qstats.items() if v['samples'] >= 2 and v['qualified_rate'] >= 0.60),
        key=lambda q: query_weights.get(q, 1.0),
        reverse=True,
    )

    providers = provider_policy(previous, scout, run_count)
    config = {
        'agent': 'AGENT_15F_LEARNING_OPTIMIZER',
        'generated_at': now,
        'learning_run_count': run_count,
        'mode': 'SAFE_OUTCOME_LEARNING',
        'principle': 'Learn source/query/provider priorities from measured outcomes; never rewrite executable code autonomously.',
        'query_weights': query_weights,
        'source_weights': source_weights,
        'boosted_queries': boosted_queries,
        'paused_queries': paused_queries,
        'blocked_domains': blocked_domains,
        'provider_policy': providers,
        'minimum_samples_before_pause': 5,
        'stats': {'queries': qstats, 'sources': sstats, 'providers': pstats},
    }

    STATE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state = {
        'agent': 'AGENT_15F_LEARNING_OPTIMIZER',
        'last_run_at': now,
        'status': 'OK' if not airtable_error else 'PARTIAL',
        'learning_run_count': run_count,
        'evaluated_opportunities': evaluated,
        'queries_learned': len(qstats),
        'sources_learned': len(sstats),
        'boosted_queries': len(boosted_queries),
        'paused_queries': len(paused_queries),
        'blocked_domains': len(blocked_domains),
        'provider_cooldowns': sum(1 for x in providers.values() if x.get('cooldown')),
        'errors': 1 if airtable_error else 0,
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    LATEST.write_text(json.dumps({'state': state, 'config': config, 'airtable_error': airtable_error}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
