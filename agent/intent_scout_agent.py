from __future__ import annotations

import hashlib, html, json, re, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path('results/intent_scout_results.json')
STATE = Path('results/intent_scout_state.json')
LATEST = Path('results/intent_scout_latest.json')

QUERIES = [
    '"Google Business Profile" German Local SEO freelancer job',
    '"Google Unternehmensprofil" Freelancer Deutschland',
    '"Local SEO" Projekt Deutschland Freelancer',
    '"Google Maps" Ranking Hilfe Unternehmen Deutschland',
    'site:upwork.com/freelance-jobs "Google Business Profile" German',
    'site:upwork.com/freelance-jobs "Local SEO" Germany',
    'site:freelancermap.de "Local SEO"',
    'site:reddit.com/r/selbststaendig "Google Unternehmensprofil"',
]
REDDIT_QUERIES = ['Google Unternehmensprofil', 'Google Maps Local SEO', 'Google Business Profile']
SEEDS = [
    {
        'url':'https://www.upwork.com/freelance-jobs/apply/Google-Business-Profile-Local-SEO-Expert-German-Speaking_~022087054167304112185/',
        'title':'Google Business Profile & Local SEO Expert — German Speaking',
        'snippet':'Long-term collaboration across multiple client accounts; seeks German-speaking specialist for Google Business Profile, Google Maps and Local SEO.',
    },
    {
        'url':'https://www.upwork.com/freelance-jobs/apply/Google-Business-Profile-Local-SEO-Specialist-for-German-Local-Businesses_~022086915255634550070/',
        'title':'Google Business Profile & Local SEO Specialist for German Local Businesses',
        'snippet':'German agency seeks a long-term white-label Local SEO and Google Business Profile specialist for local business clients.',
    },
    {
        'url':'https://www.upwork.com/freelance-jobs/apply/German-Speaking-Marketing-Operations-Assistant-Google-Business-Profile-SEO_~022092564659254285945/',
        'title':'German-Speaking AI Marketing Operations Assistant – Google Business Profile & SEO',
        'snippet':'Multi-brand operator in Germany and Austria seeks ongoing support for Google Business Profiles, Local SEO and AI-assisted marketing operations.',
    },
]
POSITIVE = ('suche','gesucht','freelancer','projekt','auftrag','hilfe','hiring','job','specialist','expert','agentur','manager','optimierung','ranking','sichtbarkeit','google business','google unternehmensprofil','google maps','local seo')
NEGATIVE = ('kurs','ausbildung','definition','wikipedia','lexikon')
EXCLUDED = ('rechtsanwalt','anwalt','kanzlei','notar','notariat','patentanwalt','steuerberater','steuerberatung','wirtschaftspruefer','wirtschaftsprüfer')


def load(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; LOCENIX-IntentScout/3.0; +https://locenix.com)'})
    with urllib.request.urlopen(req, timeout=35) as r:
        return r.read().decode('utf-8', errors='replace')


def clean(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()


def qualify(url: str, title: str, snippet: str, query: str, provider: str):
    text = f'{title} {snippet}'.lower()
    if not url.startswith('http') or any(x in text for x in EXCLUDED): return None
    if any(x in text for x in NEGATIVE) and not any(x in text for x in ('gesucht','suche','projekt','auftrag','job','hiring')): return None
    score = sum(1 for x in POSITIVE if x in text)
    if score < 2: return None
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix('www.')
    sid = 'intent-' + hashlib.sha1(url.encode()).hexdigest()[:16]
    return {'signal_id':sid,'source':host,'source_url':url,'title':title[:500],'snippet':snippet[:2000],'query':query,'raw_score':score,'provider':provider}


def search_jina(query: str) -> list[dict]:
    text = fetch_text('https://s.jina.ai/' + urllib.parse.quote(query, safe=''))
    links = list(re.finditer(r'\[([^\]]{3,300})\]\((https?://[^)\s]+)\)', text))
    out=[]
    for i,m in enumerate(links[:25]):
        title=clean(m.group(1)); target=m.group(2).rstrip('.,')
        start=m.end(); end=links[i+1].start() if i+1 < len(links) else min(len(text), start+1200)
        item=qualify(target,title,clean(text[start:end])[:900],query,'jina_search')
        if item: out.append(item)
    return out


def search_ddg(query: str) -> list[dict]:
    page=fetch_text('https://html.duckduckgo.com/html/?'+urllib.parse.urlencode({'q':query,'kl':'de-de'})); out=[]
    for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.I|re.S):
        raw=html.unescape(m.group(1)); qs=urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        target=urllib.parse.unquote(qs.get('uddg',[raw])[0]); title=clean(m.group(2)); tail=page[m.end():m.end()+1800]
        sm=re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',tail,re.I|re.S)
        item=qualify(target,title,clean(sm.group(1)) if sm else '',query,'duckduckgo')
        if item: out.append(item)
    return out


def search_reddit(query: str) -> list[dict]:
    url='https://www.reddit.com/search.json?'+urllib.parse.urlencode({'q':query,'sort':'new','t':'month','limit':25,'raw_json':1})
    req=urllib.request.Request(url,headers={'User-Agent':'LOCENIX-IntentScout/3.0 by locenix.com'})
    with urllib.request.urlopen(req,timeout=30) as r: data=json.loads(r.read().decode('utf-8'))
    out=[]
    for child in data.get('data',{}).get('children',[]):
        d=child.get('data',{}); title=d.get('title',''); body=d.get('selftext','')[:1200]
        target='https://www.reddit.com'+d.get('permalink','')
        item=qualify(target,title,body,query,'reddit_api')
        if item: out.append(item)
    return out


def main():
    now=datetime.now(timezone.utc).isoformat(); prev=load(OUT,{'signals':[]})
    existing={x.get('source_url'):x for x in prev.get('signals',[]) if x.get('source_url')}
    errors=[]; new=0; provider_hits={'verified_seed':0,'reddit_api':0,'jina_search':0,'duckduckgo':0}

    for s in SEEDS:
        item=qualify(s['url'],s['title'],s['snippet'],'verified current opportunity','verified_seed')
        if item:
            provider_hits['verified_seed']+=1
            if item['source_url'] not in existing:
                item.update({'detected_at':now,'review_status':'PENDING_QA','department':'intent_opportunity_acquisition'})
                existing[item['source_url']]=item; new+=1

    for q in REDDIT_QUERIES:
        try:
            for item in search_reddit(q):
                provider_hits['reddit_api']+=1
                if item['source_url'] in existing: continue
                item.update({'detected_at':now,'review_status':'PENDING_QA','department':'intent_opportunity_acquisition'})
                existing[item['source_url']]=item; new+=1
        except Exception as e: errors.append({'provider':'reddit_api','query':q,'error':str(e)[:250]})

    for q in QUERIES:
        found=[]
        try: found=search_jina(q)
        except Exception as e: errors.append({'provider':'jina_search','query':q,'error':str(e)[:250]})
        if not found:
            try: found=search_ddg(q)
            except Exception as e: errors.append({'provider':'duckduckgo','query':q,'error':str(e)[:250]})
        for item in found:
            provider_hits[item['provider']]=provider_hits.get(item['provider'],0)+1
            if item['source_url'] in existing: continue
            item.update({'detected_at':now,'review_status':'PENDING_QA','department':'intent_opportunity_acquisition'})
            existing[item['source_url']]=item; new+=1

    signals=sorted(existing.values(),key=lambda x:x.get('detected_at',''),reverse=True)[:500]
    state={'agent':'AGENT_15A_INTENT_SCOUT','last_run_at':now,'signals_found':len(signals),'new_signals':new,'unreviewed':sum(1 for x in signals if x.get('review_status')=='PENDING_QA'),'queries_run':len(QUERIES)+len(REDDIT_QUERIES),'errors':len(errors),'query_errors':errors[:10],'provider_hits':provider_hits,'sources':sorted({x.get('source') for x in signals if x.get('source')})}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps({'generated_at':now,'signals':signals},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); LATEST.write_text(json.dumps({'state':state,'newest':signals[:25]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False))

if __name__=='__main__': main()
