from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from playwright.sync_api import sync_playwright

SCOUT_RESULTS = Path('results/intent_scout_results.json')
SCOUT_STATE = Path('results/intent_scout_state.json')
FALLBACK_STATE = Path('results/intent_browser_fallback_state.json')
FALLBACK_LATEST = Path('results/intent_browser_fallback_latest.json')
LEARNING = Path('results/intent_learning_config.json')

QUERIES = [
    '"Google Business Profile" German Local SEO freelancer job',
    '"Google Unternehmensprofil" Freelancer Deutschland',
    '"Local SEO" Projekt Deutschland Freelancer',
    '"Google Maps" Ranking Hilfe Unternehmen Deutschland',
    'site:upwork.com/freelance-jobs "Google Business Profile" German',
    'site:upwork.com/freelance-jobs "Local SEO" Germany',
    'site:freelancermap.de "Local SEO"',
    'site:reddit.com "Google Unternehmensprofil" Deutschland',
]

RELEVANCE = (
    'google business', 'google business profile', 'google unternehmensprofil',
    'google maps', 'local seo', 'lokale sichtbarkeit', 'local search', 'standort'
)
ACTIVE_INTENT = (
    'suche ', 'gesucht', 'sucht ', 'freelancer', 'projekt', 'auftrag', 'ausschreibung',
    'hiring', 'hire ', 'looking for', 'seeking', 'seeks ', 'needed', 'need ', 'job ',
    'specialist wanted', 'expert wanted', 'support needed', 'hilfe gesucht'
)
INFORMATIONAL = (
    'what is', 'was ist', 'guide', 'leitfaden', 'learn how', 'how to', 'tutorial',
    'definition', 'lexikon', 'course', 'kurs', 'ausbildung', 'webinar',
    'definitive guide', 'comprehensive guide'
)
EXCLUDED = (
    'rechtsanwalt','anwalt','kanzlei','notar','notariat','patentanwalt',
    'steuerberater','steuerberatung','wirtschaftspruefer','wirtschaftsprüfer'
)
JOB_MARKETPLACES = (
    'upwork.com', 'freelancermap.de', 'freelancer.com', 'peopleperhour.com',
    'contra.com', 'malt.de', 'malt.com'
)
BLOCK_MARKERS = (
    'captcha', 'verify you are human', 'unusual traffic', 'access denied',
    'security check', 'temporarily blocked', 'challenge-platform'
)


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix('www.')


def site_constraint(query: str) -> str | None:
    m = re.search(r'\bsite:([^\s\"]+)', query, flags=re.I)
    return m.group(1).lower().removeprefix('www.') if m else None


def unwrap_bing_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if 'bing.com' not in parsed.netloc.lower():
            return url
        raw = parse_qs(parsed.query).get('u', [None])[0]
        if not raw:
            return url
        raw = unquote(raw)
        if raw.startswith('a1'):
            payload = raw[2:]
            payload += '=' * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload.encode()).decode('utf-8', errors='replace')
            if decoded.startswith('http'):
                return decoded
        if raw.startswith('http'):
            return raw
    except Exception:
        pass
    return url


def query_plan(config: dict) -> list[str]:
    paused = set(config.get('paused_queries') or [])
    weights = config.get('query_weights') or {}
    active = [q for q in QUERIES if q not in paused]
    if len(active) < 3:
        active = list(QUERIES)
    return sorted(active, key=lambda q: float(weights.get(q, 1.0)), reverse=True)


def qualify(url: str, title: str, snippet: str, query: str, provider: str, blocked_domains: set[str] | None = None):
    target = unwrap_bing_url(url)
    if not target.startswith('http'):
        return None

    host = host_of(target)
    text = f'{title} {snippet}'.lower()
    expected = site_constraint(query)

    if expected and not (host == expected or host.endswith('.' + expected)):
        return None
    if host in {'bing.com', 'google.com', 'duckduckgo.com'}:
        return None
    if blocked_domains and any(host == d or host.endswith('.' + d) for d in blocked_domains):
        return None
    if any(x in text for x in EXCLUDED):
        return None
    if any(x in text for x in INFORMATIONAL) and not any(x in text for x in ACTIVE_INTENT):
        return None

    relevant_hits = sum(1 for x in RELEVANCE if x in text)
    intent_hits = sum(1 for x in ACTIVE_INTENT if x in text)
    marketplace = any(host == d or host.endswith('.' + d) for d in JOB_MARKETPLACES)
    if relevant_hits < 1:
        return None
    if intent_hits < 1 and not marketplace:
        return None

    score = relevant_hits + intent_hits + (2 if marketplace else 0)
    sid = 'intent-' + hashlib.sha1(target.encode('utf-8')).hexdigest()[:16]
    return {
        'signal_id': sid,
        'source': host,
        'source_url': target,
        'title': title[:500],
        'snippet': snippet[:2000],
        'query': query,
        'raw_score': score,
        'provider': provider,
        'intent_evidence': {
            'relevance_hits': relevant_hits,
            'active_intent_hits': intent_hits,
            'marketplace_listing': marketplace,
        },
    }


def should_run() -> bool:
    state = load(SCOUT_STATE, {})
    return bool(state.get('errors')) or int(state.get('new_signals') or 0) == 0


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    learning = load(LEARNING, {})
    plan = query_plan(learning)
    blocked_domains = set(learning.get('blocked_domains') or [])

    if not should_run():
        state = {
            'agent': 'AGENT_15A_BROWSER_FALLBACK', 'last_run_at': now,
            'status': 'SKIPPED_NOT_NEEDED', 'signals_added': 0, 'pages_checked': 0,
            'blocked_pages': 0, 'human_action_required': 0, 'errors': 0,
            'learning_config_used': bool(learning), 'queries_planned': len(plan),
        }
        FALLBACK_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(state, ensure_ascii=False))
        return

    scout = load(SCOUT_RESULTS, {'signals': []})
    retained = []
    removed_stale = 0
    for old in scout.get('signals', []):
        if old.get('browser_fallback'):
            refreshed = qualify(
                old.get('source_url') or '', old.get('title') or '', old.get('snippet') or '',
                old.get('query') or '', old.get('provider') or 'cloud_browser_bing', blocked_domains,
            )
            if not refreshed:
                removed_stale += 1
                continue
        retained.append(old)

    existing = {x.get('source_url'): x for x in retained if x.get('source_url')}
    added, blockers, errors = [], [], []
    pages_checked = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        context = browser.new_context(
            locale='de-DE', timezone_id='Europe/Berlin',
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
            viewport={'width': 1440, 'height': 1000},
        )
        page = context.new_page()

        for query in plan:
            search_url = 'https://www.bing.com/search?q=' + quote(query)
            try:
                page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                pages_checked += 1
                body = (page.locator('body').inner_text(timeout=8000) or '')[:12000]
                lower = body.lower()
                if any(marker in lower for marker in BLOCK_MARKERS):
                    blockers.append({'query': query, 'url': page.url, 'reason': 'browser_security_or_access_block'})
                    continue

                links = page.locator('li.b_algo h2 a')
                count = min(links.count(), 10)
                for i in range(count):
                    a = links.nth(i)
                    href = a.get_attribute('href') or ''
                    title = (a.inner_text(timeout=3000) or '').strip()
                    li = a.locator('xpath=ancestor::li[contains(@class,"b_algo")]')
                    try:
                        snippet = (li.inner_text(timeout=3000) or '').strip()
                    except Exception:
                        snippet = title
                    item = qualify(href, title, snippet, query, 'cloud_browser_bing', blocked_domains)
                    if not item or item['source_url'] in existing:
                        continue
                    item.update({
                        'detected_at': now, 'review_status': 'PENDING_QA',
                        'department': 'intent_opportunity_acquisition', 'browser_fallback': True,
                    })
                    existing[item['source_url']] = item
                    added.append(item)
            except Exception as exc:
                errors.append({'query': query, 'error': str(exc)[:300]})

        browser.close()

    signals = sorted(existing.values(), key=lambda x: x.get('detected_at', ''), reverse=True)[:500]
    SCOUT_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    SCOUT_RESULTS.write_text(json.dumps({'generated_at': now, 'signals': signals}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    state = {
        'agent': 'AGENT_15A_BROWSER_FALLBACK', 'last_run_at': now,
        'status': 'HUMAN_ACTION_REQUIRED' if blockers else ('OK' if not errors else 'PARTIAL'),
        'signals_added': len(added), 'stale_signals_removed': removed_stale,
        'pages_checked': pages_checked, 'blocked_pages': len(blockers),
        'human_action_required': len(blockers), 'errors': len(errors),
        'learning_config_used': bool(learning), 'queries_planned': len(plan),
        'blocked_domains_learned': len(blocked_domains),
    }
    FALLBACK_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    FALLBACK_LATEST.write_text(json.dumps({
        'state': state, 'query_plan': plan, 'added': added[:25],
        'blockers': blockers[:10], 'errors': errors[:10],
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
