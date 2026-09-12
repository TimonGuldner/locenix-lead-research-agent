from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from playwright.sync_api import sync_playwright

SCOUT_RESULTS = Path('results/intent_scout_results.json')
SCOUT_STATE = Path('results/intent_scout_state.json')
FALLBACK_STATE = Path('results/intent_browser_fallback_state.json')
FALLBACK_LATEST = Path('results/intent_browser_fallback_latest.json')

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

POSITIVE = (
    'suche','gesucht','freelancer','projekt','auftrag','hilfe','hiring','job',
    'specialist','expert','agentur','manager','optimierung','ranking','sichtbarkeit',
    'google business','google unternehmensprofil','google maps','local seo','standort'
)
EXCLUDED = (
    'rechtsanwalt','anwalt','kanzlei','notar','notariat','patentanwalt',
    'steuerberater','steuerberatung','wirtschaftspruefer','wirtschaftsprüfer'
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


def qualify(url: str, title: str, snippet: str, query: str, provider: str):
    text = f'{title} {snippet}'.lower()
    if not url.startswith('http'):
        return None
    if any(x in text for x in EXCLUDED):
        return None
    score = sum(1 for x in POSITIVE if x in text)
    if score < 2:
        return None
    host = urlparse(url).netloc.lower().removeprefix('www.')
    sid = 'intent-' + hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]
    return {
        'signal_id': sid,
        'source': host,
        'source_url': url,
        'title': title[:500],
        'snippet': snippet[:2000],
        'query': query,
        'raw_score': score,
        'provider': provider,
    }


def should_run() -> bool:
    state = load(SCOUT_STATE, {})
    return bool(state.get('errors')) or int(state.get('new_signals') or 0) == 0


def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    if not should_run():
        state = {
            'agent': 'AGENT_15A_BROWSER_FALLBACK',
            'last_run_at': now,
            'status': 'SKIPPED_NOT_NEEDED',
            'signals_added': 0,
            'pages_checked': 0,
            'blocked_pages': 0,
            'human_action_required': 0,
            'errors': 0,
        }
        FALLBACK_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(state, ensure_ascii=False))
        return

    scout = load(SCOUT_RESULTS, {'signals': []})
    existing = {x.get('source_url'): x for x in scout.get('signals', []) if x.get('source_url')}
    added = []
    blockers = []
    errors = []
    pages_checked = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-dev-shm-usage', '--no-sandbox'])
        context = browser.new_context(
            locale='de-DE',
            timezone_id='Europe/Berlin',
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
            viewport={'width': 1440, 'height': 1000},
        )
        page = context.new_page()

        for query in QUERIES:
            # Browser search fallback. Bing is used as a normal rendered browser search page;
            # no CAPTCHA/security challenge is bypassed.
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
                    item = qualify(href, title, snippet, query, 'cloud_browser_bing')
                    if not item or href in existing:
                        continue
                    item.update({
                        'detected_at': now,
                        'review_status': 'PENDING_QA',
                        'department': 'intent_opportunity_acquisition',
                        'browser_fallback': True,
                    })
                    existing[href] = item
                    added.append(item)
            except Exception as exc:
                errors.append({'query': query, 'error': str(exc)[:300]})

        browser.close()

    signals = sorted(existing.values(), key=lambda x: x.get('detected_at', ''), reverse=True)[:500]
    SCOUT_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    SCOUT_RESULTS.write_text(json.dumps({'generated_at': now, 'signals': signals}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    state = {
        'agent': 'AGENT_15A_BROWSER_FALLBACK',
        'last_run_at': now,
        'status': 'HUMAN_ACTION_REQUIRED' if blockers else ('OK' if not errors else 'PARTIAL'),
        'signals_added': len(added),
        'pages_checked': pages_checked,
        'blocked_pages': len(blockers),
        'human_action_required': len(blockers),
        'errors': len(errors),
    }
    FALLBACK_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    FALLBACK_LATEST.write_text(json.dumps({'state': state, 'added': added[:25], 'blockers': blockers[:10], 'errors': errors[:10]}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    main()
