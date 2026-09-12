const RAW_BASE = 'https://raw.githubusercontent.com/TimonGuldner/locenix-lead-research-agent/main/results/';

const SOURCES = {
  leadResearch: 'deterministic_state.json',
  deepQa: 'deep_qa_state.json',
  visibility: 'visibility_state.json',
  salesQueue: 'sales_queue_state.json',
  outreach: 'outreach_controller_state.json',
  manager: 'department_head_state.json'
};

async function readJson(file) {
  const res = await fetch(`${RAW_BASE}${file}?t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) return null;
  try { return await res.json(); } catch { return null; }
}

module.exports = async function handler(req, res) {
  try {
    const entries = await Promise.all(Object.entries(SOURCES).map(async ([key, file]) => [key, await readJson(file)]));
    const states = Object.fromEntries(entries);
    const qaBacklog = states.deepQa?.remaining_eligible ?? 0;
    const visibilityBacklog = states.visibility?.remaining_eligible ?? 0;
    const airtableErrors = states.salesQueue?.airtable_errors?.length ?? 0;
    const draftErrors = states.outreach?.error_count ?? 0;

    const agents = [
      { id: 1, key: 'leadResearch', name: 'Lead Research', role: 'Findet und qualifiziert neue lokale Unternehmen', status: states.leadResearch?.complete ? 'IDLE' : 'ACTIVE', metric: `${states.leadResearch?.lead_count ?? 0} Leads`, detail: `${states.leadResearch?.a_leads ?? 0} A-Leads · ${states.leadResearch?.last_rejected ?? 0} zuletzt verworfen` },
      { id: 2, key: 'deepQa', name: 'Deep QA', role: 'Prüft Leads tiefer und verifiziert Verkaufssignale', status: qaBacklog > 0 ? 'BACKLOG' : 'HEALTHY', metric: `${states.deepQa?.processed_count ?? 0} geprüft`, detail: `${qaBacklog} offen · ${states.deepQa?.final_a_total ?? 0} FINAL_A` },
      { id: 4, key: 'visibility', name: 'Maps Visibility', role: 'Misst beobachtete Google-Maps-Sichtbarkeit', status: visibilityBacklog > 0 ? 'BACKLOG' : 'HEALTHY', metric: `${states.visibility?.processed_count ?? 0} geprüft`, detail: `${states.visibility?.weak_visibility_total ?? 0} mit schwacher Sichtbarkeit` },
      { id: 3, key: 'salesQueue', name: 'Sales Queue', role: 'Priorisiert und synchronisiert nach Airtable', status: airtableErrors > 0 ? 'ERROR' : 'HEALTHY', metric: `${states.salesQueue?.queue_count ?? 0} Queue`, detail: `${states.salesQueue?.airtable_ready_count ?? 0} Airtable-ready · ${airtableErrors} Fehler` },
      { id: 6, key: 'outreach', name: 'Outreach Drafts', role: 'Erstellt und speichert Entwürfe in Airtable', status: draftErrors > 0 ? 'ERROR' : 'HEALTHY', metric: `${states.outreach?.stored_count ?? 0} Entwürfe`, detail: `${states.outreach?.eligible_count ?? 0} berechtigt · Versand deaktiviert` },
      { id: 7, key: 'manager', name: 'Growth Manager', role: 'Steuert die Abteilung und priorisiert Engpässe', status: 'ACTIVE', metric: states.manager?.decision || 'Monitoring', detail: states.manager?.reason || 'Überwacht Pipelinezustände' }
    ];

    res.setHeader('Cache-Control', 'no-store, max-age=0');
    res.status(200).json({ ok: true, updatedAt: new Date().toISOString(), agents, states });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message });
  }
};
