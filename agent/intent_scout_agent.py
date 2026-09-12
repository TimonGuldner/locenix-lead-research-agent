from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/intent_department_task.json')
OUT = Path('results/intent_scout_results.json')
STATE = Path('results/intent_scout_state.json')
LATEST = Path('results/intent_scout_latest.json')

QUERIES = [
    '"Google Unternehmensprofil" Hilfe gesucht Deutschland',
    '"Google Business Profile" Freelancer Deutschland',
    '"Google Maps" "Local SEO" Freelancer Deutschland',
    '"Local SEO" Projekt Freelancer Deutschland',
    '"Google Unternehmensprofil" Freelancer Österreich',
    '"Google Unternehmensprofil" Freelancer Schweiz',
    'site:freelancermap.de "Local SEO"',
    'site:freelancermap.de "Google Business Profile"',
    'site:reddit.com "Google Unternehmensprofil" Deutschland',
    'site:reddit.com "Google Maps" "Local SEO" Deutschland',
    '"Local SEO Manager" Deutschland Google Business Profile',
    '"Google Business Profile" Agentur gesucht Deutschland',
]

POSITIVE = (
    'suche', 'gesucht', 'freelancer', 'projekt', 'auftrag', 'hilfe', 'support',
    'berater', 'agentur', 'manager', 'spezialist', 'experte', 'betreuung',
    'optimierung', 'ranking', 'sichtbarkeit', 'google business',
    'google unternehmensprofil', 'google maps', 'local seo', 'standort',
)
NEGATIVE = ('kurs', 'ausbildung', 'definition', 'was ist', 'wiki', 'wikipedia')
EXCLUDED = (
    'rechtsanwalt', 'anwalt', 'kanzlei', 'notar', 'notariat', 'patentanwalt',
    'steuerberater', 'steuerberatung', 'wirtschaftspruefer', 'wirtschaftsprüfer',
)


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def fetch_ddg(query: str) -> str:
    url = 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query, 'kl': 'de-de'})
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; LOCENIX-IntentScout/1.0; +https://locenix.com)'
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode('utf-8', errors='replace')


def clean_text(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def decode_result_url(raw: str) -> str:
    raw = html.unescape(raw)
    if raw.startswith('//'):
        raw = 'https:' + raw
    parsed = urllib.parse.urlparse(raw)
    qs = urllib.parse.parse_qs(parsed.query)
    if 'uddg' in qs:
        return urllib.parse.unquote(qs['uddg'][0])
    return raw


def parse_results(page: str, query: str) -> list[dict]:
    blocks = re.split(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>', page, flags=re.I)
    found = []
    for block in blocks[1:]:
        m = re.search(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        if not m:
            continue
        url = decode_result_url(m.group(1))
        title = clean_text(m.group(2))
        sm = re.search(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>', block, flags=re.I | re.S)
        snippet = clean_text((sm.group(1) or sm.group(2)) if sm else '')
        text = f'{title} {snippet}'.lower()
        if not url.startswith('http'):
            continue
        if any(x in text for x in EXCLUDED):
            continue
        if any(x in text for x in NEGATIVE) and not any(x in text for x in ('gesucht', 'suche', 'projekt', 'auftrag')):
            continue
        score = sum(1 for k in POSITIVE if k in text)
        if score < 2:
            continue
        source = urllib.parse.urlparse(url).netloc.lower().removeprefix('www.')
        key = hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]
        found.append({
            'signal_id': f'intent-{key}',
            'source': source,
            'source_url': url,
            'title': title,
            'snippet': snippet,
            'query': query,
            'raw_score': score,
        })
    return found


def main() -> None:
    task = load(TASK, {})
    now = datetime.now(timezone.utc).isoformat()
    previous = load(OUT, {'signals': []})
    existing = {x.get('source_url'): x for x in previous.get('signals', []) if x.get('source_url')}
    new_count = 0
    query_errors = []

    for query in QUERIES:
        try:
            page = fetch_ddg(query)
            for item in parse_results(page, query):
                if item['source_url'] in existing:
                    continue
                item['detected_at'] = now
                item['review_status'] = 'PENDING_QA'
                item['department'] = 'intent_opportunity_acquisition'
                existing[item['source_url']] = item
                new_count += 1
        except Exception as exc:
            query_errors.append({'query': query, 'error': str(exc)[:300]})

    signals = list(existing.values())
    signals.sort(key=lambda x: x.get('detected_at', ''), reverse=True)
    signals = signals[:500]
    unreviewed = sum(1 for x in signals if x.get('review_status') == 'PENDING_QA')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'generated_at': now, 'signals': signals}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    state = {
        'agent': 'AGENT_15A_INTENT_SCOUT',
        'last_run_at': now,
        'signals_found': len(signals),
        'new_signals': new_count,
        'unreviewed': unreviewed,
        'queries_run': len(QUERIES),
        'errors': len(query_errors),
        'query_errors': query_errors[:5],
        'sources': sorted({x.get('source') for x in signals if x.get('source')}),
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    LATEST.write_text(json.dumps({'state': state, 'newest': signals[:20]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
