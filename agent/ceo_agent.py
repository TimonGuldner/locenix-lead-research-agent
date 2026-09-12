from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TASK = Path('tasks/ceo_task.json')
REPORT = Path('results/ceo_latest.json')
STATE = Path('results/ceo_state.json')
AGENTS_DIR = Path('tasks/agents')
DEPARTMENTS_DIR = Path('tasks/departments')

SOURCES = {
    'research': Path('results/deterministic_state.json'),
    'qa': Path('results/deep_qa_state.json'),
    'visibility': Path('results/visibility_state.json'),
    'sales': Path('results/sales_queue_state.json'),
    'outreach': Path('results/outreach_controller_state.json'),
    'manager': Path('results/department_head_state.json'),
}

def load(path, default=None):
    if default is None: default = {}
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception: return default

def val(obj, key):
    v = obj.get(key)
    return 'UNKNOWN' if v is None else v

def proposed_count(directory: Path):
    if not directory.exists(): return 0
    n = 0
    for p in directory.glob('*.json'):
        d = load(p)
        if str(d.get('status','')).upper() == 'PROPOSED': n += 1
    return n

def metrics(s):
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
        'proposed_agents': proposed_count(AGENTS_DIR),
        'proposed_departments': proposed_count(DEPARTMENTS_DIR),
    }

def number(v): return v if isinstance(v, (int,float)) else None

def decide(m):
    priorities=[]
    errors=m['airtable_errors']
    if errors not in ('UNKNOWN', [], None) or (number(m['draft_errors']) or 0)>0:
        priorities.append({'rank':1,'type':'STABILITY','owner':'Agent 7','action':'Resolve downstream sync/draft errors before increasing pipeline volume.','reason':'Downstream errors can propagate bad state.'})
    qr=number(m['qa_remaining'])
    vr=number(m['visibility_remaining'])
    ar=number(m['airtable_ready'])
    fa=number(m['final_a'])
    ds=number(m['drafts_stored'])
    if qr and qr>0:
        priorities.append({'rank':0,'type':'BOTTLENECK','owner':'Agent 7','action':f'Prioritize Deep QA until backlog falls below 5; current observed backlog: {qr}.','reason':'Qualified input is waiting for validation.'})
    if vr and vr>0:
        priorities.append({'rank':0,'type':'BOTTLENECK','owner':'Agent 7','action':f'Prioritize Maps Visibility; current observed backlog: {vr}.','reason':'Validated leads are waiting for visibility evidence.'})
    if fa is not None and ar is not None and ar < fa:
        priorities.append({'rank':0,'type':'BOTTLENECK','owner':'Agent 7','action':'Move remaining FINAL_A leads through Sales Queue/Airtable.','reason':'Qualified leads have not all reached sales readiness.'})
    if ar is not None and ds is not None and ds < ar:
        priorities.append({'rank':0,'type':'BOTTLENECK','owner':'Agent 7','action':'Generate and store missing outreach drafts.','reason':'Sales-ready leads are missing prepared outreach.'})
    if not priorities:
        priorities.append({'rank':0,'type':'GROWTH','owner':'Agent 7','action':'Keep research active and add high-quality qualified leads while downstream capacity is clear.','reason':'No observed downstream bottleneck.'})
    # Stability first, then actual bottlenecks, max 3; keep deterministic.
    priorities.sort(key=lambda x: (0 if x['type']=='STABILITY' else 1 if x['type']=='BOTTLENECK' else 2))
    priorities=priorities[:3]
    for i,p in enumerate(priorities,1): p['rank']=i
    p1=priorities[0]
    status='BLOCKED' if p1['type']=='STABILITY' else ('ATTENTION' if p1['type']=='BOTTLENECK' else 'HEALTHY')
    return status, priorities

def main():
    task=load(TASK)
    if not task.get('enabled'):
        print(json.dumps({'status':'disabled'})); return
    s={k:load(v) for k,v in SOURCES.items()}
    m=metrics(s)
    status, priorities=decide(m)
    now=datetime.now(timezone.utc).isoformat()
    report={
      'agent':'AGENT_0_LOCENIX_CEO',
      'generated_at':now,
      'company_status':status,
      'north_star':task.get('north_star','paid_customers'),
      'funnel_metrics':m,
      'biggest_bottleneck':priorities[0]['reason'],
      'priority_1':priorities[0],
      'priority_2':priorities[1] if len(priorities)>1 else None,
      'priority_3':priorities[2] if len(priorities)>2 else None,
      'manager_directive':priorities[0]['action'],
      'agent_health':{k:('AVAILABLE' if s[k] else 'UNKNOWN') for k in s},
      'opportunities':[],
      'risks':([priorities[0]['reason']] if status!='HEALTHY' else []),
      'proposed_agents':m['proposed_agents'],
      'proposed_departments':m['proposed_departments'],
      'human_decision_required': bool(m['proposed_agents'] or m['proposed_departments']),
      'autonomy':task.get('autonomy',{}),
      'executive_rule':'DECIDE_DELEGATE_MEASURE_IMPROVE',
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    STATE.write_text(json.dumps({
      'last_run_at':now,'company_status':status,'north_star':report['north_star'],
      'priority_1':priorities[0]['action'],'manager_directive':report['manager_directive'],
      'human_decision_required':report['human_decision_required']
    },ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
