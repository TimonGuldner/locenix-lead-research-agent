const REPO = 'TimonGuldner/locenix-lead-research-agent';

function slugify(value) {
  return String(value || 'new-department')
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60) || 'new-department';
}

function extractName(message) {
  const clean = String(message || '').trim();
  const match = clean.match(/(?:abteilung|department)\s+[„“"']?([^,.:;\n]{2,60})/i);
  if (match) return match[1].replace(/[„“"']/g, '').trim();
  return clean.split(/[.!?\n]/)[0].replace(/^(erstelle|baue|lege|mach)\s+/i, '').trim().slice(0, 60) || 'Neue Abteilung';
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'POST required' });

  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    return res.status(503).json({
      ok: false,
      setupRequired: true,
      error: 'GITHUB_TOKEN is not configured in Vercel. No department was created.'
    });
  }

  const message = String(req.body?.message || '').trim();
  if (message.length < 5) return res.status(400).json({ ok: false, error: 'Bitte beschreibe die neue Abteilung genauer.' });

  const name = extractName(message);
  const slug = slugify(name);
  const path = `tasks/departments/${slug}.json`;
  const now = new Date().toISOString();
  const department = {
    name,
    slug,
    status: 'PROPOSED',
    created_via: 'LOCENIX_COMMAND_CENTER',
    created_at: now,
    request: message,
    manager: 'LOCENIX_GROWTH_MANAGER',
    autonomy: {
      can_research: true,
      can_write_internal_files: true,
      can_send_external_messages: false,
      requires_human_approval_for_external_actions: true
    },
    next_step: 'Growth Manager reviews the request and creates the required agent/workflow specification.'
  };

  const apiUrl = `https://api.github.com/repos/${REPO}/contents/${path}`;
  const existing = await fetch(apiUrl, { headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' } });
  if (existing.ok) return res.status(409).json({ ok: false, error: `Die Abteilung ${name} existiert bereits.` });
  if (existing.status !== 404) return res.status(existing.status).json({ ok: false, error: 'GitHub konnte nicht geprüft werden.' });

  const payload = {
    message: `Command Center: propose department ${name}`,
    content: Buffer.from(JSON.stringify(department, null, 2) + '\n').toString('base64'),
    branch: 'main'
  };

  const created = await fetch(apiUrl, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    body: JSON.stringify(payload)
  });

  const data = await created.json().catch(() => ({}));
  if (!created.ok) return res.status(created.status).json({ ok: false, error: data?.message || 'GitHub write failed' });

  res.status(201).json({ ok: true, department, path, commit: data?.commit?.sha || null });
};
