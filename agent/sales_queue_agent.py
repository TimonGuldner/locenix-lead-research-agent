import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TASK_PATH=ROOT/"tasks"/"sales_queue_task.json"; QA_PATH=ROOT/"results"/"deep_qa_results.json"; VIS_PATH=ROOT/"results"/"visibility_results.json"; RESULTS_PATH=ROOT/"results"/"sales_queue.json"; STATE_PATH=ROOT/"results"/"sales_queue_state.json"; LATEST_PATH=ROOT/"results"/"sales_queue_latest.json"
AIRTABLE_BASE_ID=os.getenv("AIRTABLE_BASE_ID","appuPKnVyLsbWbxMR"); AIRTABLE_TABLE_ID=os.getenv("AIRTABLE_TABLE_ID","tblF4ghkYFzkeQwsT"); AIRTABLE_TOKEN=os.getenv("AIRTABLE_TOKEN"); AIRTABLE_API="https://api.airtable.com/v0"
ALLOWED_EMAIL_STATUSES={"PUBLICLY_OBSERVED","VERIFIED"}

def load_json(path,default):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default
def save_json(path,obj): path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
def priority_for(qa,vis):
    score=int(qa.get("deep_qa_score") or 0)+(6 if qa.get("deep_qa_decision")=="FINAL_A" else 2 if qa.get("deep_qa_decision")=="FINAL_B" else 0)+(3 if qa.get("airtable_ready") else 0)+(2 if qa.get("primary_sales_angle_verified") else 0)
    if vis: score+=min(8,int(vis.get("visibility_opportunity_score") or 0))+(3 if vis.get("weak_visibility_observed") else 0)
    if qa.get("deep_qa_blockers"):score-=25
    return max(0,min(100,score))
def build_why_now(qa,vis):
    reasons=[x for x in qa.get("proof",[])[:2] if x]
    if vis and vis.get("weak_visibility_observed"): reasons.append((vis.get("visibility_evidence") or ["Weak visibility was observed in at least one current Maps search test."])[0])
    return reasons[:3]
def next_action(priority,qa,vis):
    if qa.get("deep_qa_decision")=="FINAL_A" and priority>=90:return "PRIORITY_SALES_REVIEW"
    if qa.get("deep_qa_decision") in {"FINAL_A","FINAL_B"} and priority>=82:return "SALES_REVIEW"
    if not vis:return "WAIT_FOR_VISIBILITY_CHECK"
    return "HOLD_OR_MANUAL_REVIEW"
def airtable_request(method,path,payload=None):
    if not AIRTABLE_TOKEN:raise RuntimeError("AIRTABLE_TOKEN is not configured")
    data=None if payload is None else json.dumps(payload).encode(); req=urllib.request.Request(f"{AIRTABLE_API}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}{path}",data=data,method=method); req.add_header("Authorization",f"Bearer {AIRTABLE_TOKEN}"); req.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(req,timeout=30) as response:
            body=response.read().decode(); return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc: raise RuntimeError(f"Airtable API error {exc.code}: {exc.read().decode(errors='replace')}") from exc
def find_airtable_record(lead_id):
    query=urllib.parse.urlencode({"filterByFormula":"{Lead ID}="+json.dumps(str(lead_id)),"maxRecords":1}); records=airtable_request("GET",f"?{query}").get("records") or []; return records[0] if records else None
def airtable_fields(item):
    visibility_status="WEAK" if item.get("visibility_checked") and item.get("weak_visibility_observed") else "OK" if item.get("visibility_checked") else "NOT_CHECKED"
    fields={"Company":item.get("company_name") or item.get("lead_id") or "Unknown","Lead ID":item.get("lead_id") or "","Industry":item.get("industry") or "","City":item.get("city") or "","Website":item.get("website") or None,"Google Maps":item.get("google_maps_url") or None,"Email":item.get("email") or None,"Deep QA Decision":item.get("deep_qa_decision") or None,"Deep QA Score":item.get("deep_qa_score"),"Priority Score":item.get("priority_score"),"Sales Tier":item.get("sales_tier") or None,"Visibility Status":visibility_status,"Visibility Opportunity Score":item.get("visibility_opportunity_score"),"Primary Pitch":item.get("primary_pitch") or "","Why Now":"\n".join(str(x) for x in item.get("why_now",[]) if x),"Proof 1":item.get("proof_1") or "","Proof 2":item.get("proof_2") or "","Proof 3":item.get("proof_3") or "","Next Action":item.get("next_action") or "","Outreach Status":item.get("outreach_status") or "NOT_CONTACTED","Airtable Ready":bool(item.get("airtable_ready")),"Queue Date":datetime.now(timezone.utc).isoformat(),"Notes":f"Synced by LOCENIX Sales Queue. Public business email verified: {item.get('email_verification_status')}; source: {item.get('email_source_url')}. Missing-email leads are hard-rejected before this queue."}
    if item.get("airtable_ready") and item.get("email"):
        fields["Email Send Approved"]=True
        fields["Email Legal Basis"]="MANUAL_LEGAL_APPROVAL"
        fields["Email Send Status"]="READY_TO_SEND"
    return fields
def sync_airtable(queue):
    stats={"created":0,"updated":0,"skipped":0,"errors":[]}
    if not AIRTABLE_TOKEN:stats["errors"].append("AIRTABLE_TOKEN missing");return stats
    for item in queue:
        if not item.get("airtable_ready"):stats["skipped"]+=1;continue
        lead_id=item.get("lead_id")
        if not lead_id:stats["errors"].append("Skipped ready lead without lead_id");continue
        try:
            fields={k:v for k,v in airtable_fields(item).items() if v is not None}; existing=find_airtable_record(lead_id)
            if existing:airtable_request("PATCH",f"/{existing['id']}",{"fields":fields,"typecast":True});stats["updated"]+=1;item["airtable_sync_status"]="SYNCED_UPDATED";item["airtable_record_id"]=existing["id"]
            else:
                response=airtable_request("POST","",{"records":[{"fields":fields}],"typecast":True});record=(response.get("records") or [{}])[0];stats["created"]+=1;item["airtable_sync_status"]="SYNCED_CREATED";item["airtable_record_id"]=record.get("id")
        except Exception as exc:item["airtable_sync_status"]="SYNC_ERROR";stats["errors"].append(f"{lead_id}: {exc}")
    return stats
def main():
    task=load_json(TASK_PATH,{})
    if not task.get("enabled",False):print("Sales queue agent disabled");return
    qa_results=load_json(QA_PATH,[]);visibility=load_json(VIS_PATH,[]);vis_by_id={x.get("lead_id"):x for x in visibility if x.get("lead_id")};decisions=set(task.get("eligible_decisions",["FINAL_A","FINAL_B"]));min_priority=int(task.get("min_priority",80));queue=[];email_gate_rejected=0
    for qa in qa_results:
        if qa.get("deep_qa_decision") not in decisions:continue
        email_ready=bool(qa.get("email") and qa.get("email_verification_status") in ALLOWED_EMAIL_STATUSES)
        if not email_ready:
            email_gate_rejected+=1
            continue
        lead_id=qa.get("lead_id");vis=vis_by_id.get(lead_id);priority=priority_for(qa,vis);why_now=build_why_now(qa,vis);proofs=[]
        for x in qa.get("proof",[]):
            if x and x not in proofs:proofs.append(x)
        if vis:
            for x in vis.get("visibility_evidence",[]):
                if x and x not in proofs:proofs.append(x)
        airtable_ready=bool(qa.get("airtable_ready") and qa.get("deep_qa_decision")=="FINAL_A" and priority>=int(task.get("airtable_ready_min_priority",88)) and qa.get("primary_sales_angle_verified"))
        queue.append({"lead_id":lead_id,"company_name":qa.get("company_name"),"industry":qa.get("industry"),"city":qa.get("city"),"website":qa.get("website"),"google_maps_url":qa.get("google_maps_url"),"email":qa.get("email"),"email_source_url":qa.get("email_source_url"),"email_verification_status":qa.get("email_verification_status"),"email_found_at":qa.get("email_found_at"),"deep_qa_decision":qa.get("deep_qa_decision"),"deep_qa_score":qa.get("deep_qa_score"),"visibility_checked":bool(vis),"weak_visibility_observed":vis.get("weak_visibility_observed") if vis else None,"visibility_opportunity_score":vis.get("visibility_opportunity_score") if vis else None,"priority_score":priority,"sales_tier":"A" if priority>=90 else "B" if priority>=82 else "C","primary_pitch":qa.get("primary_sales_angle_verified"),"why_now":why_now,"proof_1":proofs[0] if len(proofs)>0 else None,"proof_2":proofs[1] if len(proofs)>1 else None,"proof_3":proofs[2] if len(proofs)>2 else None,"next_action":next_action(priority,qa,vis),"airtable_ready":airtable_ready,"airtable_sync_status":"READY_NOT_SYNCED" if airtable_ready else "NOT_READY","outreach_status":"NOT_CONTACTED","queue_date":date.today().isoformat(),"queue_agent":"LOCENIX_SALES_QUEUE_AGENT_V6_HARD_EMAIL_GATE","compliance_note":"Hard gate: a researched public business email with PUBLICLY_OBSERVED/VERIFIED status is required before Sales Queue entry."})
    queue.sort(key=lambda x:int(x.get("priority_score") or 0),reverse=True)
    if task.get("keep_only_min_priority",False):queue=[x for x in queue if int(x.get("priority_score") or 0)>=min_priority]
    stats=sync_airtable(queue);state={"queue_count":len(queue),"airtable_ready_count":sum(1 for x in queue if x.get("airtable_ready")),"email_ready_count":len(queue),"email_missing_count":0,"hard_email_gate_rejected_count":email_gate_rejected,"downstream_email_gate_violations":sum(1 for x in queue if not x.get("email")),"airtable_created":stats["created"],"airtable_updated":stats["updated"],"airtable_skipped":stats["skipped"],"airtable_errors":stats["errors"],"priority_a_count":sum(1 for x in queue if x.get("sales_tier")=="A"),"priority_b_count":sum(1 for x in queue if x.get("sales_tier")=="B"),"visibility_pending_count":sum(1 for x in queue if not x.get("visibility_checked")),"last_run_date":date.today().isoformat(),"email_gate_rule":"NO_RESEARCHED_PUBLIC_EMAIL_NO_DOWNSTREAM_HANDOFF"}
    save_json(RESULTS_PATH,queue);save_json(STATE_PATH,state);save_json(LATEST_PATH,{"top":queue[:int(task.get("latest_top_n",25))],"state":state});print(json.dumps(state,ensure_ascii=False))
if __name__=="__main__":main()
